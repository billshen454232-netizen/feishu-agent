import hashlib
import hmac
import json

import httpx
import pytest

from src.clear_notifier import (
    EVENT_EXECUTING,
    EVENT_PREPARED,
    ClearEventPublisher,
    ClearEventRelay,
    LocalEventSource,
    WebhookSender,
)
from src.config import ClearNotificationConfig, ConfigError, load_config
from src.muliu_plan import MuliuPlan, MuliuStep
from src.outbound_events import OutboundEventStore

CLEAR = "/home/serverGeneralScript/clear"
PLAN = MuliuPlan(
    summary="国服2213清档并启服",
    steps=[
        MuliuStep(CLEAR, ["2213"], "清档 2213"),
        MuliuStep("/home/serverGeneralScript/start", ["2213"], "启服 2213"),
    ],
)
CONFIG = ClearNotificationConfig(enabled=True, webhook_url="https://hook.example/clear")


def _store(tmp_path):
    store = OutboundEventStore(tmp_path / "db.sqlite3")
    store.initialize()
    return store


def _payload(publisher, event=EVENT_EXECUTING, message_id="om_1"):
    return publisher.build_payload(
        event,
        message_id=message_id,
        plan=PLAN,
        clear_steps=publisher.clear_steps(PLAN),
        server_ids=["2213"],
        route_region="cn",
        request_text="国服2213清档 password=hunter2",
        operator={"open_id": "ou_2", "name": "王一啸"},
        requester={"open_id": "ou_1", "name": "沈涛"},
        now=1794016800,
    )


def _sender(handler, **overrides):
    config = ClearNotificationConfig(enabled=True, webhook_url="https://hook.example/clear", **overrides)
    return WebhookSender(config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


# ---------------------------------------------------------------- publisher (gateway)


def test_only_registered_clear_paths_trigger(tmp_path):
    publisher = ClearEventPublisher(CONFIG, _store(tmp_path))

    assert [step.args for step in publisher.clear_steps(PLAN)] == [["2213"]]
    assert publisher.clear_steps(MuliuPlan(summary="s", steps=[MuliuStep("/home/serverGeneralScript/start", ["1"], "d")])) == []


def test_payload_carries_event_text_and_masks_request(tmp_path):
    publisher = ClearEventPublisher(CONFIG, _store(tmp_path))
    payload = _payload(publisher)

    assert payload["event_id"] == "om_1:clear_executing"
    assert payload["text"] == "内网：王一啸 正在操作清档 2213 服"
    assert payload["occurred_at_text"] == "2026-11-07 10:00:00"
    assert payload["clear_steps"] == [{"script": "clear", "path": CLEAR, "args": ["2213"], "description": "清档 2213"}]
    assert "hunter2" not in payload["request_text"]
    assert _payload(publisher, EVENT_PREPARED)["text"] == "内网：王一啸 准备清档 2213 服"


def test_publish_records_each_event_once(tmp_path):
    store = _store(tmp_path)
    publisher = ClearEventPublisher(CONFIG, store)

    assert publisher.publish(_payload(publisher)) is True
    assert publisher.publish(_payload(publisher)) is False
    assert store.get("om_1:clear_executing")["status"] == "pending"


# ---------------------------------------------------------------- outbox


def test_outbox_lease_report_and_reclaim(tmp_path):
    store = _store(tmp_path)
    store.add("e1", "clear_prepared", {"event_id": "e1"}, now=1000)

    first = store.claim("w1", now=1001)
    assert [e["event_id"] for e in first] == ["e1"]
    assert store.claim("w2", now=1002) == []  # leased
    assert store.report("e1", "w2", delivered=True) is False  # not the leaseholder
    assert [e["event_id"] for e in store.claim("w2", now=1001 + 61)] == ["e1"]  # lease expired → re-claimable
    assert store.report("e1", "w2", delivered=True, now=1070) is True
    assert store.get("e1")["status"] == "delivered"
    assert store.claim("w1", now=1100) == []


def test_outbox_failures_retry_then_give_up_and_old_events_expire(tmp_path):
    store = _store(tmp_path)
    store.add("e1", "k", {}, now=1000)
    for attempt in range(5):
        assert store.claim("w", now=1001 + attempt)
        store.report("e1", "w", delivered=False, error="ConnectError", max_attempts=5)
    assert store.get("e1")["status"] == "failed"
    assert store.get("e1")["attempts"] == 5

    store.add("old", "k", {}, now=0)
    assert store.claim("w", now=3601, max_age_seconds=3600) == []
    assert store.get("old")["status"] == "expired"


# ---------------------------------------------------------------- sender + relay (worker)


@pytest.mark.asyncio
async def test_sender_posts_json_with_optional_signature():
    seen = []
    sender = _sender(lambda request: seen.append(request) or httpx.Response(200), secret="s3cret")

    assert await sender.send({"event_id": "e1", "event": "clear_executing"}) == (True, "")

    request = seen[0]
    body = request.content
    assert json.loads(body)["event"] == "clear_executing"
    expected = hmac.new(b"s3cret", request.headers["X-Clear-Timestamp"].encode() + b"." + body, hashlib.sha256).hexdigest()
    assert request.headers["X-Clear-Signature"] == expected


@pytest.mark.asyncio
async def test_relay_delivers_and_reports(tmp_path):
    store = _store(tmp_path)
    store.add("ok", "k", {"event_id": "ok"})
    store.add("bad", "k", {"event_id": "bad"})
    sender = _sender(lambda request: httpx.Response(500 if json.loads(request.content)["event_id"] == "bad" else 200))
    relay = ClearEventRelay(LocalEventSource(store, max_age_seconds=3600), sender, poll_interval_seconds=1)

    assert await relay.run_once() == 2

    assert store.get("ok")["status"] == "delivered"
    assert store.get("bad")["status"] == "pending"
    assert store.get("bad")["last_error"] == "HTTPStatusError"


# ---------------------------------------------------------------- config


def _write(tmp_path, section, runtime=None):
    path = tmp_path / "config.json"
    raw = {
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://x/v1", "api_key": "k", "model": "m"},
        "clear_notification": section,
    }
    if runtime:
        raw["runtime"] = runtime
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_gateway_records_events_without_webhook_url(tmp_path):
    config = load_config(_write(tmp_path, {"enabled": True}, {"role": "gateway", "worker_token": "t"})).clear_notification

    assert config.enabled is True and config.webhook_url == ""
    assert CLEAR in config.trigger_paths and len(config.trigger_paths) == 4
    assert config.max_event_age_seconds == 3600


def test_worker_role_requires_webhook_url():
    from src.config import _load_clear_notification

    with pytest.raises(ConfigError, match="worker 角色必须填写 webhook_url"):
        _load_clear_notification({"enabled": True}, runtime_role="worker")
    assert _load_clear_notification({"enabled": True, "webhook_url": "http://10.0.0.5:8080/clear"}, runtime_role="worker").enabled


@pytest.mark.parametrize("section, runtime, match", [
    ({"enabled": True}, None, "local 角色必须填写 webhook_url"),
    ({"enabled": True, "webhook_url": "ftp://x"}, None, "http:// 或 https://"),
    ({"trigger_paths": []}, None, "trigger_paths"),
])
def test_config_validation(tmp_path, section, runtime, match):
    with pytest.raises(ConfigError, match=match):
        load_config(_write(tmp_path, section, runtime))
