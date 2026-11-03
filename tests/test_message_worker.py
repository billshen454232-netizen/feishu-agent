import asyncio

import pytest

from src.config import (
    AIConfig,
    AppConfig,
    BotConfig,
    ConversationConfig,
    ExecutionInterpretationConfig,
    FeishuConfig,
    JobQueueConfig,
    KnowledgeBaseConfig,
    ServerConfig,
)
from src.conversation import ConversationStore
from src.knowledge_base import KnowledgeSearchResult, KnowledgeSource
from src.message_jobs import MessageJobStore
from src.execution_report import ExecutionReportStore
from src.message_worker import MessageJobProcessor, MessageJobQueue
from src.muliu_executor import MuliuExecutionResult, MuliuStepResult
from src.operation_message import format_execution_result


class FakeAIClient:
    def __init__(self):
        self.calls = []
        self.error = None

    async def generate(self, history, user_message):
        self.calls.append((history, user_message))
        if self.error:
            raise self.error
        return f"AI:{user_message}"


class FakeFeishuClient:
    def __init__(self):
        self.replies = []

    async def reply_text(self, message_id, text):
        self.replies.append((message_id, text))


class FakeKnowledgeBase:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self.result


class InterpretationAIClient:
    def __init__(self, reply="【存在】已找到目标 Patch。"):
        self.calls = []
        self.reply = reply
        self.error = None
        self.block = None

    async def generate(self, history, user_message):
        self.calls.append((history, user_message))
        if self.block is not None:
            await self.block.wait()
        if self.error is not None:
            raise self.error
        return self.reply


def make_config(fallback_when_miss="answer_with_notice", **overrides):
    defaults = {
        "server": ServerConfig(),
        "feishu": FeishuConfig(app_id="app", app_secret="secret", verification_token="verify"),
        "ai": AIConfig(provider="openai_compatible", base_url="https://api.example.com/v1", api_key="key", model="m"),
        "conversation": ConversationConfig(max_history_messages=4),
        "knowledge_base": KnowledgeBaseConfig(enabled=True, fallback_when_miss=fallback_when_miss),
        "bot": BotConfig(name="AI助手"),
        "job_queue": JobQueueConfig(),
    }
    defaults.update(overrides)
    return AppConfig(**defaults)


@pytest.mark.asyncio
async def test_process_one_replies_and_marks_done(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "你好", now=1000)
    ai = FakeAIClient()
    feishu = FakeFeishuClient()
    conversations = ConversationStore(4)
    processor = MessageJobProcessor(make_config(), store, conversations, ai, feishu)

    await processor.process_one("om_1")

    assert store.get("om_1").status == "done"
    assert ai.calls == [([], "你好")]
    assert feishu.replies == [("om_1", "AI:你好")]
    assert conversations.get("oc_1") == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "AI:你好"},
    ]


@pytest.mark.asyncio
async def test_process_one_uses_knowledge_base_context(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "本地问题", now=1000)
    ai = FakeAIClient()
    feishu = FakeFeishuClient()
    kb = FakeKnowledgeBase(KnowledgeSearchResult(
        hit=True,
        context="[来源: articles/a.md]\n本地知识内容",
        sources=[KnowledgeSource(kind="article", path="articles/a.md", title="A", score=5)],
    ))
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), ai, feishu, knowledge_base=kb)

    await processor.process_one("om_1")

    assert kb.queries == ["本地问题"]
    assert "请优先依据以下本地知识库资料回答" in ai.calls[0][1]
    assert "本地知识内容" in ai.calls[0][1]
    assert "用户问题：本地问题" in ai.calls[0][1]
    reply = feishu.replies[0][1]
    assert reply.startswith("📚 以下回答基于本地知识库资料生成。")
    assert "来源：\n- articles/a.md" in reply


@pytest.mark.asyncio
async def test_process_one_local_only_miss_replies_without_calling_ai(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "未知问题", now=1000)
    ai = FakeAIClient()
    feishu = FakeFeishuClient()
    kb = FakeKnowledgeBase(KnowledgeSearchResult(hit=False, context="", sources=[]))
    processor = MessageJobProcessor(make_config(fallback_when_miss="local_only"), store, ConversationStore(4), ai, feishu, knowledge_base=kb)

    await processor.process_one("om_1")

    assert store.get("om_1").status == "done"
    assert kb.queries == ["未知问题"]
    assert ai.calls == []
    assert feishu.replies == [("om_1", "📚 本地知识库没有找到与该问题相关的内容。当前已关闭外部/通用知识回答。")]


@pytest.mark.asyncio
async def test_process_one_logs_stage_timings(tmp_path, caplog):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "本地问题", now=1000)
    ai = FakeAIClient()
    feishu = FakeFeishuClient()
    kb = FakeKnowledgeBase(KnowledgeSearchResult(
        hit=True,
        context="[来源: articles/a.md]\n本地知识内容",
        sources=[KnowledgeSource(kind="article", path="articles/a.md", title="A", score=5)],
    ))
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), ai, feishu, knowledge_base=kb)

    await processor.process_one("om_1")

    assert "job timings message_id=om_1" in caplog.text
    assert "knowledge_ms=" in caplog.text
    assert "ai_ms=" in caplog.text
    assert "feishu_reply_ms=" in caplog.text
    assert "total_ms=" in caplog.text
    assert "local_knowledge_hit=True" in caplog.text
    assert "fallback=answer_with_notice" in caplog.text


@pytest.mark.asyncio
async def test_process_one_marks_failed_when_ai_raises(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "你好", now=1000)
    ai = FakeAIClient()
    ai.error = RuntimeError("model unavailable")
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), ai, feishu)

    await processor.process_one("om_1")

    job = store.get("om_1")
    assert job.status == "failed"
    assert job.attempts == 1
    assert "model unavailable" in job.last_error
    assert feishu.replies == []


def _successful_patch_result(log):
    return MuliuExecutionResult(
        summary="查询 5010 服当前 Patch 列表",
        step_results=[
            MuliuStepResult(
                step_number=1,
                description="查询 5010 服当前 Patch 列表并检查缺失文件",
                path="/home/serverGeneralScript/cc_patch.py",
                args=["-s", "5010", "-ck"],
                succeeded=True,
                log=log,
            )
        ],
    )


class FakeCardFeishuClient(FakeFeishuClient):
    def __init__(self):
        super().__init__()
        self.card_updates = []

    async def update_card(self, message_id, card):
        self.card_updates.append((message_id, card))


class _FakeContract:
    def __init__(self, path, risk):
        self.path = path
        self.risk = risk

    def matches(self, step):
        return step.path == self.path


class _FakeContracts:
    def __init__(self, contracts):
        self.contracts = contracts


class FakeCatalog:
    def __init__(self, *contracts):
        self._contracts = _FakeContracts(list(contracts))

    def read_call_contracts(self):
        return self._contracts


READ_CATALOG = FakeCatalog(
    _FakeContract("/home/serverGeneralScript/cc_patch.py", "read"),
    _FakeContract("/home/serverGeneralScript/basic_info.sh", "read"),
    _FakeContract("/home/serverGeneralScript/exc_server_merge.sh", "write"),
)


def _report_processor(tmp_path, ai, feishu, *, timeout=1.0, environment_path=None):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    report_store = ExecutionReportStore(tmp_path / "jobs.sqlite3")
    report_store.initialize()
    processor = MessageJobProcessor(
        make_config(
            execution_interpretation=ExecutionInterpretationConfig(
                background_timeout_seconds=timeout,
                server_environments_path=str(environment_path or tmp_path / "missing.json"),
            )
        ),
        store,
        ConversationStore(4),
        ai,
        feishu,
        script_catalog=READ_CATALOG,
        report_store=report_store,
    )
    return store, report_store, processor


def _card_job(store, message_id, text, *, route_region="cn"):
    store.create_if_new(message_id, "evt_" + message_id, "oc_group", "ou_user", text)
    store.mark_awaiting_confirmation(
        message_id,
        plan_json="",
        confirmation_message_id="card_" + message_id,
        confirmation_token="token_" + message_id,
        confirmation_method="card",
        route_region=route_region,
    )
    return store.get(message_id)


