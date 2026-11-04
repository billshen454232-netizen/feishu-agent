"""Hotfix threshold registry stored on the Gateway (design: docs/superpowers/specs/2026-10-30-threshold-registry-web.md).

SQLite is the single source of truth and is edited from the admin page. The Planner
only ever sees registered ASCII keys; :meth:`ThresholdStore.expand_plan` turns a
validated key-form plan into the executable ``-k <field> --table <table>`` form the
GS-1 tool already accepts, so adding a threshold never touches GS-1 or the Worker.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .ai_client import generate_with_provenance
from .muliu_plan import MuliuPlan, MuliuPlanError, MuliuStep
from .muliu_script_catalog import _CONTRACT_BLOCK_PATTERN, MuliuScriptCatalog

HOTFIX_PATH = "/home/serverGeneralScript/auto_hotfix_tool.py"
DUAL_PATCH_TYPES = (("common", "服务端"), ("client_avatar", "客户端"))
STATES = ("active", "disabled")
TEXT_FIELDS = ("name", "table_name", "field_name", "description", "source", "notes")
SEED_PATH = Path(__file__).resolve().parents[1] / "config" / "hotfix_thresholds.seed.json"
ROUTING_BEGIN = "<!-- HOTFIX_THRESHOLD_REGISTRY:BEGIN -->"
ROUTING_END = "<!-- HOTFIX_THRESHOLD_REGISTRY:END -->"

_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_MAX_TEXT = 2000
_MAX_ALIASES = 20
# Jobs that never got past the confirmation card cannot have left patches on a server.
_NOT_EXECUTED_STATUSES = ("awaiting_confirmation", "cancelled", "expired")


class ThresholdError(ValueError):
    """Invalid threshold registry change."""


class ThresholdStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self, seed_path: Optional[str | Path] = SEED_PATH) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hotfix_thresholds (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    table_name TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'active',
                    source TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    updated_by TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS hotfix_threshold_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    field TEXT NOT NULL,
                    old_value TEXT NOT NULL,
                    new_value TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    at INTEGER NOT NULL
                );
                """
            )
            empty = conn.execute("SELECT COUNT(*) FROM hotfix_thresholds").fetchone()[0] == 0
            conn.commit()
        if empty and seed_path is not None and Path(seed_path).is_file():
            self.import_snapshot(json.loads(Path(seed_path).read_text(encoding="utf-8")), operator="seed")

    # reading -----------------------------------------------------------------

    def list_thresholds(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM hotfix_thresholds ORDER BY sort_order, key").fetchall()
        items = [self._row_dict(row) for row in rows]
        executed = self._executed_references()
        for item in items:
            item["executed"] = _is_referenced(item, executed)
        return items

    def get(self, key: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM hotfix_thresholds WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        item = self._row_dict(row)
        item["executed"] = _is_referenced(item, self._executed_references())
        return item

    def active(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM hotfix_thresholds WHERE state = 'active' ORDER BY sort_order, key"
            ).fetchall()
        return [self._row_dict(row) for row in rows]

    def list_events(self, key: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM hotfix_threshold_events {} ORDER BY event_id DESC LIMIT ?".format(
            "WHERE key = ?" if key else ""
        )
        params: tuple[Any, ...] = (key, limit) if key else (limit,)
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    # writing -----------------------------------------------------------------

    def create(self, fields: dict[str, Any], *, operator: str, now: Optional[int] = None) -> dict[str, Any]:
        key = fields.get("key")
        if not isinstance(key, str) or _KEY_PATTERN.fullmatch(key) is None:
            raise ThresholdError("key 只能是小写字母开头的小写字母、数字、下划线（≤64 位）")
        if self.get(key) is not None:
            raise ThresholdError("key {} 已存在".format(key))
        values = self._validated({name: value for name, value in fields.items() if name != "key"})
        for required in ("name", "table_name", "field_name"):
            if not values.get(required):
                raise ThresholdError("{} 不能为空".format(_LABELS[required]))
        record = {"key": key, "aliases": [], "description": "", "state": "active", "source": "", "notes": "", **values}
        self._check_conflicts(record)
        timestamp = _now(now)
        with self._connect() as conn:
            order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM hotfix_thresholds").fetchone()[0]
            self._insert(conn, record, order, timestamp, operator)
            self._event(conn, key, "created", "", record["name"], operator, timestamp)
            conn.commit()
        return self.get(key)  # type: ignore[return-value]

    def update(self, key: str, changes: dict[str, Any], *, operator: str, now: Optional[int] = None) -> dict[str, Any]:
        current = self.get(key)
        if current is None:
            raise KeyError(key)
        values = self._validated(changes)
        moved = any(field in values and values[field] != current[field] for field in ("table_name", "field_name"))
        if moved and current["executed"]:
            raise ThresholdError("该阈值已执行过，不能修改生效路径（服务器上可能已有补丁）；请停用后新增一条")
        merged = {**current, **values}
        for required in ("name", "table_name", "field_name"):
            if not merged.get(required):
                raise ThresholdError("{} 不能为空".format(_LABELS[required]))
        self._check_conflicts(merged)
        timestamp = _now(now)
        with self._connect() as conn:
            for field, value in values.items():
                old = current[field]
                if old == value:
                    continue
                column = "aliases_json" if field == "aliases" else field
                stored = json.dumps(value, ensure_ascii=False) if field == "aliases" else value
                conn.execute("UPDATE hotfix_thresholds SET {} = ? WHERE key = ?".format(column), (stored, key))
                self._event(conn, key, field, _event_text(old), _event_text(value), operator, timestamp)
            conn.execute(
                "UPDATE hotfix_thresholds SET updated_at = ?, updated_by = ? WHERE key = ?", (timestamp, operator, key)
            )
            conn.commit()
        return self.get(key)  # type: ignore[return-value]

    def delete(self, key: str, *, operator: str, now: Optional[int] = None) -> bool:
        current = self.get(key)
        if current is None:
            return False
        if current["executed"]:
            raise ThresholdError("该阈值已执行过，只能停用不能删除（服务器上可能已有补丁需要还原）")
        with self._connect() as conn:
            conn.execute("DELETE FROM hotfix_thresholds WHERE key = ?", (key,))
            self._event(conn, key, "deleted", json.dumps(current, ensure_ascii=False), "", operator, _now(now))
            conn.commit()
        return True

    # snapshots ---------------------------------------------------------------

    def export_snapshot(self) -> dict[str, Any]:
        fields = ("key", "name", "aliases", "table_name", "field_name", "description", "state", "source", "notes")
        return {
            "schema_version": 1,
            "thresholds": [{field: item[field] for field in fields} for item in self.list_thresholds()],
        }

    def import_snapshot(self, snapshot: dict[str, Any], *, operator: str, now: Optional[int] = None) -> int:
        records = []
        for raw in snapshot.get("thresholds") or []:
            key = raw.get("key")
            if not isinstance(key, str) or _KEY_PATTERN.fullmatch(key) is None:
                raise ThresholdError("快照里的 key 不合法：{!r}".format(key))
            records.append({"key": key, **self._validated({k: v for k, v in raw.items() if k != "key"})})
        _check_unique(records)
        timestamp = _now(now)
        with self._connect() as conn:
            conn.execute("DELETE FROM hotfix_thresholds")
            for index, record in enumerate(records):
                self._insert(conn, record, index, timestamp, operator)
            conn.commit()
        return len(records)

    # planner integration -----------------------------------------------------

    def render_planner_catalog(self, catalog_text: str) -> str:
        """Swap the static executable hotfix contracts for Planner-form ones built from active entries."""
        active = self.active()
        text = _replace_routing_section(catalog_text, _render_routing(active))
        match = _CONTRACT_BLOCK_PATTERN.search(text)
        if match is None:
            return text
        block = json.loads(match.group(1))
        contracts = [item for item in block.get("contracts", []) if item.get("path") != HOTFIX_PATH]
        if active:
            contracts.extend(_planner_contracts(item["key"] for item in active))
        block["contracts"] = contracts
        rendered = json.dumps(block, ensure_ascii=False, indent=2)
        return text[: match.start(1)] + rendered + text[match.end(1):]

    def expand_plan(self, plan: MuliuPlan) -> MuliuPlan:
        """Translate Planner key-form hotfix steps into the executable field/table form (no AI involved)."""
        if not any(step.path == HOTFIX_PATH for step in plan.steps):
            return plan
        registry = {item["key"]: item for item in self.active()}
        steps: list[MuliuStep] = []
        for step in plan.steps:
            steps.extend(_expand_step(step, registry) if step.path == HOTFIX_PATH else [step])
        return dataclasses.replace(plan, steps=steps)

    # helpers -----------------------------------------------------------------

    def _validated(self, changes: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field, value in changes.items():
            if field in ("executed", "created_at", "updated_at", "updated_by", "sort_order"):
                continue
            if field == "state":
                if value not in STATES:
                    raise ThresholdError("状态只能是 active / disabled")
                values[field] = value
            elif field == "aliases":
                if not isinstance(value, list) or len(value) > _MAX_ALIASES or not all(isinstance(v, str) for v in value):
                    raise ThresholdError("别名必须是文本数组（≤{} 个）".format(_MAX_ALIASES))
                aliases = []
                for alias in (item.strip() for item in value):
                    if alias and alias not in aliases:
                        aliases.append(alias)
                values[field] = aliases
            elif field in TEXT_FIELDS:
                if not isinstance(value, str) or len(value) > _MAX_TEXT:
                    raise ThresholdError("{} 必须是不超过 {} 字的文本".format(_LABELS[field], _MAX_TEXT))
                values[field] = value.strip()
            else:
                raise ThresholdError("不支持修改字段 {}".format(field))
        for field in ("table_name", "field_name"):
            if values.get(field) and _IDENTIFIER_PATTERN.fullmatch(values[field]) is None:
                raise ThresholdError("{} 只能是字母或下划线开头的字母、数字、下划线（≤64 位）".format(_LABELS[field]))
        return values

    def _check_conflicts(self, record: dict[str, Any]) -> None:
        with self._connect() as conn:
            others = [self._row_dict(row) for row in conn.execute(
                "SELECT * FROM hotfix_thresholds WHERE key != ?", (record["key"],)
            ).fetchall()]
        _check_unique([*others, record])

    def _executed_references(self) -> set[str]:
        """Arguments of hotfix steps in jobs that went past confirmation (old key form and new field form)."""
        placeholders = ",".join("?" for _ in _NOT_EXECUTED_STATUSES)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT plan_json FROM message_jobs WHERE plan_json LIKE ? AND status NOT IN ({})".format(placeholders),
                    ("%auto_hotfix_tool.py%", *_NOT_EXECUTED_STATUSES),
                ).fetchall()
        except sqlite3.OperationalError:
            return set()
        references: set[str] = set()
        for (plan_json,) in rows:
            try:
                steps = json.loads(plan_json).get("steps") or []
            except (ValueError, AttributeError):
                continue
            for step in steps:
                if isinstance(step, dict) and step.get("path") == HOTFIX_PATH:
                    references.update(str(arg) for arg in step.get("args") or [])
        return references

    @staticmethod
    def _insert(conn: sqlite3.Connection, record: dict[str, Any], order: int, timestamp: int, operator: str) -> None:
        conn.execute(
            """
            INSERT INTO hotfix_thresholds (key, name, aliases_json, table_name, field_name, description, state,
                                           source, notes, sort_order, created_at, updated_at, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["key"], record["name"], json.dumps(record.get("aliases") or [], ensure_ascii=False),
                record["table_name"], record["field_name"], record.get("description", ""),
                record.get("state", "active"), record.get("source", ""), record.get("notes", ""),
                order, timestamp, timestamp, operator,
            ),
        )

    @staticmethod
    def _event(conn: sqlite3.Connection, key: str, field: str, old: str, new: str, operator: str, at: int) -> None:
        conn.execute(
            "INSERT INTO hotfix_threshold_events (key, field, old_value, new_value, operator, at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, field, old, new, operator, at),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "key": row["key"],
            "name": row["name"],
            "aliases": json.loads(row["aliases_json"] or "[]"),
            "table_name": row["table_name"],
            "field_name": row["field_name"],
            "description": row["description"],
            "state": row["state"],
            "source": row["source"],
            "notes": row["notes"],
            "sort_order": row["sort_order"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }


REVIEW_TIMEOUT_SECONDS = 90
_SIDES = {"client": "客户端", "server": "服务端", "both": "双端", "unknown": "无法判断"}


async def review_threshold(client: Any, record: dict[str, Any], others: list[dict[str, Any]]) -> dict[str, Any]:
    """Advisory AI review of one entry's metadata; never blocks saving and never produces Lua."""
    existing = "\n".join(
        "- {key}：{name}（{table_name}.{field_name}）".format(**item) for item in others if item["key"] != record.get("key")
    )
    entry = "key={}\n中文名={}\n别名={}\n表名={}\n字段名={}\n说明={}".format(
        record.get("key", ""), record.get("name", ""), "、".join(record.get("aliases") or []),
        record.get("table_name", ""), record.get("field_name", ""), record.get("description", ""),
    )
    prompt = (
        "你在审核游戏测试服「热更阈值注册表」的一条登记，只看元数据，不要输出 Lua 或执行建议。"
        "输出一个 JSON 对象，不要输出其他内容：\n"
        '{"consistent": true 或 false（字段说明 / 中文名与表名、字段名是否自洽）, "reason": "一句话理由", '
        '"duplicate_of": "疑似与哪个已有 key 重复，没有就留空", '
        '"side": "client / server / both / unknown（从字段名与说明判断更像哪一侧读取）", '
        '"comments": "其他需要人注意的点，没有就留空"}\n\n'
        "待审核条目：\n" + entry + "\n\n已有条目：\n" + (existing or "（无）")
    )
    generation = await asyncio.wait_for(generate_with_provenance(client, [], prompt), timeout=REVIEW_TIMEOUT_SECONDS)
    parsed = _parse_json_object(str(generation.text))
    side = str(parsed.get("side") or "unknown")
    return {
        "consistent": bool(parsed.get("consistent")) if parsed else None,
        "reason": str(parsed.get("reason") or ("AI 没有返回可解析的结论" if not parsed else "")),
        "duplicate_of": str(parsed.get("duplicate_of") or ""),
        "side": _SIDES.get(side, _SIDES["unknown"]),
        "comments": str(parsed.get("comments") or ""),
        "model": str(generation.model or ""),
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


class HotfixPlannerCatalog(MuliuScriptCatalog):
    """The Gateway Planner's view of the catalog: hotfix routes and key whitelist come from the live registry."""

    def __init__(self, path: str | Path, store: ThresholdStore) -> None:
        super().__init__(path)
        self._store = store

    def read(self) -> str:
        return self._store.render_planner_catalog(super().read())


_LABELS = {
    "name": "中文名", "table_name": "表名", "field_name": "字段名", "description": "说明",
    "source": "来源", "notes": "备注",
}


def _check_unique(records: list[dict[str, Any]]) -> None:
    """Chinese route terms and Lua paths must each map to exactly one key, disabled entries included."""
    terms: dict[str, str] = {}
    paths: dict[tuple[str, str], str] = {}
    keys: set[str] = set()
    for record in records:
        if record["key"] in keys:
            raise ThresholdError("key {} 重复".format(record["key"]))
        keys.add(record["key"])
        for term in [record["name"], *record.get("aliases", [])]:
            previous = terms.get(term)
            if previous is not None and previous != record["key"]:
                raise ThresholdError("中文名 / 别名「{}」已被 {} 使用".format(term, previous))
            terms[term] = record["key"]
        path = (record["table_name"], record["field_name"])
        if path in paths:
            raise ThresholdError("生效路径 {}.{} 已被 {} 使用".format(*path, paths[path]))
        paths[path] = record["key"]


def _is_referenced(item: dict[str, Any], references: set[str]) -> bool:
    return item["key"] in references or item["field_name"] in references


def _render_routing(active: list[dict[str, Any]]) -> str:
    lines = [
        "#### 已登记热更阈值中文路由（自动生成）",
        "",
        "> 唯一来源：Gateway 热更阈值注册表（https://ops.ttlink.asia/thresholds）。仅本表中的中文标准名和别名可以映射到对应 `-k`；不得按相近词义创建别名。",
        "",
    ]
    if not active:
        lines.append("当前没有已登记的热更阈值，所有热更请求都必须拒绝。")
    for item in active:
        aliases = "；别名：{}".format("、".join("`{}`".format(a) for a in item["aliases"])) if item["aliases"] else ""
        lines.append("- 中文标准名：`{}`{}；执行：`-k {}`".format(item["name"], aliases, item["key"]))
    return "\n".join(lines)


def _replace_routing_section(text: str, generated: str) -> str:
    if text.count(ROUTING_BEGIN) != 1 or text.count(ROUTING_END) != 1:
        return text
    begin = text.index(ROUTING_BEGIN)
    end = text.index(ROUTING_END)
    if end <= begin:
        return text
    return "{}{}\n{}\n{}".format(text[:begin], ROUTING_BEGIN, generated, text[end:])


def _planner_contracts(keys: Iterable[str]) -> list[dict[str, Any]]:
    key_pattern = "(?:{})".format("|".join(re.escape(key) for key in sorted(keys)))
    server = {"server_id": "[0-9]{3,8}"}
    patch_types = "avatar|battle|center|client_account|client_avatar|common|login|scene|test|union|unionmirror"
    base = {"path": HOTFIX_PATH, "runner": "python3.7", "risk": "write"}
    return [
        {"name": "hotfix-server-threshold", **base,
         "args": ["-s", "{server_id}", "-k", "{key}", "-v", "{value}"],
         "variables": {**server, "key": key_pattern, "value": "[0-9]{1,16}"}},
        {"name": "hotfix-server-threshold-with-patch-type", **base,
         "args": ["-s", "{server_id}", "-k", "{key}", "-v", "{value}", "--patch-type", "{patch_type}"],
         "variables": {**server, "key": key_pattern, "value": "[0-9]{1,16}", "patch_type": patch_types}},
        {"name": "cleanup-legacy-auto-hotfix", **base,
         "args": ["-s", "{server_id}", "--cleanup-legacy", "{key}"],
         "variables": {**server, "key": key_pattern}},
        {"name": "restore-auto-hotfix", **base,
         "args": ["-s", "{server_id}", "--restore-auto", "{key}"],
         "variables": {**server, "key": key_pattern}},
    ]


def _expand_step(step: MuliuStep, registry: dict[str, dict[str, Any]]) -> list[MuliuStep]:
    args = list(step.args)
    if len(args) == 4 and args[0] == "-s" and args[2] in ("--cleanup-legacy", "--restore-auto"):
        item = _registered(registry, args[3])
        return [dataclasses.replace(step, args=[args[0], args[1], args[2], item["field_name"]])]
    if len(args) in (6, 8) and args[0] == "-s" and args[2] == "-k" and args[4] == "-v":
        item = _registered(registry, args[3])
        if len(args) == 8:
            if args[6] != "--patch-type":
                raise MuliuPlanError("热更步骤参数不符合登记的调用合同")
            sides = [(args[7], "")]
        else:
            sides = [(patch_type, "（{}）".format(label)) for patch_type, label in DUAL_PATCH_TYPES]
        return [
            dataclasses.replace(
                step,
                args=["-s", args[1], "-k", item["field_name"], "--table", item["table_name"], "-v", args[5],
                      "--patch-type", patch_type],
                description=step.description + suffix,
            )
            for patch_type, suffix in sides
        ]
    raise MuliuPlanError("热更步骤参数不符合登记的调用合同")


def _registered(registry: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    item = registry.get(key)
    if item is None:
        raise MuliuPlanError("热更阈值 {} 未登记或已停用，请先在阈值注册表登记".format(key))
    return item


def _event_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _now(now: Optional[int]) -> int:
    return int(time.time()) if now is None else now
