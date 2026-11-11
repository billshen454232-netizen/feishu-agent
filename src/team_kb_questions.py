"""Feishu questions for the team knowledge base, waiting on the Gateway for the intranet team-kb service.

Design: docs/superpowers/specs/2026-11-10-team-knowledge-base.md §5. The Gateway stores only the question and the
final answer; the team-kb service long-polls ``claim``, retrieves and answers on the Worker, and posts the answer back.
Questions that nobody claims, or that are not answered in time, fail with a generic reply.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

QUESTION_PENDING = "pending"
QUESTION_CLAIMED = "claimed"
QUESTION_ANSWERED = "answered"
QUESTION_FAILED = "failed"

CLAIM_TIMEOUT_SECONDS = 60
ANSWER_TIMEOUT_SECONDS = 120
MAX_QUESTION_CHARS = 500
MAX_ANSWER_CHARS = 3000
MAX_SOURCES = 5
MAX_REFERENCES = 3

# 「问题 浮桥谁测的」「问题：…」; a separator is required so 「问题是 5000 服起不来」 still goes to the planner.
PREFIX = re.compile(r"^\s*问题(?:\s*[:：,，]\s*|\s+|$)")
REPLY_HEADER = "📒 组内知识库"
UNAVAILABLE_REPLY = "📒 组内知识库暂时不可用（内网知识库服务没有响应），请稍后再问，或到内网知识库站点搜索。"
FAILED_REPLY = "📒 组内知识库这次没能生成回答，请稍后再问一次，或到内网知识库站点搜索。"


class QuestionError(ValueError):
    """Invalid answer payload."""


def strip_prefix(text: str) -> Optional[str]:
    """The question when the message starts with 「问题」 + space / colon / comma (forces the knowledge base, design §5.7-1)."""
    match = PREFIX.match(text or "")
    if not match:
        return None
    return text[match.end():].strip()


def parse_answer(raw: Any) -> dict[str, Any]:
    """Validate the team-kb service's answer (bounded strings only)."""
    if not isinstance(raw, dict):
        raise QuestionError("answer must be an object")
    ok = bool(raw.get("ok"))
    text = str(raw.get("text") or "").strip()[:MAX_ANSWER_CHARS]
    if ok and not text:
        raise QuestionError("text required when ok")
    sources = []
    for item in (raw.get("sources") or [])[:MAX_SOURCES]:
        if isinstance(item, dict):
            sources.append({key: str(item.get(key) or "")[:200] for key in ("title", "group", "owner", "path")})
    try:
        elapsed_ms = max(0, int(raw.get("elapsed_ms") or 0))
    except (TypeError, ValueError):
        elapsed_ms = 0
    return {"ok": ok, "text": text, "sources": sources, "model": str(raw.get("model") or "")[:100],
            "error": str(raw.get("error") or raw.get("reason") or "")[:300], "elapsed_ms": elapsed_ms}


def format_reply(answer: dict[str, Any]) -> str:
    if not answer["ok"]:
        return FAILED_REPLY
    reply = "{}\n\n{}".format(REPLY_HEADER, answer["text"])
    named = ["{}（{} · {}）".format(s["title"], s["group"], s["owner"] or "—") for s in answer["sources"] if s["title"]]
    if named:
        reply += "\n\n来源：" + "、".join(named)
    return reply


class TeamKbQuestionStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS team_kb_questions (
                    question_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    asked_at INTEGER NOT NULL,
                    claimed_at INTEGER NOT NULL DEFAULT 0,
                    answered_at INTEGER NOT NULL DEFAULT 0,
                    answer TEXT NOT NULL DEFAULT '',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    model TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_team_kb_questions_status ON team_kb_questions (status, asked_at);
                """
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS team_kb_features (path TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', "
                "updated_at INTEGER NOT NULL DEFAULT 0)"
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(team_kb_questions)")}
            if "references_json" not in columns:  # reviewed answers sent along with the question (§5.8)
                conn.execute("ALTER TABLE team_kb_questions ADD COLUMN references_json TEXT NOT NULL DEFAULT '[]'")
            conn.commit()

    def ask(self, question_id: str, chat_id: str, text: str, now: Optional[int] = None, *,
            references: Optional[list[dict[str, Any]]] = None) -> bool:
        """Queue a question (keyed by the Feishu message id, so a re-processed message is not asked twice).

        ``references`` are reviewed answers (``{id, question, answer}``) the team-kb answer must prefer.
        """
        timestamp = int(time.time()) if now is None else now
        refs = [{"id": int(r["id"]), "question": str(r["question"])[:300], "answer": str(r["answer"])[:2000],
                 "stale": bool(r.get("stale"))}
                for r in (references or [])[:MAX_REFERENCES]]
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO team_kb_questions (question_id, chat_id, text, status, asked_at, references_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (question_id, chat_id, text[:MAX_QUESTION_CHARS], QUESTION_PENDING, timestamp,
                 json.dumps(refs, ensure_ascii=False)),
            )
            conn.commit()
            return cursor.rowcount > 0

    def claim(self, limit: int = 5, now: Optional[int] = None) -> list[dict[str, Any]]:
        timestamp = int(time.time()) if now is None else now
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT question_id, text, asked_at, references_json FROM team_kb_questions WHERE status = ? "
                "ORDER BY asked_at LIMIT ?",
                (QUESTION_PENDING, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                cursor = conn.execute(
                    "UPDATE team_kb_questions SET status = ?, claimed_at = ? WHERE question_id = ? AND status = ?",
                    (QUESTION_CLAIMED, timestamp, row["question_id"], QUESTION_PENDING),
                )
                if cursor.rowcount:
                    claimed.append({"id": row["question_id"], "text": row["text"], "asked_at": row["asked_at"],
                                    "references": json.loads(row["references_json"] or "[]")})
            conn.commit()
        return claimed

    def finish(self, question_id: str, answer: dict[str, Any], now: Optional[int] = None) -> Optional[dict[str, Any]]:
        """Store the answer of a claimed question; None when it is unknown, already finished or timed out."""
        timestamp = int(time.time()) if now is None else now
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE team_kb_questions SET status = ?, answered_at = ?, answer = ?, sources_json = ?, model = ?, error = ? "
                "WHERE question_id = ? AND status = ?",
                (QUESTION_ANSWERED if answer["ok"] else QUESTION_FAILED, timestamp, answer["text"],
                 json.dumps(answer["sources"], ensure_ascii=False), answer["model"], answer["error"],
                 question_id, QUESTION_CLAIMED),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
        return self.get(question_id)

    def expire(self, now: Optional[int] = None, *, claim_timeout: int = CLAIM_TIMEOUT_SECONDS,
               answer_timeout: int = ANSWER_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
        """Fail questions nobody claimed or answered in time; returns them so the asker can be told."""
        timestamp = int(time.time()) if now is None else now
        expired = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT question_id, status FROM team_kb_questions WHERE (status = ? AND asked_at <= ?) "
                "OR (status = ? AND claimed_at <= ?)",
                (QUESTION_PENDING, timestamp - claim_timeout, QUESTION_CLAIMED, timestamp - answer_timeout),
            ).fetchall()
            for row in rows:
                reason = "nobody claimed it" if row["status"] == QUESTION_PENDING else "no answer in time"
                cursor = conn.execute(
                    "UPDATE team_kb_questions SET status = ?, answered_at = ?, error = ? WHERE question_id = ? AND status = ?",
                    (QUESTION_FAILED, timestamp, "timeout: " + reason, row["question_id"], row["status"]),
                )
                if cursor.rowcount:
                    expired.append(row["question_id"])
            conn.commit()
        return [item for item in (self.get(question_id) for question_id in expired) if item]

    def replace_features(self, items: list[dict[str, Any]]) -> int:
        """The team-kb service's current feature list (path, title, last upload time), used to flag stale archives."""
        rows = []
        for item in items:
            path = str(item.get("path") or "")[:300]
            if path:
                try:
                    updated = max(0, int(item.get("updated_at") or 0))
                except (TypeError, ValueError):
                    updated = 0
                rows.append((path, str(item.get("title") or "")[:200], updated))
        with self._connect() as conn:
            conn.execute("DELETE FROM team_kb_features")
            conn.executemany("INSERT OR REPLACE INTO team_kb_features (path, title, updated_at) VALUES (?, ?, ?)", rows)
            conn.commit()
        return len(rows)

    def get(self, question_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM team_kb_questions WHERE question_id = ?", (question_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["sources"] = json.loads(item.pop("sources_json") or "[]")
        item["references"] = json.loads(item.pop("references_json", None) or "[]")
        return item

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn
