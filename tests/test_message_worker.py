import pytest

from src.config import AIConfig, AppConfig, BotConfig, ConversationConfig, FeishuConfig, JobQueueConfig, KnowledgeBaseConfig, ServerConfig
from src.conversation import ConversationStore
from src.knowledge_base import KnowledgeSearchResult, KnowledgeSource
from src.message_jobs import MessageJobStore
from src.message_worker import MessageJobProcessor, MessageJobQueue


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


def make_config(fallback_when_miss="answer_with_notice"):
    return AppConfig(
        server=ServerConfig(),
        feishu=FeishuConfig(app_id="app", app_secret="secret", verification_token="verify"),
        ai=AIConfig(provider="openai_compatible", base_url="https://api.example.com/v1", api_key="key", model="m"),
        conversation=ConversationConfig(max_history_messages=4),
        knowledge_base=KnowledgeBaseConfig(enabled=True, fallback_when_miss=fallback_when_miss),
        bot=BotConfig(name="AI助手"),
        job_queue=JobQueueConfig(),
    )


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


@pytest.mark.asyncio
async def test_queue_puts_and_gets_message_ids():
    queue = MessageJobQueue()

    await queue.enqueue("om_1")
    message_id = await queue.get()

    assert message_id == "om_1"
