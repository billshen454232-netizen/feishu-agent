"""Deterministic execution-result presentation and the reusable failure template library.

Design: docs/superpowers/specs/2026-11-02-execution-report-library.md

Trusted terminal states (done / failed / submitted_result_unknown) are decided by
the Worker/Gateway before anything here runs. This module only decides *how to
explain* a result: deterministic template, a stored failure template, or a
background AI reading. Nothing here can change a persisted terminal state.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .muliu_executor import MuliuExecutionResult, MuliuStepResult
from .muliu_plan import MuliuStep
from .operation_message import clean_muliu_execution_log, mask_sensitive_operation_data

PATTERN_CANDIDATE = "candidate"
PATTERN_VERIFIED = "verified"
PATTERN_STATUSES = (PATTERN_CANDIDATE, PATTERN_VERIFIED)

# Human-set presentation verdict of a template. ``warning`` = "the query ran but
# the server state is questionable"; it only applies to verified templates on
# read-only results and never changes the persisted terminal state.
OUTCOME_FAILURE = "failure"
OUTCOME_WARNING = "warning"
PATTERN_OUTCOMES = (OUTCOME_FAILURE, OUTCOME_WARNING)

INTERPRETATION_FAILURE = "failure"
INTERPRETATION_READ_SUCCESS = "read_success"

READ_SUCCESS_MODE_AI = "ai"
READ_SUCCESS_MODE_TEMPLATE = "template"
READ_SUCCESS_MODES = (READ_SUCCESS_MODE_AI, READ_SUCCESS_MODE_TEMPLATE)
SETTING_READ_SUCCESS_MODE = "read_success_mode"

KEY_ERROR_LINE_COUNT = 3
ERROR_KEYWORDS = (
    "error",
    "traceback",
    "exception",
    "fail",
    "fatal",
    "denied",
    "refused",
    "timed out",
    "timeout",
    "not found",
    "no such",
    "exit code",
    "exit_code",
    "失败",
    "异常",
    "错误",
    "报错",
    "超时",
    "未找到",
    "不存在",
)
_MAX_SCOPES = 20
_MAX_SAMPLES = 5
_MAX_ARTIFACT_CHARS = 4000

_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?|\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"
)
_UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX_PATTERN = re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{12,}\b")
_IPV4_PATTERN = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_TEMP_PATH_PATTERN = re.compile(r"/tmp/\S+")
_LONG_NUMBER_PATTERN = re.compile(r"\d{4,}")
_EXIT_CODE_PATTERN = re.compile(r"exit[_ ]?(?:code|status)\s*[=:：]?\s*(-?\d+)", re.IGNORECASE)
_HTTP_STATUS_PATTERN = re.compile(r"\bHTTP\s*(?:status\s*)?[=:：]?\s*([1-5]\d{2})\b", re.IGNORECASE)
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class ErrorSignature:
    fingerprint: str
    script_path: str
    failure_category: str
    normalized_reason: str
    key_error_lines: tuple[str, ...]


@dataclass(frozen=True)
class ErrorPattern:
    pattern_id: int
    script_path: str
    error_fingerprint: str
    failure_category: str
    normalized_reason: str
    key_error_lines: tuple[str, ...]
    diagnosis: str
    status: str
    seen_scopes: tuple[str, ...]
    sample_message_ids: tuple[str, ...]
    first_seen_at: int
    last_seen_at: int
    hit_count: int
    approved_at: int | None
    outcome: str = OUTCOME_FAILURE

    def as_jsonable(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "script_path": self.script_path,
            "failure_category": self.failure_category,
            "normalized_reason": self.normalized_reason,
            "key_error_lines": list(self.key_error_lines),
            "diagnosis": self.diagnosis,
            "status": self.status,
            "seen_scopes": list(self.seen_scopes),
            "sample_message_ids": list(self.sample_message_ids),
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "hit_count": self.hit_count,
            "approved_at": self.approved_at,
            "outcome": self.outcome,
        }


@dataclass(frozen=True)
class InterpretationTask:
    message_id: str
    kind: str
    status: str
    attempts: int
    payload_json: str
    error_fingerprint: str
    created_at: int
    updated_at: int


# ---------------------------------------------------------------- signatures


def normalize_error_text(text: str, *, variable_values: Iterable[str] = ()) -> str:
    """Remove values that vary between occurrences of the same error.

    Exit codes, HTTP statuses, script names and component names are kept; IPs,
    timestamps, UUIDs, temp paths, long numbers and this run's own argument values
    are replaced, so the same error on another server produces the same text.
    """
    normalized = mask_sensitive_operation_data(text)
    for value in sorted({v for v in variable_values if len(v) >= 2 and not v.startswith("-")}, key=len, reverse=True):
        normalized = re.sub(r"(?<![0-9A-Za-z_]){}(?![0-9A-Za-z_])".format(re.escape(value)), "<ARG>", normalized)
    normalized = _TIMESTAMP_PATTERN.sub("<TIME>", normalized)
    normalized = _UUID_PATTERN.sub("<UUID>", normalized)
    normalized = _IPV4_PATTERN.sub("<IP>", normalized)
    normalized = _HEX_PATTERN.sub("<HEX>", normalized)
    normalized = _TEMP_PATH_PATTERN.sub("<TMP>", normalized)
    normalized = _LONG_NUMBER_PATTERN.sub("<N>", normalized)
    return _WHITESPACE_PATTERN.sub(" ", normalized).strip()


def failed_step(result: MuliuExecutionResult) -> MuliuStepResult | None:
    if result.failed_step_number is not None:
        for step in result.step_results:
            if step.step_number == result.failed_step_number:
                return step
        if 0 < result.failed_step_number <= len(result.step_results):
            return result.step_results[result.failed_step_number - 1]
    for step in result.step_results:
        if not step.succeeded:
            return step
    return result.step_results[-1] if result.step_results else None


def extract_key_error_lines(log: str, *, variable_values: Iterable[str] = (), count: int = KEY_ERROR_LINE_COUNT) -> tuple[str, ...]:
    """Last ``count`` keyword lines of the cleaned log, else its last ``count`` lines."""
    cleaned = clean_muliu_execution_log(log or "")
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    lines = [line for line in lines if line != "（无额外输出日志）"]
    if not lines:
        return ()
    keyword_lines = [line for line in lines if any(keyword in line.lower() for keyword in ERROR_KEYWORDS)]
    chosen = (keyword_lines or lines)[-count:]
    values = tuple(variable_values)
    return tuple(normalize_error_text(line, variable_values=values) for line in chosen)


def failure_category(result: MuliuExecutionResult, key_lines: Iterable[str] = ()) -> str:
    sources = [result.failure_reason, *key_lines]
    for source in sources:
        match = _EXIT_CODE_PATTERN.search(source or "")
        if match:
            return "exit_code={}".format(match.group(1))
    for source in sources:
        match = _HTTP_STATUS_PATTERN.search(source or "")
        if match:
            return "http={}".format(match.group(1))
    return "unclassified"


def compute_error_signature(result: MuliuExecutionResult) -> ErrorSignature:
    step = failed_step(result)
    script_path = step.path if step is not None else ""
    variable_values = tuple(step.args) if step is not None else ()
    key_lines = extract_key_error_lines(step.log if step is not None else "", variable_values=variable_values)
    reason = normalize_error_text(result.failure_reason, variable_values=variable_values)
    category = failure_category(result, key_lines)
    material = json.dumps([script_path, category, reason, list(key_lines)], ensure_ascii=False)
    fingerprint = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return ErrorSignature(
        fingerprint=fingerprint,
        script_path=script_path,
        failure_category=category,
        normalized_reason=reason,
        key_error_lines=key_lines,
    )


def result_scope(result: MuliuExecutionResult, route_region: str = "") -> str:
    """Display-only description of where a failure happened; never part of matching."""
    step = failed_step(result)
    args = " ".join(step.args) if step is not None else ""
    scope = "{} {}".format(route_region or "-", args).strip()
    return mask_sensitive_operation_data(scope)[:200]


def is_read_only_result(result: MuliuExecutionResult, contracts: Any | None) -> bool:
    """True only when every executed step matches exactly one ``risk=read`` contract."""
    if contracts is None or not result.step_results:
        return False
    for step_result in result.step_results:
        step = MuliuStep(path=step_result.path, args=list(step_result.args), description=step_result.description)
        matching = [contract for contract in contracts.contracts if contract.matches(step)]
        if len(matching) != 1 or matching[0].risk != "read":
            return False
    return True


def redact_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Mask secrets in a serialized execution result before persisting it."""
    redacted = dict(payload)
    for key in ("summary", "failure_reason", "result_unknown_reason"):
        if isinstance(redacted.get(key), str):
            redacted[key] = mask_sensitive_operation_data(redacted[key])
    steps = []
    for step in redacted.get("step_results", []) or []:
        step = dict(step)
        for key in ("description", "log"):
            if isinstance(step.get(key), str):
                step[key] = mask_sensitive_operation_data(step[key])
        steps.append(step)
    redacted["step_results"] = steps
    return redacted


