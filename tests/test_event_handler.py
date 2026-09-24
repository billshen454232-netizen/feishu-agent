import json

import pytest

from src.config import AIConfig, AppConfig, BotConfig, ConversationConfig, FeishuConfig, JobQueueConfig, KnowledgeBaseConfig, ServerConfig
from src.event_handler import FeishuEventHandler


class FakeJobStore:
    def __init__(self):
        self.jobs = {}
        self.create_calls = []

    def create_if_new(
        self,
        message_id,
        event_id,
        chat_id,
        user_id,
        text,
        now=None,
        *,
        chat_type="",
    ):
        self.create_calls.append((message_id, event_id, chat_id, chat_type, user_id, text))
        if message_id in self.jobs:
            return self.jobs[message_id], False
        job = type("Job", (), {
            "message_id": message_id,
            "event_id": event_id,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "user_id": user_id,
            "text": text,
            "status": "queued",
        })()
        self.jobs[message_id] = job
        return job, True


class FakeJobQueue:
    def __init__(self):
        self.enqueued = []
        self.confirmations = []

    async def enqueue(self, message_id):
        self.enqueued.append(message_id)

    async def enqueue_confirmation(self, message_id, chat_id, confirmation_token):
        self.confirmations.append((message_id, chat_id, confirmation_token))


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


class FakeCardProcessor:
    def __init__(self, card=None):
        self.card = card or {"header": {"title": {"content": "已提交"}}}
        self.calls = []

    async def process_card_action(self, **kwargs):
        self.calls.append(kwargs)
        return self.card


def card_action_event(
    *,
    action="confirm_operation",
    request_message_id="om_request",
    confirmation_token="ABC123",
    operator_id="ou_requester",
    card_message_id="om_card",
    chat_id="oc_1",
    event_id="evt-card",
):
    return {
        "schema": "2.0",
        "header": {"event_id": event_id, "event_type": "card.action.trigger", "token": "verify"},
        "event": {
            "operator": {"open_id": operator_id},
            "action": {
                "tag": "button",
                "value": {
                    "action": action,
                    "request_message_id": request_message_id,
                    "confirmation_token": confirmation_token,
                },
            },
            "context": {"open_message_id": card_message_id, "open_chat_id": chat_id},
        },
    }


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
    assert store.create_calls == [("om_1", "evt-1", "oc_1", "p2p", "", "你好")]
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
    assert store.create_calls == [("om_1", "evt-1", "oc_1", "group", "", "帮我总结")]
    assert queue.enqueued == ["om_1"]


@pytest.mark.asyncio
async def test_group_confirmation_is_queued_without_creating_a_new_job():
    store = FakeJobStore()
    queue = FakeJobQueue()
    handler = FeishuEventHandler(
        make_config(),
        store,
        queue,
        job_processor=object(),
    )

    result = await handler.handle(
        message_event(
            chat_type="group",
            text="确认执行 aBc123",
            event_id="evt-confirm",
            message_id="om_confirm",
        )
    )

    assert result == {"code": 0}
    assert store.create_calls == []
    assert queue.enqueued == []
    assert queue.confirmations == [("om_confirm", "oc_1", "ABC123")]


@pytest.mark.asyncio
async def test_group_confirmation_with_mention_is_queued_without_creating_a_new_job():
    store = FakeJobStore()
    queue = FakeJobQueue()
    handler = FeishuEventHandler(
        make_config(),
        store,
        queue,
        job_processor=object(),
    )
    mentions = [{"name": "AI助手", "key": "@_user_1"}]

    result = await handler.handle(
        message_event(
            chat_type="group",
            text="@_user_1 确认执行 aBc123",
            mentions=mentions,
            event_id="evt-confirm-mentioned",
            message_id="om_confirm_mentioned",
        )
    )

    assert result == {"code": 0}
    assert store.create_calls == []
    assert queue.enqueued == []
    assert queue.confirmations == [("om_confirm_mentioned", "oc_1", "ABC123")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("确认执行 aBc123", "ABC123"),
        ("@沈涛的飞书 CLI 确认执行 632DB4", "632DB4"),
        ("  随意文本 确认执行 632DB4  ", "632DB4"),
        ("确认执行 632DB", None),
        ("确认执行 632DB4 后继续", None),
        ("请确认执行 632DB4", None),
        ("确认执行 632DB4;whoami", None),
    ],
)
def test_confirmation_token_parser_isolated_from_feishu_event_handling(text, expected):
    handler = FeishuEventHandler(
        make_config(),
        FakeJobStore(),
        FakeJobQueue(),
        job_processor=object(),
    )

    assert handler._extract_confirmation_token(text) == expected


@pytest.mark.asyncio
async def test_group_confirmation_with_visible_bot_mention_is_queued():
    store = FakeJobStore()
    queue = FakeJobQueue()
    handler = FeishuEventHandler(
        make_config(),
        store,
        queue,
        job_processor=object(),
    )
    mentions = [{"name": "AI助手", "key": "@_user_1"}]

    result = await handler.handle(
        message_event(
            chat_type="group",
            text="@沈涛的飞书 CLI 确认执行 632DB4",
            mentions=mentions,
            event_id="evt-visible-mention-confirm",
            message_id="om_visible_mention_confirm",
        )
    )

    assert result == {"code": 0}
    assert store.create_calls == []
    assert queue.enqueued == []
    assert queue.confirmations == [("om_visible_mention_confirm", "oc_1", "632DB4")]


@pytest.mark.asyncio
async def test_card_action_passes_only_identity_values_to_processor():
    processor = FakeCardProcessor()
    handler = FeishuEventHandler(make_config(), FakeJobStore(), FakeJobQueue(), job_processor=processor)

    result = await handler.handle(card_action_event())

    assert result == {
        "toast": {"type": "success", "content": "操作已受理"},
        "card": {"type": "raw", "data": processor.card},
    }
    assert processor.calls == [{
        "action": "confirm_operation",
        "request_message_id": "om_request",
        "confirmation_message_id": "om_card",
        "chat_id": "oc_1",
        "user_id": "ou_requester",
        "confirmation_token": "ABC123",
    }]


@pytest.mark.asyncio
async def test_card_action_missing_identity_is_rejected_without_calling_processor():
    processor = FakeCardProcessor()
    payload = card_action_event()
    del payload["event"]["context"]["open_message_id"]
    handler = FeishuEventHandler(make_config(), FakeJobStore(), FakeJobQueue(), job_processor=processor)

    result = await handler.handle(payload)

    assert result == {"toast": {"type": "warning", "content": "卡片操作信息不完整，未执行。"}}
    assert processor.calls == []


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
