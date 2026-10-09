import asyncio
import json
from pathlib import Path

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
from src.conversation_log import ConversationLog
from src.conversation import ConversationStore
from src.knowledge_base import KnowledgeSearchResult, KnowledgeSource
from src.message_jobs import MessageJobStore
from src.message_worker import MessageJobProcessor
from src.muliu_executor import (
    MuliuExecutionError,
    MuliuExecutionResult,
    MuliuStepResult,
    MuliuSubmissionUnknownError,
    MuliuSubmittedError,
)
from src.muliu_firewall import MuliuFirewall
from src.muliu_intent import MuliuIntentRouter
from src.muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
from src.muliu_planner import MuliuPlanGeneration, MuliuPlanGenerationError
from src.muliu_script_catalog import parse_call_contracts


class FakePlanner:
    def __init__(self, plan):
        self.plan = plan
        self.requests = []

    async def create_plan(self, text):
        self.requests.append(text)
        return self.plan


class FakeTracePlanner(FakePlanner):
    async def create_plan_with_trace(self, text):
        self.requests.append(text)
        return MuliuPlanGeneration(
            plan=self.plan,
            raw_responses=(
                json.dumps(
                    {"kind": self.plan.kind.value, "summary": "测试", "steps": []},
                    ensure_ascii=False,
                ),
            ),
            validation_errors=(None,),
            response_stages=("initial",),
        )


class FakeCatalog:
    def read_capability_context(self):
        return "当前机器人可执行能力注册表（唯一权威）：测试合同"


class TraceFailingPlanner:
    async def create_plan_with_trace(self, _text):
        raise MuliuPlanGenerationError(
            "AI 返回的请求决策格式不合规，已自动纠正 1 次仍失败：steps 必须是数组",
            raw_responses=(
                '{"kind":"operation","summary":"检查","steps":"bad"}',
                '{"kind":"operation","summary":"检查","steps":"still-bad"}',
            ),
            validation_errors=("steps 必须是数组", "steps 必须是数组"),
            response_stages=("initial", "initial_repair"),
            repair_attempts=1,
        )


class FakeFeishuClient:
    def __init__(self):
        self.replies = []
        self.cards = []
        self.card_updates = []

    async def reply_text(self, message_id, text):
        self.replies.append((message_id, text))
        return "om_reply_{}".format(len(self.replies))

    async def reply_card(self, message_id, card):
        self.cards.append((message_id, card))
        return "om_card_{}".format(len(self.cards))

    async def update_card(self, message_id, card):
        self.card_updates.append((message_id, card))


class FailingExecutionResultDeliveryClient(FakeFeishuClient):
    async def reply_text(self, message_id, text):
        raise OSError("Feishu unavailable")


class FakeExecutor:
    def __init__(self):
        self.plans = []

    async def execute_plan(self, plan):
        self.plans.append(plan)
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
                    log="server info",
                )
            ],
        )


class FakeAIClient:
    def __init__(self):
        self.calls = []

    async def generate(self, history, prompt):
        self.calls.append((history, prompt))
        return "Patch 列表可使用资料中说明的检查模式。"


class FailingConversationLog:
    def append(self, _entry):
        raise OSError("disk unavailable")


class FakeScriptKnowledgeBase:
    def __init__(self):
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return KnowledgeSearchResult(
            hit=True,
            context="[来源: knowledge/docs/cc_patch.md]\n# cc_patch.py\nPatch 列表检查资料。",
            sources=[
                KnowledgeSource(
                    kind="script_knowledge",
                    path="knowledge/docs/cc_patch.md",
                    title="cc_patch.py",
                    score=8,
                )
            ],
        )


