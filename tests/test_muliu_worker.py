import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from src.config import (
    AIConfig,
    AppConfig,
    BotConfig,
    ConversationConfig,
    FeishuConfig,
    JobQueueConfig,
    KnowledgeBaseConfig,
    MuliuConfig,
    MuliuRouteConfig,
    RuntimeConfig,
    ServerConfig,
)
from src.muliu_executor import MuliuExecutionResult, MuliuStepResult
from src.muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
from src.muliu_routing import MuliuPlanRouteResolver
from src.muliu_script_catalog import MuliuCallContract, MuliuCallContracts
from src.muliu_worker import ClaimedWorkerJob, GatewayWorkerClient, GatewayWorkerClientError, MuliuWorker
from src.worker_protocol import (
    WorkerProtocolError,
    parse_execution_result,
    serialize_execution_result,
    validate_execution_result_matches_plan,
)


def make_config():
    return AppConfig(
        server=ServerConfig(),
        feishu=FeishuConfig(app_id="", app_secret=""),
        ai=AIConfig(provider="openai_compatible", base_url="", api_key="", model=""),
        conversation=ConversationConfig(),
        knowledge_base=KnowledgeBaseConfig(),
        bot=BotConfig(),
        job_queue=JobQueueConfig(),
        runtime=RuntimeConfig(
            role="worker",
            worker_token="shared-token",
            worker_id="jenkins-worker-1",
            gateway_base_url="https://gateway.example.com",
            poll_interval_seconds=0.01,
            heartbeat_interval_seconds=0.01,
            lease_timeout_seconds=1,
        ),
        muliu=MuliuConfig(
            script_root="/home/serverGeneralScript",
            routes={
                "cn": MuliuRouteConfig(
                    "route-cn",
                    task_id=101,
                    base_url="https://cn.example.com",
                    username="cn",
                    password="secret",
                ),
                "overseas": MuliuRouteConfig(
                    "route-overseas",
                    task_id=89,
                    base_url="https://overseas.example.com",
                    username="overseas",
                    password="secret",
                ),
            },
        ),
    )


def make_route_resolver(tmp_path):
    environment_path = Path(tmp_path) / "server_environments.json"
    environment_path.write_text(
        json.dumps({
            "regions": {
                "cn": {"name": "国服", "environments": {"qa": ["2003"]}},
                "global": {"name": "全球服", "environments": {"dev": ["6001"]}},
            },
            "groups": {},
        }),
        encoding="utf-8",
    )
    contracts = MuliuCallContracts(
        max_plan_steps=10,
        contracts=(
            MuliuCallContract(
                name="查询基础信息",
                path="/home/serverGeneralScript/basic_info.sh",
                args=("{server_id}",),
                variable_patterns={"server_id": re.compile(r"[0-9]{4}")},
                runner="bash",
                risk="read",
            ),
            MuliuCallContract(
                name="按剧本查询范围服务器",
                path="/home/serverGeneralScript/basic_info.sh",
                args=("ssinfo", "{scenario}"),
                variable_patterns={"scenario": re.compile(r"[0-9]{1,8}")},
                runner="bash",
                risk="read",
                route_scopes=frozenset({"cn", "overseas"}),
            ),
        ),
    )
    return MuliuPlanRouteResolver(str(environment_path), contracts)


def make_plan():
    return MuliuPlan(
        summary="查询 6001 服基础信息",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/basic_info.sh",
                args=["6001"],
                description="查询 6001 服基础信息",
            )
        ],
    )


def make_cn_plan():
    return MuliuPlan(
        summary="查询 2003 服基础信息",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/basic_info.sh",
                args=["2003"],
                description="查询 2003 服基础信息",
            )
        ],
    )


