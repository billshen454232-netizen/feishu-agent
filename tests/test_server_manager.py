import dataclasses
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from src.admin_app import create_admin_app
from src.config import MuliuConfig, ServerManagerConfig
from src.execution_report import ExecutionReportStore
from src.execution_routes import ExecutionRouteStore, RouteError
from src.muliu_executor import MuliuExecutor, MuliuSubmittedError
from src.muliu_plan import MuliuPlan, MuliuStep
from src.muliu_script_catalog import MuliuScriptCatalog
from src.muliu_worker import ClaimedWorkerJob, MuliuWorker
from src.server_manager import (
    ApiTask,
    ServerManagerClient,
    ServerManagerStepRunner,
    submission_id,
    translate,
)
from src.worker_protocol import validate_execution_result_matches_plan
from tests.test_muliu_worker import make_config

CATALOG = Path(__file__).parents[1] / "config" / "muliu_script_catalog.md"
SCRIPT = "/home/serverGeneralScript/"
HEADERS = {"X-Admin-Request": "1"}


def _step(name, *args):
    return MuliuStep(path=SCRIPT + name, args=list(args), description="d")


@pytest.mark.parametrize(
    ("contract", "step", "expected"),
    [
        ("start-game-only", _step("start", "2006", "noz"), [("start", ("game_2006",))]),
        ("start-server", _step("start", "2006"), [("start", ("zone_200601",)), ("start", ("game_2006",))]),
        ("shutdown-normal", _step("shutdown", "5010"), [("stop", ("game_5010",)), ("stop", ("zone_501001",))]),
        ("shutdown-mode", _step("shutdown", "5010", "normal"), [("stop", ("game_5010",)), ("stop", ("zone_501001",))]),
        ("shutdown-mode", _step("shutdown", "5010", "kill"), [("kill", ("game_5010", "zone_501001"))]),
        ("clear-server", _step("clear", "2006"), [("kill", ("game_2006", "zone_200601")), ("clear", ("game_2006", "zone_200601"))]),
    ],
)
def test_translation_table(contract, step, expected):
    assert [(task.task_type, task.targets) for task in translate(contract, step)] == expected


@pytest.mark.parametrize(
    ("contract", "step"),
    [
        ("shutdown-mode", _step("shutdown", "5010", "autokill")),
        ("shutdown-mode", _step("shutdown", "5010", "gvgzone")),
        ("clear-server", _step("clear", "4001")),
        ("start-server", _step("start", "4001")),
        ("clear-server-opening-time", _step("clear", "2006", "by")),
        ("hotfix-threshold-apply", _step("auto_hotfix_tool.py", "-s", "5010")),
    ],
)
def test_untranslatable_steps_stay_on_muliu(contract, step):
    assert translate(contract, step) is None


def test_submission_id_is_stable_and_valid():
    value = submission_id("om_" + "x" * 200, 3, 2)
    assert value == submission_id("om_" + "x" * 200, 3, 2) and len(value) <= 96
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) and value.endswith("-s3-t2")


