import json

import pytest

from src.config import AIConfig, AppConfig, BotConfig, ConversationConfig, FeishuConfig, JobQueueConfig, KnowledgeBaseConfig, ServerConfig
from src.event_handler import FeishuEventHandler


class FakeJobStore:
    def __init__(self):
        self.jobs = {}
        self.create_calls = []

    def create_if_new(self, message_id, event_id, chat_id, user_id, text, now=None):
        self.create_calls.append((message_id, event_id, chat_id, user_id, text))
        if message_id in self.jobs:
            return self.jobs[message_id], False
        job = type("Job", (), {
            "message_id": message_id,
            "event_id": event_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "text": text,
            "status": "queued",
        })()
        self.jobs[message_id] = job
        return job, True


class FakeJobQueue:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, message_id):
        self.enqueued.append(message_id)


def make_config(verification_token="verify"):
    return AppConfig(
        server=ServerConfig(),
        feishu=FeishuConfig(app_id="app", app_secret="secret", verification_token=verification_token),
        ai=AIConfig(provider="openai_compatible", base_url="https://api.example.com/v1", api_key="key", model="m"),
        conversation=ConversationConfig(max_history_messages=4),
        knowledge_base=KnowledgeBaseConfig(enabled=False),
        bot=BotConfig(name="AI助手"),
        job_queue=JobQueueConfig(),
    )


def message_event(chat_type="p2p", text="你好", mentions=None, event_id="evt-1", message_id="om_1"):
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "im.message.receive_v1", "token": "verify"},
        "event": {
            "message": {
                "message_id": message_id,
                "chat_id": "oc_1",
                "chat_type": chat_type,
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "mentions": mentions or [],
            }
        },
    }


@pytest.mark.asyncio
async def test_returns_challenge_for_url_verification():
    handler = FeishuEventHandler(make_config(), FakeJobStore(), FakeJobQueue())

    result = await handler.handle({"challenge": "abc"})

    assert result == {"challenge": "abc"}


@pytest.mark.asyncio
async def test_private_message_is_queued_without_calling_ai_or_feishu():
    store = FakeJobStore()
    queue = FakeJobQueue()
    handler = FeishuEventHandler(make_config(), store, queue)

    result = await handler.handle(message_event(chat_type="p2p", text="你好"))

    assert result == {"code": 0}
    assert store.create_calls == [("om_1", "evt-1", "oc_1", "", "你好")]
    assert queue.enqueued == ["om_1"]


@pytest.mark.asyncio
async def test_duplicate_message_id_is_acknowledged_without_requeueing():
    store = FakeJobStore()
    queue = FakeJobQueue()
    handler = FeishuEventHandler(make_config(), store, queue)

    await handler.handle(message_event(event_id="evt-first"))
    result = await handler.handle(message_event(event_id="evt-retry"))

    assert result == {"code": 0}
    assert len(store.create_calls) == 2
    assert queue.enqueued == ["om_1"]


@pytest.mark.asyncio
async def test_group_message_without_mention_is_ignored():
    store = FakeJobStore()
    queue = FakeJobQueue()
    handler = FeishuEventHandler(make_config(), store, queue)

    result = await handler.handle(message_event(chat_type="group", text="大家好"))

    assert result == {"code": 0, "ignored": "not_mentioned"}
    assert store.create_calls == []
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_group_message_with_mention_removes_mention_text_before_queueing():
    store = FakeJobStore()
    queue = FakeJobQueue()
    mentions = [{"name": "AI助手", "key": "@_user_1"}]
    handler = FeishuEventHandler(make_config(), store, queue)

    result = await handler.handle(message_event(chat_type="group", text="@_user_1 帮我总结", mentions=mentions))

    assert result == {"code": 0}
    assert store.create_calls == [("om_1", "evt-1", "oc_1", "", "帮我总结")]
    assert queue.enqueued == ["om_1"]


@pytest.mark.asyncio
async def test_invalid_verification_token_is_ignored():
    store = FakeJobStore()
    queue = FakeJobQueue()
    payload = message_event()
    payload["header"]["token"] = "bad"
    handler = FeishuEventHandler(make_config(verification_token="verify"), store, queue)

    result = await handler.handle(payload)

    assert result == {"code": 0, "ignored": "invalid_token"}
    assert store.create_calls == []
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_logs_received_event_and_job_queued(caplog):
    store = FakeJobStore()
    queue = FakeJobQueue()
    handler = FeishuEventHandler(make_config(), store, queue)

    result = await handler.handle(message_event(chat_type="p2p", text="你好", event_id="evt-log"))

    assert result == {"code": 0}
    assert "received event_id=evt-log message_id=om_1 chat_id=oc_1" in caplog.text
    assert "job queued message_id=om_1" in caplog.text