def make_config(*, runtime=RuntimeConfig()):
    return AppConfig(
        server=ServerConfig(),
        feishu=FeishuConfig(app_id="app", app_secret="secret"),
        ai=AIConfig(provider="openai_compatible", base_url="https://api.example.com", api_key="key", model="m"),
        conversation=ConversationConfig(),
        knowledge_base=KnowledgeBaseConfig(enabled=False),
        bot=BotConfig(),
        job_queue=JobQueueConfig(),
        runtime=runtime,
        muliu=MuliuConfig(
            script_root="/home/serverGeneralScript",
            confirmation_prefix="确认执行",
            confirmation_timeout_seconds=600,
        ),
    )


def make_plan():
    return MuliuPlan(
        summary="查询 6001 服信息",
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/basic_info.sh",
                args=["6001"],
                description="查询 6001 服基础信息",
            )
        ],
        kind=MuliuPlanKind.OPERATION,
    )


def make_shutdown_plan():
    return MuliuPlan(
        summary="正常关闭 5000 服",
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/shutdown",
                args=["5000"],
                description="正常关闭 5000 服 game 与 Zone 服务",
            )
        ],
        kind=MuliuPlanKind.OPERATION,
    )


def make_clear_then_config_plan():
    return MuliuPlan(
        summary="清档 5000 服并改为赛季 10、剧本 5",
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/clear",
                args=["5000"],
                description="清档 5000 服",
            ),
            MuliuStep(
                path="/home/serverGeneralScript/modify_game_config.sh",
                args=["5000", "10", "5"],
                description="将 5000 服改为赛季 10、剧本 5",
            ),
        ],
        kind=MuliuPlanKind.OPERATION,
    )


def make_scenario_plan():
    return MuliuPlan(
        summary="查询 6000 服当前剧本",
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/basic_info.sh",
                args=["6000"],
                description="查询 6000 服当前剧本配置",
            )
        ],
        kind=MuliuPlanKind.OPERATION,
    )


def make_knowledge_plan():
    return MuliuPlan(
        summary="这是已登记的 Patch 查询能力说明，不会执行。",
        steps=[],
        kind=MuliuPlanKind.KNOWLEDGE,
    )


def make_clarify_plan():
    return MuliuPlan(
        summary="需要补充合法服务器编号。",
        steps=[],
        kind=MuliuPlanKind.CLARIFY,
    )


def production_firewall(config):
    catalog_path = Path(__file__).parents[1] / "config" / "muliu_script_catalog.md"
    return MuliuFirewall(
        config.muliu,
        call_contracts=parse_call_contracts(catalog_path.read_text(encoding="utf-8")),
    )


@pytest.mark.asyncio
async def test_request_creates_plan_and_waits_for_group_confirmation(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    planner = FakePlanner(make_plan())
    feishu = FakeFeishuClient()
    executor = FakeExecutor()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=planner,
        muliu_firewall=production_firewall(config),
        muliu_executor=executor,
    )

    await processor.process_one("om_request")

    job = store.get("om_request")
    assert job is not None
    assert job.status == "awaiting_confirmation"
    assert executor.plans == []
    assert feishu.cards[0][0] == "om_request"
    assert feishu.cards[0][1]["header"]["title"]["content"] == "待确认的测试服操作"
    assert feishu.cards[0][1]["elements"][-1]["actions"][0]["value"] == {
        "action": "confirm_operation",
        "request_message_id": "om_request",
        "confirmation_token": job.confirmation_token,
    }
    assert json.loads(job.plan_json)["kind"] == "operation"
    assert json.loads(job.plan_json)["steps"][0]["args"] == ["6001"]


@pytest.mark.asyncio
async def test_shutdown_request_waits_for_confirmation_without_executing(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    request = "正常关闭 5000 服"
    store.create_if_new("om_shutdown", "evt_shutdown", "oc_group", "ou_user", request, chat_type="group")
    feishu = FakeFeishuClient()
    executor = FakeExecutor()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=FakePlanner(make_shutdown_plan()),
        muliu_firewall=production_firewall(config),
        muliu_executor=executor,
    )

    await processor.process_one("om_shutdown")

    job = store.get("om_shutdown")
    assert job is not None
    assert job.status == "awaiting_confirmation"
    assert executor.plans == []
    assert json.loads(job.plan_json) == {
        "kind": "operation",
        "summary": "正常关闭 5000 服",
        "steps": [
            {
                "path": "/home/serverGeneralScript/shutdown",
                "args": ["5000"],
                "description": "正常关闭 5000 服 game 与 Zone 服务",
            }
        ],
    }
    assert feishu.cards[0][0] == "om_shutdown"
    assert feishu.cards[0][1]["header"]["title"]["content"] == "待确认的测试服操作"