class FakeApi:
    """Programmable Server Manager: records requests, scripted responses."""

    def __init__(self, *, post="ok", statuses=("queued", "succeeded"), registered=True, final="succeeded", config_error=False):
        self.post, self.statuses, self.registered, self.final = post, list(statuses), registered, final
        self.config_error = config_error
        self.requests = []
        self.queries = []

    def handler(self, request):
        self.requests.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        self.queries.append(str(request.url.query, "ascii") if isinstance(request.url.query, bytes) else str(request.url.query))
        path = request.url.path
        if request.method == "POST" and path.endswith("/tasks"):
            if self.post == "timeout":
                raise httpx.ReadTimeout("slow")
            if self.post == "400":
                return httpx.Response(400, json={"detail": "bad targets"})
            return httpx.Response(200, json={"status": "accepted"})
        if "/task-submissions/" in path:
            if not self.registered:
                return httpx.Response(404, json={"status": "error", "code": "submission_not_found"})
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"submission_id": path.rsplit("/", 1)[1], "run_id": "run_1", "status": status})
        if path.endswith("/run_1/result"):
            return httpx.Response(200, json={
                "status": self.final, "summary": "start game_2006: 3/3 items succeeded, server_state=ready",
                "server_states": {"game_2006": "ready"},
                "items": [{"game_id": "game_2006", "machine_id": "gs-5", "status": self.final}],
            })
        if path.endswith("/servers"):
            return httpx.Response(200, json={"servers": [
                {"id": "game_2006", "release_version": {"value": "ob_release_261103"}}, {"id": "zone_200601"}]})
        if path.endswith("/server-config"):
            if self.config_error:
                return httpx.Response(500, json={"detail": "boom"})
            return httpx.Response(200, json={"items": [{"primary": True, "scenario": 3001, "config": {
                "scenario": 3001, "season": 1, "is_publish": False, "nslg_mode": "qa_gs",
                "open_server_time": "2026-01-01/10:00:00", "debug_server_start_time": 1794016800,
                "nslg_game": {"password": "must-not-leak"}}}]})
        return httpx.Response(500)


def _client(fake, timeout=60.0):
    ticks = iter(range(10_000))
    return ServerManagerClient(
        "https://sm.example.com", "qa_test", http_client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
        poll_interval_seconds=0, task_timeout_seconds=timeout, sleep=lambda _s: None, clock=lambda: float(next(ticks)),
    )


def test_client_submits_with_submission_id_and_polls_to_terminal():
    fake = FakeApi(statuses=("queued", "running", "succeeded"))
    outcome = _client(fake).run(ApiTask("start", ("game_2006",)), "fb-abc-s1-t1", "飞书机器人 第1步：起服")

    assert outcome.succeeded and outcome.run_id == "run_1" and outcome.server_states == {"game_2006": "ready"}
    method, path, body = fake.requests[0]
    assert (method, path) == ("POST", "/server_manager/api/tasks")
    assert body == {"env": "qa_test", "task_type": "start", "targets": ["game_2006"], "params": {"fast": True},
                    "submission_id": "fb-abc-s1-t1", "reason": "飞书机器人 第1步：起服"}


def test_empty_env_is_not_sent():
    fake = FakeApi()
    client = ServerManagerClient(
        "https://sm.example.com", "", http_client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
        poll_interval_seconds=0, sleep=lambda _s: None,
    )
    client.run(ApiTask("start", ("game_2006",)), "fb-5", "r")
    client.server_ids()

    assert "env" not in fake.requests[0][2]
    assert all("env=" not in query for query in fake.queries)


def test_ambiguous_submit_resolved_by_submission_record():
    with pytest.raises(MuliuSubmittedError) as not_sent:
        _client(FakeApi(post="timeout", registered=False)).run(ApiTask("stop", ("game_2006",)), "fb-1", "r")
    assert not_sent.value.submitted is False and "确认未执行" in str(not_sent.value)

    outcome = _client(FakeApi(post="timeout", registered=True)).run(ApiTask("stop", ("game_2006",)), "fb-2", "r")
    assert outcome.succeeded


def test_rejected_submit_is_not_executed_and_slow_task_is_unknown():
    with pytest.raises(MuliuSubmittedError) as rejected:
        _client(FakeApi(post="400")).run(ApiTask("clear", ("game_2006",)), "fb-3", "r")
    assert rejected.value.submitted is False and "bad targets" in str(rejected.value)

    with pytest.raises(MuliuSubmittedError) as slow:
        _client(FakeApi(statuses=("running",)), timeout=3).run(ApiTask("clear", ("game_2006",)), "fb-4", "r")
    assert slow.value.submitted is True and "run_1" in str(slow.value)


