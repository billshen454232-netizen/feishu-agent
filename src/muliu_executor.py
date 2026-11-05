"""Dynamic Muliu Task executor used by the Feishu operation bot."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from http.client import HTTPException
from http.cookiejar import CookieJar
from typing import Any, Callable, Optional, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .config import MuliuConfig
from .muliu_plan import MuliuPlan, MuliuStep


SCRIPT_ROOT = "/home/serverGeneralScript"
START_SCRIPT_PATH = "{}/start".format(SCRIPT_ROOT)
BASIC_INFO_SCRIPT_PATH = "{}/basic_info.sh".format(SCRIPT_ROOT)
_DISPATCHER_TERMINAL_MARKER_RE = re.compile(
    r"AI_OP_END\s+status=(?:success|failed)(?:\s+error=.*)?"
)
_MULIU_TASK_END_RE = re.compile(r"^END\s*$", flags=re.MULTILINE)
# Unique per Muliu task run; used to tell "a new run started" from "stale previous log".
_RUN_MARKER_RE = re.compile(r"^MULIU_RUN_VERSION:\s*(\S+)", flags=re.MULTILINE)
# Pre-submission requests (read version, save arguments) are safe to retry: nothing runs yet.
PRE_SUBMIT_ATTEMPTS = 3
# After a lost execute response, how many log polls to look for a new run before deciding.
SUBMISSION_RECONCILE_POLLS = 6

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class MuliuExecutionError(RuntimeError):
    """Raised when Muliu cannot log in, save parameters, execute, or return a log."""


class MuliuLogPendingError(MuliuExecutionError):
    """Raised while the selected Muliu task has not created a readable log yet."""


class MuliuResultUnknownError(MuliuExecutionError):
    """Raised after Muliu accepted an operation but no trustworthy terminal result arrived."""


class MuliuSubmissionUnknownError(MuliuExecutionError):
    """Raised when an execute request may have reached Muliu without a response."""


class MuliuHttpResponseError(MuliuExecutionError):
    """Raised when Muliu returned an HTTP error response.

    The message keeps only the status and a short page title; the raw body
    (often a full HTML error page with internal hosts) stays in ``body``.
    """

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__("Muliu HTTP {}：{}".format(status, _brief_http_body(body)))


class MuliuSessionExpiredError(MuliuExecutionError):
    """Muliu redirected to its login page: the session was dropped or expired."""


class MuliuTransportError(MuliuExecutionError):
    """Raised when no HTTP response was received from Muliu."""


@dataclass(frozen=True)
class MuliuSubmittedError(MuliuExecutionError):
    """Wrap an error with whether retrying the step could duplicate a remote operation."""

    cause: MuliuExecutionError
    submitted: bool

    def __str__(self) -> str:
        return str(self.cause)


@dataclass(frozen=True)
class MuliuStepResult:
    step_number: int
    description: str
    path: str
    args: list[str]
    succeeded: bool
    log: str


@dataclass(frozen=True)
class MuliuExecutionResult:
    summary: str
    step_results: list[MuliuStepResult]
    failed_step_number: int | None = None
    failure_reason: str = ""
    result_unknown_reason: str = ""
    # Set only when the failed step was proven never submitted: steps from here
    # can be offered to the requester as "continue remaining steps".
    resumable_from_step: int | None = None
    # Worker-local: the failure came from Muliu transport/HTTP trouble, not from
    # the script's own terminal marker. Never serialized.
    transient_error: bool = False

    @property
    def succeeded(self) -> bool:
        return self.failed_step_number is None and not self.result_unknown_reason

    @property
    def result_unknown(self) -> bool:
        return bool(self.result_unknown_reason)


class MuliuHttpClient:
    """Blocking HTTP client that owns one Muliu login session."""

    def __init__(self, config: MuliuConfig) -> None:
        self._config = config
        self._cookies = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookies))
        self._csrf_token = ""

    def login(self) -> None:
        if not self._config.base_url:
            raise MuliuExecutionError("缺少 muliu.base_url 配置")
        if not self._config.username or not self._config.password:
            raise MuliuExecutionError("缺少 Muliu 登录账号或密码配置")

        _, login_page = self._request_text(
            "GET",
            "/muliu/login",
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        self._csrf_token = extract_csrf_token(login_page)
        status, result = self.request_json(
            "POST",
            "/muliu/api/login",
            json_data={
                "username": self._config.username,
                "password": self._config.password,
            },
            include_csrf=True,
        )
        if not 200 <= status < 300 or not isinstance(result, dict) or result.get("code") != 0:
            raise MuliuExecutionError("登录失败：{}".format(_result_message(result)))

    def execute_step(self, step: MuliuStep) -> str:
        accepted = False
        try:
            previous_run = self.latest_run_marker()
            version = self.retry_before_submit(self.get_current_version, "读取任务版本")
            self.retry_before_submit(lambda: self.save_arguments(version, step), "保存执行参数")
            version = self.retry_before_submit(self.get_current_version, "读取任务版本")
            try:
                self.execute_task(version)
            except MuliuSubmissionUnknownError:
                started = self.wait_for_new_run(previous_run)
                if started is False:
                    raise MuliuExecutionError(
                        "提交请求未得到 Muliu 响应，且随后未出现新的运行记录，判定本步未执行"
                    )
                if started is None:
                    raise
                logger.warning("muliu execute response lost but a new run started; continuing")
            accepted = True
            return self.wait_for_completed_step_log(previous_run)
        except MuliuSubmittedError:
            raise
        except MuliuSubmissionUnknownError as exc:
            raise MuliuSubmittedError(exc, submitted=True) from exc
        except MuliuExecutionError as exc:
            raise MuliuSubmittedError(exc, submitted=accepted) from exc

    def retry_before_submit(self, action: Callable[[], _T], label: str) -> _T:
        return retry_before_submit(action, label, delay_seconds=self._config.log_wait_seconds)

    def latest_run_marker(self) -> str | None:
        """Marker of the latest Muliu run; ``""`` when none exists, ``None`` when unreadable."""
        try:
            return run_marker(self.get_latest_step_log())
        except MuliuLogPendingError:
            return ""
        except MuliuExecutionError:
            return None

    def wait_for_new_run(self, previous_run: str | None) -> bool | None:
        """After a lost execute response: True if a new run appeared, False if not, None if unknowable."""
        if previous_run is None:
            return None
        for poll in range(SUBMISSION_RECONCILE_POLLS):
            current = self.latest_run_marker()
            if current is None:
                return None
            if current and current != previous_run:
                return True
            if poll < SUBMISSION_RECONCILE_POLLS - 1:
                time.sleep(self._config.log_wait_seconds)
        return False

    def wait_for_completed_step_log(self, previous_run: str | None = None) -> str:
        """Wait for the selected Muliu task run to emit a dispatcher terminal marker.

        The Muliu log endpoint can return live ``Running`` output before the
        child script has ended. That is an in-progress state, not a failed script.
        Accept a result only after both the Task wrapper and dispatcher have
        reached their terminal markers. A log still carrying ``previous_run``'s
        marker belongs to the previous step and is ignored. Transient read
        failures are retried until the deadline (polling is read-only).
        """
        interval_seconds = self._config.log_wait_seconds
        timeout_seconds = self._config.log_completion_timeout_seconds
        deadline = time.monotonic() + timeout_seconds
        latest_log = ""

        while True:
            try:
                latest_log = self.get_latest_step_log()
            except MuliuLogPendingError:
                latest_log = ""
            except (MuliuTransportError, MuliuHttpResponseError) as exc:
                if isinstance(exc, MuliuHttpResponseError) and exc.status < 500:
                    raise
                logger.warning("muliu log poll failed, will retry error=%s", exc)
                latest_log = ""
            if latest_log and previous_run and run_marker(latest_log) == previous_run:
                latest_log = ""
            if latest_log and is_completed_step_log(latest_log):
                return latest_log
            if time.monotonic() >= deadline:
                raise MuliuResultUnknownError(
                    "受控 Muliu 任务在 {} 秒内未返回脚本终态；操作可能仍在运行，请勿重复确认或重试".format(
                        _format_seconds(timeout_seconds)
                    )
                )
            time.sleep(interval_seconds)

    def get_current_version(self) -> int:
        _, result = self._request_read_json_with_session_recovery(
            "/muliu/api/task/detail?id={}".format(self._config.task_id),
        )
        if not isinstance(result, dict) or result.get("code") != 0:
            raise MuliuExecutionError("读取 Muliu 任务详情失败：{}".format(_result_message(result)))

        version = result.get("data", {}).get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise MuliuExecutionError("Muliu 任务详情中没有有效 version")
        return version

    def save_arguments(self, version: int, step: MuliuStep) -> None:
        """Save the step's arguments; re-login once if the session dropped (nothing runs yet)."""
        try:
            self._save_arguments_once(version, step)
        except MuliuSessionExpiredError:
            logger.warning("muliu session expired before submit, logging in again")
            self.login()
            self._save_arguments_once(version, step)

    def _save_arguments_once(self, version: int, step: MuliuStep) -> None:
        execution_request = json.dumps(
            {"path": step.path, "args": step.args},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        status, result = self.request_json(
            "POST",
            "/muliu/api/task/argument/save",
            form_data={
                "id": self._config.task_id,
                "step": self._config.step,
                "script": self._config.script_index,
                "parameter": self._config.parameter_index,
                "configValue": execution_request,
                "version": version,
            },
            include_csrf=True,
        )
        if not 200 <= status < 300 or not isinstance(result, dict) or result.get("code") != 0:
            raise MuliuExecutionError("保存执行参数失败：{}".format(_result_message(result)))

    def execute_task(self, version: int) -> None:
        try:
            status, result = self.request_json(
                "POST",
                "/muliu/api/task/execute",
                form_data={
                    "id": self._config.task_id,
                    "step": self._config.step,
                    "version": version,
                },
                include_csrf=True,
            )
        except (MuliuTransportError, MuliuHttpResponseError, MuliuSessionExpiredError) as exc:
            # Never resubmit blindly: the caller reconciles against Muliu's run marker.
            raise MuliuSubmissionUnknownError(
                "提交受控 Muliu 任务时未取得可确认的 Muliu 接受结果；远端可能已开始执行，请勿重复确认或重试：{}".format(exc)
            ) from exc
        if not 200 <= status < 300 or not isinstance(result, dict):
            raise MuliuSubmissionUnknownError(
                "提交受控 Muliu 任务时返回格式异常；远端可能已开始执行，请勿重复确认或重试：{}".format(
                    _result_message(result)
                )
            )
        if result.get("code") != 0:
            raise MuliuExecutionError("Muliu 明确拒绝执行任务：{}".format(_result_message(result)))

    def get_latest_step_log(self) -> str:
        _, result = self._request_read_json_with_session_recovery(
            "/muliu/api/task/log?id={}&step={}&num=0".format(
                self._config.task_id,
                self._config.step,
            ),
        )
        if not isinstance(result, dict) or result.get("code") != 0:
            raise MuliuExecutionError("读取 Muliu 日志失败：{}".format(_result_message(result)))

        logs = result.get("data")
        if not isinstance(logs, list) or not logs:
            raise MuliuLogPendingError("Muliu 尚未返回当前任务日志")
        latest_log = logs[0]
        if not isinstance(latest_log, dict):
            raise MuliuExecutionError("Muliu 最新日志格式无效")
        content = latest_log.get("content")
        if not isinstance(content, str) or not content.strip():
            raise MuliuLogPendingError("Muliu 当前任务日志尚未写入内容")
        return content.strip()

    def _request_read_json_with_session_recovery(self, path: str) -> tuple[int, Any]:
        """Retry one safe task-read request after an expired Muliu session."""
        try:
            status, result = self.request_json("GET", path)
            expired = _is_muliu_login_page(result)
        except MuliuSessionExpiredError:
            expired = True
        if not expired:
            return status, result
        logger.warning("muliu session expired on read, logging in again")

        self.login()
        status, result = self.request_json("GET", path)
        if _is_muliu_login_page(result):
            raise MuliuExecutionError("Muliu 会话恢复后仍返回登录页，无法读取任务状态")
        return status, result

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
        include_csrf: bool = False,
    ) -> tuple[int, Any]:
        if json_data is not None and form_data is not None:
            raise ValueError("请求不能同时携带 JSON 和表单数据")

        data: bytes | None = None
        headers = self._base_headers()
        headers["Accept"] = "*/*"
        if json_data is not None:
            data = json.dumps(json_data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif form_data is not None:
            data = urlencode(form_data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        if method == "POST":
            headers["Origin"] = self._config.base_url
        if include_csrf:
            if not self._csrf_token:
                raise MuliuExecutionError("尚未取得 Muliu CSRF Token")
            headers["X-CSRFToken"] = self._csrf_token

        status, text = self._request_text(method, path, data=data, headers=headers)
        try:
            return status, json.loads(text)
        except json.JSONDecodeError:
            return status, text

    def _base_headers(self) -> dict[str, str]:
        return {
            "Referer": "{}/muliu/tasks/detail?id={}".format(
                self._config.base_url,
                self._config.task_id,
            ),
            "X-Requested-With": "XMLHttpRequest",
        }

    def _request_text(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str],
    ) -> tuple[int, str]:
        request = Request(
            "{}{}".format(self._config.base_url, path),
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with self._opener.open(request, timeout=self._config.request_timeout_seconds) as response:
                return response.status, decode_response(
                    response.read(),
                    response.headers.get_content_charset(),
                )
        except HTTPError as exc:
            text = decode_response(
                exc.read(),
                exc.headers.get_content_charset() if exc.headers else None,
            )
            if "/muliu/login" in str(getattr(exc, "url", "") or exc.geturl() or "") and not path.startswith("/muliu/login"):
                raise MuliuSessionExpiredError("Muliu 会话已失效（被重定向到登录页，HTTP {}）".format(exc.code)) from exc
            raise MuliuHttpResponseError(exc.code, text) from exc
        except URLError as exc:
            raise MuliuTransportError("Muliu 请求失败：{}".format(exc.reason)) from exc
        except (TimeoutError, ConnectionError, HTTPException) as exc:
            # A read timeout after the connection is up is not wrapped in URLError.
            raise MuliuTransportError("Muliu 请求超时或连接中断：{}".format(exc)) from exc


class MuliuExecutor:
    """Run plans one at a time so selected-task parameters cannot overwrite each other."""

    def __init__(self, config: MuliuConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()

    async def execute_plan(
        self,
        plan: MuliuPlan,
        *,
        api_steps: Optional[list[Any]] = None,
        api_runner: Any = None,
    ) -> MuliuExecutionResult:
        """``api_steps[i]`` holds the Server Manager task list for step i+1, or None to use Muliu."""
        if not self._config.enabled:
            raise MuliuExecutionError("Muliu 执行功能未启用")

        async with self._lock:
            return await asyncio.to_thread(self._execute_plan_blocking, plan, api_steps, api_runner)

    def _execute_plan_blocking(
        self, plan: MuliuPlan, api_steps: Optional[list[Any]] = None, api_runner: Any = None
    ) -> MuliuExecutionResult:
        client = MuliuHttpClient(self._config)
        logged_in = False
        results: list[MuliuStepResult] = []
        total = len(plan.steps)

        for step_number, step in enumerate(plan.steps, start=1):
            label = "第 {}/{} 步「{}」".format(step_number, total, step.description)
            api_tasks = api_steps[step_number - 1] if api_steps and api_runner is not None else None
            if not api_tasks and not logged_in:
                # Muliu login happens only once a Muliu step is reached; nothing of this step ran yet.
                try:
                    retry_before_submit(client.login, "登录", delay_seconds=self._config.log_wait_seconds)
                except MuliuExecutionError as exc:
                    if not results:
                        raise
                    return MuliuExecutionResult(
                        summary=plan.summary,
                        step_results=results,
                        failed_step_number=step_number,
                        failure_reason="{}未执行：木流登录失败：{}".format(label, exc),
                        resumable_from_step=step_number,
                        transient_error=True,
                    )
                logged_in = True
            try:
                if api_tasks:
                    log = api_runner.execute_step(step, step_number, api_tasks)
                else:
                    log = client.execute_step(step)
            except MuliuSubmittedError as exc:
                if exc.submitted:
                    logger.warning("muliu step result unknown step=%s/%s error=%s", step_number, total, exc)
                    return MuliuExecutionResult(
                        summary=plan.summary,
                        step_results=results,
                        result_unknown_reason="{}已提交，结果未知：{}".format(label, exc),
                        transient_error=True,
                    )
                logger.warning("muliu step not executed step=%s/%s error=%s", step_number, total, exc)
                return MuliuExecutionResult(
                    summary=plan.summary,
                    step_results=results,
                    failed_step_number=step_number,
                    failure_reason="{}未执行：{}".format(label, exc),
                    resumable_from_step=step_number,
                    transient_error=True,
                )
            except Exception as exc:  # noqa: BLE001 - keep completed steps; this step may have run
                logger.exception("muliu step crashed step=%s/%s", step_number, total)
                return MuliuExecutionResult(
                    summary=plan.summary,
                    step_results=results,
                    result_unknown_reason="{}执行时发生异常，是否已执行未知：{}；请勿重复确认或重试".format(label, exc),
                    transient_error=True,
                )
            step_succeeded, failure_reason = interpret_script_outcome(log)
            results.append(
                MuliuStepResult(
                    step_number=step_number,
                    description=step.description,
                    path=step.path,
                    args=step.args,
                    succeeded=step_succeeded,
                    log=log,
                )
            )
            if not step_succeeded:
                return MuliuExecutionResult(
                    summary=plan.summary,
                    step_results=results,
                    failed_step_number=step_number,
                    failure_reason=failure_reason,
                )

            if step.path == START_SCRIPT_PATH and not api_tasks:
                time.sleep(self._config.start_verification_wait_seconds)
                verification_step = _build_start_verification_step(step)
                try:
                    verification_log = client.execute_step(verification_step)
                except MuliuSubmittedError as exc:
                    return MuliuExecutionResult(
                        summary=plan.summary,
                        step_results=results,
                        result_unknown_reason="起服脚本已成功结束；起服状态结果尚未核验：{}".format(exc),
                    )
                verified, verification_error = interpret_start_verification_outcome(verification_log)
                results.append(
                    MuliuStepResult(
                        step_number=step_number,
                        description="核验 {} 服是否起服成功".format(step.args[0]),
                        path=verification_step.path,
                        args=verification_step.args,
                        succeeded=verified,
                        log=verification_log,
                    )
                )
                if not verified:
                    return MuliuExecutionResult(
                        summary=plan.summary,
                        step_results=results,
                        failed_step_number=step_number,
                        failure_reason=verification_error,
                    )

        return MuliuExecutionResult(summary=plan.summary, step_results=results)


def _brief_http_body(body: str, limit: int = 120) -> str:
    """Page title or a short tag-free excerpt of an HTTP error body."""
    title = re.search(r"(?is)<title>(.*?)</title>", body or "")
    text = title.group(1) if title else re.sub(r"(?s)<[^>]+>", " ", body or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:limit] or "（无内容）"


def retry_before_submit(action: Callable[[], _T], label: str, *, delay_seconds: float) -> _T:
    """Retry a request made before ``execute``: nothing has run yet, so it is safe."""
    for attempt in range(1, PRE_SUBMIT_ATTEMPTS + 1):
        try:
            return action()
        except (MuliuTransportError, MuliuHttpResponseError) as exc:
            retryable = isinstance(exc, MuliuTransportError) or exc.status >= 500
            if not retryable or attempt == PRE_SUBMIT_ATTEMPTS:
                raise
            logger.warning("muliu %s failed, retrying attempt=%s/%s error=%s", label, attempt, PRE_SUBMIT_ATTEMPTS, exc)
            time.sleep(delay_seconds)
    raise AssertionError("unreachable")


def run_marker(log: str) -> str:
    match = _RUN_MARKER_RE.search(log or "")
    return match.group(1) if match else ""


def is_completed_step_log(log: str) -> bool:
    """Return true only after both the dispatcher and Muliu wrapper have ended."""
    return bool(_DISPATCHER_TERMINAL_MARKER_RE.search(log) and _MULIU_TASK_END_RE.search(log))


def _format_seconds(seconds: float) -> str:
    return str(int(seconds)) if isinstance(seconds, (int, float)) and float(seconds).is_integer() else str(seconds)


def _build_start_verification_step(start_step: MuliuStep) -> MuliuStep:
    if len(start_step.args) < 1 or not start_step.args[0]:
        raise MuliuExecutionError("起服计划缺少服务器编号，无法执行起服状态核验")
    return MuliuStep(
        path=BASIC_INFO_SCRIPT_PATH,
        args=[start_step.args[0]],
        description="核验 {} 服是否起服成功".format(start_step.args[0]),
    )


def interpret_start_verification_outcome(log: str) -> tuple[bool, str]:
    """Require completed basic-info output to explicitly prove the server started."""
    script_succeeded, failure_reason = interpret_script_outcome(log)
    if not script_succeeded:
        return False, failure_reason
    if re.search(r"^开服成功\s*$", log, flags=re.MULTILINE) is None:
        return False, "起服脚本已结束，但核验日志未确认“开服成功”"
    return True, ""


def interpret_script_outcome(log: str) -> tuple[bool, str]:
    """Interpret the terminal dispatcher marker from a completed Muliu log."""
    markers = list(re.finditer(r"AI_OP_END\s+status=(success|failed)(?:\s+error=(.*))?", log))
    if not markers:
        wrapper_error = re.search(r"^AI_OP_ERROR:\s*(.+)$", log, flags=re.MULTILINE)
        if wrapper_error:
            return False, wrapper_error.group(1).strip()
        return False, "GS-1 分发器未返回 AI_OP_END 终态标记"

    marker = markers[-1]
    if marker.group(1) == "success":
        return True, ""

    detail = (marker.group(2) or "GS-1 脚本返回失败状态").strip()
    return False, detail


def extract_csrf_token(login_page: str) -> str:
    patterns = (
        r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']csrf-token["\']',
        r'<input[^>]+name=["\']csrf_token["\'][^>]+value=["\']([^"\']+)["\']',
        r'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']csrf_token["\']',
        r'(?:csrfToken|csrf_token)\s*[:=]\s*["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, login_page, flags=re.IGNORECASE)
        if match:
            return html.unescape(match.group(1))
    raise MuliuExecutionError("Muliu 登录页中未找到 CSRF Token")


def decode_response(raw_body: bytes, declared_charset: str | None) -> str:
    for encoding in (declared_charset, "utf-8", "gb18030"):
        if not encoding:
            continue
        try:
            return raw_body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_body.decode("utf-8", errors="replace")


def _is_muliu_login_page(result: Any) -> bool:
    """Recognize the Muliu login document without treating arbitrary HTML as auth loss."""
    if not isinstance(result, str):
        return False
    normalized = result.lower()
    return (
        "<html" in normalized
        and "/muliu/login" in normalized
        and "csrf" in normalized
        and "password" in normalized
    )


def _result_message(result: Any) -> str:
    if _is_muliu_login_page(result):
        return "Muliu 返回登录页，会话可能已失效"
    if isinstance(result, dict):
        return str(result.get("msg") or result.get("message") or result)
    if isinstance(result, str) and "<html" in result.lower():
        return "Muliu 返回非预期 HTML 响应"
    return str(result)
