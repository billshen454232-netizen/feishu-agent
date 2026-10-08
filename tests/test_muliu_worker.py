import asyncio
import json

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
    RuntimeConfig,
    ServerConfig,
)
from src.muliu_executor import MuliuExecutionResult, MuliuStepResult
from src.muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
from src.muliu_worker import ClaimedWorkerJob, GatewayWorkerClient, MuliuWorker
from src.worker_protocol import (
    WorkerProtocolError,
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
        muliu=MuliuConfig(script_root="/home/serverGeneralScript"),
    )


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


class AcceptAllFirewall:
    def __init__(self):
        self.plans = []

    def validate(self, plan):
        self.plans.append(plan)


class RejectingFirewall:
    def validate(self, _plan):
        raise ValueError("调用合同不允许此参数")


class RecordingExecutor:
    def __init__(self, result):
        self.result = result
        self.plans = []

    async def execute_plan(self, plan):
        self.plans.append(plan)
        return self.result


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
async def test_worker_revalidates_claimed_plan_before_executing():
    config = make_config()
    plan = make_plan()
    gateway = RecordingGatewayClient()
    executor = RecordingExecutor(success_result(plan))
    firewall = AcceptAllFirewall()
    worker = MuliuWorker(config, firewall, executor, gateway)

    await worker._execute_claimed_job(
        ClaimedWorkerJob("om_request", json.dumps({
            "kind": "operation",
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }))
    )

    assert len(firewall.plans) == 1
    assert executor.plans == [plan]
    assert gateway.results == [("om_request", success_result(plan))]


@pytest.mark.asyncio
async def test_worker_reports_local_validation_failure_without_executing():
    config = make_config()
    plan = make_plan()
    gateway = RecordingGatewayClient()
    executor = RecordingExecutor(success_result(plan))
    worker = MuliuWorker(config, RejectingFirewall(), executor, gateway)

    await worker._execute_claimed_job(
        ClaimedWorkerJob("om_request", json.dumps({
            "kind": "operation",
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }))
    )

    assert executor.plans == []
    assert len(gateway.results) == 1
    _, result = gateway.results[0]
    assert result.summary == plan.summary
    assert result.failed_step_number == 1
    assert "本地重新校验失败" in result.failure_reason


@pytest.mark.asyncio
async def test_worker_does_not_claim_concurrently_while_execution_runs():
    config = make_config()
    started = asyncio.Event()
    release = asyncio.Event()
    plan = make_plan()

    class BlockingExecutor:
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
                return ClaimedWorkerJob("om_request", json.dumps({
                    "kind": "operation",
                    "summary": plan.summary,
                    "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
                }))
            return None

    gateway = ClaimingGatewayClient()
    worker = MuliuWorker(config, AcceptAllFirewall(), BlockingExecutor(), gateway)
    task = asyncio.create_task(worker.run_forever())
    await started.wait()
    await asyncio.sleep(0.03)

    assert gateway.claims == 1

    release.set()
    await asyncio.sleep(0)
    worker.stop()
    await asyncio.wait_for(task, timeout=1)
    assert gateway.closed is True


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