def make_scoped_scenario_plan(route_scope):
    return MuliuPlan(
        summary="查询 {}范围 1000 剧本服务器".format(route_scope),
        kind=MuliuPlanKind.OPERATION,
        route_scope=route_scope,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/basic_info.sh",
                args=["ssinfo", "1000"],
                description="查询范围内 1000 剧本服务器",
            )
        ],
    )


def plan_json(plan):
    payload = {
        "kind": plan.kind.value,
        "summary": plan.summary,
        "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
    }
    if plan.route_scope is not None:
        payload["route_scope"] = plan.route_scope
    return json.dumps(payload)


class AcceptAllFirewall:
    def __init__(self):
        self.plans = []

    def validate(self, plan):
        self.plans.append(plan)


class RejectingFirewall:
    def validate(self, _plan):
        raise ValueError("调用合同不允许此参数")


class RecordingExecutor:
    instances = []

    def __init__(self, config):
        self.config = config
        self.plans = []
        self.__class__.instances.append(self)

    async def execute_plan(self, plan):
        self.plans.append(plan)
        return success_result(plan)


class RecordingGatewayClient:
    def __init__(self):
        self.results = []
        self.heartbeats = []
        self.closed = False

    async def submit_result(self, message_id, result):
        self.results.append((message_id, result))
        return True

    async def heartbeat(self, message_id):
        self.heartbeats.append(message_id)
        return True

    async def close(self):
        self.closed = True


def success_result(plan):
    step = plan.steps[0]
    return MuliuExecutionResult(
        summary=plan.summary,
        step_results=[
            MuliuStepResult(
                step_number=1,
                description=step.description,
                path=step.path,
                args=step.args,
                succeeded=True,
                log="AI_OP_END status=success\nEND",
            )
        ],
    )


@pytest.mark.asyncio
async def test_worker_revalidates_claimed_plan_before_executing(tmp_path, monkeypatch):
    config = make_config()
    plan = make_plan()
    gateway = RecordingGatewayClient()
    firewall = AcceptAllFirewall()
    RecordingExecutor.instances = []
    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", RecordingExecutor)
    worker = MuliuWorker(config, firewall, gateway, make_route_resolver(tmp_path))

    await worker._execute_claimed_job(
        ClaimedWorkerJob(
            "om_request",
            json.dumps({
                "kind": "operation",
                "summary": plan.summary,
                "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
            }),
            "overseas",
        )
    )

    assert len(firewall.plans) == 1
    assert len(RecordingExecutor.instances) == 1
    assert RecordingExecutor.instances[0].config.base_url == "https://overseas.example.com"
    assert RecordingExecutor.instances[0].config.task_id == 89
    assert RecordingExecutor.instances[0].plans == [plan]
    assert gateway.results == [("om_request", success_result(plan))]


@pytest.mark.asyncio
async def test_worker_selects_cn_profile_task_id_with_the_cn_muliu_url(tmp_path, monkeypatch):
    config = make_config()
    plan = make_cn_plan()
    gateway = RecordingGatewayClient()
    RecordingExecutor.instances = []
    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", RecordingExecutor)
    worker = MuliuWorker(config, AcceptAllFirewall(), gateway, make_route_resolver(tmp_path))

    await worker._execute_claimed_job(
        ClaimedWorkerJob(
            "om_cn_request",
            json.dumps({
                "kind": "operation",
                "summary": plan.summary,
                "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
            }),
            "cn",
        )
    )

    assert len(RecordingExecutor.instances) == 1
    assert RecordingExecutor.instances[0].config.base_url == "https://cn.example.com"
    assert RecordingExecutor.instances[0].config.task_id == 101
    assert RecordingExecutor.instances[0].plans == [plan]
    assert gateway.results == [("om_cn_request", success_result(plan))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_scope", "base_url", "task_id"),
    [
        ("cn", "https://cn.example.com", 101),
        ("overseas", "https://overseas.example.com", 89),
    ],
)
async def test_worker_selects_range_route_profile_without_a_server_id(
    tmp_path,
    monkeypatch,
    route_scope,
    base_url,
    task_id,
):
    config = make_config()
    plan = make_scoped_scenario_plan(route_scope)
    gateway = RecordingGatewayClient()
    RecordingExecutor.instances = []
    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", RecordingExecutor)
    worker = MuliuWorker(config, AcceptAllFirewall(), gateway, make_route_resolver(tmp_path))

    await worker._execute_claimed_job(
        ClaimedWorkerJob("om_scoped", plan_json(plan), route_scope)
    )

    assert len(RecordingExecutor.instances) == 1
    assert RecordingExecutor.instances[0].config.base_url == base_url
    assert RecordingExecutor.instances[0].config.task_id == task_id
    assert RecordingExecutor.instances[0].plans == [plan]
    assert gateway.results == [("om_scoped", success_result(plan))]