def _card_detail(card):
    return card["elements"][2]["text"]["content"]


def _merge_failure(server_id):
    return MuliuExecutionResult(
        "合服 {} → 2018".format(server_id),
        [
            MuliuStepResult(
                1,
                "合服 {} 到 2018".format(server_id),
                "/home/serverGeneralScript/exc_server_merge.sh",
                [server_id, "2018", "17"],
                False,
                "start merge {}\n2026-11-02 11:40:01 ssh: connect to host 10.1.2.3 port 22: Connection refused\n"
                "exit code 255".format(server_id),
            )
        ],
        failed_step_number=1,
        failure_reason="脚本退出码非 0（exit code 255）",
    )


@pytest.mark.asyncio
async def test_read_success_delivers_first_then_ai_reads_in_background(tmp_path):
    environment_path = tmp_path / "server_environments.json"
    environment_path.write_text(
        '{"regions":{"hmt":{"name":"港澳台繁体","environments":{"weekly":["5010"]}}},"groups":{}}',
        encoding="utf-8",
    )
    ai = InterpretationAIClient("【存在】已在 5010 服 Patch 列表中找到 avatar_patch_79941.lua。")
    feishu = FakeFeishuClient()
    store, report_store, processor = _report_processor(tmp_path, ai, feishu, environment_path=environment_path)
    job, _ = store.create_if_new("om_patch", "evt_patch", "oc_group", "ou_user", "5010 服务器是否存在 avatar_patch_79941 patch？")
    result = _successful_patch_result(
        """MULIU_RUN_VERSION: opaque
WORKSPACE_PATH: /data/app/muliu_ws/workspace
AI_OP_START task_id=89
password=super-secret
Authorization: Bearer bearer-secret
Cookie: session=secret-cookie
mysql://root:db-secret@10.202.28.45:3306/game
/data/app/muliu_ws/workspace/taskDir/task_89
{\"file\": \"avatar_patch_79941.lua\", \"patch_type\": \"avatar\", \"order\": 1}
AI_OP_END status=success
END"""
    )

    await processor.present_execution_result(job, result, "om_patch")
    assert len(feishu.replies) == 1
    assert feishu.replies[0][1].startswith("✅ 执行完成")
    await processor.wait_for_interpretations()

    assert len(ai.calls) == 1
    prompt = ai.calls[0][1]
    assert "avatar_patch_79941.lua" in prompt
    assert "港澳台繁体（hmt）：weekly：5010" in prompt
    for secret in ("super-secret", "bearer-secret", "secret-cookie", "db-secret", "10.202.28.45", "/data/app"):
        assert secret not in prompt
    assert "AI_OP_START" not in prompt
    assert feishu.replies[1] == ("om_patch", "【存在】已在 5010 服 Patch 列表中找到 avatar_patch_79941.lua。")
    task = report_store.get_interpretation("om_patch")
    assert task.status == "done"
    assert "super-secret" not in task.payload_json


@pytest.mark.asyncio
async def test_read_success_template_mode_never_calls_ai(tmp_path):
    ai = InterpretationAIClient()
    feishu = FakeFeishuClient()
    store, report_store, processor = _report_processor(tmp_path, ai, feishu)
    report_store.set_read_success_mode("template")
    job, _ = store.create_if_new("om_patch", "evt_patch", "oc_group", "ou_user", "查询 Patch")

    await processor.present_execution_result(job, _successful_patch_result('{"file": "a.lua"}'), "om_patch")
    await processor.wait_for_interpretations()

    assert ai.calls == []
    assert len(feishu.replies) == 1


@pytest.mark.asyncio
async def test_write_success_and_unknown_never_call_ai(tmp_path):
    ai = InterpretationAIClient()
    feishu = FakeCardFeishuClient()
    store, _report_store, processor = _report_processor(tmp_path, ai, feishu)
    write_job = _card_job(store, "om_merge", "合服")
    unknown_job = _card_job(store, "om_unknown", "合服")
    write_ok = MuliuExecutionResult(
        "合服 2213 → 2018",
        [MuliuStepResult(1, "合服", "/home/serverGeneralScript/exc_server_merge.sh", ["2213", "2018", "17"], True, "ok")],
    )
    unknown = MuliuExecutionResult("合服 2213 → 2018", [], result_unknown_reason="Worker 租约过期")

    await processor.present_execution_result(write_job, write_ok, "om_merge")
    await processor.present_execution_result(unknown_job, unknown, "om_unknown")
    await processor.wait_for_interpretations()

    assert ai.calls == []
    assert _card_detail(feishu.card_updates[0][1]).startswith("【执行完成】")
    assert feishu.card_updates[1][1]["header"]["template"] == "yellow"
    assert "请勿重复确认或重试" in _card_detail(feishu.card_updates[1][1])


@pytest.mark.asyncio
async def test_failure_miss_collects_candidate_and_same_error_on_other_server_reuses_it(tmp_path):
    ai = InterpretationAIClient("SSH 连接被拒绝：目标服 SSH 不可达，请核对目标服状态与网络。")
    feishu = FakeCardFeishuClient()
    store, report_store, processor = _report_processor(tmp_path, ai, feishu)
    first = _card_job(store, "om_first", "合服 2213 到 2018")
    second = _card_job(store, "om_second", "合服 3001 到 2018", route_region="overseas")

    await processor.present_execution_result(first, _merge_failure("2213"), "om_first")
    first_card = feishu.card_updates[0][1]
    assert first_card["header"]["template"] == "red"
    assert "AI 正在后台分析" in _card_detail(first_card)
    await processor.wait_for_interpretations()

    assert len(ai.calls) == 1
    assert "可信终态】failed" in ai.calls[0][1]
    assert "exit code 255" in ai.calls[0][1]
    updated = feishu.card_updates[1][1]
    assert updated["header"]["template"] == "red"
    assert "AI 新分析（待人工确认）" in _card_detail(updated)
    candidates = report_store.list_patterns("candidate")
    assert len(candidates) == 1

    await processor.present_execution_result(second, _merge_failure("3001"), "om_second")
    await processor.wait_for_interpretations()

    assert len(ai.calls) == 1
    reused = _card_detail(feishu.card_updates[2][1])
    assert "AI 分析 · 未人工确认（已复用 1 次）" in reused
    assert "SSH 连接被拒绝" in reused

    report_store.set_pattern_status([candidates[0].pattern_id], "verified")
    third = _card_job(store, "om_third", "合服 4001 到 2018")
    await processor.present_execution_result(third, _merge_failure("4001"), "om_third")
    assert "已审核典型故障（命中 2 次）" in _card_detail(feishu.card_updates[3][1])
    assert len(ai.calls) == 1


@pytest.mark.asyncio
async def test_failure_with_different_error_does_not_reuse_template(tmp_path):
    ai = InterpretationAIClient("诊断")
    feishu = FakeCardFeishuClient()
    store, report_store, processor = _report_processor(tmp_path, ai, feishu)
    await processor.present_execution_result(_card_job(store, "om_a", "合服"), _merge_failure("2213"), "om_a")
    await processor.wait_for_interpretations()
    other = MuliuExecutionResult(
        "合服 2213 → 2018",
        [MuliuStepResult(1, "合服", "/home/serverGeneralScript/exc_server_merge.sh", ["2213", "2018", "17"], False, "配置生成失败：缺少 combine_demo_new.lua")],
        failed_step_number=1,
        failure_reason="未检测到合服完成文本",
    )

    await processor.present_execution_result(_card_job(store, "om_b", "合服"), other, "om_b")
    await processor.wait_for_interpretations()

    assert len(ai.calls) == 2
    assert len(report_store.list_patterns("candidate")) == 2