@pytest.mark.asyncio
async def test_ordered_registered_operations_wait_for_one_confirmation(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    request = "5000 服清档，然后改为赛季 10、剧本 5"
    store.create_if_new("om_combo", "evt_combo", "oc_group", "ou_user", request, chat_type="group")
    plan = make_clear_then_config_plan()
    feishu = FakeFeishuClient()
    executor = FakeExecutor()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=FakePlanner(plan),
        muliu_firewall=production_firewall(config),
        muliu_executor=executor,
    )

    await processor.process_one("om_combo")

    job = store.get("om_combo")
    assert job.status == "awaiting_confirmation"
    assert executor.plans == []
    card_text = str(feishu.cards[0][1])
    assert "1/2 清档 5000 服" in card_text
    assert "2/2 将 5000 服改为赛季 10、剧本 5" in card_text
    assert json.loads(job.plan_json)["steps"] == [
        {"path": "/home/serverGeneralScript/clear", "args": ["5000"], "description": "清档 5000 服"},
        {
            "path": "/home/serverGeneralScript/modify_game_config.sh",
            "args": ["5000", "10", "5"],
            "description": "将 5000 服改为赛季 10、剧本 5",
        },
    ]


@pytest.mark.asyncio
async def test_operation_plan_is_written_to_human_readable_log(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    planner = FakePlanner(make_plan())
    feishu = FakeFeishuClient()
    log_directory = tmp_path / "conversation-logs"
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=planner,
        muliu_firewall=production_firewall(config),
        conversation_log=ConversationLog(log_directory),
    )

    await processor.process_one("om_request")

    log_files = list(log_directory.glob("*.md"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "执行计划生成" in content
    assert "查询 6001 服" in content
    assert "最终状态：计划已通过本地校验与防火墙检查，正在等待原请求人在交互卡片中确认。" in content


@pytest.mark.asyncio
async def test_log_write_failure_does_not_fail_operation_reply(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=FakePlanner(make_plan()),
        muliu_firewall=production_firewall(config),
        conversation_log=FailingConversationLog(),
    )

    await processor.process_one("om_request")

    assert store.get("om_request").status == "awaiting_confirmation"
    assert feishu.cards[0][0] == "om_request"


@pytest.mark.asyncio
async def test_rejected_plan_writes_raw_ai_outputs_and_validation_errors(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    feishu = FakeFeishuClient()
    log_directory = tmp_path / "conversation-logs"
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=TraceFailingPlanner(),
        muliu_firewall=production_firewall(config),
        conversation_log=ConversationLog(log_directory),
    )

    await processor.process_one("om_request")

    assert store.get("om_request").status == "done"
    assert "未执行" in feishu.replies[0][1]
    log_files = list(log_directory.glob("*.md"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "执行计划拒绝" in content
    assert "#### AI 第 1 次输出（初始决策）" in content
    assert "#### AI 第 2 次输出（初始决策格式修复）" in content
    assert content.count("- 本地校验：未通过：steps 必须是数组") == 2
    assert "最终状态：计划已拒绝，未进入 Muliu 执行。" in content


@pytest.mark.asyncio
async def test_registry_knowledge_decision_answers_without_confirmation(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    question = "我要怎么查询某个服务器现在的 Patch 列表？"
    store.create_if_new("om_question", "evt_question", "oc_group", "ou_user", question, chat_type="group")
    planner = FakeTracePlanner(make_knowledge_plan())
    feishu = FakeFeishuClient()
    ai = FakeAIClient()
    script_knowledge = FakeScriptKnowledgeBase()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=ai,
        feishu_client=feishu,
        operation_planner=planner,
        muliu_firewall=production_firewall(config),
        script_knowledge_base=script_knowledge,
        script_catalog=FakeCatalog(),
        intent_router=MuliuIntentRouter(),
    )

    await processor.process_one("om_question")

    job = store.get("om_question")
    assert job is not None
    assert job.status == "done"
    assert planner.requests == [question]
    assert script_knowledge.queries == [question]
    assert len(ai.calls) == 1
    assert "不得生成执行计划" in ai.calls[0][1]
    assert "knowledge/docs/cc_patch.md" in ai.calls[0][1]
    assert "确认执行" not in feishu.replies[0][1]
    assert feishu.replies[0][1].startswith("📚 以下回答基于当前执行注册表和本地脚本资料生成。")


@pytest.mark.asyncio
async def test_registry_knowledge_decision_writes_sources_and_stages_to_log(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    question = "我要怎么查询某个服务器现在的 Patch 列表？"
    store.create_if_new("om_question", "evt_question", "oc_group", "ou_user", question, chat_type="group")
    feishu = FakeFeishuClient()
    log_directory = tmp_path / "conversation-logs"
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=FakeAIClient(),
        feishu_client=feishu,
        operation_planner=FakeTracePlanner(make_knowledge_plan()),
        muliu_firewall=production_firewall(config),
        script_knowledge_base=FakeScriptKnowledgeBase(),
        script_catalog=FakeCatalog(),
        intent_router=MuliuIntentRouter(),
        conversation_log=ConversationLog(log_directory),
    )

    await processor.process_one("om_question")

    log_files = list(log_directory.glob("*.md"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "脚本资料问答" in content
    assert question in content
    assert "knowledge/docs/cc_patch.md" in content
    assert "#### AI 第 1 次输出（初始决策）" in content


@pytest.mark.asyncio
async def test_targeted_request_bypasses_script_knowledge_and_creates_plan(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    request = "查询 2003 服当前 Patch 列表"
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", request, chat_type="group")
    planner = FakePlanner(make_plan())
    feishu = FakeFeishuClient()
    ai = FakeAIClient()
    script_knowledge = FakeScriptKnowledgeBase()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=ai,
        feishu_client=feishu,
        operation_planner=planner,
        muliu_firewall=production_firewall(config),
        script_knowledge_base=script_knowledge,
        intent_router=MuliuIntentRouter(),
    )

    await processor.process_one("om_request")

    job = store.get("om_request")
    assert job is not None
    assert job.status == "awaiting_confirmation"
    assert script_knowledge.queries == []
    assert planner.requests == [request]
    assert ai.calls == []
    assert feishu.cards[0][0] == "om_request"


@pytest.mark.asyncio
async def test_targeted_live_scenario_question_bypasses_knowledge_and_waits_for_confirmation(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    request = "6000服现在是什么剧本？"
    store.create_if_new("om_scenario", "evt_scenario", "oc_group", "ou_user", request, chat_type="group")
    planner = FakePlanner(make_scenario_plan())
    feishu = FakeFeishuClient()
    ai = FakeAIClient()
    script_knowledge = FakeScriptKnowledgeBase()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=ai,
        feishu_client=feishu,
        operation_planner=planner,
        muliu_firewall=production_firewall(config),
        script_knowledge_base=script_knowledge,
        intent_router=MuliuIntentRouter(),
    )

    await processor.process_one("om_scenario")

    job = store.get("om_scenario")
    assert job is not None
    assert job.status == "awaiting_confirmation"
    assert script_knowledge.queries == []
    assert planner.requests == [request]
    assert ai.calls == []
    assert json.loads(job.plan_json) == {
        "kind": "operation",
        "summary": "查询 6000 服当前剧本",
        "steps": [
            {
                "path": "/home/serverGeneralScript/basic_info.sh",
                "args": ["6000"],
                "description": "查询 6000 服当前剧本配置",
            }
        ],
    }
    assert feishu.cards[0][0] == "om_scenario"
    assert "当前剧本" in str(feishu.cards[0][1])


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [make_clarify_plan(), MuliuPlan(
    summary="当前能力未登记。",
    steps=[],
    kind=MuliuPlanKind.REJECT,
)])
async def test_non_operation_decisions_do_not_create_confirmation_tokens(tmp_path, decision):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "执行未知操作", chat_type="group")
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=FakeTracePlanner(decision),
        muliu_firewall=production_firewall(config),
    )

    await processor.process_one("om_request")

    job = store.get("om_request")
    assert job is not None
    assert job.status == "done"
    assert job.confirmation_token == ""
    assert "确认执行" not in feishu.replies[0][1]


@pytest.mark.asyncio
async def test_private_operation_is_rejected_without_confirmation_token(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_private", "evt_private", "oc_p2p", "ou_user", "查询 6001 服", chat_type="p2p")
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=FakeTracePlanner(make_plan()),
        muliu_firewall=production_firewall(config),
    )

    await processor.process_one("om_private")

    job = store.get("om_private")
    assert job is not None
    assert job.status == "done"
    assert job.confirmation_token == ""
    assert "只能在群内发起并确认" in feishu.replies[0][1]


@pytest.mark.asyncio
async def test_unknown_legacy_chat_type_is_rejected_without_confirmation_token(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_legacy", "evt_legacy", "oc_legacy", "ou_user", "查询 6001 服")
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=FakeTracePlanner(make_plan()),
        muliu_firewall=production_firewall(config),
    )

    await processor.process_one("om_legacy")

    job = store.get("om_legacy")
    assert job is not None
    assert job.status == "done"
    assert job.confirmation_token == ""
    assert "无法确认该请求来自可执行的群聊" in feishu.replies[0][1]


@pytest.mark.asyncio
async def test_matching_confirmation_executes_stored_plan(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = make_plan()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({
            "kind": plan.kind.value,
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }),
        confirmation_message_id="om_plan",
        confirmation_token="ABC123",
    )
    feishu = FakeFeishuClient()
    executor = FakeExecutor()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        muliu_executor=executor,
    )

    handled = await processor.process_confirmation("oc_group", "ABC123", "om_confirm")
    await asyncio.sleep(0)
    repeated = await processor.process_confirmation("oc_group", "ABC123", "om_confirm_again")

    assert handled is True
    assert repeated is False
    assert len(executor.plans) == 1
    assert store.get("om_request").status == "done"
    assert feishu.replies[0][0] == "om_confirm"
    assert "执行完成" in feishu.replies[0][1]


@pytest.mark.asyncio
async def test_card_confirmation_claims_stored_plan_and_updates_original_card(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = make_plan()
    store.create_if_new("om_request", "evt", "oc_group", "ou_requester", "查询 6001 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({
            "kind": plan.kind.value,
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }),
        confirmation_message_id="om_card",
        confirmation_token="ABC123",
        confirmation_method="card",
    )
    feishu = FakeFeishuClient()
    executor = FakeExecutor()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        muliu_executor=executor,
    )

    card = await processor.process_card_action(
        action="confirm_operation",
        request_message_id="om_request",
        confirmation_message_id="om_card",
        chat_id="oc_group",
        user_id="ou_requester",
        confirmation_token="ABC123",
    )
    await asyncio.sleep(0)

    assert card["header"]["title"]["content"] == "测试服操作已提交"
    assert len(executor.plans) == 1
    assert store.get("om_request").status == "done"
    assert feishu.card_updates[0][0] == "om_card"
    assert feishu.card_updates[0][1]["header"]["title"]["content"] == "测试服操作已完成"
    assert feishu.replies[0][0] == "om_card"


