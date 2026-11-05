"""Server Manager API route for selected capabilities (design: docs/superpowers/specs/2026-11-04-server-manager-executor.md).

Option C: capabilities the API covers can be switched, one by one and per region,
from the Muliu web task to the Server Manager API. This module holds the
deterministic translation table (catalog contract → API task sequence), a small
synchronous client (it runs inside the executor thread) and the step runner that
turns API outcomes into the same log shape Muliu returns, so result cards,
templates and AI readings keep working unchanged.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from .muliu_executor import MuliuExecutionError, MuliuSubmittedError
from .muliu_plan import MuliuStep

logger = logging.getLogger(__name__)

ROUTE_MULIU = "muliu"
ROUTE_API = "api"
ROUTES = (ROUTE_MULIU, ROUTE_API)
REGIONS = ("cn", "overseas")
# GS-1 scripts special-case 4001 (yc_* helpers, opening-time handling); the API has no equivalent.
EXCLUDED_SERVERS = frozenset({"4001"})
_NON_TERMINAL = {"", "queued", "pending", "running", "starting", "submitted", "published"}


@dataclass(frozen=True)
class ApiTask:
    task_type: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class RouteSpec:
    """What switching a contract to the API means — shown on the admin page."""

    contract: str
    label: str
    sequence: str
    exceptions: str


ROUTE_SPECS = {
    "basic-server-info": RouteSpec(
        "basic-server-info", "单服信息查询（basic_info X，只读）",
        "读 server-config（剧本 / 赛季 / 开服时间 / 对外）+ servers（代码版本）+ status [game_X, zone_X01]（进程状态）",
        "4001；服号不在 API 清单；API 不提供 supervisor / bglog 异常日志（排查起服失败请用木流）",
    ),
    "start-game-only": RouteSpec("start-game-only", "只起 game（start X noz）", "start [game_X]", "4001；服号不在 API 清单"),
    "start-server": RouteSpec("start-server", "起服（start X）", "start [zone_X01] → start [game_X]", "4001；服号不在 API 清单"),
    "shutdown-normal": RouteSpec("shutdown-normal", "正常关服（shutdown X）", "stop [game_X] → stop [zone_X01]", "4001；服号不在 API 清单"),
    "shutdown-mode": RouteSpec(
        "shutdown-mode", "指定方式关服（shutdown X normal/kill）",
        "normal：stop [game_X] → stop [zone_X01]；kill：kill [game_X, zone_X01]",
        "autokill / gvgzone / gvgdaemon；4001；服号不在 API 清单",
    ),
    "clear-server": RouteSpec(
        "clear-server", "清档（clear X）", "kill [game_X, zone_X01] → clear [game_X, zone_X01]",
        "4001（GS-1 有特殊分支）；服号不在 API 清单",
    ),
}


def translate(contract_name: str, step: MuliuStep) -> Optional[list[ApiTask]]:
    """API task sequence for one validated step, or None when this step must stay on Muliu."""
    if contract_name not in ROUTE_SPECS or not step.args:
        return None
    server_id = step.args[0]
    if not server_id.isdigit() or server_id in EXCLUDED_SERVERS:
        return None
    game, zone = "game_{}".format(server_id), "zone_{}01".format(server_id)
    mode = step.args[1] if len(step.args) > 1 else ""
    if contract_name == "basic-server-info":
        return [ApiTask("info", (game,))]
    if contract_name == "start-game-only":
        return [ApiTask("start", (game,))]
    if contract_name == "start-server":
        return [ApiTask("start", (zone,)), ApiTask("start", (game,))]
    if contract_name == "shutdown-normal" or (contract_name == "shutdown-mode" and mode == "normal"):
        return [ApiTask("stop", (game,)), ApiTask("stop", (zone,))]
    if contract_name == "shutdown-mode" and mode == "kill":
        return [ApiTask("kill", (game, zone))]
    if contract_name == "clear-server":
        return [ApiTask("kill", (game, zone)), ApiTask("clear", (game, zone))]
    return None


def submission_id(message_id: str, step_number: int, task_index: int) -> str:
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:20]
    return "fb-{}-s{}-t{}".format(digest, step_number, task_index)


@dataclass(frozen=True)
class ApiOutcome:
    run_id: str
    status: str
    summary: str
    server_states: dict[str, Any]
    items: list[str]

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


class ServerManagerClient:
    """Synchronous client for one Server Manager instance (one region)."""

    def __init__(
        self,
        base_url: str,
        env: str,
        *,
        http_client: Optional[httpx.Client] = None,
        poll_interval_seconds: float = 3.0,
        task_timeout_seconds: float = 900.0,
        request_timeout_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = base_url.rstrip("/") + "/server_manager/api"
        self._env = env
        self._http = http_client or httpx.Client(timeout=request_timeout_seconds)
        self._poll = poll_interval_seconds
        self._timeout = task_timeout_seconds
        self._sleep = sleep
        self._clock = clock

    def server_ids(self) -> set[str]:
        response = self._http.get(self._base + "/servers", params={"env": self._env} if self._env else None)
        response.raise_for_status()
        return {str(item.get("id")) for item in response.json().get("servers", []) if item.get("id")}

    def server_info(self, server_id: str, submission: str, reason: str) -> list[str]:
        """basic_info-like lines from read-only API data (config, release version, process status)."""
        game, zone = "game_{}".format(server_id), "zone_{}01".format(server_id)
        params = {"target": game, **({"env": self._env} if self._env else {})}
        response = self._http.get(self._base + "/server-config", params=params)
        response.raise_for_status()
        items = response.json().get("items") or [{}]
        item = next((entry for entry in items if entry.get("primary")), items[0])
        config = item.get("config") or {}
        servers = self._http.get(self._base + "/servers", params={"env": self._env} if self._env else None)
        servers.raise_for_status()
        listed = {str(entry.get("id")): entry for entry in servers.json().get("servers", [])}
        version = ((listed.get(game) or {}).get("release_version") or {}).get("value") or "未知"
        targets = (game, zone) if zone in listed else (game,)
        status = self.run(ApiTask("status", targets), submission, reason)
        lines = ["当前代码版本:", str(version)]
        start_ts = config.get("debug_server_start_time")
        if start_ts:
            lines.append("开服时间 时间戳/时间如下：")
            lines.append("{} -> {}".format(start_ts, _beijing(start_ts)))
        lines.append("开服时间配置（open_server_time）：{}".format(config.get("open_server_time") or "未配置"))
        lines.append("剧本 {}，赛季 {}，对外 {}，启动模式 {}".format(
            config.get("scenario", item.get("scenario", "未知")), config.get("season", "未知"),
            config.get("is_publish", "未知"), config.get("nslg_mode", "未知")))
        states = ", ".join("{}={}".format(key, value) for key, value in status.server_states.items()) or "未知"
        lines.append("进程状态（status 任务 run_id={}）：{}".format(status.run_id or "无", states))
        if status.server_states and all(value == "ready" for value in status.server_states.values()):
            lines.append("服务器已就绪（ready）")
        else:
            lines.append("服务器未就绪；需要看起服报错时可以说「诊断 {} 服起服失败」".format(server_id))
        return lines

    def run(self, task: ApiTask, submission: str, reason: str) -> ApiOutcome:
        body = {
            "task_type": task.task_type, "targets": list(task.targets),
            "params": {"fast": True}, "submission_id": submission, "reason": reason[:200],
        }
        if self._env:  # the server team may drop ``env`` (only qa_test exists); empty config = omit it
            body["env"] = self._env
        try:
            response = self._http.post(self._base + "/tasks", json=body)
        except httpx.HTTPError as exc:
            self._raise_unless_registered(submission, "提交请求无响应：{}".format(type(exc).__name__))
        else:
            if 400 <= response.status_code < 500 and response.status_code != 409:
                raise MuliuSubmittedError(
                    MuliuExecutionError("API 拒绝提交（HTTP {}）：{}".format(response.status_code, _error_text(response))),
                    submitted=False,
                )
            if response.status_code >= 500 or response.status_code == 409:
                self._raise_unless_registered(submission, "提交返回 HTTP {}".format(response.status_code))
        return self._wait(submission)

    def _raise_unless_registered(self, submission: str, reason: str) -> None:
        """After an ambiguous submit, the submission record decides: absent = never submitted."""
        try:
            response = self._http.get(self._base + "/task-submissions/" + submission)
        except httpx.HTTPError as exc:
            raise MuliuSubmittedError(
                MuliuExecutionError("{}，且无法查询提交记录（{}）".format(reason, type(exc).__name__)), submitted=True
            ) from exc
        if response.status_code == 404:
            raise MuliuSubmittedError(MuliuExecutionError("{}；API 无此提交记录，确认未执行".format(reason)), submitted=False)
        if response.status_code >= 400:
            raise MuliuSubmittedError(
                MuliuExecutionError("{}，提交记录查询返回 HTTP {}".format(reason, response.status_code)), submitted=True
            )

    def _wait(self, submission: str) -> ApiOutcome:
        deadline = self._clock() + self._timeout
        record: dict[str, Any] = {}
        while True:
            try:
                response = self._http.get(self._base + "/task-submissions/" + submission)
                if response.status_code < 400:
                    record = response.json()
                    if str(record.get("status") or "") not in _NON_TERMINAL:
                        break
            except (httpx.HTTPError, ValueError):
                logger.warning("server manager submission poll failed submission=%s", submission, exc_info=True)
            if self._clock() >= deadline:
                raise MuliuSubmittedError(
                    MuliuExecutionError("API 任务 {} 秒内未结束（run_id={}）".format(int(self._timeout), record.get("run_id") or "未知")),
                    submitted=True,
                )
            self._sleep(self._poll)
        run_id = str(record.get("run_id") or "")
        result: dict[str, Any] = {}
        if run_id:
            try:
                response = self._http.get(self._base + "/tasks/" + run_id + "/result")
                if response.status_code < 400:
                    result = response.json()
            except (httpx.HTTPError, ValueError):
                logger.warning("server manager result read failed run_id=%s", run_id, exc_info=True)
        return ApiOutcome(
            run_id=run_id,
            status=str(result.get("status") or record.get("status") or ""),
            summary=str(result.get("summary") or ""),
            server_states=dict(result.get("server_states") or {}),
            items=[
                "{} {} {}".format(item.get("game_id", ""), item.get("machine_id", ""), item.get("status", ""))
                + ("（{}）".format(item.get("reason")) if item.get("reason") else "")
                for item in result.get("items") or [] if isinstance(item, dict)
            ][:20],
        )


class ServerManagerStepRunner:
    """Runs one validated plan step through the API and returns a Muliu-shaped log."""

    def __init__(self, client: ServerManagerClient, message_id: str) -> None:
        self._client = client
        self._message_id = message_id

    def execute_step(self, step: MuliuStep, step_number: int, tasks: list[ApiTask]) -> str:
        if tasks and tasks[0].task_type == "info":
            reason = "飞书机器人 第{}步：{}".format(step_number, step.description)
            try:
                info = self._client.server_info(step.args[0], submission_id(self._message_id, step_number, 1), reason)
            except MuliuSubmittedError:
                raise
            except Exception as exc:  # noqa: BLE001 - read-only: report and let the requester retry
                return "[API 路线] 只读查询失败：{}\nAI_OP_END status=failed error=API 查询失败：{}".format(
                    type(exc).__name__, type(exc).__name__)
            return "\n".join(["[API 路线] Server Manager 只读查询", *info, "AI_OP_END status=success"])
        lines = ["[API 路线] Server Manager，共 {} 次提交".format(len(tasks))]
        for index, task in enumerate(tasks, start=1):
            submission = submission_id(self._message_id, step_number, index)
            reason = "飞书机器人 第{}步：{}".format(step_number, step.description)
            try:
                outcome = self._client.run(task, submission, reason)
            except MuliuSubmittedError as exc:
                if index > 1:
                    # An earlier sub-task already changed the server: never report "not executed".
                    raise MuliuSubmittedError(
                        MuliuExecutionError("第 {}/{} 次 API 提交失败，前面的提交已执行：{}".format(index, len(tasks), exc)),
                        submitted=True,
                    ) from exc
                raise
            lines.append("{}/{} {} {} → {}（run_id={}）".format(
                index, len(tasks), task.task_type, ",".join(task.targets), outcome.status or "未知", outcome.run_id or "无"))
            if outcome.summary:
                lines.append("  " + outcome.summary)
            if outcome.server_states:
                lines.append("  server_states: " + ", ".join("{}={}".format(k, v) for k, v in outcome.server_states.items()))
            lines.extend("  " + item for item in outcome.items)
            if not outcome.succeeded:
                lines.append("AI_OP_END status=failed error=API 任务 {} 状态 {}：{}".format(
                    task.task_type, outcome.status or "未知", outcome.summary or "无摘要"))
                return "\n".join(lines)
        if any(task.task_type == "start" for task in tasks):
            lines.append("起服已由 API 按 wait_until=ready 校验进程状态（替代木流 basic_info 核验）")
        lines.append("AI_OP_END status=success")
        return "\n".join(lines)


def _beijing(timestamp: Any) -> str:
    try:
        moment = datetime.datetime.fromtimestamp(int(timestamp), datetime.timezone(datetime.timedelta(hours=8)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "无法换算"
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
        return str(data.get("detail") or data.get("error") or data.get("code") or data)[:200]
    except ValueError:
        return response.text[:200]