@pytest.mark.asyncio
async def test_failure_ai_timeout_keeps_trusted_card_and_marks_task(tmp_path):
    ai = InterpretationAIClient()
    ai.block = asyncio.Event()
    feishu = FakeCardFeishuClient()
    store, report_store, processor = _report_processor(tmp_path, ai, feishu, timeout=0.01)

    await processor.present_execution_result(_card_job(store, "om_t", "合服"), _merge_failure("2213"), "om_t")
    await processor.wait_for_interpretations()

    assert report_store.get_interpretation("om_t").status == "timed_out"
    assert report_store.list_patterns("candidate") == []
    final = feishu.card_updates[-1][1]
    assert final["header"]["template"] == "red"
    assert "AI 分析未完成" in _card_detail(final)
    assert store.get("om_t").status == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_interrupted_interpretation_is_recovered_on_start(tmp_path):
    ai = InterpretationAIClient("诊断")
    feishu = FakeCardFeishuClient()
    store, report_store, processor = _report_processor(tmp_path, ai, feishu)
    _card_job(store, "om_r", "合服")
    from src.worker_protocol import serialize_execution_result

    report_store.enqueue_interpretation("om_r", "failure", serialize_execution_result(_merge_failure("2213")))
    report_store.claim_interpretation("om_r")  # simulate a crash while running

    processor.recover_interpretations()
    await processor.wait_for_interpretations()

    assert report_store.get_interpretation("om_r").status == "done"
    assert len(ai.calls) == 1
    assert len(report_store.list_patterns("candidate")) == 1


@pytest.mark.asyncio
async def test_queue_puts_and_gets_request_items():
    queue = MessageJobQueue()

    await queue.enqueue("om_1")
    item = await queue.get()

    assert item.kind == "request"
    assert item.message_id == "om_1"
    assert item.chat_id == ""
    assert item.confirmation_token == ""


@pytest.mark.asyncio
async def test_queue_puts_and_gets_confirmation_items():
    queue = MessageJobQueue()

    await queue.enqueue_confirmation("om_confirm", "oc_group", "ABC123")
    item = await queue.get()

    assert item.kind == "confirmation"
    assert item.message_id == "om_confirm"
    assert item.chat_id == "oc_group"
    assert item.confirmation_token == "ABC123"


@pytest.mark.asyncio
async def test_background_reading_uses_interpretation_client_not_chat_client(tmp_path):
    chat_ai = InterpretationAIClient("chat")
    strong_ai = InterpretationAIClient("强模型诊断")
    feishu = FakeCardFeishuClient()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    report_store = ExecutionReportStore(tmp_path / "jobs.sqlite3")
    report_store.initialize()
    processor = MessageJobProcessor(
        make_config(
            execution_interpretation=ExecutionInterpretationConfig(server_environments_path=str(tmp_path / "missing.json"))
        ),
        store,
        ConversationStore(4),
        chat_ai,
        feishu,
        script_catalog=READ_CATALOG,
        report_store=report_store,
        interpretation_ai_client=strong_ai,
    )

    await processor.present_execution_result(_card_job(store, "om_s", "合服"), _merge_failure("2213"), "om_s")
    await processor.wait_for_interpretations()

    assert chat_ai.calls == []
    assert len(strong_ai.calls) == 1
    assert report_store.list_patterns("candidate")[0].diagnosis == "强模型诊断"


def _basic_info_failure(server_id):
    return MuliuExecutionResult(
        "查询 {} 服基础信息".format(server_id),
        [
            MuliuStepResult(
                1,
                "查询 {} 服基础信息".format(server_id),
                "/home/serverGeneralScript/basic_info.sh",
                [server_id],
                False,
                "NSLG_HOSTNUM = {}\n开服时间文件 [运行目录] 不存在或为空。\n未检测到开服成功信息".format(server_id),
            )
        ],
        failed_step_number=1,
        failure_reason="脚本执行失败，exit_code=1",
    )