# ---------------------------------------------------------------- storage


class ExecutionReportStore:
    """Template library, background interpretation tasks and runtime settings.

    Lives in the same SQLite file as ``message_jobs``.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS execution_error_patterns (
                    pattern_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    script_path TEXT NOT NULL,
                    error_fingerprint TEXT NOT NULL UNIQUE,
                    failure_category TEXT NOT NULL,
                    normalized_reason TEXT NOT NULL,
                    key_error_lines TEXT NOT NULL,
                    diagnosis TEXT NOT NULL,
                    status TEXT NOT NULL,
                    seen_scopes_json TEXT NOT NULL DEFAULT '[]',
                    sample_message_ids TEXT NOT NULL DEFAULT '[]',
                    first_seen_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    approved_at INTEGER,
                    outcome TEXT NOT NULL DEFAULT 'failure'
                );
                CREATE TABLE IF NOT EXISTS execution_interpretations (
                    message_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    error_fingerprint TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    error_kind TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    input_chars INTEGER NOT NULL DEFAULT 0,
                    elapsed_ms INTEGER NOT NULL DEFAULT 0,
                    candidate_pattern_id INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_artifacts (
                    message_id TEXT PRIMARY KEY,
                    terminal_status TEXT NOT NULL,
                    report_source TEXT NOT NULL,
                    error_fingerprint TEXT NOT NULL DEFAULT '',
                    summary_text TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_report_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(execution_error_patterns)")}
            if "outcome" not in columns:
                conn.execute("ALTER TABLE execution_error_patterns ADD COLUMN outcome TEXT NOT NULL DEFAULT 'failure'")
            conn.commit()

    # settings -------------------------------------------------------------

    def read_success_mode(self) -> str:
        value = self._get_setting(SETTING_READ_SUCCESS_MODE)
        return value if value in READ_SUCCESS_MODES else READ_SUCCESS_MODE_AI

    def set_read_success_mode(self, mode: str) -> None:
        if mode not in READ_SUCCESS_MODES:
            raise ValueError("unknown read success mode: {}".format(mode))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO execution_report_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SETTING_READ_SUCCESS_MODE, mode),
            )
            conn.commit()

    def _get_setting(self, key: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM execution_report_settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    # patterns -------------------------------------------------------------

    def find_pattern(self, fingerprint: str) -> ErrorPattern | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_error_patterns WHERE error_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        return self._row_to_pattern(row) if row else None

    def get_pattern(self, pattern_id: int) -> ErrorPattern | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM execution_error_patterns WHERE pattern_id = ?", (pattern_id,)).fetchone()
        return self._row_to_pattern(row) if row else None

    def record_pattern_hit(self, pattern_id: int, message_id: str, scope: str, now: int | None = None) -> ErrorPattern | None:
        timestamp = self._now(now)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT seen_scopes_json, sample_message_ids FROM execution_error_patterns WHERE pattern_id = ?",
                (pattern_id,),
            ).fetchone()
            if row is None:
                return None
            scopes = _append_unique(json.loads(row["seen_scopes_json"]), scope, _MAX_SCOPES)
            samples = _append_recent(json.loads(row["sample_message_ids"]), message_id, _MAX_SAMPLES)
            conn.execute(
                """
                UPDATE execution_error_patterns
                SET hit_count = hit_count + 1, last_seen_at = ?, seen_scopes_json = ?, sample_message_ids = ?
                WHERE pattern_id = ?
                """,
                (timestamp, json.dumps(scopes, ensure_ascii=False), json.dumps(samples), pattern_id),
            )
            conn.commit()
        return self.get_pattern(pattern_id)

    def add_candidate(
        self,
        signature: ErrorSignature,
        diagnosis: str,
        message_id: str,
        scope: str,
        now: int | None = None,
    ) -> ErrorPattern:
        """Collect an AI diagnosis as a pending template; keep any existing one."""
        existing = self.find_pattern(signature.fingerprint)
        if existing is not None:
            return existing
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO execution_error_patterns (
                    script_path, error_fingerprint, failure_category, normalized_reason,
                    key_error_lines, diagnosis, status, seen_scopes_json, sample_message_ids,
                    first_seen_at, last_seen_at, hit_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    signature.script_path,
                    signature.fingerprint,
                    signature.failure_category,
                    signature.normalized_reason,
                    json.dumps(list(signature.key_error_lines), ensure_ascii=False),
                    diagnosis,
                    PATTERN_CANDIDATE,
                    json.dumps([scope] if scope else [], ensure_ascii=False),
                    json.dumps([message_id]),
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        pattern = self.find_pattern(signature.fingerprint)
        if pattern is None:
            raise RuntimeError("failed to store candidate template")
        return pattern

    def list_patterns(self, status: str) -> list[ErrorPattern]:
        if status not in PATTERN_STATUSES:
            raise ValueError("unknown pattern status: {}".format(status))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_error_patterns WHERE status = ? ORDER BY hit_count DESC, last_seen_at DESC",
                (status,),
            ).fetchall()
        return [self._row_to_pattern(row) for row in rows]

    def count_patterns(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM execution_error_patterns GROUP BY status"
            ).fetchall()
        counts = {status: 0 for status in PATTERN_STATUSES}
        counts.update({str(row["status"]): int(row["n"]) for row in rows})
        return counts

    def set_pattern_status(self, pattern_ids: Iterable[int], status: str, now: int | None = None) -> int:
        if status not in PATTERN_STATUSES:
            raise ValueError("unknown pattern status: {}".format(status))
        ids = [int(pattern_id) for pattern_id in pattern_ids]
        if not ids:
            return 0
        approved_at = self._now(now) if status == PATTERN_VERIFIED else None
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE execution_error_patterns SET status = ?, approved_at = ? WHERE pattern_id IN ({})".format(
                    ",".join("?" for _ in ids)
                ),
                (status, approved_at, *ids),
            )
            conn.commit()
        return cursor.rowcount

    def set_pattern_outcome(self, pattern_id: int, outcome: str) -> bool:
        if outcome not in PATTERN_OUTCOMES:
            raise ValueError("unknown pattern outcome: {}".format(outcome))
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE execution_error_patterns SET outcome = ? WHERE pattern_id = ?",
                (outcome, pattern_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def update_diagnosis(self, pattern_id: int, diagnosis: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE execution_error_patterns SET diagnosis = ? WHERE pattern_id = ?",
                (diagnosis, pattern_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def delete_patterns(self, pattern_ids: Iterable[int]) -> int:
        ids = [int(pattern_id) for pattern_id in pattern_ids]
        if not ids:
            return 0
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM execution_error_patterns WHERE pattern_id IN ({})".format(",".join("?" for _ in ids)),
                ids,
            )
            conn.commit()
        return cursor.rowcount

    # artifacts ------------------------------------------------------------

    def record_artifact(
        self,
        message_id: str,
        terminal_status: str,
        report_source: str,
        summary_text: str,
        *,
        error_fingerprint: str = "",
        now: int | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_artifacts (
                    message_id, terminal_status, report_source, error_fingerprint, summary_text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET report_source = excluded.report_source
                """,
                (
                    message_id,
                    terminal_status,
                    report_source,
                    error_fingerprint,
                    mask_sensitive_operation_data(summary_text)[:_MAX_ARTIFACT_CHARS],
                    self._now(now),
                ),
            )
            conn.commit()

    def set_artifact_source(self, message_id: str, report_source: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE execution_artifacts SET report_source = ? WHERE message_id = ?",
                (report_source, message_id),
            )
            conn.commit()

    def list_artifacts(self, message_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(message_ids)
        if not ids:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_artifacts WHERE message_id IN ({}) ORDER BY created_at DESC".format(
                    ",".join("?" for _ in ids)
                ),
                ids,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_executions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent results with how they were explained (read-only review view)."""
        with self._connect() as conn:
            has_jobs = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'message_jobs'"
            ).fetchone() is not None
            request_column = "COALESCE(j.text, '')" if has_jobs else "''"
            join_jobs = "LEFT JOIN message_jobs j ON j.message_id = a.message_id" if has_jobs else ""
            rows = conn.execute(
                """
                SELECT a.message_id, a.terminal_status, a.report_source, a.error_fingerprint,
                       a.summary_text, a.created_at,
                       {request_column} AS request_text,
                       COALESCE(i.kind, '') AS interpretation_kind,
                       COALESCE(i.status, '') AS interpretation_status,
                       COALESCE(i.model_name, '') AS model_name,
                       COALESCE(i.elapsed_ms, 0) AS elapsed_ms,
                       COALESCE(i.error_kind, '') AS error_kind,
                       COALESCE(i.summary, '') AS ai_summary
                FROM execution_artifacts a
                LEFT JOIN execution_interpretations i ON i.message_id = a.message_id
                {join_jobs}
                ORDER BY a.created_at DESC
                LIMIT ?
                """.format(request_column=request_column, join_jobs=join_jobs),
                (limit,),
            ).fetchall()
        return [
            {**dict(row), "request_text": mask_sensitive_operation_data(str(row["request_text"]))[:300]}
            for row in rows
        ]

    def prune_artifacts(self, retention_days: int, now: int | None = None) -> int:
        cutoff = self._now(now) - retention_days * 86400
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM execution_artifacts WHERE created_at < ?", (cutoff,))
            conn.execute(
                "DELETE FROM execution_interpretations WHERE status IN ('done', 'failed', 'timed_out') AND updated_at < ?",
                (cutoff,),
            )
            conn.commit()
        return cursor.rowcount

    # interpretation tasks -------------------------------------------------

    def enqueue_interpretation(
        self,
        message_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        error_fingerprint: str = "",
        now: int | None = None,
    ) -> bool:
        timestamp = self._now(now)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO execution_interpretations (
                    message_id, kind, status, payload_json, error_fingerprint, created_at, updated_at
                )
                VALUES (?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    message_id,
                    kind,
                    json.dumps(redact_result_payload(payload), ensure_ascii=False),
                    error_fingerprint,
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        return cursor.rowcount > 0

    def claim_interpretation(self, message_id: str, now: int | None = None) -> InterpretationTask | None:
        timestamp = self._now(now)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE execution_interpretations
                SET status = 'running', attempts = attempts + 1, updated_at = ?
                WHERE message_id = ? AND status = 'pending'
                """,
                (timestamp, message_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
        return self.get_interpretation(message_id)

    def finish_interpretation(
        self,
        message_id: str,
        status: str,
        *,
        summary: str = "",
        error_kind: str = "",
        model_name: str = "",
        input_chars: int = 0,
        elapsed_ms: int = 0,
        candidate_pattern_id: int | None = None,
        now: int | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE execution_interpretations
                SET status = ?, summary = ?, error_kind = ?, model_name = ?, input_chars = ?,
                    elapsed_ms = ?, candidate_pattern_id = ?, updated_at = ?
                WHERE message_id = ?
                """,
                (
                    status,
                    mask_sensitive_operation_data(summary),
                    error_kind,
                    model_name,
                    input_chars,
                    elapsed_ms,
                    candidate_pattern_id,
                    self._now(now),
                    message_id,
                ),
            )
            conn.commit()

    def get_interpretation(self, message_id: str) -> InterpretationTask | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message_id, kind, status, attempts, payload_json, error_fingerprint, created_at, updated_at
                FROM execution_interpretations WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
        return InterpretationTask(**dict(row)) if row else None

    def recover_interpretations(self, *, max_attempts: int = 3) -> list[str]:
        """Return interrupted/pending tasks after restart; give up after ``max_attempts``."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE execution_interpretations SET status = 'failed', error_kind = 'max_attempts' "
                "WHERE status IN ('pending', 'running') AND attempts >= ?",
                (max_attempts,),
            )
            conn.execute("UPDATE execution_interpretations SET status = 'pending' WHERE status = 'running'")
            rows = conn.execute(
                "SELECT message_id FROM execution_interpretations WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
            conn.commit()
        return [str(row["message_id"]) for row in rows]

    # helpers --------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now(now: int | None) -> int:
        return int(time.time()) if now is None else now

    @staticmethod
    def _row_to_pattern(row: sqlite3.Row) -> ErrorPattern:
        return ErrorPattern(
            pattern_id=int(row["pattern_id"]),
            script_path=str(row["script_path"]),
            error_fingerprint=str(row["error_fingerprint"]),
            failure_category=str(row["failure_category"]),
            normalized_reason=str(row["normalized_reason"]),
            key_error_lines=tuple(json.loads(row["key_error_lines"])),
            diagnosis=str(row["diagnosis"]),
            status=str(row["status"]),
            seen_scopes=tuple(json.loads(row["seen_scopes_json"])),
            sample_message_ids=tuple(json.loads(row["sample_message_ids"])),
            first_seen_at=int(row["first_seen_at"]),
            last_seen_at=int(row["last_seen_at"]),
            hit_count=int(row["hit_count"]),
            approved_at=None if row["approved_at"] is None else int(row["approved_at"]),
            outcome=str(row["outcome"]) if "outcome" in row.keys() else OUTCOME_FAILURE,
        )


def _append_unique(items: list[str], value: str, limit: int) -> list[str]:
    if not value or value in items:
        return items[-limit:]
    return [*items, value][-limit:]


def _append_recent(items: list[str], value: str, limit: int) -> list[str]:
    return [*(item for item in items if item != value), value][-limit:]
