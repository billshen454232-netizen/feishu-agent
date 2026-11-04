"""Knowledge Q&A review, correction, archive and knowledge-doc write-back.

Design: docs/superpowers/specs/2026-11-04-qa-review-archive.md

Source priority when answering: execution catalog > archived Q&A > knowledge/docs MD.
Archived answers only *explain* registered capabilities; nothing here plans or
executes operations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .ai_client import generate_with_provenance
from .operation_message import mask_sensitive_operation_data
from .utils import text_match

logger = logging.getLogger(__name__)

QA_PENDING = "pending"
QA_CORRECTING = "correcting"
QA_ARCHIVED = "archived"
QA_DISMISSED = "dismissed"
QA_REUSED = "reused"
QA_STATUSES = (QA_PENDING, QA_CORRECTING, QA_ARCHIVED, QA_DISMISSED, QA_REUSED)

ARCHIVE_ACTIVE = "active"
ARCHIVE_RETIRED = "retired"
ARCHIVE_STATUSES = (ARCHIVE_ACTIVE, ARCHIVE_RETIRED)

FIX_PENDING = "pending"
FIX_ACCEPTED = "accepted"
FIX_IGNORED = "ignored"
FIX_APPLIED = "applied"
FIX_STATUSES = (FIX_PENDING, FIX_ACCEPTED, FIX_IGNORED, FIX_APPLIED)

CATEGORIES = ("开服", "关服", "清档", "合服", "热更", "配置", "查询", "通用")
# Words that suggest a category in a question (bonus only, never a filter).
CATEGORY_HINTS = {
    "开服": ("开服", "启服", "起服", "对外", "外部体验", "启动"),
    "关服": ("关服", "停服", "关闭服务", "kill"),
    "清档": ("清档", "清服", "清库", "清数据", "清理数据"),
    "合服": ("合服", "合并", "combine"),
    "热更": ("热更", "patch", "补丁", "阈值"),
    "配置": ("剧本", "赛季", "开服时间", "配置", "时间戳"),
    "查询": ("查询", "查看", "状态", "信息", "版本"),
}

ARCHIVE_REFERENCE_LIMIT = 3
ARCHIVE_MIN_COVERAGE = 0.34
_MAX_TEXT = 8000


# ---------------------------------------------------------------- store


class QaStore:
    """Q&A records, correction rounds, archive and doc-fix suggestions (same SQLite as message_jobs)."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS qa_records (
                    message_id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '{}',
                    answer_model TEXT NOT NULL DEFAULT '',
                    answer_ms INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    reviewed_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS qa_revisions (
                    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    round INTEGER NOT NULL,
                    correction TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    elapsed_ms INTEGER NOT NULL DEFAULT 0,
                    operator TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qa_archive (
                    archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_message_id TEXT NOT NULL DEFAULT '',
                    canonical_question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    category TEXT NOT NULL,
                    keywords TEXT NOT NULL DEFAULT '',
                    scripts TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    last_hit_at INTEGER,
                    written_back INTEGER NOT NULL DEFAULT 0,
                    catalog_digest TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS doc_fix_suggestions (
                    suggestion_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    archive_id INTEGER NOT NULL,
                    doc_path TEXT NOT NULL,
                    original_excerpt TEXT NOT NULL,
                    proposed_excerpt TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            conn.commit()

    # records ----------------------------------------------------------------

    def record(
        self,
        message_id: str,
        question: str,
        answer: str,
        *,
        sources: dict[str, Any],
        model: str,
        elapsed_ms: int,
        status: str = QA_PENDING,
        now: int | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO qa_records
                    (message_id, question, answer, sources_json, answer_model, answer_ms, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    _mask(question),
                    _mask(answer),
                    json.dumps(sources, ensure_ascii=False),
                    model,
                    elapsed_ms,
                    status,
                    _now(now),
                ),
            )
            conn.commit()

    def get_record(self, message_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM qa_records WHERE message_id = ?", (message_id,)).fetchone()
        return self._record_dict(row) if row else None

    def list_records(self, status: str, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        if status not in QA_STATUSES:
            raise ValueError("unknown qa status: {}".format(status))
        with self._connect() as conn:
            has_jobs = self._has_table(conn, "message_jobs")
            join = "LEFT JOIN message_jobs j ON j.message_id = r.message_id" if has_jobs else ""
            extra = (
                "COALESCE(j.user_id, '') AS user_id, COALESCE(j.user_name, '') AS user_name, "
                "COALESCE(j.planning_model, '') AS planning_model, COALESCE(j.planning_ms, 0) AS planning_ms"
                if has_jobs and self._has_columns(conn, "message_jobs", ("user_name", "planning_model"))
                else "'' AS user_id, '' AS user_name, '' AS planning_model, 0 AS planning_ms"
            )
            rows = conn.execute(
                "SELECT r.*, {} FROM qa_records r {} WHERE r.status = ? "
                "ORDER BY r.created_at DESC LIMIT ? OFFSET ?".format(extra, join),
                (status, limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM qa_records WHERE status = ?", (status,)).fetchone()[0]
        records = [self._record_dict(row) for row in rows]
        for item in records:
            item["revisions"] = self.list_revisions(item["message_id"])
        return records, int(total)

    def count_records(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM qa_records GROUP BY status").fetchall()
        counts = {status: 0 for status in QA_STATUSES}
        counts.update({str(row["status"]): int(row["n"]) for row in rows})
        return counts

    def set_record_status(self, message_id: str, status: str, now: int | None = None) -> bool:
        if status not in QA_STATUSES:
            raise ValueError("unknown qa status: {}".format(status))
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE qa_records SET status = ?, reviewed_at = ? WHERE message_id = ?",
                (status, _now(now), message_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def add_revision(
        self,
        message_id: str,
        correction: str,
        answer: str,
        *,
        model: str,
        elapsed_ms: int,
        operator: str,
        now: int | None = None,
    ) -> int:
        with self._connect() as conn:
            current = conn.execute(
                "SELECT COALESCE(MAX(round), 0) FROM qa_revisions WHERE message_id = ?", (message_id,)
            ).fetchone()[0]
            round_number = int(current) + 1
            conn.execute(
                """
                INSERT INTO qa_revisions (message_id, round, correction, answer, model, elapsed_ms, operator, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, round_number, _mask(correction), _mask(answer), model, elapsed_ms, operator, _now(now)),
            )
            conn.execute(
                "UPDATE qa_records SET status = ?, reviewed_at = ? WHERE message_id = ?",
                (QA_CORRECTING, _now(now), message_id),
            )
            conn.commit()
        return round_number

    def list_revisions(self, message_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT round, correction, answer, model, elapsed_ms, operator, created_at "
                "FROM qa_revisions WHERE message_id = ? ORDER BY round",
                (message_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # archive ----------------------------------------------------------------

    def add_archive(
        self,
        *,
        source_message_id: str,
        canonical_question: str,
        answer: str,
        category: str,
        keywords: str,
        scripts: str,
        catalog_digest: str,
        operator: str,
        now: int | None = None,
    ) -> int:
        timestamp = _now(now)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO qa_archive (source_message_id, canonical_question, answer, category, keywords, scripts,
                                        status, catalog_digest, operator, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_message_id,
                    _mask(canonical_question),
                    _mask(answer),
                    category if category in CATEGORIES else "通用",
                    keywords,
                    scripts,
                    ARCHIVE_ACTIVE,
                    catalog_digest,
                    operator,
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        return int(cursor.lastrowid)

    def get_archive(self, archive_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM qa_archive WHERE archive_id = ?", (archive_id,)).fetchone()
        return dict(row) if row else None

    def active_archive(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM qa_archive WHERE status = ?", (ARCHIVE_ACTIVE,)).fetchall()
        return [dict(row) for row in rows]

    def list_archive(self, status: str, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        if status not in ARCHIVE_STATUSES:
            raise ValueError("unknown archive status: {}".format(status))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM qa_archive WHERE status = ? ORDER BY hit_count DESC, updated_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM qa_archive WHERE status = ?", (status,)).fetchone()[0]
        return [dict(row) for row in rows], int(total)

    def count_archive(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM qa_archive GROUP BY status").fetchall()
        counts = {status: 0 for status in ARCHIVE_STATUSES}
        counts.update({str(row["status"]): int(row["n"]) for row in rows})
        return counts

    def update_archive(self, archive_id: int, fields: dict[str, Any], now: int | None = None) -> bool:
        allowed = {"canonical_question", "answer", "category", "keywords", "scripts", "status"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if "category" in values and values["category"] not in CATEGORIES:
            raise ValueError("unknown category")
        if "status" in values and values["status"] not in ARCHIVE_STATUSES:
            raise ValueError("unknown archive status")
        for key in ("canonical_question", "answer"):
            if key in values:
                values[key] = _mask(str(values[key]))
        if not values:
            return False
        assignments = ", ".join("{} = ?".format(key) for key in values)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE qa_archive SET {}, updated_at = ? WHERE archive_id = ?".format(assignments),
                (*values.values(), _now(now), archive_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def delete_archive(self, archive_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM qa_archive WHERE archive_id = ?", (archive_id,))
            conn.commit()
        return cursor.rowcount > 0

    def record_hits(self, archive_ids: Iterable[int], now: int | None = None) -> None:
        ids = list(archive_ids)
        if not ids:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE qa_archive SET hit_count = hit_count + 1, last_hit_at = ? WHERE archive_id IN ({})".format(
                    ",".join("?" for _ in ids)
                ),
                (_now(now), *ids),
            )
            conn.commit()

    # doc fixes --------------------------------------------------------------

    def add_doc_fix(
        self,
        archive_id: int,
        doc_path: str,
        original_excerpt: str,
        proposed_excerpt: str,
        reason: str = "",
        now: int | None = None,
    ) -> int:
        timestamp = _now(now)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO doc_fix_suggestions
                    (archive_id, doc_path, original_excerpt, proposed_excerpt, reason, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (archive_id, doc_path, original_excerpt, proposed_excerpt, reason, FIX_PENDING, timestamp, timestamp),
            )
            conn.commit()
        return int(cursor.lastrowid)

    def list_doc_fixes(self, status: str, *, limit: int = 100, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        if status not in FIX_STATUSES:
            raise ValueError("unknown fix status: {}".format(status))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT f.*, COALESCE(a.canonical_question, '') AS canonical_question "
                "FROM doc_fix_suggestions f LEFT JOIN qa_archive a ON a.archive_id = f.archive_id "
                "WHERE f.status = ? ORDER BY f.created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM doc_fix_suggestions WHERE status = ?", (status,)).fetchone()[0]
        return [dict(row) for row in rows], int(total)

    def count_doc_fixes(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM doc_fix_suggestions GROUP BY status").fetchall()
        counts = {status: 0 for status in FIX_STATUSES}
        counts.update({str(row["status"]): int(row["n"]) for row in rows})
        return counts

    def update_doc_fix(self, suggestion_id: int, *, status: str | None = None, proposed_excerpt: str | None = None,
                       now: int | None = None) -> bool:
        assignments, values = [], []
        if status is not None:
            if status not in FIX_STATUSES:
                raise ValueError("unknown fix status")
            assignments.append("status = ?")
            values.append(status)
        if proposed_excerpt is not None:
            assignments.append("proposed_excerpt = ?")
            values.append(proposed_excerpt)
        if not assignments:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE doc_fix_suggestions SET {}, updated_at = ? WHERE suggestion_id = ?".format(", ".join(assignments)),
                (*values, _now(now), suggestion_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def mark_fixes_applied(self, suggestion_ids: Iterable[int], now: int | None = None) -> int:
        """Mark accepted suggestions applied; archives whose suggestions are all applied become written_back."""
        ids = [int(item) for item in suggestion_ids]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE doc_fix_suggestions SET status = ?, updated_at = ? "
                "WHERE suggestion_id IN ({}) AND status = ?".format(placeholders),
                (FIX_APPLIED, _now(now), *ids, FIX_ACCEPTED),
            )
            conn.execute(
                """
                UPDATE qa_archive SET written_back = 1
                WHERE archive_id IN (SELECT archive_id FROM doc_fix_suggestions WHERE suggestion_id IN ({}))
                  AND NOT EXISTS (
                      SELECT 1 FROM doc_fix_suggestions f
                      WHERE f.archive_id = qa_archive.archive_id AND f.status IN (?, ?)
                  )
                """.format(placeholders),
                (*ids, FIX_PENDING, FIX_ACCEPTED),
            )
            conn.commit()
        return cursor.rowcount

    # helpers ----------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _has_table(conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    @staticmethod
    def _has_columns(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> bool:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info({})".format(table))}
        return all(column in existing for column in columns)

    @staticmethod
    def _record_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["sources"] = json.loads(item.pop("sources_json") or "{}")
        return item


# ---------------------------------------------------------------- retrieval


@dataclass(frozen=True)
class ArchiveMatch:
    archive: dict[str, Any]
    score: float


def question_categories(question: str) -> set[str]:
    lowered = question.lower()
    return {category for category, hints in CATEGORY_HINTS.items() if any(hint in lowered for hint in hints)}


def rank_archive(question: str, archive: list[dict[str, Any]], *, limit: int = ARCHIVE_REFERENCE_LIMIT) -> list[ArchiveMatch]:
    """Lexical ranking: coverage of the question by 标准问法 + 关键词, plus script / category bonuses."""
    query_terms = text_match.terms(question)
    lowered = question.lower()
    categories = question_categories(question)
    matches: list[ArchiveMatch] = []
    for item in archive:
        target = "{} {}".format(item.get("canonical_question", ""), item.get("keywords", ""))
        base = text_match.coverage(query_terms, text_match.terms(target))
        scripts = [script.strip().lower() for script in str(item.get("scripts", "")).split(",") if script.strip()]
        script_bonus = 0.2 if any(script and script.split(".")[0] in lowered for script in scripts) else 0.0
        category_bonus = 0.1 if item.get("category") in categories else 0.0
        score = base + script_bonus + category_bonus
        if base >= ARCHIVE_MIN_COVERAGE or script_bonus:
            matches.append(ArchiveMatch(item, score))
    matches.sort(key=lambda match: (-match.score, -int(match.archive.get("hit_count", 0))))
    return matches[:limit]


def exact_archive(question: str, archive: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = text_match.normalize(question)
    if not normalized:
        return None
    for item in archive:
        if text_match.normalize(item.get("canonical_question", "")) == normalized:
            return item
    return None


# ---------------------------------------------------------------- service (prompts + AI)


@dataclass
class QaAnswer:
    text: str
    model: str
    elapsed_ms: int
    doc_paths: list[str] = field(default_factory=list)
    archive_ids: list[int] = field(default_factory=list)
    reused_archive_id: int | None = None


_RULES = (
    "资料优先级：当前执行注册表（唯一权威）> 已审核问答 > 本地资料。已审核问答是人工确认过的标准答案，应优先依据；"
    "若它与注册表冲突，以注册表为准，并指出该问答可能已过时。"
    "回答应简洁、准确，用中文说明当前可执行能力、用途、参数形状、风险和前置检查。"
    "不得生成执行计划、不得请求确认执行、不得输出自由 Shell 命令、不得补充资料中没有的服务器地址、账号、凭据、内部环境信息或操作细节。"
    "如果注册表没有对应能力，直接说明尚未登记，不要猜测。"
)


class QaService:
    """Builds knowledge prompts and calls AI for answers, corrections and archive drafts."""

    def __init__(
        self,
        *,
        answer_client: Any,
        curation_client: Any | None = None,
        knowledge_base: Any | None = None,
        capability_context: Callable[[], str] | None = None,
        catalog_text: Callable[[], str] | None = None,
        store: QaStore | None = None,
        knowledge_root: str | Path = "knowledge",
    ) -> None:
        self._answer_client = answer_client
        self._curation_client = curation_client or answer_client
        self._knowledge_base = knowledge_base
        self._capability_context = capability_context or (lambda: "当前执行注册表未提供可展示摘要。")
        self._catalog_text = catalog_text or (lambda: "")
        self.store = store
        self._knowledge_root = Path(knowledge_root)

    def catalog_digest(self) -> str:
        try:
            return hashlib.sha256(self._catalog_text().encode("utf-8")).hexdigest()[:16]
        except Exception:  # noqa: BLE001
            return ""

    # answering ----------------------------------------------------------------

    async def answer(self, question: str, decision_summary: str = "") -> QaAnswer:
        archive = self._safe_active_archive()
        exact = exact_archive(question, archive)
        if exact is not None:
            self._safe_hits([exact["archive_id"]])
            return QaAnswer(
                text=exact["answer"], model="", elapsed_ms=0,
                archive_ids=[exact["archive_id"]], reused_archive_id=exact["archive_id"],
            )
        matches = rank_archive(question, archive)
        doc_context, doc_paths = self._documents(question)
        prompt = (
            "你是测试服脚本资料问答助手。只依据下列当前执行注册表、已审核问答和本地资料回答用户的问题。{rules}\n\n"
            "规划器的受限决策摘要：{summary}\n\n当前执行注册表：\n{registry}\n\n已审核问答：\n{archive}\n\n"
            "本地资料：\n{docs}\n\n来源列表：\n{sources}\n\n用户问题：{question}"
        ).format(
            rules=_RULES,
            summary=decision_summary or "（无）",
            registry=self._capability_context(),
            archive=self._archive_context(matches),
            docs=doc_context,
            sources="\n".join("- {}".format(path) for path in doc_paths) or "（无）",
            question=question,
        )
        started = time.perf_counter()
        generation = await generate_with_provenance(self._answer_client, [], prompt)
        archive_ids = [match.archive["archive_id"] for match in matches]
        self._safe_hits(archive_ids)
        return QaAnswer(
            text=str(generation.text).strip(),
            model=str(generation.model or ""),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            doc_paths=doc_paths,
            archive_ids=archive_ids,
        )

    async def reanswer(self, record: dict[str, Any], corrections: list[str]) -> QaAnswer:
        """Answer again, taking the reviewer's corrections into account."""
        doc_context, doc_paths = self._documents(record["question"])
        notes = "\n".join("{}. {}".format(index, text) for index, text in enumerate(corrections, start=1))
        previous = record["revisions"][-1]["answer"] if record.get("revisions") else record["answer"]
        prompt = (
            "你是测试服脚本资料问答助手。上一次的回答被人工评审判定为有误。请依据人工纠正说明、当前执行注册表和本地资料，"
            "给出修正后的完整回答（直接写给提问人，不要提及“纠正”“上次回答”）。人工纠正说明是可信的事实来源，"
            "但不得据此扩大注册表之外的能力。{rules}\n\n当前执行注册表：\n{registry}\n\n本地资料：\n{docs}\n\n"
            "用户问题：{question}\n\n被判定有误的回答：\n{previous}\n\n人工纠正说明：\n{notes}"
        ).format(
            rules=_RULES,
            registry=self._capability_context(),
            docs=doc_context,
            question=record["question"],
            previous=previous,
            notes=notes,
        )
        started = time.perf_counter()
        generation = await generate_with_provenance(self._curation_client, [], prompt)
        return QaAnswer(
            text=str(generation.text).strip(),
            model=str(generation.model or ""),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            doc_paths=doc_paths,
        )

    async def draft_archive(self, record: dict[str, Any], final_answer: str, corrected: bool) -> dict[str, Any]:
        """AI-suggested 标准问法 / 类别 / 关键词 / 脚本, plus MD fix suggestions when the answer was corrected."""
        cited_docs = [path for path in record.get("sources", {}).get("docs", []) if self._doc_file(path)]
        doc_blocks = []
        if corrected:
            for path in cited_docs:
                text = self._doc_file(path).read_text(encoding="utf-8", errors="replace")[:_MAX_TEXT]
                doc_blocks.append("[文档 {}]\n{}".format(path, text))
        notes = "\n".join(revision["correction"] for revision in record.get("revisions", []))
        prompt = (
            "你在整理测试服运维机器人的已审核问答库。根据下面的问题与最终答案，输出一个 JSON 对象，不要输出其他内容：\n"
            '{{"canonical_question": "规范、完整、去掉口语的问法",\n'
            ' "category": "从 {categories} 中选一个",\n'
            ' "keywords": ["同义词与常见说法，含中英文、脚本参数，5-12 个"],\n'
            ' "scripts": ["涉及的注册表脚本文件名，如 start、control_is_pub.sh"],\n'
            ' "doc_fixes": [{{"doc_path": "文档路径", "original_excerpt": "文档中原样存在的、需要修改的连续文字",'
            ' "proposed_excerpt": "修改后的文字", "reason": "为什么改"}}]}}\n'
            "doc_fixes 规则：只在提供了文档且文档内容与最终答案矛盾或缺失关键说法时给出；original_excerpt 必须逐字复制自文档，"
            "尽量短（只含需要改的几行）；不要重写整篇；没有需要改的就给空数组。\n\n"
            "问题：{question}\n\n最终答案：\n{answer}\n\n人工纠正说明：\n{notes}\n\n{docs}\n\n当前执行注册表：\n{registry}"
        ).format(
            categories=" / ".join(CATEGORIES),
            question=record["question"],
            answer=final_answer,
            notes=notes or "（无）",
            docs="\n\n".join(doc_blocks) or "（未提供文档，doc_fixes 给空数组）",
            registry=self._capability_context(),
        )
        generation = await generate_with_provenance(self._curation_client, [], prompt)
        draft = _parse_json_object(str(generation.text))
        fixes = []
        for fix in draft.get("doc_fixes") or []:
            if not isinstance(fix, dict):
                continue
            path = str(fix.get("doc_path", ""))
            original = str(fix.get("original_excerpt", ""))
            proposed = str(fix.get("proposed_excerpt", ""))
            doc = self._doc_file(path) if path in cited_docs else None
            if not doc or not original or original == proposed:
                continue
            if doc.read_text(encoding="utf-8", errors="replace").count(original) != 1:
                logger.warning("doc fix dropped: excerpt not found exactly once doc=%s", path)
                continue
            fixes.append({"doc_path": path, "original_excerpt": original, "proposed_excerpt": proposed,
                          "reason": str(fix.get("reason", ""))[:300]})
        category = str(draft.get("category", "通用"))
        return {
            "canonical_question": str(draft.get("canonical_question") or record["question"])[:300],
            "answer": final_answer,
            "category": category if category in CATEGORIES else "通用",
            "keywords": _join_list(draft.get("keywords")),
            "scripts": _join_list(draft.get("scripts")),
            "doc_fixes": fixes,
        }

    # helpers ------------------------------------------------------------------

    def _documents(self, question: str) -> tuple[str, list[str]]:
        if self._knowledge_base is None:
            return "（未命中补充脚本资料）", []
        result = self._knowledge_base.search(question)
        if result is None or not result.hit:
            return "（未命中补充脚本资料）", []
        return result.context, [source.path for source in result.sources]

    def _doc_file(self, path: str) -> Path | None:
        """Resolve ``knowledge/docs/x.md`` / ``knowledge/index.md`` inside the knowledge root only."""
        if not path.startswith("knowledge/"):
            return None
        relative = path[len("knowledge/"):]
        if not (relative == "index.md" or re.fullmatch(r"docs/[^/\\]+\.md", relative)):
            return None
        candidate = self._knowledge_root / relative
        return candidate if candidate.is_file() else None

    @staticmethod
    def _archive_context(matches: list[ArchiveMatch]) -> str:
        if not matches:
            return "（未命中已审核问答）"
        return "\n\n".join(
            "[已审核问答 #{} | 类别 {}]\n问：{}\n答：{}".format(
                match.archive["archive_id"], match.archive["category"],
                match.archive["canonical_question"], match.archive["answer"],
            )
            for match in matches
        )

    def _safe_active_archive(self) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        try:
            return self.store.active_archive()
        except Exception:  # noqa: BLE001 - archive lookup must never break answering
            logger.warning("qa archive unavailable", exc_info=True)
            return []

    def _safe_hits(self, archive_ids: list[int]) -> None:
        if self.store is None or not archive_ids:
            return
        try:
            self.store.record_hits(archive_ids)
        except Exception:  # noqa: BLE001
            logger.warning("qa archive hit count not recorded", exc_info=True)


def _parse_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _join_list(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return str(value or "").strip()


def _mask(text: str) -> str:
    return mask_sensitive_operation_data(text or "")[:_MAX_TEXT]


def _now(now: int | None) -> int:
    return int(time.time()) if now is None else now