@pytest.mark.asyncio
async def test_verified_warning_template_shows_query_done_with_warning_but_keeps_state(tmp_path):
    ai = InterpretationAIClient("开服时间文件缺失，服务可能未完成启动。")
    feishu = FakeCardFeishuClient()
    store, report_store, processor = _report_processor(tmp_path, ai, feishu)
    await processor.present_execution_result(_card_job(store, "om_1", "查看7020"), _basic_info_failure("7020"), "om_1")
    await processor.wait_for_interpretations()
    pattern = report_store.list_patterns("candidate")[0]

    report_store.set_pattern_outcome(pattern.pattern_id, "warning")
    await processor.present_execution_result(_card_job(store, "om_2", "查看7021"), _basic_info_failure("7021"), "om_2")
    candidate_card = feishu.card_updates[-1][1]
    assert candidate_card["header"]["template"] == "red"  # not verified yet → still failure

    report_store.set_pattern_status([pattern.pattern_id], "verified")
    await processor.present_execution_result(_card_job(store, "om_3", "查看7022"), _basic_info_failure("7022"), "om_3")
    card = feishu.card_updates[-1][1]

    assert card["header"]["template"] == "orange"
    assert card["header"]["title"]["content"] == "测试服查询完成 · 服务器状态存疑"
    assert "【执行完成 · 服务器状态存疑】" in _card_detail(card)
    assert "机器判定：脚本执行失败，exit_code=1" in _card_detail(card)
    assert len(ai.calls) == 1
    rows = {row["message_id"]: row for row in report_store.list_recent_executions()}
    assert rows["om_3"]["terminal_status"] == "failed"
    assert rows["om_3"]["report_source"] == "verified_warning"


@pytest.mark.asyncio
async def test_warning_verdict_never_applies_to_write_scripts(tmp_path):
    ai = InterpretationAIClient("诊断")
    feishu = FakeCardFeishuClient()
    store, report_store, processor = _report_processor(tmp_path, ai, feishu)
    await processor.present_execution_result(_card_job(store, "om_1", "合服"), _merge_failure("2213"), "om_1")
    await processor.wait_for_interpretations()
    pattern = report_store.list_patterns("candidate")[0]
    report_store.set_pattern_outcome(pattern.pattern_id, "warning")
    report_store.set_pattern_status([pattern.pattern_id], "verified")

    await processor.present_execution_result(_card_job(store, "om_2", "合服"), _merge_failure("3001"), "om_2")

    card = feishu.card_updates[-1][1]
    assert card["header"]["template"] == "red"
    assert "已审核典型故障" in _card_detail(card)


@pytest.mark.asyncio
async def test_requester_name_is_looked_up_once_per_chat_and_stored(tmp_path):
    class MemberFeishu(FakeFeishuClient):
        def __init__(self):
            super().__init__()
            self.lookups = []

        async def list_chat_member_names(self, chat_id):
            self.lookups.append(chat_id)
            return {"ou_user": "沈涛"}

    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    feishu = MemberFeishu()
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), FakeAIClient(), feishu)
    for message_id in ("om_1", "om_2"):
        store.create_if_new(message_id, "evt_" + message_id, "oc_group", "ou_user", "你好", chat_type="group")
        job = store.get(message_id)
        await processor._remember_requester_name(job)

    unknown = store.create_if_new("om_3", "evt_3", "oc_group", "ou_other", "你好", chat_type="group")[0]
    await processor._remember_requester_name(unknown)  # within 5 min of last lookup → no refetch

    assert feishu.lookups == ["oc_group"]
    import sqlite3
    rows = dict(sqlite3.connect(tmp_path / "jobs.sqlite3").execute("select message_id, user_name from message_jobs").fetchall())
    assert rows == {"om_1": "沈涛", "om_2": "沈涛", "om_3": ""}


@pytest.mark.asyncio
async def test_requester_name_lookup_failure_never_breaks_processing(tmp_path):
    class BrokenFeishu(FakeFeishuClient):
        async def list_chat_member_names(self, chat_id):
            raise RuntimeError("99991672 no permission")

    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_group", "ou_user", "你好", chat_type="group")
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), FakeAIClient(), BrokenFeishu())

    await processor.process_one("om_1")
    await processor.wait_for_background()

    assert store.get("om_1").status == "done"