def test_step_runner_logs_in_muliu_shape():
    step = _step("clear", "2006")
    log = ServerManagerStepRunner(_client(FakeApi()), "om_1").execute_step(step, 2, translate("clear-server", step))

    assert log.startswith("[API 路线]") and log.splitlines()[-1] == "AI_OP_END status=success"
    assert "kill game_2006,zone_200601" in log and "clear game_2006,zone_200601" in log and "run_id=run_1" in log

    failed = ServerManagerStepRunner(_client(FakeApi(final="failed", statuses=("failed",))), "om_1").execute_step(
        step, 1, translate("clear-server", step))
    assert failed.splitlines()[-1].startswith("AI_OP_END status=failed error=API 任务 kill 状态 failed")


def test_step_runner_never_reports_not_executed_after_a_sub_task_ran():
    class SecondFails:
        calls = 0

        def run(self, task, submission, reason):
            self.calls += 1
            if self.calls == 2:
                raise MuliuSubmittedError(RuntimeError("no record"), submitted=False)
            return _client(FakeApi()).run(task, submission, reason)

    step = _step("clear", "2006")
    with pytest.raises(MuliuSubmittedError) as exc:
        ServerManagerStepRunner(SecondFails(), "om_1").execute_step(step, 1, translate("clear-server", step))
    assert exc.value.submitted is True and "前面的提交已执行" in str(exc.value)


def test_executor_runs_api_steps_without_logging_into_muliu():
    # Any Muliu request would fail: the base URL is unreachable and retries would raise.
    config = MuliuConfig(enabled=True, base_url="http://127.0.0.1:9", username="u", password="p", log_wait_seconds=0)
    plan = MuliuPlan(summary="起服", steps=[_step("start", "2006")])
    runner = ServerManagerStepRunner(_client(FakeApi()), "om_1")

    result = MuliuExecutor(config)._execute_plan_blocking(plan, [translate("start-server", plan.steps[0])], runner)

    assert result.failed_step_number is None and not result.result_unknown_reason
    assert [step.succeeded for step in result.step_results] == [True, True]
    assert "wait_until=ready" in result.step_results[0].log
    verification = result.step_results[1]
    assert verification.path.endswith("/basic_info.sh") and verification.args == ["2006"]
    assert "开服成功" in verification.log
    validate_execution_result_matches_plan(result, plan)  # the Gateway accepts it unchanged


def test_route_store_defaults_switches_and_step_routes(tmp_path):
    store = ExecutionRouteStore(tmp_path / "db.sqlite3")
    store.initialize()
    contracts = MuliuScriptCatalog(CATALOG).read_call_contracts()
    plan = MuliuPlan(summary="s", steps=[_step("start", "2006"), _step("start", "4001"), _step("clear", "2006", "by")])

    assert store.step_routes(plan, "cn", contracts) == ["muliu"] * 3
    store.set_backend("start-server", "cn", "api", operator="owner")
    assert store.step_routes(plan, "cn", contracts) == ["api", "muliu", "muliu"]
    assert store.step_routes(plan, "overseas", contracts) == ["muliu"] * 3
    with pytest.raises(RouteError):
        store.set_backend("hotfix-threshold-apply", "cn", "api", operator="owner")
    with pytest.raises(RouteError):
        store.set_backend("start-server", "mars", "api", operator="owner")

    overview = store.overview(contracts)
    assert [item["contract"] for item in overview[:6]] == [
        "basic-server-info", "start-game-only", "start-server", "shutdown-normal", "shutdown-mode", "clear-server"]
    assert overview[2]["regions"]["cn"]["backend"] == "api" and not overview[6]["switchable"]
    assert store.list_events()[0]["new_backend"] == "api"


