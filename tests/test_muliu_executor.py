import json

import pytest

from src.config import MuliuConfig
from src.muliu_executor import (
    BASIC_INFO_SCRIPT_PATH,
    START_SCRIPT_PATH,
    MuliuExecutionError,
    MuliuExecutor,
    MuliuHttpClient,
    MuliuHttpResponseError,
    MuliuResultUnknownError,
    MuliuSubmissionUnknownError,
    MuliuSubmittedError,
    MuliuTransportError,
    is_completed_step_log,
    interpret_script_outcome,
    interpret_start_verification_outcome,
    _is_muliu_login_page,
)
from src.muliu_plan import MuliuPlan, MuliuStep


def make_config(*, task_id=89):
    return MuliuConfig(
        base_url="https://muliu.example.com",
        username="user",
        password="password",
        task_id=task_id,
        step=11,
        script_index=0,
        parameter_index=0,
        log_wait_seconds=0.001,
    )


def test_save_arguments_uses_dynamic_path_and_args(monkeypatch):
    client = MuliuHttpClient(make_config())
    captured = {}

    def fake_request_json(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["form_data"] = kwargs["form_data"]
        return 200, {"code": 0}

    monkeypatch.setattr(client, "request_json", fake_request_json)
    client.save_arguments(
        19,
        MuliuStep(
            path="/home/serverGeneralScript/cc_patch.py",
            args=["-s", "6001", "-ck"],
            description="检查补丁",
        ),
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/muliu/api/task/argument/save"
    assert captured["form_data"]["id"] == 89
    assert captured["form_data"]["step"] == 11
    assert captured["form_data"]["version"] == 19
    assert json.loads(captured["form_data"]["configValue"]) == {
        "path": "/home/serverGeneralScript/cc_patch.py",
        "args": ["-s", "6001", "-ck"],
    }


def test_client_uses_the_selected_route_task_id_for_every_task_endpoint(monkeypatch):
    client = MuliuHttpClient(make_config(task_id=101))
    captured = []

    def fake_request_json(method, path, **kwargs):
        captured.append((method, path, kwargs))
        if path.startswith("/muliu/api/task/detail"):
            return 200, {"code": 0, "data": {"version": 19}}
        if path.startswith("/muliu/api/task/log"):
            return 200, {"code": 0, "data": [{"content": "AI_OP_END status=success\nEND"}]}
        return 200, {"code": 0}

    monkeypatch.setattr(client, "request_json", fake_request_json)

    assert client.get_current_version() == 19
    client.save_arguments(19, MuliuStep("/home/serverGeneralScript/cc_patch.py", ["-s", "2003"], "写入补丁"))
    client.execute_task(19)
    assert client.get_latest_step_log() == "AI_OP_END status=success\nEND"

    assert [path for _method, path, _kwargs in captured] == [
        "/muliu/api/task/detail?id=101",
        "/muliu/api/task/argument/save",
        "/muliu/api/task/execute",
        "/muliu/api/task/log?id=101&step=11&num=0",
    ]
    assert captured[1][2]["form_data"]["id"] == 101
    assert captured[2][2]["form_data"]["id"] == 101


LOGIN_PAGE = """<!DOCTYPE html>
<html>
  <head><meta name="csrf-token" content="expired"></head>
  <body><form action="/muliu/login"><input type="password" name="password"></form></body>
</html>"""


def test_recognizes_only_muliu_login_html_as_an_expired_session():
    assert _is_muliu_login_page(LOGIN_PAGE) is True
    assert _is_muliu_login_page("<html><body>proxy error</body></html>") is False
    assert _is_muliu_login_page({"code": 0}) is False


def test_get_current_version_recovers_expired_session_once(monkeypatch):
    client = MuliuHttpClient(make_config())
    calls = []
    responses = iter([
        (200, LOGIN_PAGE),
        (200, {"code": 0, "data": {"version": 19}}),
    ])
    monkeypatch.setattr(
        client,
        "request_json",
        lambda method, path, **_kwargs: (calls.append((method, path)) or next(responses)),
    )
    monkeypatch.setattr(client, "login", lambda: calls.append(("LOGIN", "")))

    assert client.get_current_version() == 19
    assert calls == [
        ("GET", "/muliu/api/task/detail?id=89"),
        ("LOGIN", ""),
        ("GET", "/muliu/api/task/detail?id=89"),
    ]


def test_get_latest_step_log_recovers_expired_session_once(monkeypatch):
    client = MuliuHttpClient(make_config())
    calls = []
    responses = iter([
        (200, LOGIN_PAGE),
        (200, {"code": 0, "data": [{"content": "AI_OP_END status=success\\nEND"}]}),
    ])
    monkeypatch.setattr(
        client,
        "request_json",
        lambda method, path, **_kwargs: (calls.append((method, path)) or next(responses)),
    )
    monkeypatch.setattr(client, "login", lambda: calls.append(("LOGIN", "")))

    assert client.get_latest_step_log() == "AI_OP_END status=success\\nEND"
    assert calls == [
        ("GET", "/muliu/api/task/log?id=89&step=11&num=0"),
        ("LOGIN", ""),
        ("GET", "/muliu/api/task/log?id=89&step=11&num=0"),
    ]


def test_session_recovery_stops_after_one_read_retry_and_hides_login_html(monkeypatch):
    client = MuliuHttpClient(make_config())
    calls = []
    monkeypatch.setattr(
        client,
        "request_json",
        lambda method, path, **_kwargs: (calls.append((method, path)) or (200, LOGIN_PAGE)),
    )
    monkeypatch.setattr(client, "login", lambda: calls.append(("LOGIN", "")))

    with pytest.raises(MuliuExecutionError, match="会话恢复后仍返回登录页") as raised:
        client.get_current_version()

    assert LOGIN_PAGE not in str(raised.value)
    assert calls == [
        ("GET", "/muliu/api/task/detail?id=89"),
        ("LOGIN", ""),
        ("GET", "/muliu/api/task/detail?id=89"),
    ]


def test_execute_step_recovers_detail_read_without_replaying_control_posts(monkeypatch):
    client = MuliuHttpClient(make_config())
    calls = []
    detail_responses = iter([
        (200, LOGIN_PAGE),
        (200, {"code": 0, "data": {"version": 10}}),
        (200, {"code": 0, "data": {"version": 11}}),
    ])

    def fake_request_json(method, path, **_kwargs):
        calls.append((method, path))
        if path.startswith("/muliu/api/task/detail"):
            return next(detail_responses)
        if path == "/muliu/api/task/argument/save":
            return 200, {"code": 0}
        if path == "/muliu/api/task/execute":
            return 200, {"code": 0}
        if path.startswith("/muliu/api/task/log"):
            return 200, {"code": 0, "data": [{"content": "MULIU_RUN_VERSION: run-0\nAI_OP_END status=success\nEND"}]}
        raise AssertionError("unexpected request: {} {}".format(method, path))

    monkeypatch.setattr(client, "request_json", fake_request_json)
    monkeypatch.setattr(client, "login", lambda: calls.append(("LOGIN", "")))
    monkeypatch.setattr(client, "wait_for_completed_step_log", lambda *_: "AI_OP_END status=success\\nEND")

    assert client.execute_step(MuliuStep("/home/serverGeneralScript/start", ["5000"], "启动 5000 服"))
    assert calls == [
        ("GET", "/muliu/api/task/log?id=89&step=11&num=0"),
        ("GET", "/muliu/api/task/detail?id=89"),
        ("LOGIN", ""),
        ("GET", "/muliu/api/task/detail?id=89"),
        ("POST", "/muliu/api/task/argument/save"),
        ("GET", "/muliu/api/task/detail?id=89"),
        ("POST", "/muliu/api/task/execute"),
    ]


def test_interprets_dispatcher_failure_marker_as_failed_script():
    succeeded, failure_reason = interpret_script_outcome(
        "START\nAI_OP_END status=failed error=脚本执行失败，exit_code=1\nEND"
    )

    assert succeeded is False
    assert failure_reason == "脚本执行失败，exit_code=1"


def test_requires_dispatcher_terminal_marker_before_reporting_success():
    assert interpret_script_outcome("AI_OP_END status=success") == (True, "")
    assert interpret_script_outcome("AI_OP_ERROR: missing -p args file path") == (
        False,
        "missing -p args file path",
    )
    assert interpret_script_outcome("legacy task output") == (
        False,
        "GS-1 分发器未返回 AI_OP_END 终态标记",
    )


def test_uses_the_last_dispatcher_terminal_marker():
    assert interpret_script_outcome(
        "AI_OP_END status=success\nAI_OP_END status=failed error=脚本执行失败，exit_code=1"
    ) == (False, "脚本执行失败，exit_code=1")


def test_completed_step_log_requires_both_dispatcher_and_wrapper_terminal_markers():
    assert is_completed_step_log("Running\nAI_OP_END status=success") is False
    assert is_completed_step_log("Running\nEND") is False
    assert is_completed_step_log("Running\nAI_OP_END status=success\nEND") is True
    assert is_completed_step_log("AI_OP_END status=failed error=exit_code=1\nEND") is True


def test_wait_for_completed_step_log_polls_until_the_task_finishes(monkeypatch):
    client = MuliuHttpClient(make_config())
    logs = iter([
        "MULIU_RUN_VERSION: run-1\nSTART\nRunning",
        "MULIU_RUN_VERSION: run-1\nSTART\nRunning\nAI_OP_END status=success\nEND",
    ])
    sleeps = []
    monkeypatch.setattr(client, "get_latest_step_log", lambda: next(logs))
    monkeypatch.setattr("src.muliu_executor.time.sleep", lambda seconds: sleeps.append(seconds))

    completed_log = client.wait_for_completed_step_log()

    assert completed_log.endswith("AI_OP_END status=success\nEND")
    assert sleeps == [0.001]


def test_step_timeout_after_muliu_acceptance_is_marked_result_unknown(monkeypatch):
    client = MuliuHttpClient(make_config())
    monkeypatch.setattr(client, "get_current_version", lambda: 1)
    monkeypatch.setattr(client, "save_arguments", lambda _version, _step: None)
    monkeypatch.setattr(client, "execute_task", lambda _version: None)
    monkeypatch.setattr(client, "latest_run_marker", lambda: "run-0")
    monkeypatch.setattr(
        client,
        "wait_for_completed_step_log",
        lambda *_: (_ for _ in ()).throw(MuliuResultUnknownError("未取得终态")),
    )

    with pytest.raises(MuliuSubmittedError) as raised:
        client.execute_step(MuliuStep("/home/serverGeneralScript/start", ["5000"], "启动 5000 服"))

    assert raised.value.submitted is True
    assert str(raised.value) == "未取得终态"


def test_execute_task_transport_error_is_submission_unknown(monkeypatch):
    client = MuliuHttpClient(make_config())
    monkeypatch.setattr(
        client,
        "request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MuliuTransportError("连接超时")),
    )

    with pytest.raises(MuliuSubmissionUnknownError, match="远端可能已开始执行"):
        client.execute_task(1)


@pytest.mark.parametrize(
    "response",
    [
        MuliuHttpResponseError(500, "server error"),
        (200, "not-json"),
    ],
)
def test_execute_task_without_a_confirmed_acceptance_is_submission_unknown(monkeypatch, response):
    client = MuliuHttpClient(make_config())
    if isinstance(response, Exception):
        monkeypatch.setattr(
            client,
            "request_json",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(response),
        )
    else:
        monkeypatch.setattr(client, "request_json", lambda *_args, **_kwargs: response)

    with pytest.raises(MuliuSubmissionUnknownError, match="远端可能已开始执行"):
        client.execute_task(1)


def test_execute_task_explicit_rejection_is_not_submission_unknown(monkeypatch):
    client = MuliuHttpClient(make_config())
    monkeypatch.setattr(client, "request_json", lambda *_args, **_kwargs: (200, {"code": 1, "msg": "version conflict"}))

    with pytest.raises(MuliuExecutionError, match="明确拒绝执行"):
        client.execute_task(1)


@pytest.mark.asyncio
async def test_executor_returns_result_unknown_after_muliu_accepts_a_step(monkeypatch):
    executor = MuliuExecutor(make_config())

    class FakeClient:
        def __init__(self, _config):
            pass

        def login(self):
            pass

        def execute_step(self, _step):
            raise MuliuSubmittedError(MuliuResultUnknownError("未取得终态"), submitted=True)

    monkeypatch.setattr("src.muliu_executor.MuliuHttpClient", FakeClient)
    plan = MuliuPlan(
        summary="启动 5000 服",
        steps=[MuliuStep(START_SCRIPT_PATH, ["5000"], "启动 5000 服")],
    )

    result = await executor.execute_plan(plan)

    assert result.result_unknown is True
    assert result.succeeded is False
    assert result.failed_step_number is None
    assert result.result_unknown_reason == "第 1/1 步「启动 5000 服」已提交，结果未知：未取得终态"


@pytest.mark.asyncio
async def test_executor_returns_result_unknown_when_execute_request_response_is_lost(monkeypatch):
    executor = MuliuExecutor(make_config())

    class FakeClient:
        def __init__(self, _config):
            pass

        def login(self):
            pass

        def execute_step(self, _step):
            raise MuliuSubmittedError(
                MuliuSubmissionUnknownError("提交响应丢失；远端可能已开始执行"),
                submitted=True,
            )

    monkeypatch.setattr("src.muliu_executor.MuliuHttpClient", FakeClient)
    result = await executor.execute_plan(
        MuliuPlan(
            summary="启动 5000 服",
            steps=[MuliuStep(START_SCRIPT_PATH, ["5000"], "启动 5000 服")],
        )
    )

    assert result.result_unknown is True
    assert "远端可能已开始执行" in result.result_unknown_reason


def test_start_verification_requires_explicit_start_success_signal():
    assert interpret_start_verification_outcome(
        "AI_OP_END status=success\nEND\n开服成功"
    ) == (True, "")
    assert interpret_start_verification_outcome(
        "AI_OP_END status=success\nEND\n未检测到开服成功信息"
    ) == (False, "起服脚本已结束，但核验日志未确认“开服成功”")


@pytest.mark.asyncio
async def test_executor_waits_before_verifying_start_after_start_script_reaches_terminal_success(monkeypatch):
    executor = MuliuExecutor(make_config())
    calls = []
    sleeps = []
    monkeypatch.setattr("src.muliu_executor.time.sleep", lambda seconds: sleeps.append(seconds))

    class FakeClient:
        def __init__(self, _config):
            pass

        def login(self):
            calls.append("login")

        def execute_step(self, step):
            calls.append((step.path, step.args))
            if step.path == START_SCRIPT_PATH:
                return "AI_OP_END status=success\nEND"
            assert step.path == BASIC_INFO_SCRIPT_PATH
            assert step.args == ["5000"]
            return "AI_OP_END status=success\nEND\n开服成功"

    monkeypatch.setattr("src.muliu_executor.MuliuHttpClient", FakeClient)
    plan = MuliuPlan(
        summary="启动 5000 服",
        steps=[MuliuStep(START_SCRIPT_PATH, ["5000"], "启动 5000 服")],
    )

    result = await executor.execute_plan(plan)

    assert result.succeeded is True
    assert calls == [
        "login",
        (START_SCRIPT_PATH, ["5000"]),
        (BASIC_INFO_SCRIPT_PATH, ["5000"]),
    ]
    assert sleeps == [60.0]
    assert [step.description for step in result.step_results] == [
        "启动 5000 服",
        "核验 5000 服是否起服成功",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("submitted", [False, True])
async def test_executor_marks_start_result_unknown_when_verification_is_unavailable(monkeypatch, submitted):
    executor = MuliuExecutor(make_config())
    monkeypatch.setattr("src.muliu_executor.time.sleep", lambda _seconds: None)

    class FakeClient:
        def __init__(self, _config):
            pass

        def login(self):
            pass

        def execute_step(self, step):
            if step.path == START_SCRIPT_PATH:
                return "AI_OP_END status=success\nEND"
            raise MuliuSubmittedError(
                MuliuExecutionError("Muliu 会话恢复后仍返回登录页，无法读取任务状态"),
                submitted=submitted,
            )

    monkeypatch.setattr("src.muliu_executor.MuliuHttpClient", FakeClient)
    result = await executor.execute_plan(
        MuliuPlan(
            summary="启动 5000 服",
            steps=[MuliuStep(START_SCRIPT_PATH, ["5000"], "启动 5000 服")],
        )
    )

    assert result.result_unknown is True
    assert result.failed_step_number is None
    assert "起服脚本已成功结束" in result.result_unknown_reason
    assert "结果尚未核验" in result.result_unknown_reason
    assert len(result.step_results) == 1
    assert result.step_results[0].succeeded is True


@pytest.mark.asyncio
async def test_executor_marks_start_failed_when_post_start_log_lacks_success_signal(monkeypatch):
    executor = MuliuExecutor(make_config())
    monkeypatch.setattr("src.muliu_executor.time.sleep", lambda _seconds: None)

    class FakeClient:
        def __init__(self, _config):
            pass

        def login(self):
            pass

        def execute_step(self, step):
            if step.path == START_SCRIPT_PATH:
                return "AI_OP_END status=success\nEND"
            return "AI_OP_END status=success\nEND\n未检测到开服成功信息"

    monkeypatch.setattr("src.muliu_executor.MuliuHttpClient", FakeClient)
    plan = MuliuPlan(
        summary="启动 5000 服",
        steps=[MuliuStep(START_SCRIPT_PATH, ["5000"], "启动 5000 服")],
    )

    result = await executor.execute_plan(plan)

    assert result.succeeded is False
    assert result.failed_step_number == 1
    assert result.failure_reason == "起服脚本已结束，但核验日志未确认“开服成功”"
    assert len(result.step_results) == 2
    assert result.step_results[-1].succeeded is False


@pytest.mark.asyncio
async def test_executor_stops_after_first_failed_step(monkeypatch):
    executor = MuliuExecutor(make_config())
    calls = []

    class FakeClient:
        def __init__(self, _config):
            pass

        def login(self):
            calls.append("login")

        def execute_step(self, step):
            calls.append(step.description)
            if step.description == "失败步骤":
                raise MuliuSubmittedError(MuliuExecutionError("测试失败"), submitted=False)
            return "AI_OP_END status=success"

    monkeypatch.setattr("src.muliu_executor.MuliuHttpClient", FakeClient)
    plan = MuliuPlan(
        summary="两步测试",
        steps=[
            MuliuStep("/home/serverGeneralScript/a.sh", [], "成功步骤"),
            MuliuStep("/home/serverGeneralScript/b.sh", [], "失败步骤"),
            MuliuStep("/home/serverGeneralScript/c.sh", [], "不应执行"),
        ],
    )

    result = await executor.execute_plan(plan)

    assert calls == ["login", "成功步骤", "失败步骤"]
    assert result.succeeded is False
    assert result.failed_step_number == 2
    assert len(result.step_results) == 1


# ---------------------------------------------------------------- timeouts, retries and step reconciliation


def test_read_timeout_after_connect_is_a_transport_error():
    client = MuliuHttpClient(make_config())

    class TimeoutOpener:
        def open(self, request, timeout=None):
            raise TimeoutError("The read operation timed out")

    client._opener = TimeoutOpener()

    with pytest.raises(MuliuTransportError, match="请求超时或连接中断"):
        client.request_json("GET", "/muliu/api/task/detail?id=89")


def test_pre_submit_requests_retry_transport_errors_but_not_client_errors(monkeypatch):
    from src.muliu_executor import retry_before_submit

    monkeypatch.setattr("src.muliu_executor.time.sleep", lambda _s: None)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise MuliuTransportError("timeout")
        return 42

    assert retry_before_submit(flaky, "读取任务版本", delay_seconds=0) == 42
    assert len(attempts) == 3

    rejected = []
    with pytest.raises(MuliuHttpResponseError):
        retry_before_submit(lambda: rejected.append(1) or (_ for _ in ()).throw(MuliuHttpResponseError(403, "no")), "x", delay_seconds=0)
    assert len(rejected) == 1


def _client_with(monkeypatch, *, markers, execute_error=None, completed_log="MULIU_RUN_VERSION: run-1\nAI_OP_END status=success\nEND"):
    client = MuliuHttpClient(make_config())
    marker_iter = iter(markers)
    monkeypatch.setattr("src.muliu_executor.time.sleep", lambda _s: None)
    monkeypatch.setattr(client, "latest_run_marker", lambda: next(marker_iter))
    monkeypatch.setattr(client, "get_current_version", lambda: 1)
    monkeypatch.setattr(client, "save_arguments", lambda _v, _s: None)

    def execute(_version):
        if execute_error:
            raise execute_error

    monkeypatch.setattr(client, "execute_task", execute)
    monkeypatch.setattr(client, "wait_for_completed_step_log", lambda previous_run=None: completed_log)
    return client


STEP = MuliuStep("/home/serverGeneralScript/modify_game_config.sh", ["5001", "7", "14"], "设置赛季 7 剧本 14")


def test_lost_execute_response_without_new_run_is_not_executed(monkeypatch):
    client = _client_with(monkeypatch, markers=["run-0"] * 10, execute_error=MuliuSubmissionUnknownError("lost"))

    with pytest.raises(MuliuSubmittedError, match="判定本步未执行") as raised:
        client.execute_step(STEP)

    assert raised.value.submitted is False


def test_lost_execute_response_with_new_run_continues_to_the_result(monkeypatch):
    client = _client_with(monkeypatch, markers=["run-0", "run-0", "run-1"], execute_error=MuliuSubmissionUnknownError("lost"))

    assert client.execute_step(STEP).endswith("END")


def test_lost_execute_response_with_unreadable_log_stays_unknown(monkeypatch):
    client = _client_with(monkeypatch, markers=["run-0", None], execute_error=MuliuSubmissionUnknownError("lost"))

    with pytest.raises(MuliuSubmittedError) as raised:
        client.execute_step(STEP)

    assert raised.value.submitted is True


def test_log_poll_ignores_previous_run_and_retries_transient_errors(monkeypatch):
    client = MuliuHttpClient(make_config())
    responses = iter([
        "MULIU_RUN_VERSION: run-0\nAI_OP_END status=success\nEND",  # previous step's finished log
        MuliuTransportError("The read operation timed out"),
        MuliuHttpResponseError(502, "bad gateway"),
        "MULIU_RUN_VERSION: run-1\nRunning",
        "MULIU_RUN_VERSION: run-1\nAI_OP_END status=success\nEND",
    ])

    def next_log():
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(client, "get_latest_step_log", next_log)
    monkeypatch.setattr("src.muliu_executor.time.sleep", lambda _s: None)

    assert client.wait_for_completed_step_log("run-0").startswith("MULIU_RUN_VERSION: run-1")


@pytest.mark.asyncio
async def test_plan_keeps_completed_steps_and_marks_unsubmitted_step_resumable(monkeypatch):
    executor = MuliuExecutor(make_config())

    class FakeClient:
        def __init__(self, _config):
            self.calls = 0

        def login(self):
            pass

        def execute_step(self, step):
            self.calls += 1
            if self.calls == 1:
                return "AI_OP_END status=success\nEND"
            raise MuliuSubmittedError(MuliuExecutionError("提交请求未得到 Muliu 响应，判定本步未执行"), submitted=False)

    monkeypatch.setattr("src.muliu_executor.MuliuHttpClient", FakeClient)
    plan = MuliuPlan(summary="5001 清档改配置启服", steps=[
        MuliuStep("/home/serverGeneralScript/clear", ["5001"], "清档 5001"),
        STEP,
        MuliuStep(START_SCRIPT_PATH, ["5001"], "启服 5001"),
    ])

    result = await executor.execute_plan(plan)

    assert result.failed_step_number == 2
    assert result.resumable_from_step == 2
    assert [s.description for s in result.step_results] == ["清档 5001"]
    assert result.failure_reason.startswith("第 2/3 步「设置赛季 7 剧本 14」未执行")


@pytest.mark.asyncio
async def test_plan_unexpected_exception_keeps_completed_steps_as_unknown(monkeypatch):
    executor = MuliuExecutor(make_config())

    class FakeClient:
        def __init__(self, _config):
            self.calls = 0

        def login(self):
            pass

        def execute_step(self, step):
            self.calls += 1
            if self.calls == 1:
                return "AI_OP_END status=success\nEND"
            raise TimeoutError("The read operation timed out")

    monkeypatch.setattr("src.muliu_executor.MuliuHttpClient", FakeClient)
    plan = MuliuPlan(summary="s", steps=[MuliuStep("/home/serverGeneralScript/clear", ["5001"], "清档 5001"), STEP])

    result = await executor.execute_plan(plan)

    assert result.result_unknown is True
    assert result.resumable_from_step is None
    assert [s.description for s in result.step_results] == ["清档 5001"]
    assert "第 2/2 步「设置赛季 7 剧本 14」执行时发生异常" in result.result_unknown_reason


# ---------------------------------------------------------------- dropped session (redirect to /muliu/login → 404)

NOT_FOUND_PAGE = (
    '<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML 2.0//EN"><html><head><title>404 Not Found</title></head>'
    "<body><h1>404 Not Found</h1><td>http://za-all-gm-nslghk.newslg.com/muliu/login</td>"
    "<td>nslg-ali-hk-test-all-gs-08</td></body></html>"
)


def _http_error(url, code=404, body=NOT_FOUND_PAGE):
    import io
    from email.message import Message
    from urllib.error import HTTPError

    headers = Message()
    headers["Content-Type"] = "text/html; charset=utf-8"
    return HTTPError(url, code, "Not Found", headers, io.BytesIO(body.encode("utf-8")))


class _ScriptedOpener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.paths = []

    def open(self, request, timeout=None):
        import io

        self.paths.append(request.full_url.replace("https://muliu.example.com", ""))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome

        class Response(io.BytesIO):
            status = 200
            headers = type("H", (), {"get_content_charset": staticmethod(lambda: "utf-8")})()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Response(json.dumps(outcome).encode("utf-8"))


def test_redirect_to_login_is_recognized_and_read_recovers_by_logging_in(monkeypatch):
    client = MuliuHttpClient(make_config())
    client._opener = _ScriptedOpener([
        _http_error("http://za-all-gm-nslghk.newslg.com/muliu/login"),
        {"code": 0, "data": {"version": 7}},
    ])
    logins = []
    monkeypatch.setattr(client, "login", lambda: logins.append(1))

    assert client.get_current_version() == 7
    assert logins == [1]


def test_save_arguments_relogs_once_when_session_dropped(monkeypatch):
    client = MuliuHttpClient(make_config())
    client._csrf_token = "csrf"
    client._opener = _ScriptedOpener([
        _http_error("http://za-all-gm-nslghk.newslg.com/muliu/login"),
        {"code": 0},
    ])
    logins = []
    monkeypatch.setattr(client, "login", lambda: logins.append(1))

    client.save_arguments(1, MuliuStep("/home/serverGeneralScript/start", ["5001"], "启服 5001"))

    assert logins == [1]
    assert client._opener.paths == ["/muliu/api/task/argument/save", "/muliu/api/task/argument/save"]


def test_execute_with_dropped_session_is_reconciled_not_resubmitted(monkeypatch):
    client = MuliuHttpClient(make_config())
    client._csrf_token = "csrf"
    client._opener = _ScriptedOpener([_http_error("http://za-all-gm-nslghk.newslg.com/muliu/login")])

    with pytest.raises(MuliuSubmissionUnknownError):
        client.execute_task(1)
    assert len(client._opener.paths) == 1  # never retried blindly


def test_http_error_message_drops_html_and_internal_hosts():
    from src.operation_message import mask_sensitive_operation_data as _mask

    error = MuliuHttpResponseError(404, NOT_FOUND_PAGE)

    assert str(error) == "Muliu HTTP 404：404 Not Found"
    assert error.body == NOT_FOUND_PAGE
    masked = _mask("见 http://za-all-gm-nslghk.newslg.com/muliu/login 与 nslg-ali-hk-test-all-gs-08")
    assert "newslg.com" not in masked and "nslg-ali" not in masked
    assert "[内部域名]" in masked and "[内部主机]" in masked