@pytest.mark.asyncio
async def test_clear_plan_pushes_prepared_then_executing_to_wecom(tmp_path):
    import json as _json

    import httpx

    from src.clear_notifier import ClearNotifier
    from src.config import ClearNotificationConfig, RuntimeConfig
    from src.muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
    from src.muliu_planner import MuliuPlanGeneration
    from src.muliu_routing import MuliuRouteSnapshot
    from src.muliu_script_catalog import MuliuScriptCatalog

    clear_plan = MuliuPlan(
        summary="国服2213清档并启服",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep("/home/serverGeneralScript/clear", ["2213"], "清档 2213"),
            MuliuStep("/home/serverGeneralScript/start", ["2213"], "启服 2213"),
        ],
    )

    class Planner:
        async def create_plan_with_trace(self, text):
            return MuliuPlanGeneration(plan=clear_plan, raw_responses=())

    class Firewall:
        def validate(self, plan):
            return None

    class Resolver:
        def resolve(self, plan):
            return MuliuRouteSnapshot.from_region("cn")

    class CardFeishu(FakeCardFeishuClient):
        async def reply_card(self, message_id, card):
            return "card_" + message_id

        async def list_chat_member_names(self, chat_id):
            return {"ou_user": "沈涛"}

    pushed = []
    notifier = ClearNotifier(
        ClearNotificationConfig(enabled=True, webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: pushed.append(_json.loads(request.content)) or httpx.Response(200, json={"errcode": 0})
        )),
    )
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_c", "evt_c", "oc_group", "ou_user", "国服2213清档 然后启服", chat_type="group")
    processor = MessageJobProcessor(
        make_config(runtime=RuntimeConfig(role="gateway", worker_token="t")),
        store,
        ConversationStore(4),
        FakeAIClient(),
        CardFeishu(),
        operation_planner=Planner(),
        muliu_firewall=Firewall(),
        route_resolver=Resolver(),
        script_catalog=MuliuScriptCatalog("config/muliu_script_catalog.md"),
        clear_notifier=notifier,
    )

    await processor.process_one("om_c")
    await processor.wait_for_background()
    job = store.get("om_c")
    assert job.status == "awaiting_confirmation"
    assert len(pushed) == 1
    assert pushed[0]["text"]["content"].startswith("内网：沈涛 准备清档 2213 服")
    assert pushed[0]["text"]["mentioned_mobile_list"] == ["@all"]

    card = await processor.process_card_action(
        action="confirm_operation",
        request_message_id="om_c",
        confirmation_message_id=job.confirmation_message_id,
        chat_id="oc_group",
        user_id="ou_user",
        confirmation_token=job.confirmation_token,
    )
    await processor.wait_for_background()

    assert card["header"]["title"]["content"] == "测试服操作已提交"
    assert store.get("om_c").status == "queued_for_worker"
    assert len(pushed) == 2
    assert pushed[1]["text"]["content"].startswith("内网：沈涛 正在操作清档 2213 服")


@pytest.mark.asyncio
async def test_non_clear_plan_and_push_failure_do_not_affect_flow(tmp_path):
    import httpx

    from src.clear_notifier import ClearNotifier
    from src.config import ClearNotificationConfig
    from src.muliu_plan import MuliuPlan, MuliuStep

    calls = []
    notifier = ClearNotifier(
        ClearNotificationConfig(enabled=True, webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(503))),
    )
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    job = store.create_if_new("om_s", "evt_s", "oc_group", "ou_user", "启服", chat_type="group")[0]
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), FakeAIClient(), FakeFeishuClient(), clear_notifier=notifier)

    processor._notify_clear("clear_prepared", job, MuliuPlan(summary="s", steps=[MuliuStep("/home/serverGeneralScript/start", ["1"], "d")]), "cn", operator_id="ou_user")
    await processor.wait_for_background()
    assert calls == []

    processor._notify_clear("clear_prepared", job, MuliuPlan(summary="s", steps=[MuliuStep("/home/serverGeneralScript/clear", ["2213"], "d")]), "cn", operator_id="ou_user")
    await processor.wait_for_background()
    assert len(calls) == 1  # 503 → logged, nothing raised