@pytest.mark.asyncio
async def test_text_confirmation_in_gateway_mode_acknowledges_queued_worker_plan(tmp_path):
    config = make_config(runtime=RuntimeConfig(role="gateway", worker_token="worker-token"))
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = make_plan()
    store.create_if_new("om_request", "evt", "oc_group", "ou_requester", "查询 6001 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({
            "kind": plan.kind.value,
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }),
        confirmation_message_id="om_card",
        confirmation_token="ABC123",
        confirmation_method="text",
    )
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        muliu_executor=None,
    )

    assert await processor.process_confirmation("oc_group", "ABC123", "om_confirm") is True

    assert store.get("om_request").status == "queued_for_worker"
    assert len(feishu.replies) == 1
    assert feishu.replies[0][0] == "om_confirm"
    assert "已排入内网 Muliu Worker 队列" in feishu.replies[0][1]


@pytest.mark.asyncio
async def test_card_confirmation_from_another_member_is_rejected_without_execution(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt", "oc_group", "ou_requester", "查询 6001 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({"kind": "operation", "summary": "查询", "steps": []}),
        confirmation_message_id="om_card",
        confirmation_token="ABC123",
    )
    executor = FakeExecutor()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=FakeFeishuClient(),
        muliu_executor=executor,
    )

    card = await processor.process_card_action(
        action="confirm_operation",
        request_message_id="om_request",
        confirmation_message_id="om_card",
        chat_id="oc_group",
        user_id="ou_other",
        confirmation_token="ABC123",
    )

    assert card is None
    assert executor.plans == []
    assert store.get("om_request").status == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_card_cancellation_prevents_execution_and_returns_status_card(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_request", "evt", "oc_group", "ou_requester", "关闭 5000 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({"kind": "operation", "summary": "关闭 5000 服", "steps": []}),
        confirmation_message_id="om_card",
        confirmation_token="ABC123",
    )
    executor = FakeExecutor()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=FakeFeishuClient(),
        muliu_executor=executor,
    )

    card = await processor.process_card_action(
        action="cancel_operation",
        request_message_id="om_request",
        confirmation_message_id="om_card",
        chat_id="oc_group",
        user_id="ou_requester",
        confirmation_token="ABC123",
    )

    assert card["header"]["title"]["content"] == "测试服操作已取消"
    assert store.get("om_request").status == "cancelled"
    assert executor.plans == []


@pytest.mark.asyncio
async def test_confirmed_long_operation_does_not_block_other_message_processing(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = make_plan()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({
            "kind": plan.kind.value,
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }),
        confirmation_message_id="om_plan",
        confirmation_token="ABC123",
    )
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingExecutor:
        async def execute_plan(self, _plan):
            started.set()
            await release.wait()
            return MuliuExecutionResult(summary="查询 6001 服信息", step_results=[])

    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=FakeFeishuClient(),
        muliu_executor=BlockingExecutor(),
    )

    assert await processor.process_confirmation("oc_group", "ABC123", "om_confirm") is True
    await started.wait()
    assert store.get("om_request").status == "submission_in_progress"

    await processor.process_one("missing-message")

    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_submission_error_before_muliu_acceptance_is_reported_as_not_executed(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = make_plan()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({
            "kind": plan.kind.value,
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }),
        confirmation_message_id="om_plan",
        confirmation_token="ABC123",
    )

    class FailingExecutor:
        async def execute_plan(self, _plan):
            raise MuliuExecutionError("保存执行参数失败")

    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        muliu_executor=FailingExecutor(),
    )

    assert await processor.process_confirmation("oc_group", "ABC123", "om_confirm") is True
    await asyncio.sleep(0)

    assert store.get("om_request").status == "failed"
    assert "未执行" in feishu.replies[0][1]


@pytest.mark.asyncio
async def test_submission_response_loss_is_reported_as_result_unknown(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = make_plan()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({
            "kind": plan.kind.value,
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }),
        confirmation_message_id="om_plan",
        confirmation_token="ABC123",
    )

    class ResponseLostExecutor:
        async def execute_plan(self, _plan):
            raise MuliuSubmittedError(
                MuliuSubmissionUnknownError("提交响应丢失；远端可能已开始执行"),
                submitted=True,
            )

    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        muliu_executor=ResponseLostExecutor(),
    )

    assert await processor.process_confirmation("oc_group", "ABC123", "om_confirm") is True
    await asyncio.sleep(0)

    job = store.get("om_request")
    assert job.status == "submitted_result_unknown"
    assert "已提交，结果尚未核验" in feishu.replies[0][1]
    assert "请勿重复确认或重试" in feishu.replies[0][1]


@pytest.mark.asyncio
async def test_execution_result_delivery_failure_preserves_confirmed_outcome(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = make_plan()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({
            "kind": plan.kind.value,
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }),
        confirmation_message_id="om_plan",
        confirmation_token="ABC123",
    )
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=FailingExecutionResultDeliveryClient(),
        muliu_executor=FakeExecutor(),
    )

    assert await processor.process_confirmation("oc_group", "ABC123", "om_confirm") is True
    await asyncio.sleep(0)

    assert store.get("om_request").status == "done"


@pytest.mark.asyncio
async def test_execution_result_is_written_to_human_readable_log(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = make_plan()
    store.create_if_new("om_request", "evt_request", "oc_group", "ou_user", "查询 6001 服", chat_type="group")
    store.mark_awaiting_confirmation(
        "om_request",
        plan_json=json.dumps({
            "kind": plan.kind.value,
            "summary": plan.summary,
            "steps": [{"path": plan.steps[0].path, "args": plan.steps[0].args, "description": plan.steps[0].description}],
        }),
        confirmation_message_id="om_plan",
        confirmation_token="ABC123",
    )
    log_directory = tmp_path / "conversation-logs"
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=FakeFeishuClient(),
        muliu_executor=FakeExecutor(),
        conversation_log=ConversationLog(log_directory),
    )

    handled = await processor.process_confirmation("oc_group", "ABC123", "om_confirm")
    await asyncio.sleep(0)

    assert handled is True
    log_files = list(log_directory.glob("*.md"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "执行结果" in content
    assert "最终状态：执行结果已回传飞书" in content


@pytest.mark.asyncio
async def test_wrong_confirmation_token_does_not_execute(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    feishu = FakeFeishuClient()
    executor = FakeExecutor()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        muliu_executor=executor,
    )

    handled = await processor.process_confirmation("oc_group", "WRONG", "om_confirm")

    assert handled is False
    assert executor.plans == []
    assert feishu.replies == []


@pytest.mark.asyncio
async def test_batch_environment_version_query_passes_firewall_and_waits_for_confirmation(tmp_path):
    config = make_config()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_batch", "evt_batch", "oc_group", "ou_user", "查看海外所有DEV环境服务器代码版本", chat_type="group")
    batch_plan = MuliuPlan(
        summary="批量查询海外所有DEV环境服务器代码版本",
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/batch_server_query.py",
                args=["--env", "overseas_dev"],
                description="并发查询海外所有DEV环境（港澳台+韩服DEV）服务器代码版本",
            )
        ],
        kind=MuliuPlanKind.OPERATION,
    )
    feishu = FakeFeishuClient()
    executor = FakeExecutor()
    processor = MessageJobProcessor(
        config,
        store,
        ConversationStore(4),
        ai_client=None,
        feishu_client=feishu,
        operation_planner=FakePlanner(batch_plan),
        muliu_firewall=production_firewall(config),
        muliu_executor=executor,
    )

    await processor.process_one("om_batch")

    job = store.get("om_batch")
    assert job is not None
    assert job.status == "awaiting_confirmation"
    assert executor.plans == []
    assert len(feishu.cards) == 1
    assert "batch_server_query.py" in str(feishu.cards[0][1])
    assert "--env overseas_dev" in str(feishu.cards[0][1])

