from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

JobStatus = Literal["queued", "processing", "done", "failed", "expired"]


@dataclass(frozen=True)
class MessageJob:
    message_id: str
    event_id: str
    chat_id: str
    user_id: str
    text: str
    status: str
    attempts: int
    created_at: int
    updated_at: int
    last_error: str


class MessageJobStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_jobs (
                    message_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_error TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create_if_new(
        self,
        message_id: str,
        event_id: str,
        chat_id: str,
        user_id: str,
        text: str,
        now: int | None = None,
    ) -> tuple[MessageJob, bool]:
        current = self.get(message_id)
        if current is not None:
            return current, False
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO message_jobs (message_id, event_id, chat_id, user_id, text, status, attempts, created_at, updated_at, last_error)
                VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, '')
                """,
                (message_id, event_id, chat_id, user_id, text, timestamp, timestamp),
            )
            conn.commit()
        job = self.get(message_id)
        if job is None:
            raise RuntimeError(f"Failed to create message job: {message_id}")
        return job, True

    def get(self, message_id: str) -> MessageJob | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message_id, event_id, chat_id, user_id, text, status, attempts, created_at, updated_at, last_error
                FROM message_jobs
                WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def mark_processing(self, message_id: str, now: int | None = None) -> None:
        self._update_status(message_id, "processing", self._now(now))

    def mark_done(self, message_id: str, now: int | None = None) -> None:
        self._update_status(message_id, "done", self._now(now))

    def mark_failed(self, message_id: str, error: str, now: int | None = None) -> None:
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE message_jobs
                SET status = 'failed', attempts = attempts + 1, last_error = ?, updated_at = ?
                WHERE message_id = ?
                """,
                (error, timestamp, message_id),
            )
            conn.commit()

    def list_recoverable_unfinished(self, now: int | None, recovery_window_seconds: int) -> list[MessageJob]:
        timestamp = self._now(now)
        minimum_created_at = timestamp - recovery_window_seconds
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, event_id, chat_id, user_id, text, status, attempts, created_at, updated_at, last_error
                FROM message_jobs
                WHERE status IN ('queued', 'processing') AND created_at >= ?
                ORDER BY created_at ASC
                """,
                (minimum_created_at,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def expire_stale_unfinished(self, now: int | None, recovery_window_seconds: int) -> list[MessageJob]:
        timestamp = self._now(now)
        maximum_created_at = timestamp - recovery_window_seconds
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, event_id, chat_id, user_id, text, status, attempts, created_at, updated_at, last_error
                FROM message_jobs
                WHERE status IN ('queued', 'processing') AND created_at < ?
                ORDER BY created_at ASC
                """,
                (maximum_created_at,),
            ).fetchall()
            conn.execute(
                """
                UPDATE message_jobs
                SET status = 'expired', updated_at = ?
                WHERE status IN ('queued', 'processing') AND created_at < ?
                """,
                (timestamp, maximum_created_at),
            )
            conn.commit()
        return [self._row_to_job(row) for row in rows]

    def _update_status(self, message_id: str, status: JobStatus, timestamp: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE message_jobs SET status = ?, updated_at = ? WHERE message_id = ?",
                (status, timestamp, message_id),
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now(now: int | None) -> int:
        return int(time.time()) if now is None else now

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> MessageJob:
        return MessageJob(
            message_id=str(row["message_id"]),
            event_id=str(row["event_id"]),
            chat_id=str(row["chat_id"]),
            user_id=str(row["user_id"]),
            text=str(row["text"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            last_error=str(row["last_error"]),
        )
