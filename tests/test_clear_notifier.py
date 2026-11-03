import json

import httpx
import pytest

from src.clear_notifier import EVENT_EXECUTING, EVENT_PREPARED, ClearNotifier
from src.config import ClearNotificationConfig, ConfigError, load_config
from src.muliu_plan import MuliuPlan, MuliuStep

CLEAR = "/home/serverGeneralScript/clear"
WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key"
PLAN = MuliuPlan(
    summary="国服2213清档并启服",
    steps=[
        MuliuStep(CLEAR, ["2213"], "清档 2213"),
        MuliuStep("/home/serverGeneralScript/start", ["2213"], "启服 2213"),
    ],
)


def _notifier(handler=None, **overrides):
    config = ClearNotificationConfig(enabled=True, webhook_url=WEBHOOK, **overrides)
    transport = httpx.MockTransport(handler or (lambda request: httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})))
    return ClearNotifier(config, http_client=httpx.AsyncClient(transport=transport))


def test_only_registered_clear_paths_trigger():
    notifier = _notifier()

    assert [step.args for step in notifier.clear_steps(PLAN)] == [["2213"]]
    assert notifier.clear_steps(MuliuPlan(summary="s", steps=[MuliuStep("/home/serverGeneralScript/start", ["1"], "d")])) == []


def test_text_for_both_events():
    prepared = ClearNotifier.build_text(EVENT_PREPARED, operator_name="沈涛", server_ids=["2213"], plan_summary="国服2213清档并启服", now=1794016800)
    executing = ClearNotifier.build_text(EVENT_EXECUTING, operator_name="沈涛", server_ids=["2213", "2018"], plan_summary="s", now=1794016800)

    assert prepared.splitlines() == [
        "内网：沈涛 准备清档 2213 服（已生成计划，等待确认）",
        "计划：国服2213清档并启服",
        "时间：2026-11-07 10:00:00",
    ]
    assert executing.splitlines()[0] == "内网：沈涛 正在操作清档 2213、2018 服（已确认，开始执行）"


@pytest.mark.asyncio
async def test_send_posts_wecom_text_with_mention_all():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    assert await _notifier(handler).send("内网：沈涛 正在操作清档 2213 服") is True

    assert str(seen[0].url) == WEBHOOK
    assert json.loads(seen[0].content) == {
        "msgtype": "text",
        "text": {"content": "内网：沈涛 正在操作清档 2213 服", "mentioned_mobile_list": ["@all"]},
    }


@pytest.mark.asyncio
async def test_mention_all_can_be_turned_off():
    seen = []
    notifier = _notifier(lambda r: seen.append(r) or httpx.Response(200, json={"errcode": 0}), mention_all=False)

    await notifier.send("t")

    assert json.loads(seen[0].content)["text"] == {"content": "t"}


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid webhook url"}),
    httpx.Response(500),
])
async def test_send_failure_returns_false_without_raising(response):
    assert await _notifier(lambda request: response).send("t") is False


@pytest.mark.asyncio
async def test_disabled_notifier_never_sends():
    seen = []
    notifier = ClearNotifier(
        ClearNotificationConfig(enabled=False, webhook_url=WEBHOOK),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))),
    )

    assert await notifier.send("t") is False
    assert seen == []


def _write(tmp_path, section):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://x/v1", "api_key": "k", "model": "m"},
        "clear_notification": section,
    }), encoding="utf-8")
    return path


@pytest.mark.parametrize("section, match", [
    ({"enabled": True}, "必须填写 webhook_url"),
    ({"enabled": True, "webhook_url": "ftp://x"}, "http:// 或 https://"),
    ({"trigger_paths": []}, "trigger_paths"),
])
def test_config_validation(tmp_path, section, match):
    with pytest.raises(ConfigError, match=match):
        load_config(_write(tmp_path, section))


def test_config_defaults(tmp_path):
    config = load_config(_write(tmp_path, {"enabled": True, "webhook_url": WEBHOOK})).clear_notification

    assert config.mention_all is True
    assert CLEAR in config.trigger_paths and len(config.trigger_paths) == 4
