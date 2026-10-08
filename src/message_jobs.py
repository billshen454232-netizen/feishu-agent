from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

JobStatus = Literal[
    "queued",
    "processing",
    "awaiting_confirmation",
    "confirmed",
    "queued_for_worker",
    "leased",
    "submission_in_progress",
    "submitted_result_unknown",
    "done",
    "failed",
    "cancelled",
    "expired",
]


@dataclass(frozen=True)
class MessageJob:
    message_id: str
    event_id: str
    chat_id: str
    chat_type: str
    user_id: str
    text: str
    status: str
    attempts: int
    created_at: int
    updated_at: int
    last_error: str
    plan_json: str
    confirmation_message_id: str
    confirmation_token: str
    confirmation_method: str
    worker_id: str
    worker_leased_at: int | None


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
                    chat_type TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_error TEXT NOT NULL,
                    plan_json TEXT NOT NULL DEFAULT '',
                    confirmation_message_id TEXT NOT NULL DEFAULT '',
                    confirmation_token TEXT NOT NULL DEFAULT '',
                    confirmation_method TEXT NOT NULL DEFAULT 'text',
                    worker_id TEXT NOT NULL DEFAULT '',
                    worker_leased_at INTEGER
                )
                """
            )
            self._add_column_if_missing(conn, "chat_type", "TEXT NOT NULL DEFAULT ''")
            self._add_column_if_missing(conn, "plan_json", "TEXT NOT NULL DEFAULT ''")
            self._add_column_if_missing(conn, "confirmation_message_id", "TEXT NOT NULL DEFAULT ''")
            self._add_column_if_missing(conn, "confirmation_token", "TEXT NOT NULL DEFAULT ''")
            self._add_column_if_missing(conn, "confirmation_method", "TEXT NOT NULL DEFAULT 'text'")
            self._add_column_if_missing(conn, "worker_id", "TEXT NOT NULL DEFAULT ''")
            self._add_column_if_missing(conn, "worker_leased_at", "INTEGER")
            conn.commit()

    def create_if_new(
        self,
        message_id: str,
        event_id: str,
        chat_id: str,
        user_id: str,
        text: str,
        now: int | None = None,
        *,
        chat_type: str = "",
    ) -> tuple[MessageJob, bool]:
        current = self.get(message_id)
        if current is not None:
            return current, False
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO message_jobs (
                    message_id, event_id, chat_id, chat_type, user_id, text,
                    status, attempts, created_at, updated_at, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, '')
                """,
                (message_id, event_id, chat_id, chat_type, user_id, text, timestamp, timestamp),
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
                SELECT message_id, event_id, chat_id, chat_type, user_id, text,
                       status, attempts, created_at, updated_at, last_error,
                       plan_json, confirmation_message_id, confirmation_token, confirmation_method,
                       worker_id, worker_leased_at
                FROM message_jobs
                WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def mark_processing(self, message_id: str, now: int | None = None) -> None:
        self._update_status(message_id, "processing", self._now(now))

    def mark_submission_in_progress(self, message_id: str, now: int | None = None) -> None:
        """Record that a confirmed request is being submitted to Muliu."""
        self._update_status(message_id, "submission_in_progress", self._now(now))

    def queue_for_worker(self, message_id: str, now: int | None = None) -> bool:
        """Make a locally validated, confirmed plan available to an intranet worker."""
        timestamp = self._now(now)
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE message_jobs
                SET status = 'queued_for_worker', worker_id = '', worker_leased_at = NULL,
                    updated_at = ?
                WHERE message_id = ? AND status = 'confirmed'
                """,
                (timestamp, message_id),
            )
            conn.commit()
        return updated.rowcount == 1

    def claim_next_worker_job(self, worker_id: str, now: int | None = None) -> MessageJob | None:
        """Atomically lease the next plan while globally serializing Muliu Task 89."""
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active_lease = conn.execute(
                "SELECT 1 FROM message_jobs WHERE status = 'leased' LIMIT 1"
            ).fetchone()
            if active_lease is not None:
                conn.rollback()
                return None
            row = conn.execute(
                """
                SELECT message_id, event_id, chat_id, chat_type, user_id, text,
                       status, attempts, created_at, updated_at, last_error,
                       plan_json, confirmation_message_id, confirmation_token, confirmation_method,
                       worker_id, worker_leased_at
                FROM message_jobs
                WHERE status = 'queued_for_worker'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            updated = conn.execute(
                """
                UPDATE message_jobs
                SET status = 'leased', worker_id = ?, worker_leased_at = ?, updated_at = ?
                WHERE message_id = ? AND status = 'queued_for_worker'
                """,
                (worker_id, timestamp, timestamp, row["message_id"]),
            )
            if updated.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
        return self.get(str(row["message_id"]))

    def complete_worker_job(
        self,
        message_id: str,
        worker_id: str,
        *,
        status: str,
        error: str = "",
        now: int | None = None,
    ) -> MessageJob | None:
        """Store a terminal result reported by the worker that owns the lease."""
        if status not in {"done", "failed", "submitted_result_unknown"}:
            raise ValueError("worker terminal status is invalid")
        timestamp = self._now(now)
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE message_jobs
                SET status = ?, last_error = ?, updated_at = ?
                WHERE message_id = ? AND status = 'leased' AND worker_id = ?
                """,
                (status, error, timestamp, message_id, worker_id),
            )
            conn.commit()
        if updated.rowcount != 1:
            return None
        return self.get(message_id)

    def renew_worker_lease(self, message_id: str, worker_id: str, now: int | None = None) -> bool:
        """Renew only the lease owned by the calling worker."""
        timestamp = self._now(now)
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE message_jobs
                SET worker_leased_at = ?, updated_at = ?
                WHERE message_id = ? AND status = 'leased' AND worker_id = ?
                """,
                (timestamp, timestamp, message_id, worker_id),
            )
            conn.commit()
        return updated.rowcount == 1

    def expire_worker_leases(
        self,
        lease_timeout_seconds: float,
        now: int | None = None,
    ) -> list[MessageJob]:
        """Mark lost worker leases unknown instead of allowing a remote replay."""
        timestamp = self._now(now)
        latest_allowed_lease = timestamp - lease_timeout_seconds
        reason = "内网 Worker 心跳超时，无法确认已领取操作是否已提交给 Muliu；请勿重复确认或重试"
        expired_ids: list[str] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id
                FROM message_jobs
                WHERE status = 'leased' AND worker_leased_at IS NOT NULL
                      AND worker_leased_at < ?
                ORDER BY worker_leased_at ASC
                """,
                (latest_allowed_lease,),
            ).fetchall()
            for row in rows:
                updated = conn.execute(
                    """
                    UPDATE message_jobs
                    SET status = 'submitted_result_unknown', last_error = ?, updated_at = ?
                    WHERE message_id = ? AND status = 'leased' AND worker_leased_at < ?
                    """,
                    (reason, timestamp, row["message_id"], latest_allowed_lease),
                )
                if updated.rowcount == 1:
                    expired_ids.append(str(row["message_id"]))
            conn.commit()
        return [job for message_id in expired_ids if (job := self.get(message_id)) is not None]

    def mark_submitted_result_unknown(self, message_id: str, reason: str, now: int | None = None) -> None:
        """Preserve an accepted or interrupted operation without allowing replay."""
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE message_jobs
                SET status = 'submitted_result_unknown', last_error = ?, updated_at = ?
                WHERE message_id = ?
                """,
                (reason, timestamp, message_id),
            )
            conn.commit()

    def mark_submission_failed(self, message_id: str, error: str, now: int | None = None) -> None:
        """Mark an operation failed only when Muliu was not known to accept it."""
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE message_jobs
                SET status = 'failed', attempts = attempts + 1, last_error = ?, updated_at = ?
                WHERE message_id = ? AND status = 'submission_in_progress'
                """,
                (error, timestamp, message_id),
            )
            conn.commit()

    def mark_done(self, message_id: str, now: int | None = None) -> None:
        self._update_status(message_id, "done", self._now(now))

    def claim_confirmation(
        self,
        chat_id: str,
        confirmation_token: str,
        max_age_seconds: int,
        now: int | None = None,
    ) -> MessageJob | None:
        """Atomically claim one pending plan, preventing duplicate confirmations."""
        timestamp = self._now(now)
        minimum_created_at = timestamp - max_age_seconds
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message_id, event_id, chat_id, chat_type, user_id, text,
                       status, attempts, created_at, updated_at, last_error,
                       plan_json, confirmation_message_id, confirmation_token, confirmation_method,
                       worker_id, worker_leased_at
                FROM message_jobs
                WHERE chat_id = ? AND confirmation_token = ?
                      AND confirmation_method = 'text'
                      AND status = 'awaiting_confirmation' AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (chat_id, confirmation_token, minimum_created_at),
            ).fetchone()
            if row is None:
                return None
            updated = conn.execute(
                """
                UPDATE message_jobs
                SET status = 'confirmed', updated_at = ?
                WHERE message_id = ? AND status = 'awaiting_confirmation'
                """,
                (timestamp, row["message_id"]),
            )
            if updated.rowcount != 1:
                return None
            conn.commit()
        return self._row_to_job(row)

    def claim_card_confirmation(
        self,
        request_message_id: str,
        confirmation_message_id: str,
        chat_id: str,
        user_id: str,
        confirmation_token: str,
        max_age_seconds: int,
        now: int | None = None,
    ) -> MessageJob | None:
        """Atomically confirm the exact stored card plan for its original requester.

        Card payloads are untrusted identifiers only. This lookup never receives a
        script path or argument list from Feishu, and the subsequent executor still
        parses the plan persisted in ``message_jobs``.
        """
        timestamp = self._now(now)
        minimum_created_at = timestamp - max_age_seconds
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message_id, event_id, chat_id, chat_type, user_id, text,
                       status, attempts, created_at, updated_at, last_error,
                       plan_json, confirmation_message_id, confirmation_token, confirmation_method,
                       worker_id, worker_leased_at
                FROM message_jobs
                WHERE message_id = ? AND confirmation_message_id = ?
                      AND chat_id = ? AND user_id = ?
                      AND confirmation_token = ?
                      AND status = 'awaiting_confirmation' AND created_at >= ?
                """,
                (
                    request_message_id,
                    confirmation_message_id,
                    chat_id,
                    user_id,
                    confirmation_token,
                    minimum_created_at,
                ),
            ).fetchone()
            if row is None:
                return None
            updated = conn.execute(
                """
                UPDATE message_jobs
                SET status = 'confirmed', updated_at = ?
                WHERE message_id = ? AND status = 'awaiting_confirmation'
                """,
                (timestamp, request_message_id),
            )
            if updated.rowcount != 1:
                return None
            conn.commit()
        return self._row_to_job(row)

    def cancel_card_confirmation(
        self,
        request_message_id: str,
        confirmation_message_id: str,
        chat_id: str,
        user_id: str,
        confirmation_token: str,
        max_age_seconds: int,
        now: int | None = None,
    ) -> MessageJob | None:
        """Atomically cancel an exact pending card plan for its original requester."""
        timestamp = self._now(now)
        minimum_created_at = timestamp - max_age_seconds
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message_id, event_id, chat_id, chat_type, user_id, text,
                       status, attempts, created_at, updated_at, last_error,
                       plan_json, confirmation_message_id, confirmation_token, confirmation_method,
                       worker_id, worker_leased_at
                FROM message_jobs
                WHERE message_id = ? AND confirmation_message_id = ?
                      AND chat_id = ? AND user_id = ?
                      AND confirmation_token = ?
                      AND status = 'awaiting_confirmation' AND created_at >= ?
                """,
                (
                    request_message_id,
                    confirmation_message_id,
                    chat_id,
                    user_id,
                    confirmation_token,
                    minimum_created_at,
                ),
            ).fetchone()
            if row is None:
                return None
            updated = conn.execute(
                """
                UPDATE message_jobs
                SET status = 'cancelled', last_error = ?, updated_at = ?
                WHERE message_id = ? AND status = 'awaiting_confirmation'
                """,
                ("请求人已在交互卡片中取消待确认操作", timestamp, request_message_id),
            )
            if updated.rowcount != 1:
                return None
            conn.commit()
        return self._row_to_job(row)

    def mark_awaiting_confirmation(
        self,
        message_id: str,
        plan_json: str,
        confirmation_message_id: str,
        confirmation_token: str,
        confirmation_method: str = "text",
        now: int | None = None,
    ) -> None:
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE message_jobs
                SET status = 'awaiting_confirmation', plan_json = ?,
                    confirmation_message_id = ?, confirmation_token = ?,
                    confirmation_method = ?, updated_at = ?
                WHERE message_id = ?
                """,
                (
                    plan_json,
                    confirmation_message_id,
                    confirmation_token,
                    confirmation_method,
                    timestamp,
                    message_id,
                ),
            )
            conn.commit()

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
                SELECT message_id, event_id, chat_id, chat_type, user_id, text,
                       status, attempts, created_at, updated_at, last_error,
                       plan_json, confirmation_message_id, confirmation_token, confirmation_method,
                       worker_id, worker_leased_at
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
                SELECT message_id, event_id, chat_id, chat_type, user_id, text,
                       status, attempts, created_at, updated_at, last_error,
                       plan_json, confirmation_message_id, confirmation_token, confirmation_method,
                       worker_id, worker_leased_at
                FROM message_jobs
                WHERE status IN ('queued', 'processing', 'awaiting_confirmation') AND created_at < ?
                ORDER BY created_at ASC
                """,
                (maximum_created_at,),
            ).fetchall()
            conn.execute(
                """
                UPDATE message_jobs
                SET status = 'expired', updated_at = ?
                WHERE status IN ('queued', 'processing', 'awaiting_confirmation') AND created_at < ?
                """,
                (timestamp, maximum_created_at),
            )
            conn.execute(
                """
                UPDATE message_jobs
                SET status = 'submitted_result_unknown',
                    last_error = ?, updated_at = ?
                WHERE status IN ('submission_in_progress', 'confirmed')
                """,
                ("机器人重启时无法确认已提交操作的最终结果；请勿重复确认或重试", timestamp),
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
    def _add_column_if_missing(conn: sqlite3.Connection, column_name: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(message_jobs)")}
        if column_name not in columns:
            conn.execute("ALTER TABLE message_jobs ADD COLUMN {} {}".format(column_name, definition))

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> MessageJob:
        return MessageJob(
            message_id=str(row["message_id"]),
            event_id=str(row["event_id"]),
            chat_id=str(row["chat_id"]),
            chat_type=str(row["chat_type"]),
            user_id=str(row["user_id"]),
            text=str(row["text"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            last_error=str(row["last_error"]),
            plan_json=str(row["plan_json"]),
            confirmation_message_id=str(row["confirmation_message_id"]),
            confirmation_token=str(row["confirmation_token"]),
            confirmation_method=str(row["confirmation_method"]),
            worker_id=str(row["worker_id"]),
            worker_leased_at=(int(row["worker_leased_at"]) if row["worker_leased_at"] is not None else None),
        )