@pytest.mark.asyncio
async def test_worker_reports_local_validation_failure_without_executing(tmp_path, monkeypatch):
    config = make_config()
    plan = make_plan()
    gateway = RecordingGatewayClient()
    RecordingExecutor.instances = []
    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", RecordingExecutor)
    worker = MuliuWorker(config, RejectingFirewall(), gateway, make_route_resolver(tmp_path))

    await worker._execute_claimed_job(
        ClaimedWorkerJob(
            "om_request",
            json.dumps({
                "kind": "operation",
                "summary": plan.summary,
                "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
            }),
            "overseas",
        )
    )

    assert RecordingExecutor.instances == []
    assert len(gateway.results) == 1
    _, result = gateway.results[0]
    assert result.summary == plan.summary
    assert result.failed_step_number == 1
    assert "本地重新校验失败" in result.failure_reason


@pytest.mark.asyncio
async def test_worker_does_not_claim_concurrently_while_execution_runs(tmp_path, monkeypatch):
    config = make_config()
    started = asyncio.Event()
    release = asyncio.Event()
    plan = make_plan()

    class BlockingExecutor:
        def __init__(self, _config):
            pass

        async def execute_plan(self, _plan):
            started.set()
            await release.wait()
            return success_result(plan)

    class ClaimingGatewayClient(RecordingGatewayClient):
        def __init__(self):
            super().__init__()
            self.claims = 0

        async def claim(self):
            self.claims += 1
            if self.claims == 1:
                return ClaimedWorkerJob(
                    "om_request",
                    json.dumps({
                        "kind": "operation",
                        "summary": plan.summary,
                        "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
                    }),
                    "overseas",
                )
            return None

    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", BlockingExecutor)
    gateway = ClaimingGatewayClient()
    worker = MuliuWorker(config, AcceptAllFirewall(), gateway, make_route_resolver(tmp_path))
    task = asyncio.create_task(worker.run_forever())
    await started.wait()
    await asyncio.sleep(0.03)

    assert gateway.claims == 1

    release.set()
    await asyncio.sleep(0)
    worker.stop()
    await asyncio.wait_for(task, timeout=1)
    assert gateway.closed is True


@pytest.mark.asyncio
async def test_worker_rejects_gateway_route_that_differs_from_local_plan_route(tmp_path, monkeypatch):
    config = make_config()
    plan = make_plan()
    gateway = RecordingGatewayClient()
    RecordingExecutor.instances = []
    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", RecordingExecutor)
    worker = MuliuWorker(config, AcceptAllFirewall(), gateway, make_route_resolver(tmp_path))

    await worker._execute_claimed_job(
        ClaimedWorkerJob(
            "om_request",
            json.dumps({
                "kind": "operation",
                "summary": plan.summary,
                "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
            }),
            "cn",
        )
    )

    assert RecordingExecutor.instances == []
    assert len(gateway.results) == 1
    assert "不一致" in gateway.results[0][1].failure_reason


