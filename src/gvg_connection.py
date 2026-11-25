"""Inspect and conditionally update Weekly game-server GVG configuration."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .server_environments import ServerEnvironments
from .utils.time_text import BEIJING

GVG_QUERY_PATH = "/home/serverGeneralScript/_bot/gvg_connection"
WEEKLY_GVG_SCOPE = "weeklygvg"
GVG_CONFIG_PATH = "/data/app/etc/nslg_game.lua"
GVG_CHANGE_ID_PATTERN = r"[0-9a-f]{16}"
_SERVER_ID = re.compile(r"[0-9]{3,8}")
_KEEP_REQUEST = re.compile(r"(?:国服\s*)?Weekly\s*GVG\s*(?:只|仅)保留\s*([0-9]{3,8})\s*(?:服)?(?:的)?(?:GVG\s*)?连接配置[。！!]?", re.I)
_RESTORE_REQUEST = re.compile(r"恢复\s*(?:国服\s*)?(?:Weekly\s*)?GVG\s*变更单\s*(" + GVG_CHANGE_ID_PATTERN + r")[。！!]?", re.I)
_FIELD = re.compile(r"\bNSLG_GVGZONENUM\b")
_ASSIGNMENT = re.compile(r"[ \t]*NSLG_GVGZONENUM[ \t]*=[ \t]*([0-9]{1,8}|nil|false)[ \t]*;?[ \t]*")
# The deployment file uses literal assignments. Do not interpret commented or quoted examples as settings.
_LUA_TEXT = re.compile(
    r'''--\[(?P<comment_eq>=*)\[.*?\](?P=comment_eq)\]|--[^\r\n]*|'''
    r'''\[(?P<string_eq>=*)\[.*?\](?P=string_eq)\]|"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*' ''',
    re.S | re.X,
)


class GvgQueryError(ValueError):
    """A query could not establish its scope or read its configuration source."""


@dataclass(frozen=True)
class WeeklyGvgScope:
    cluster: int
    game_servers: tuple[str, ...]

    @classmethod
    def load(cls, path: str | Path) -> WeeklyGvgScope:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            assets = ServerEnvironments.from_data(raw)
            entry = raw["gvg_clusters"][WEEKLY_GVG_SCOPE]
            cluster, nodes = entry["number"], entry["nodes"]
            if (entry["region"] != "cn" or entry["game_environment"] != "weekly" or type(cluster) is not int
                    or not 1 <= cluster <= 99_999_999 or not isinstance(nodes, list) or not nodes
                    or any(not isinstance(node, str) or not re.fullmatch(r"[0-9]{3,8}", node) for node in nodes)):
                raise ValueError("invalid Weekly GVG mapping")
            weekly = assets.regions["cn"]["environments"]["weekly"]
            servers = tuple(sorted(set(weekly) - set(nodes), key=int))
            if not servers:
                raise ValueError("Weekly game servers missing")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GvgQueryError("国服 Weekly GVG 资产配置不可用。") from exc
        return cls(cluster, servers)


def change_request(text: str) -> tuple[str, str] | None:
    if match := _KEEP_REQUEST.fullmatch(text.strip()):
        return "preview-keep", match[1]
    if match := _RESTORE_REQUEST.fullmatch(text.strip()):
        return "preview-restore", match[1].lower()
    return None


def configured_zone(content: str) -> str:
    # Erase comments, but retain a marker for strings so an expression cannot become a literal by stripping it.
    commented = False

    def strip_text(match: re.Match[str]) -> str:
        nonlocal commented
        text = match[0]
        if text.startswith("--"):
            commented |= bool(re.search(r"\bNSLG_GVGZONENUM[ \t]*=", text))
            marker = " "
        else:
            marker = " <字符串> "
        return "\n" * text.count("\n") + marker

    body = _LUA_TEXT.sub(strip_text, content)
    mentions = [line for line in body.splitlines() if _FIELD.search(line)]
    if not mentions:
        return "已注释" if commented else "未配置"
    match = _ASSIGNMENT.fullmatch(mentions[0]) if len(mentions) == 1 else None
    if match is None:
        return "未知"
    return str(int(match[1])) if match[1].isdigit() else match[1]


def list_weekly_gvg(scope: WeeklyGvgScope, read_config: Callable[[str], str]) -> str:
    def read(server: str) -> str:
        try:
            return configured_zone(read_config(server))
        except GvgQueryError:
            return "未知"

    with ThreadPoolExecutor(max_workers=3) as pool:
        values = dict(zip(scope.game_servers, pool.map(read, scope.game_servers)))
    unknown = [server for server, value in values.items() if value == "未知"]
    members = [server for server, value in values.items() if value == str(scope.cluster)]
    if len(unknown) == len(values):
        raise GvgQueryError("国服 Weekly 游戏服的 GVG 配置均无法核实。")
    return "\n".join([
        f"配置指向国服 Weekly GVG {scope.cluster} 的游戏服：{'、'.join(members) or '已核实的服中无记录'}",
        f"核对范围：{len(values)} 台登记的普通游戏服，已核实 {len(values) - len(unknown)} 台",
        *(f"{server} 服：{value}" for server, value in values.items()),
        f"查询时间：{datetime.now(BEIJING):%Y-%m-%d %H:%M:%S}（北京时间）",
        "实连状态未核实。",
    ])


class GvgChangeUnknown(GvgQueryError):
    """A submitted configuration change needs reconciliation before retrying."""


@dataclass(frozen=True)
class GvgConfigFile:
    server_id: str
    machine_id: str
    content: str
    sha256: str

    def with_zone(self, old: str, new: str) -> str:
        if configured_zone(self.content) != old:
            raise GvgQueryError(f"{self.server_id} 服的连接配置已变化或无法确定。")
        # Replace only the literal on the original line. Preserve whitespace and inline comments.
        assignment = re.compile(
            r"^([ \t]*NSLG_GVGZONENUM[ \t]*=[ \t]*)" + re.escape(old)
            + r"(?=[ \t]*;?[ \t]*(?:--[^\r\n]*)?\r?$)", re.M,
        )
        updated, count = assignment.subn(lambda match: match[1] + new, self.content)
        if count != 1 or configured_zone(updated) != new:
            raise GvgQueryError(f"{self.server_id} 服的配置无法按单一赋值修改。")
        return updated


def change_id(message_id: str) -> str:
    return hashlib.sha256(message_id.encode()).hexdigest()[:16]


def parse_preview(log: str, message_id: str) -> tuple[str, int, list[str]]:
    try:
        data = json.loads(log)
        identifier, cluster, targets = data["id"], data["cluster"], data["targets"]
        if (identifier != change_id(message_id) or type(cluster) is not int or not 1 <= cluster <= 99_999_999
                or not isinstance(targets, list) or len(targets) > 20
                or any(not isinstance(server, str) or not _SERVER_ID.fullmatch(server) for server in targets)
                or len(set(targets)) != len(targets)):
            raise ValueError("Invalid preview")
    except (ValueError, KeyError, TypeError) as exc:
        raise GvgQueryError("无法核实 Worker 返回的 GVG 配置预检结果。") from exc
    return identifier, cluster, targets


class GvgChangeStore:
    """Worker-local records. Store file identities, digests and outcomes only."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS gvg_changes (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, started INTEGER NOT NULL DEFAULT 0)")

    def put(self, identifier: str, snapshot: dict) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            cursor = conn.execute("INSERT OR IGNORE INTO gvg_changes (id, snapshot) VALUES (?, ?)",
                                  (identifier, json.dumps(snapshot)))
            if cursor.rowcount != 1:
                raise GvgQueryError("该请求已经有预检记录，请重新发起变更。")

    def get(self, identifier: str) -> dict:
        with closing(sqlite3.connect(self.path)) as conn:
            row = conn.execute("SELECT snapshot FROM gvg_changes WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise GvgQueryError("未找到 GVG 变更单。")
        return json.loads(row[0])

    def begin(self, identifier: str) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            cursor = conn.execute("UPDATE gvg_changes SET started = 1 WHERE id = ? AND started = 0", (identifier,))
            if cursor.rowcount != 1:
                raise GvgQueryError("该变更单已有执行记录，请先核对结果。")

    def record(self, identifier: str, snapshot: dict, *, original: tuple[str, dict] | None = None) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE gvg_changes SET snapshot = ? WHERE id = ?", (json.dumps(snapshot), identifier))
            if original is not None:
                original_id, original_snapshot = original
                conn.execute("UPDATE gvg_changes SET snapshot = ? WHERE id = ?",
                             (json.dumps(original_snapshot), original_id))


class WeeklyGvgChange:
    def __init__(self, scope: WeeklyGvgScope, store: GvgChangeStore, client, message_id: str) -> None:
        self.scope = scope
        self.store = store
        self.client = client
        self.message_id = message_id
        self.identifier = change_id(message_id)

    def _files(self, mode: str, argument: str) -> dict[str, GvgConfigFile]:
        if mode == "preview-keep":
            with ThreadPoolExecutor(max_workers=3) as pool:
                files = dict(zip(self.scope.game_servers, pool.map(self.client.gvg_config_file, self.scope.game_servers)))
            zones = {server: configured_zone(config.content) for server, config in files.items()}
            unknown = [server for server, value in zones.items() if value == "未知"]
            if unknown:
                raise GvgQueryError("以下服的 GVG 配置无法核实：" + "、".join(unknown))
            cluster = str(self.scope.cluster)
            if zones.get(argument) != cluster:
                raise GvgQueryError(f"保留服必须是配置指向 GVG {cluster} 的 Weekly 普通游戏服。")
            return {server: files[server] for server, value in zones.items() if value == cluster and server != argument}

        original = self.store.get(argument)
        if original["mode"] != "preview-keep" or original["cluster"] != self.scope.cluster:
            raise GvgQueryError("该变更单不属于当前 GVG 的连接关闭操作。")
        if any(item["state"] in {"unknown", "restore_unknown"} for item in original["items"].values()):
            raise GvgQueryError("原变更单有结果待核对的服，请先确认实际配置。")
        files = {}
        for server, item in original["items"].items():
            if item["state"] != "written":
                continue
            if server not in self.scope.game_servers:
                raise GvgQueryError(f"{server} 服已不在当前 Weekly 普通游戏服范围。")
            config = self.client.gvg_config_file(server)
            if (config.machine_id, config.sha256) != (item["machine_id"], item["after"]):
                raise GvgQueryError(f"{server} 服的配置已变化，无法按原变更单恢复。")
            files[server] = config
        return files

    def _check_stopped(self, server: str, phase: str) -> None:
        if not self.client.gvg_game_stopped(server, self.message_id, phase):
            raise GvgQueryError(f"{server} 服的 game 进程尚未确认停止。")

    def preview(self, mode: str, argument: str) -> str:
        files = self._files(mode, argument)
        if len(files) > 20:
            raise GvgQueryError("变更范围超过 20 台游戏服。")
        cluster = str(self.scope.cluster)
        old, new = (cluster, "nil") if mode == "preview-keep" else ("nil", cluster)
        for config in files.values():
            config.with_zone(old, new)
            self._check_stopped(config.server_id, "preview")
        if files:
            self.store.put(self.identifier, {
                "mode": mode, "argument": argument, "cluster": self.scope.cluster, "old": old, "new": new,
                "items": {server: {"machine_id": config.machine_id, "before": config.sha256, "state": "pending"}
                          for server, config in files.items()},
            })
        return json.dumps({"id": self.identifier, "cluster": self.scope.cluster, "targets": list(files)})

    def apply(self, identifier: str, targets: list[str]) -> tuple[str, str, bool]:
        if identifier != self.identifier:
            raise GvgQueryError("变更单与当前确认请求不匹配。")
        snapshot = self.store.get(identifier)
        items = snapshot["items"]
        if targets != list(items) or snapshot["cluster"] != self.scope.cluster:
            raise GvgQueryError("确认名单和预检范围不一致，请重新发起。")
        files = self._files(snapshot["mode"], snapshot["argument"])
        if list(files) != targets:
            raise GvgQueryError("预检后的目标名单已变化，请重新生成确认卡。")
        contents = {}
        for server, config in files.items():
            item = items[server]
            if (config.machine_id, config.sha256) != (item["machine_id"], item["before"]):
                raise GvgQueryError(f"{server} 服的配置版本已变化，请重新预检。")
            contents[server] = config.with_zone(snapshot["old"], snapshot["new"])
            self._check_stopped(server, "apply")
        original_id = snapshot["argument"] if snapshot["mode"] == "preview-restore" else None
        original = self.store.get(original_id) if original_id else None
        self.store.begin(identifier)

        lines = [f"GVG 变更单 {identifier}"]
        for index, (server, config) in enumerate(files.items()):
            try:
                # Minimize the interval between checking the process and submitting a conditional write.
                self._check_stopped(server, "before-write")
                items[server]["state"] = "unknown"
                if original_id is not None and original is not None:
                    original["items"][server]["state"] = "restore_unknown"
                    self.store.record(identifier, snapshot, original=(original_id, original))
                else:
                    self.store.record(identifier, snapshot)
                self.client.write_gvg_config(config, contents[server])
                try:
                    updated = self.client.gvg_config_file(server)
                    if updated.machine_id != config.machine_id or updated.content != contents[server]:
                        raise GvgQueryError("回读配置与预计内容不一致。")
                except GvgQueryError as exc:
                    raise GvgChangeUnknown("提交后未能核实回读结果。") from exc
                items[server].update(state="written", after=updated.sha256)
                if original_id is not None and original is not None:
                    original["items"][server]["state"] = "restored"
                    self.store.record(identifier, snapshot, original=(original_id, original))
                else:
                    self.store.record(identifier, snapshot)
                lines.append(f"{server} 服 {snapshot['old']} → {snapshot['new']}，回读一致")
            except GvgQueryError as exc:
                unknown = isinstance(exc, GvgChangeUnknown)
                items[server]["state"] = "unknown" if unknown else "failed"
                if original_id is not None and original is not None and not unknown:
                    original["items"][server]["state"] = "written"
                    self.store.record(identifier, snapshot, original=(original_id, original))
                else:
                    self.store.record(identifier, snapshot)
                reason = f"{server} 服{'结果待核对' if unknown else '未修改'}：{exc}"
                lines.append(reason)
                if targets[index + 1:]:
                    lines.append("待处理服号：" + "、".join(targets[index + 1:]))
                return "\n".join(lines), reason, unknown
        lines.append("游戏服下次启动时加载新配置。")
        return "\n".join(lines), "", False
