"""Gateway outbox for events relayed to the intranet by the Worker.

The Gateway (public cloud) cannot reach intranet services, so it only records
events here; the Worker pulls them over its existing authenticated channel,
delivers them inside the intranet and reports the outcome.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

EVENT_PENDING = "pending"
EVENT_LEASED = "leased"
EVENT_DELIVERED = "delivered"
EVENT_FAILED = "failed"
EVENT_EXPIRED = "expired"


class OutboundEventStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_events (
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    leased_at INTEGER,
                    leased_by TEXT NOT NULL DEFAULT '',
                    delivered_at INTEGER,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.commit()

    def add(self, event_id: str, kind: str, payload: dict[str, Any], now: int | None = None) -> bool:
        """Record an event once (same ``event_id`` is ignored)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO outbound_events (event_id, kind, payload_json, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_id, kind, json.dumps(payload, ensure_ascii=False), EVENT_PENDING, self._now(now)),
            )
            conn.commit()
        return cursor.rowcount > 0

    def claim(
        self,
        worker_id: str,
        *,
        limit: int = 10,
        lease_seconds: int = 60,
        max_age_seconds: int = 3600,
        max_attempts: int = 5,
        now: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lease pending (or lease-expired) events; stale or exhausted ones are retired."""
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE outbound_events SET status = ?, last_error = 'too old to deliver' "
                "WHERE status IN (?, ?) AND created_at < ?",
                (EVENT_EXPIRED, EVENT_PENDING, EVENT_LEASED, timestamp - max_age_seconds),
            )
            conn.execute(
                "UPDATE outbound_events SET status = ? WHERE status = ? AND leased_at < ? AND attempts >= ?",
                (EVENT_FAILED, EVENT_LEASED, timestamp - lease_seconds, max_attempts),
            )
            rows = conn.execute(
                "SELECT event_id, kind, payload_json FROM outbound_events "
                "WHERE status = ? OR (status = ? AND leased_at < ?) ORDER BY created_at LIMIT ?",
                (EVENT_PENDING, EVENT_LEASED, timestamp - lease_seconds, limit),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE outbound_events SET status = ?, leased_by = ?, leased_at = ?, attempts = attempts + 1 "
                    "WHERE event_id = ?",
                    (EVENT_LEASED, worker_id, timestamp, row["event_id"]),
                )
            conn.commit()
        return [
            {"event_id": row["event_id"], "kind": row["kind"], "payload": json.loads(row["payload_json"])}
            for row in rows
        ]

    def report(
        self,
        event_id: str,
        worker_id: str,
        *,
        delivered: bool,
        error: str = "",
        max_attempts: int = 5,
        now: int | None = None,
    ) -> bool:
        """Apply the Worker's delivery outcome; only the leaseholder may report."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM outbound_events WHERE event_id = ? AND status = ? AND leased_by = ?",
                (event_id, EVENT_LEASED, worker_id),
            ).fetchone()
            if row is None:
                return False
            if delivered:
                status, delivered_at = EVENT_DELIVERED, self._now(now)
            else:
                status = EVENT_FAILED if int(row["attempts"]) >= max_attempts else EVENT_PENDING
                delivered_at = None
            conn.execute(
                "UPDATE outbound_events SET status = ?, delivered_at = ?, last_error = ? WHERE event_id = ?",
                (status, delivered_at, error[:300], event_id),
            )
            conn.commit()
        return True

    def get(self, event_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM outbound_events WHERE event_id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now(now: int | None) -> int:
        return int(time.time()) if now is None else now