def test_routes_admin_page(tmp_path):
    reports = ExecutionReportStore(tmp_path / "db.sqlite3")
    reports.initialize()
    store = ExecutionRouteStore(tmp_path / "db.sqlite3")
    store.initialize()
    client = TestClient(
        create_admin_app(reports, route_store=store, route_contracts=MuliuScriptCatalog(CATALOG).read_call_contracts()),
        base_url="http://127.0.0.1:8901",
    )

    assert "/api/routes" in client.get("/routes").text
    assert client.put("/api/routes/clear-server", json={"region": "cn", "backend": "api"}).status_code == 403
    changed = client.put("/api/routes/clear-server", headers=HEADERS, json={"region": "cn", "backend": "api"}).json()
    assert changed["backend"] == "api" and changed["updated_by"] == "ssh-tunnel"
    assert client.put("/api/routes/cc-patch", headers=HEADERS, json={"region": "cn", "backend": "api"}).status_code == 400
    item = next(i for i in client.get("/api/routes").json()["items"] if i["contract"] == "clear-server")
    assert item["regions"] == {"cn": {"backend": "api", "updated_at": changed["updated_at"], "updated_by": "ssh-tunnel"},
                               "overseas": {"backend": "muliu", "updated_at": 0, "updated_by": ""}}
    assert client.get("/api/routes/events").json()["events"][0]["contract"] == "clear-server"


def _worker(tmp_path, enabled=True):
    config = dataclasses.replace(
        make_config(),
        server_manager=ServerManagerConfig(enabled=enabled, regions={"cn": "https://sm.example.com"}),
    )
    worker = MuliuWorker(config, firewall=None, gateway_client=None, route_resolver=None,
                         call_contracts=MuliuScriptCatalog(CATALOG).read_call_contracts())
    worker._sm_clients["cn"] = _client(FakeApi())
    return worker


@pytest.mark.asyncio
async def test_worker_rechecks_api_routes(tmp_path):
    plan = MuliuPlan(summary="s", steps=[_step("start", "2006"), _step("start", "2007"), _step("clear", "2006", "by")])
    job = ClaimedWorkerJob("om_1", "{}", "cn", ("api", "api", "muliu"))

    api_steps, runner = await _worker(tmp_path)._api_route(plan, job, "cn")
    assert [bool(item) for item in api_steps] == [True, False, False]  # 2007 is not in the API inventory
    assert runner is not None

    assert await _worker(tmp_path, enabled=False)._api_route(plan, job, "cn") == (None, None)
    assert await _worker(tmp_path)._api_route(plan, job, "overseas") == (None, None)  # no URL for this region
    assert await _worker(tmp_path)._api_route(plan, ClaimedWorkerJob("om_1", "{}", "cn", ("api",)), "cn") == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(("routes", "ok"), [(["api", "muliu"], True), (None, True), (["rocket"], False)])
async def test_worker_client_parses_step_routes(routes, ok):
    from src.muliu_worker import GatewayWorkerClient, GatewayWorkerClientError

    body = {"message_id": "om_1", "plan_json": "{}", "route_region": "cn"}
    if routes is not None:
        body["step_routes"] = routes
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    client = GatewayWorkerClient(make_config(), http_client=httpx.AsyncClient(transport=transport))
    if ok:
        job = await client.claim()
        assert job.step_routes == tuple(routes or ())
    else:
        with pytest.raises(GatewayWorkerClientError):
            await client.claim()


def test_server_info_route_reads_config_version_and_status():
    step = _step("basic_info.sh", "2006")
    tasks = translate("basic-server-info", step)
    assert [(task.task_type, task.targets) for task in tasks] == [("info", ("game_2006",))]

    fake = FakeApi()
    log = ServerManagerStepRunner(_client(fake), "om_9").execute_step(step, 1, tasks)

    assert log.splitlines()[0] == "[API 路线] Server Manager 只读查询"
    assert "当前代码版本:\nob_release_261103" in log
    assert "1794016800 -> 2026-11-" in log and "剧本 3001，赛季 1，对外 False，启动模式 qa_gs" in log
    assert "服务器已就绪（ready）" in log and log.splitlines()[-1] == "AI_OP_END status=success"
    assert "must-not-leak" not in log and "诊断" not in log  # ready: no troubleshooting hint
    status_post = next(body for method, path, body in fake.requests if method == "POST")
    assert status_post["task_type"] == "status" and status_post["targets"] == ["game_2006", "zone_200601"]

    failed = ServerManagerStepRunner(_client(FakeApi(config_error=True)), "om_9").execute_step(step, 1, tasks)
    assert failed.splitlines()[-1].startswith("AI_OP_END status=failed error=API 查询失败")