@pytest.mark.asyncio
async def test_worker_rejects_gateway_route_that_differs_from_scoped_plan_route(tmp_path, monkeypatch):
    config = make_config()
    plan = make_scoped_scenario_plan("cn")
    gateway = RecordingGatewayClient()
    RecordingExecutor.instances = []
    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", RecordingExecutor)
    worker = MuliuWorker(config, AcceptAllFirewall(), gateway, make_route_resolver(tmp_path))

    await worker._execute_claimed_job(
        ClaimedWorkerJob("om_scoped", plan_json(plan), "overseas")
    )

    assert RecordingExecutor.instances == []
    assert len(gateway.results) == 1
    assert "不一致" in gateway.results[0][1].failure_reason


def test_gateway_client_accepts_only_the_minimal_route_region_claim_shape():
    config = make_config()

    async def handler(_request):
        return httpx.Response(200, json={
            "message_id": "om_request",
            "plan_json": "{\"kind\":\"operation\",\"summary\":\"查询\",\"steps\":[]}",
            "route_region": "cn",
        })

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = GatewayWorkerClient(config, http_client=http_client)
            claimed = await client.claim()
            await client.close()
            return claimed

    claimed = asyncio.run(exercise())

    assert claimed == ClaimedWorkerJob(
        "om_request",
        '{"kind":"operation","summary":"查询","steps":[]}',
        "cn",
    )


def test_gateway_client_sends_only_authenticated_protocol_headers():
    config = make_config()
    seen = {}

    async def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["worker_id"] = request.headers.get("x-feishu-worker-id")
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)

    async def exercise():
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = GatewayWorkerClient(config, http_client=http_client)
            assert await client.claim() is None
            await client.close()

    asyncio.run(exercise())

    assert seen == {
        "url": "https://gateway.example.com/internal/worker/jobs/claim",
        "authorization": "Bearer shared-token",
        "worker_id": "jenkins-worker-1",
        "body": {},
    }


def test_worker_result_must_match_original_plan_and_start_verification():
    plan = MuliuPlan(
        summary="启动 6001 服",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/start",
                args=["6001"],
                description="启动 6001 服",
            )
        ],
    )
    valid = MuliuExecutionResult(
        summary=plan.summary,
        step_results=[
            MuliuStepResult(1, "启动 6001 服", "/home/serverGeneralScript/start", ["6001"], True, "start done"),
            MuliuStepResult(1, "核验 6001 服是否起服成功", "/home/serverGeneralScript/basic_info.sh", ["6001"], True, "verified"),
        ],
    )
    tampered = MuliuExecutionResult(
        summary=plan.summary,
        step_results=[
            MuliuStepResult(1, "启动 6001 服", "/home/serverGeneralScript/shutdown", ["6001"], True, "bad"),
        ],
    )

    validate_execution_result_matches_plan(valid, plan)
    with pytest.raises(WorkerProtocolError, match="不一致"):
        validate_execution_result_matches_plan(tampered, plan)


def test_serialized_result_round_trip_is_bounded_to_protocol_shape():
    plan = make_plan()
    result = success_result(plan)

    payload = serialize_execution_result(result)

    assert payload["summary"] == plan.summary
    assert payload["step_results"][0]["args"] == ["6001"]


def test_oversized_log_and_reason_are_clipped_so_the_gateway_still_accepts_the_result():
    plan = make_plan()
    step = plan.steps[0]
    huge_log = "x" * 150_000 + "\nAI_OP_END status=success\nEND"
    result = MuliuExecutionResult(
        summary=plan.summary,
        step_results=[MuliuStepResult(1, step.description, step.path, step.args, True, log=huge_log)],
    )
    unknown = MuliuExecutionResult(summary=plan.summary, step_results=[], result_unknown_reason="超时" * 5_000)

    for outcome in (result, unknown):
        parsed = parse_execution_result(serialize_execution_result(outcome), max_steps=10)
        validate_execution_result_matches_plan(parsed, plan)

    clipped = serialize_execution_result(result)["step_results"][0]["log"]
    assert clipped.startswith("[日志过长") and clipped.endswith("AI_OP_END status=success\nEND")
    assert len(clipped) == 100_000
    assert len(serialize_execution_result(unknown)["result_unknown_reason"]) == 4_000


