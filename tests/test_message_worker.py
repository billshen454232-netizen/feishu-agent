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
from src.message_worker import MessageJobProcessor, MessageJobQueue, should_interpret_with_ai
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


@pytest.mark.asyncio
async def test_interpret_execution_result_sends_only_cleaned_redacted_context(tmp_path):
    environment_path = tmp_path / "server_environments.json"
    environment_path.write_text(
        '{"regions":{"hmt":{"name":"港澳台繁体","environments":{"weekly":["5010"]}}},"groups":{}}',
        encoding="utf-8",
    )
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    job, _ = store.create_if_new(
        "om_patch",
        "evt_patch",
        "oc_group",
        "ou_user",
        "5010 服务器是否存在 avatar_patch_79941 patch？",
    )
    ai = InterpretationAIClient("【存在】已在 5010 服 Patch 列表中找到 avatar_patch_79941.lua。")
    processor = MessageJobProcessor(
        make_config(
            execution_interpretation=ExecutionInterpretationConfig(
                timeout_seconds=1,
                server_environments_path=str(environment_path),
            )
        ),
        store,
        ConversationStore(4),
        ai,
        FakeFeishuClient(),
    )
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
    fallback = format_execution_result(result)

    reply = await processor.interpret_execution_result(job, result, fallback)

    assert reply.startswith("【存在】")
    assert "执行明细：" in reply
    assert len(ai.calls) == 1
    prompt = ai.calls[0][1]
    assert "avatar_patch_79941.lua" in prompt
    assert "港澳台繁体（hmt）：weekly：5010" in prompt
    for secret in ("super-secret", "bearer-secret", "secret-cookie", "db-secret", "10.202.28.45", "/data/app"):
        assert secret not in prompt
        assert secret not in reply
    assert "MULIU_RUN_VERSION" not in prompt
    assert "AI_OP_START" not in prompt
    assert "AI_OP_END" not in prompt


@pytest.mark.asyncio
async def test_interpret_execution_result_falls_back_when_ai_times_out(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    job, _ = store.create_if_new("om_patch", "evt_patch", "oc_group", "ou_user", "查询 Patch 列表")
    ai = InterpretationAIClient()
    ai.block = asyncio.Event()
    processor = MessageJobProcessor(
        make_config(
            execution_interpretation=ExecutionInterpretationConfig(
                timeout_seconds=0.01,
                server_environments_path=str(tmp_path / "missing.json"),
            )
        ),
        store,
        ConversationStore(4),
        ai,
        FakeFeishuClient(),
    )
    result = _successful_patch_result('{"file": "avatar_patch_79941.lua"}')
    fallback = format_execution_result(result)

    reply = await processor.interpret_execution_result(job, result, fallback)

    assert reply == fallback
    assert len(ai.calls) == 1


@pytest.mark.asyncio
async def test_interpret_execution_result_summarizes_every_terminal_outcome(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    job, _ = store.create_if_new("om_result", "evt_result", "oc_group", "ou_user", "查询 5010 服状态")
    ai = InterpretationAIClient("【执行失败】Muliu 未返回可用的脚本终态。")
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), ai, FakeFeishuClient())
    failed = MuliuExecutionResult("查询 5010 服状态", [], failed_step_number=1, failure_reason="失败")
    unknown = MuliuExecutionResult("查询 5010 服状态", [], result_unknown_reason="未知")
    basic = MuliuExecutionResult(
        "查询 5010 服状态",
        [MuliuStepResult(1, "查询 5010 服状态", "/home/serverGeneralScript/basic_info.sh", ["5010"], True, "正常")],
    )

    assert should_interpret_with_ai(job.text, failed) is True
    assert should_interpret_with_ai(job.text, unknown) is True
    assert should_interpret_with_ai(job.text, basic) is True
    assert (await processor.interpret_execution_result(job, failed, "failed")).startswith("【执行失败】")
    assert (await processor.interpret_execution_result(job, unknown, "unknown")).startswith("【执行失败】")
    assert (await processor.interpret_execution_result(job, basic, "basic")).startswith("【执行失败】")
    assert len(ai.calls) == 3
    assert all("没有可用脚本输出" in prompt for _history, prompt in ai.calls[:2])


@pytest.mark.asyncio
async def test_interpret_execution_result_summarizes_multi_server_basic_info(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    job, _ = store.create_if_new(
        "om_weekly",
        "evt_weekly",
        "oc_group",
        "ou_user",
        "检查海外所有 weekly 环境服务器是否已起服",
    )
    ai = InterpretationAIClient("【开服状态汇总】\n- 已开服：5000\n- 已关闭：5010\n- 查询报错/无法确认：无")
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), ai, FakeFeishuClient())
    result = MuliuExecutionResult(
        "检查海外所有 weekly 环境服务器是否已起服",
        [
            MuliuStepResult(
                1,
                "检查 5000 服版本、开服信息和进程状态",
                "/home/serverGeneralScript/basic_info.sh",
                ["5000"],
                True,
                "服务器正在运行\n开服成功",
            ),
            MuliuStepResult(
                2,
                "检查 5010 服版本、开服信息和进程状态",
                "/home/serverGeneralScript/basic_info.sh",
                ["5010"],
                True,
                "服务器已正常关闭",
            ),
        ],
    )

    reply = await processor.interpret_execution_result(job, result, "执行明细")

    assert reply.startswith("【开服状态汇总】")
    assert "执行明细：" in reply
    assert len(ai.calls) == 1
    assert "已开服、已关闭、查询报错/无法确认三组" in ai.calls[0][1]


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
