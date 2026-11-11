"""Admin review of team knowledge base uploads that hit the sensitive-value rules (stored on the Gateway).

Design: docs/superpowers/specs/2026-11-10-team-knowledge-base.md §4.1. The intranet team-kb service holds such
files instead of rejecting them and reports only metadata plus masked hit lines; the full text never leaves the
Worker (the admin opens it through an intranet-only link). The admin approves or rejects here; the service pulls
the decision, applies it and reports back.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"
REVIEW_STATUSES = (REVIEW_PENDING, REVIEW_APPROVED, REVIEW_REJECTED)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_MAX_HITS = 50
_TEXT_LIMITS = {"uploader": 40, "group_name": 20, "owner": 40, "file_name": 120, "target_path": 300, "view_url": 300, "title": 200}


class ReviewError(ValueError):
    """Invalid review payload or decision."""


def parse_review_item(raw: Any) -> dict[str, Any]:
    """Validate one item reported by the team-kb service (bounded strings only, no file content)."""
    if not isinstance(raw, dict):
        raise ReviewError("item must be an object")
    review_id = str(raw.get("id") or "")
    if not _ID.fullmatch(review_id):
        raise ReviewError("invalid id")
    item: dict[str, Any] = {"review_id": review_id}
    for key, limit in _TEXT_LIMITS.items():
        source = {"group_name": "group", "file_name": "name", "target_path": "path"}.get(key, key)
        item[key] = str(raw.get(source) or "")[:limit]
    if item["view_url"] and not item["view_url"].startswith(("http://", "https://")):
        raise ReviewError("invalid view_url")
    hits = raw.get("hits") or []
    if not isinstance(hits, list) or not hits:
        raise ReviewError("hits required")
    item["hits"] = [
        {"kind": str(h.get("kind") or "")[:20], "line": int(h.get("line") or 0), "context": str(h.get("context") or "")[:300]}
        for h in hits[:_MAX_HITS] if isinstance(h, dict)
    ]
    try:
        item["cases"] = max(0, int(raw.get("cases") or 0))
        item["uploaded_at"] = max(0, int(raw.get("uploaded_at") or 0))
    except (TypeError, ValueError) as exc:
        raise ReviewError("invalid number") from exc
    return item


class TeamKbReviewStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS team_kb_reviews (
                    review_id TEXT PRIMARY KEY,
                    uploader TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    cases INTEGER NOT NULL DEFAULT 0,
                    hits_json TEXT NOT NULL,
                    view_url TEXT NOT NULL DEFAULT '',
                    uploaded_at INTEGER NOT NULL DEFAULT 0,
                    received_at INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    decided_by TEXT NOT NULL DEFAULT '',
                    decided_at INTEGER NOT NULL DEFAULT 0,
                    applied_at INTEGER NOT NULL DEFAULT 0,
                    apply_ok INTEGER NOT NULL DEFAULT 0,
                    apply_message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_team_kb_reviews_status ON team_kb_reviews (status, received_at);
                """
            )
            conn.commit()

    def report(self, items: list[dict[str, Any]], now: Optional[int] = None) -> int:
        """Insert newly held files; re-reports of a known id are ignored (decisions are never overwritten)."""
        timestamp = int(time.time()) if now is None else now
        inserted = 0
        with self._connect() as conn:
            for item in items:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO team_kb_reviews (review_id, uploader, group_name, owner, file_name, target_path,
                        title, cases, hits_json, view_url, uploaded_at, received_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (item["review_id"], item["uploader"], item["group_name"], item["owner"], item["file_name"],
                     item["target_path"], item["title"], item["cases"], json.dumps(item["hits"], ensure_ascii=False),
                     item["view_url"], item["uploaded_at"], timestamp, REVIEW_PENDING),
                )
                inserted += cursor.rowcount
            conn.commit()
        return inserted

    def list_reviews(self, status: str, *, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        if status not in REVIEW_STATUSES:
            raise ReviewError("unknown status")
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM team_kb_reviews WHERE status = ?", (status,)).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM team_kb_reviews WHERE status = ? ORDER BY received_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        return [self._public(row) for row in rows], total

    def count_pending(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM team_kb_reviews WHERE status = ?", (REVIEW_PENDING,)).fetchone()[0]

    def decide(self, review_id: str, action: str, *, operator: str, note: str = "", now: Optional[int] = None) -> dict[str, Any]:
        status = {"approve": REVIEW_APPROVED, "reject": REVIEW_REJECTED}.get(action)
        if status is None:
            raise ReviewError("action 只能是 approve / reject")
        timestamp = int(time.time()) if now is None else now
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE team_kb_reviews SET status = ?, note = ?, decided_by = ?, decided_at = ? "
                "WHERE review_id = ? AND status = ?",
                (status, note[:500], operator, timestamp, review_id, REVIEW_PENDING),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise ReviewError("该文件不在待审核状态（可能已处理）")
        return {"review_id": review_id, "status": status}

    def decisions_to_apply(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT review_id, status, decided_by, note FROM team_kb_reviews WHERE status != ? AND applied_at = 0",
                (REVIEW_PENDING,),
            ).fetchall()
        return [{"id": row["review_id"], "action": "approve" if row["status"] == REVIEW_APPROVED else "reject",
                 "operator": row["decided_by"], "note": row["note"]} for row in rows]

    def mark_applied(self, review_id: str, *, ok: bool, message: str, now: Optional[int] = None) -> bool:
        timestamp = int(time.time()) if now is None else now
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE team_kb_reviews SET applied_at = ?, apply_ok = ?, apply_message = ? "
                "WHERE review_id = ? AND status != ?",
                (timestamp, 1 if ok else 0, message[:500], review_id, REVIEW_PENDING),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["hits"] = json.loads(item.pop("hits_json") or "[]")
        return item

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn
