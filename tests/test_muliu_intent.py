import pytest

from src.muliu_intent import MuliuIntentRouter, MuliuRequestIntent


@pytest.mark.parametrize(
    ("user_text", "expected_hint"),
    [
        ("我要怎么查询某个服务器现在的 Patch 列表？", MuliuRequestIntent.OPERATION),
        ("查询 2003 服当前 Patch 列表", MuliuRequestIntent.OPERATION),
        ("查看 2003 服基础信息", MuliuRequestIntent.OPERATION),
        ("6000服当前剧本怎么查？", MuliuRequestIntent.OPERATION),
        ("检查 5000 服起服失败原因", MuliuRequestIntent.OPERATION),
        ("查询 Patch 列表", MuliuRequestIntent.OPERATION),
        ("这个脚本的风险是什么", MuliuRequestIntent.KNOWLEDGE),
        ("随便聊聊", MuliuRequestIntent.UNCERTAIN),
    ],
)
def test_router_returns_only_observability_hint(user_text, expected_hint):
    router = MuliuIntentRouter()

    assert router.route(user_text) is expected_hint


def test_question_mark_cannot_turn_router_into_execution_authority():
    router = MuliuIntentRouter()

    assert router.route("2003 服 Patch 列表怎么查询？") is MuliuRequestIntent.OPERATION
