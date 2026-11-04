"""Project plan board stored on the Gateway (design: docs/superpowers/specs/2026-11-04-plan-board-server.md).

SQLite is the single source of truth; ``docs/plan-board.json`` seeds an empty
database and receives exported snapshots for git history.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

PLAN_STATUSES = ("done", "active", "todo")
LIST_FIELDS = ("done", "current", "remaining")
TEXT_FIELDS = ("title", "desc", "area", "note", "question")
DEFAULT_AREAS = ("执行链", "热更", "AI", "审批台", "知识库", "部署", "文档", "其他")
SEED_PATH = Path(__file__).resolve().parents[1] / "docs" / "plan-board.json"
_MAX_TEXT = 4000
_MAX_ITEMS = 60


class PlanError(ValueError):
    """Invalid plan board change."""


class PlanStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self, seed_path: str | Path | None = SEED_PATH) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS plan_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plan_steps (
                    step_id TEXT PRIMARY KEY,
                    sort_order INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    area TEXT NOT NULL DEFAULT '其他',
                    title TEXT NOT NULL,
                    desc TEXT NOT NULL DEFAULT '',
                    done_json TEXT NOT NULL DEFAULT '[]',
                    current_json TEXT NOT NULL DEFAULT '[]',
                    remaining_json TEXT NOT NULL DEFAULT '[]',
                    flow_json TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    question TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL,
                    updated_by TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS plan_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    step_id TEXT NOT NULL,
                    field TEXT NOT NULL,
                    old_value TEXT NOT NULL,
                    new_value TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    at INTEGER NOT NULL
                );
                """
            )
            empty = conn.execute("SELECT COUNT(*) FROM plan_steps").fetchone()[0] == 0
            conn.commit()
        if empty and seed_path is not None and Path(seed_path).is_file():
            self.import_snapshot(json.loads(Path(seed_path).read_text(encoding="utf-8")), operator="seed")

    # reading -----------------------------------------------------------------

    def get_plan(self) -> dict[str, Any]:
        with self._connect() as conn:
            meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM plan_meta")}
            rows = conn.execute("SELECT * FROM plan_steps ORDER BY sort_order, step_id").fetchall()
        steps = [self._step_dict(row) for row in rows]
        counts = {status: sum(1 for step in steps if step["status"] == status) for status in PLAN_STATUSES}
        areas = json.loads(meta.pop("areas", "[]") or "[]") or list(DEFAULT_AREAS)
        return {
            "meta": meta,
            "areas": areas,
            "counts": counts,
            "total": len(steps),
            "updated_at": max((step["updated_at"] for step in steps), default=0),
            "steps": steps,
        }

    def get_step(self, step_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM plan_steps WHERE step_id = ?", (step_id,)).fetchone()
        return self._step_dict(row) if row else None

    def list_events(self, step_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM plan_events {} ORDER BY event_id DESC LIMIT ?".format("WHERE step_id = ?" if step_id else "")
        params: tuple[Any, ...] = (step_id, limit) if step_id else (limit,)
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    # writing -----------------------------------------------------------------

    def update_step(self, step_id: str, changes: dict[str, Any], *, operator: str, now: int | None = None) -> dict[str, Any]:
        current = self.get_step(step_id)
        if current is None:
            raise KeyError(step_id)
        values = self._validated(changes)
        timestamp = _now(now)
        with self._connect() as conn:
            for field, value in values.items():
                old = current[field]
                if old == value:
                    continue
                column = "{}_json".format(field) if field in LIST_FIELDS else field
                stored = json.dumps(value, ensure_ascii=False) if field in LIST_FIELDS else value
                conn.execute("UPDATE plan_steps SET {} = ? WHERE step_id = ?".format(column), (stored, step_id))
                conn.execute(
                    "INSERT INTO plan_events (step_id, field, old_value, new_value, operator, at) VALUES (?, ?, ?, ?, ?, ?)",
                    (step_id, field, _event_text(old), _event_text(value), operator, timestamp),
                )
            conn.execute(
                "UPDATE plan_steps SET updated_at = ?, updated_by = ? WHERE step_id = ?",
                (timestamp, operator, step_id),
            )
            conn.commit()
        return self.get_step(step_id)  # type: ignore[return-value]

    def create_step(self, fields: dict[str, Any], *, operator: str, now: int | None = None) -> dict[str, Any]:
        values = self._validated(fields)
        if not values.get("title"):
            raise PlanError("标题不能为空")
        timestamp = _now(now)
        with self._connect() as conn:
            ids = [int(row[0]) for row in conn.execute("SELECT step_id FROM plan_steps") if str(row[0]).isdigit()]
            step_id = "{:02d}".format((max(ids) if ids else 0) + 1)
            order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM plan_steps").fetchone()[0]
            conn.execute(
                """
                INSERT INTO plan_steps (step_id, sort_order, status, area, title, desc, done_json, current_json,
                                        remaining_json, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id, order, values.get("status", "todo"), values.get("area", "其他"), values["title"],
                    values.get("desc", ""), *(json.dumps(values.get(field, []), ensure_ascii=False) for field in LIST_FIELDS),
                    timestamp, operator,
                ),
            )
            conn.execute(
                "INSERT INTO plan_events (step_id, field, old_value, new_value, operator, at) VALUES (?, 'created', '', ?, ?, ?)",
                (step_id, values["title"], operator, timestamp),
            )
            conn.commit()
        return self.get_step(step_id)  # type: ignore[return-value]

    def delete_step(self, step_id: str, *, operator: str, now: int | None = None) -> bool:
        current = self.get_step(step_id)
        if current is None:
            return False
        with self._connect() as conn:
            conn.execute("DELETE FROM plan_steps WHERE step_id = ?", (step_id,))
            conn.execute(
                "INSERT INTO plan_events (step_id, field, old_value, new_value, operator, at) VALUES (?, 'deleted', ?, '', ?, ?)",
                (step_id, json.dumps(current, ensure_ascii=False), operator, _now(now)),
            )
            conn.commit()
        return True

    def migrate_local(self, overrides: dict[str, str], notes: dict[str, dict[str, str]], *, operator: str) -> dict[str, int]:
        """Apply old browser-local status overrides and notes (notes are appended, never overwritten)."""
        statuses = applied_notes = 0
        for step_id, status in (overrides or {}).items():
            step = self.get_step(str(step_id))
            if step and status in PLAN_STATUSES and step["status"] != status:
                self.update_step(step["id"], {"status": status}, operator=operator)
                statuses += 1
        for step_id, record in (notes or {}).items():
            step = self.get_step(str(step_id))
            if not step or not isinstance(record, dict):
                continue
            changes = {}
            for field in ("note", "question"):
                incoming = str(record.get(field, "")).strip()
                if incoming and incoming not in step[field]:
                    changes[field] = "\n\n".join(part for part in (step[field], incoming) if part)
            if changes:
                self.update_step(step["id"], changes, operator=operator)
                applied_notes += 1
        return {"statuses": statuses, "notes": applied_notes}

    # snapshots ---------------------------------------------------------------

    def export_snapshot(self) -> dict[str, Any]:
        plan = self.get_plan()
        return {
            "schema_version": 1,
            "meta": plan["meta"],
            "areas": plan["areas"],
            "steps": [
                {key: step[key] for key in ("id", "sort_order", "status", "area", "title", "desc", "done", "current",
                                            "remaining", "flow", "note", "question")}
                for step in plan["steps"]
            ],
        }

    def import_snapshot(self, snapshot: dict[str, Any], *, operator: str, now: int | None = None) -> int:
        timestamp = _now(now)
        steps = snapshot.get("steps") or []
        with self._connect() as conn:
            conn.execute("DELETE FROM plan_steps")
            conn.execute("DELETE FROM plan_meta")
            for key, value in (snapshot.get("meta") or {}).items():
                conn.execute("INSERT INTO plan_meta (key, value) VALUES (?, ?)", (key, str(value)))
            conn.execute("INSERT INTO plan_meta (key, value) VALUES ('areas', ?)",
                         (json.dumps(snapshot.get("areas") or list(DEFAULT_AREAS), ensure_ascii=False),))
            for index, step in enumerate(steps):
                conn.execute(
                    """
                    INSERT INTO plan_steps (step_id, sort_order, status, area, title, desc, done_json, current_json,
                                            remaining_json, flow_json, note, question, updated_at, updated_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(step["id"]), int(step.get("sort_order", index)),
                        step.get("status") if step.get("status") in PLAN_STATUSES else "todo",
                        step.get("area", "其他"), step["title"], step.get("desc", ""),
                        *(json.dumps(step.get(field) or [], ensure_ascii=False) for field in LIST_FIELDS),
                        json.dumps(step["flow"], ensure_ascii=False) if step.get("flow") else "",
                        step.get("note", ""), step.get("question", ""), timestamp, operator,
                    ),
                )
            conn.commit()
        return len(steps)

    # helpers -----------------------------------------------------------------

    def _validated(self, changes: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field, value in changes.items():
            if field == "status":
                if value not in PLAN_STATUSES:
                    raise PlanError("状态只能是 done / active / todo")
                values[field] = value
            elif field in TEXT_FIELDS:
                if not isinstance(value, str) or len(value) > _MAX_TEXT:
                    raise PlanError("{} 必须是不超过 {} 字的文本".format(field, _MAX_TEXT))
                values[field] = value.strip() if field in ("title", "area") else value
            elif field in LIST_FIELDS:
                if not isinstance(value, list) or len(value) > _MAX_ITEMS or not all(isinstance(item, str) for item in value):
                    raise PlanError("{} 必须是文本数组".format(field))
                values[field] = [item.strip() for item in value if item.strip()]
            else:
                raise PlanError("不支持修改字段 {}".format(field))
        if "title" in values and not values["title"]:
            raise PlanError("标题不能为空")
        return values

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _step_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["step_id"],
            "sort_order": row["sort_order"],
            "status": row["status"],
            "area": row["area"],
            "title": row["title"],
            "desc": row["desc"],
            **{field: json.loads(row["{}_json".format(field)] or "[]") for field in LIST_FIELDS},
            "flow": json.loads(row["flow_json"]) if row["flow_json"] else None,
            "note": row["note"],
            "question": row["question"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }


def _event_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _now(now: int | None) -> int:
    return int(time.time()) if now is None else now
