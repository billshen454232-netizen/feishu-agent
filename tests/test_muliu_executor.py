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
)
from src.muliu_plan import MuliuPlan, MuliuStep


def make_config():
    return MuliuConfig(
        base_url="https://muliu.example.com",
        username="user",
        password="password",
        task_id=89,
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
    monkeypatch.setattr(
        client,
        "wait_for_completed_step_log",
        lambda: (_ for _ in ()).throw(MuliuResultUnknownError("未取得终态")),
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
    assert result.result_unknown_reason == "未取得终态"


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