def test_gateway_rejection_reason_is_kept_in_the_worker_error():
    response = httpx.Response(422, json={"detail": "step_results[1].log 必须是长度不超过 100000 的字符串"})
    with pytest.raises(GatewayWorkerClientError, match="422：step_results"):
        GatewayWorkerClient._response_json(response)


def _claimed(plan):
    return ClaimedWorkerJob("om_request", plan_json(plan), "overseas")


@pytest.mark.asyncio
@pytest.mark.parametrize("transient, expected_runs", [(True, 2), (False, 1)])
async def test_worker_retries_read_only_plan_once_on_transient_failure(tmp_path, monkeypatch, transient, expected_runs):
    plan = make_plan()
    runs = []

    class FlakyExecutor:
        def __init__(self, config):
            pass

        async def execute_plan(self, plan):
            runs.append(plan)
            if len(runs) == 1:
                return MuliuExecutionResult(
                    summary=plan.summary, step_results=[], failed_step_number=1,
                    failure_reason="第 1/1 步未执行：Muliu 请求超时", transient_error=transient,
                )
            return success_result(plan)

    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", FlakyExecutor)
    resolver = make_route_resolver(tmp_path)
    gateway = RecordingGatewayClient()
    worker = MuliuWorker(make_config(), AcceptAllFirewall(), gateway, resolver, call_contracts=resolver._call_contracts)

    await worker._execute_claimed_job(_claimed(plan))

    assert len(runs) == expected_runs
    assert gateway.results[0][1].succeeded is transient


@pytest.mark.asyncio
async def test_worker_never_retries_write_plans(tmp_path, monkeypatch):
    from src.muliu_script_catalog import MuliuCallContract

    plan = make_plan()
    runs = []

    class FailingExecutor:
        def __init__(self, config):
            pass

        async def execute_plan(self, plan):
            runs.append(plan)
            return MuliuExecutionResult(summary=plan.summary, step_results=[], result_unknown_reason="未知", transient_error=True)

    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", FailingExecutor)
    resolver = make_route_resolver(tmp_path)
    write_contracts = MuliuCallContracts(
        max_plan_steps=1,
        contracts=tuple(
            MuliuCallContract(c.name, c.path, c.args, c.variable_patterns, c.runner, "write", c.route_scopes)
            for c in resolver._call_contracts.contracts
        ),
    )
    worker = MuliuWorker(make_config(), AcceptAllFirewall(), RecordingGatewayClient(), resolver, call_contracts=write_contracts)

    await worker._execute_claimed_job(_claimed(plan))

    assert len(runs) == 1


def test_resumable_step_round_trips_and_is_validated():
    from src.worker_protocol import WorkerProtocolError, parse_execution_result

    plan = MuliuPlan(summary="s", kind=MuliuPlanKind.OPERATION, steps=[
        MuliuStep("/home/serverGeneralScript/basic_info.sh", ["6001"], "a"),
        MuliuStep("/home/serverGeneralScript/basic_info.sh", ["6002"], "b"),
    ])
    done = success_result(make_plan()).step_results
    result = MuliuExecutionResult(summary="s", step_results=done, failed_step_number=2, failure_reason="第 2/2 步未执行", resumable_from_step=2)

    payload = serialize_execution_result(result)
    parsed = parse_execution_result(payload, max_steps=10)

    assert payload["resumable_from_step"] == 2
    assert parsed.resumable_from_step == 2
    assert "resumable_from_step" not in serialize_execution_result(success_result(make_plan()))
    with pytest.raises(WorkerProtocolError, match="resumable_from_step"):
        parse_execution_result(dict(payload, resumable_from_step=1), max_steps=10)
