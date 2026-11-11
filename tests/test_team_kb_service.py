import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[1] / "tools/team_kb"
sys.path.insert(0, str(_TOOLS))
_SPEC = importlib.util.spec_from_file_location("team_kb_server", _TOOLS / "server.py")
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)
normalize = server.normalize

TABLE = """---
负责人: 唐黎艳
状态: 待补充
---
# 浮桥 用例集
| 模块 | 二级 | 分级 | 前置条件 | 执行步骤 | 预期结果 | 实际结果 | 备注 |
|---|---|---|---|---|---|---|---|
| 浮桥 | 建造 | P0 | 有同盟 | 点击建造浮桥 | 浮桥开始建造 |  |  |
|  |  |  | 无同盟 | 点击建造 | 提示需要同盟 |  |  |
| 浮桥 | 拆除 | p1 | 已建造 | 拆除浮桥 | 木材×9000\\|铁矿×9000 |  |  |
"""


def _b64(text, encoding="utf-8"):
    return base64.b64encode(text.encode(encoding)).decode()


def test_normalize_fills_merged_cells_marks_unrated_and_keeps_properties():
    stored, summary = normalize.normalize(TABLE, title_fallback="x", group="赛季组", owner="唐黎艳", extra_meta={"uploaded_by": "沈涛"})
    assert summary == {"title": "浮桥 用例集", "cases": 3, "unrated": 1, "warnings": []}
    assert "> 原文档属性：负责人: 唐黎艳；状态: 待补充" in stored
    assert "| 模块 | 二级模块 | 分级 | 前置条件 | 执行步骤 | 预期结果 | 实际结果 | 备注 |" in stored
    assert "| 浮桥 | 建造 | 未分级 | 无同盟 | 点击建造 | 提示需要同盟 |  |  |" in stored
    assert "| 浮桥 | 拆除 | P1 | 已建造 | 拆除浮桥 | 木材×9000\\|铁矿×9000 |  |  |" in stored
    assert 'uploaded_by: "沈涛"' in stored and "levels: {P0: 1, P1: 1, 未分级: 1}" in stored


def test_sensitive_values_are_found_but_versions_and_tapd_links_are_not():
    assert normalize.sensitive_hits("版本 1.2.3.4，见 https://www.tapd.cn/123") == []
    hits = normalize.sensitive_hits("服务器 10.20.30.40\n密码：abc123\n联系 13812345678")
    assert [h.split("：")[0] for h in hits] == ["IP 地址", "密码", "手机号"]


def test_spilled_multiline_cells_are_warned():
    text = TABLE + "| 2.点击前往 |  |  |  |  | x |  |  |\n"
    _, summary = normalize.normalize(text, title_fallback="x", group="赛季组", owner="a", extra_meta={})
    assert any("单元格内换行" in w for w in summary["warnings"])


def _kb(tmp_path):
    for name in ("cases", "cards", "site"):
        (tmp_path / name).mkdir()
    (tmp_path / "site/index.html").write_text("keep", encoding="utf-8")
    kb = server.KnowledgeBase(tmp_path)
    kb.index.load()
    return kb


def test_upload_stores_rebuilds_and_becomes_searchable(tmp_path):
    kb = _kb(tmp_path)
    result = kb.upload({"uploader": "沈涛", "group": "赛季组", "owner": "唐黎艳",
                        "files": [{"name": "浮桥用例集.md", "content": _b64(TABLE, "gbk")}]})

    assert result["ok"] and result["files"][0]["status"] == "added"
    assert result["files"][0]["path"] == "赛季组/唐黎艳/浮桥用例集.md"
    assert "暂无摘要卡片" in result["files"][0]["warnings"]
    assert (tmp_path / "site/赛季组/唐黎艳/浮桥用例集.md").is_file()
    assert (tmp_path / "site/index.html").read_text(encoding="utf-8") == "keep"
    assert list((tmp_path / "inbox").iterdir())[0].name.endswith("-沈涛")

    found = kb.index.search("同盟 建造")
    assert found["features"] == [] or found["features"][0]["title"] == "浮桥 用例集"
    assert found["rows"][0]["item"] == "点击建造浮桥" and found["rows"][0]["level"] == "P0"
    assert kb.index.search("浮桥")["features"][0]["path"] == "赛季组/唐黎艳/浮桥用例集.md"

    again = kb.upload({"uploader": "沈涛", "group": "赛季组", "owner": "唐黎艳",
                       "files": [{"name": "浮桥用例集.md", "content": _b64(TABLE)}]})
    assert again["files"][0]["status"] == "unchanged"


def test_upload_rejects_bad_names_and_holds_sensitive_files_for_review(tmp_path):
    kb = _kb(tmp_path)
    assert kb.upload({"uploader": "", "group": "赛季组", "files": [{"name": "a.md", "content": _b64("x")}]})["ok"] is False
    result = kb.upload({"uploader": "沈涛", "group": "赛季组", "files": [
        {"name": "../evil.md", "content": _b64("x")},
        {"name": "a.txt", "content": _b64("x")},
        {"name": "密码.md", "content": _b64("# 登录\n登录密码：hunter22\n")},
    ]})
    assert [f["status"] for f in result["files"]] == ["rejected", "rejected", "held"]
    held = result["files"][2]
    assert "管理员审核" in held["warnings"][0] and "hunter22" not in json.dumps(result, ensure_ascii=False)
    assert list((tmp_path / "cases").rglob("*.md")) == []
    meta = kb.unreported()[0]
    assert meta["id"] == held["review_id"] and meta["hits"][0]["context"] == "登录密码：***22"
    assert "hunter22" in kb.pending_text(held["review_id"])  # full text only through the intranet link


def _held(kb):
    result = kb.upload({"uploader": "沈涛", "group": "赛季组", "owner": "唐黎艳",
                        "files": [{"name": "浮桥用例集.md", "content": _b64(TABLE + "测试服 10.20.30.40\n")}]})
    return result["files"][0]["review_id"]


def test_approved_review_is_stored_committed_and_searchable(tmp_path):
    kb = _kb(tmp_path)
    review_id = _held(kb)
    assert kb.apply_decision({"id": review_id, "action": "approve", "operator": "boss"}) == (True, "已入库（新增）：赛季组/唐黎艳/浮桥用例集.md")
    assert (tmp_path / "cases/赛季组/唐黎艳/浮桥用例集.md").is_file()
    assert (tmp_path / "pending/_done" / review_id).is_dir() and kb.unreported() == []
    assert kb.index.search("浮桥")["features"]
    assert kb.apply_decision({"id": review_id, "action": "approve"}) == (True, "此前已处理")


def test_rejected_review_is_archived_without_storing(tmp_path):
    kb = _kb(tmp_path)
    review_id = _held(kb)
    assert kb.apply_decision({"id": review_id, "action": "reject"}) == (True, "已拒绝，未入库")
    assert list((tmp_path / "cases").rglob("*.md")) == []
    assert (tmp_path / "pending/_rejected" / review_id).is_dir()
    assert kb.apply_decision({"id": "20990101-000000-00000000", "action": "approve"}) == (False, "内网找不到这个待审核文件")
    assert kb.apply_decision({"id": "../../etc", "action": "approve"})[0] is False


def test_gateway_sync_reports_once_and_applies_decisions(tmp_path):
    kb = _kb(tmp_path)
    review_id = _held(kb)
    calls = []

    def fake_call(method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/internal/team-kb/decisions":
            return {"items": [{"id": review_id, "action": "reject", "operator": "boss"}]}
        return {}

    sync = server.GatewaySync(kb, "https://gateway.example", "token")
    sync._call = fake_call
    sync.sync_once()
    reported = calls[0][2]["items"][0]
    assert calls[0][1] == "/internal/team-kb/reviews" and reported["id"] == review_id
    assert "summary" not in reported and "10.20.30.40" not in json.dumps(reported, ensure_ascii=False)
    assert calls[-1] == ("POST", f"/internal/team-kb/reviews/{review_id}/applied", {"ok": True, "message": "已拒绝，未入库"})
    calls.clear()
    sync.sync_once()
    assert [c[1] for c in calls] == ["/internal/team-kb/decisions", f"/internal/team-kb/reviews/{review_id}/applied"]


cards = server.cards
REPLY = """好的，卡片如下：
```
=== 浮桥用例集.md ===
功能名：浮桥
一句话：同盟可建造和拆除的浮桥。
简介：浮桥需要同盟才能建造。
用例验证建造与拆除。
测试关注点：
- 建造：有同盟才能建造。
- 拆除：拆除返还资源。
关键词：浮桥、同盟、建造、拆除
典型问题：
- 浮桥建造需要什么条件？
```
以上。"""


def test_card_input_is_masked_compact_and_p0_first():
    title, summary = cards.card_input(normalize.normalize(TABLE + "| 浮桥 | 运维 | P2 | 无 | 登录 10.20.30.40 重启 | 正常 |  |  |\n",
                                                          title_fallback="x", group="赛季组", owner="唐黎艳", extra_meta={})[0])
    assert title == "浮桥 用例集"
    assert "- 浮桥：建造、拆除、运维" in summary and "用例数：4" in summary
    assert summary.index("[P0]") < summary.index("[P1]") < summary.index("[P2]")
    assert "10.20.30.40" not in summary and "10.***40" in summary
    assert cards.card_input("---\ntitle: \"README\"\n---\n| 预期结果 |\n|---|\n| x |\n") is None
    assert cards.card_input("---\ntitle: \"说明\"\n---\n只有文字\n") is None


def test_card_input_skips_spilled_rows_and_counts_rows_when_the_header_was_not_recognised():
    spilled = TABLE + "| 2.停起服 | 数据存盘 |  |  |  | x |  |  |\n| （第4天中午12点前） | 初始状态 | ui |  | 点击 | y |  |  |\n"
    _, summary = cards.card_input(normalize.normalize(spilled, title_fallback="x", group="赛季组", owner="a", extra_meta={})[0])
    assert "停起服" not in summary and "第4天" not in summary and "- 浮桥：建造、拆除" in summary

    odd = ('---\ntitle: "弱网"\ncase_count: 0\nlevels: {未分级: 2, 快速切换: 1}\n---\n| 模块 | 列B | 预期结果 |\n|---|---|---|\n'
           '| 登录 | 切服 |  |\n| 登录 | 切服 | 正常切换服务器 |\n| 登录 | 切服 | 正常切换服务器 |\n')
    _, summary = cards.card_input(odd)
    assert "用例数：3" in summary and "分级：未分级 2\n" in summary and "- 登录\n" in summary
    samples = summary.split("最多 8 条）\n")[1].splitlines()
    assert samples == ["- [] 登录｜｜预期：正常切换服务器", "- [] 登录｜｜预期："]  # filled first, duplicates once


def test_clean_card_rebuilds_layout_and_rejects_incomplete_replies():
    card = cards.clean_card(REPLY, "浮桥 用例集")
    assert card.startswith("功能名：浮桥 用例集\n一句话：同盟可建造和拆除的浮桥。\n简介：浮桥需要同盟才能建造。用例验证建造与拆除。\n")
    assert "```" not in card and "===" not in card and "以上" not in card
    assert card.endswith("典型问题：\n- 浮桥建造需要什么条件？\n")
    assert cards.clean_card("功能名：浮桥\n一句话：x\n", "浮桥") is None
    assert cards.clean_card("对不起，我无法处理", "浮桥") is None


def test_card_ai_falls_back_to_the_next_model_and_reports_without_keys(tmp_path):
    ai = cards.CardAI([{"model": "a", "api_key": "sk-secret", "base_url": "https://x"},
                       {"model": "b", "api_key": "sk-secret", "base_url": "https://x"}])
    replies = {"a": cards.CardError("HTTP 502"), "b": REPLY}

    def fake_chat(model, summary):
        reply = replies[model["model"]]
        if isinstance(reply, Exception):
            raise reply
        return reply

    ai._chat = fake_chat
    assert ai.generate("浮桥", "摘要")[1] == "b"
    replies["b"] = "不知道"
    with pytest.raises(cards.CardError) as error:
        ai.generate("浮桥", "摘要")
    assert str(error.value) == "a: HTTP 502；b: 输出格式不符" and "sk-secret" not in str(error.value)

    assert cards.CardAI.from_file(str(tmp_path / "none.json")) is None
    (tmp_path / "ai.json").write_text(json.dumps({"models": [{"model": "m", "api_key": "", "base_url": "https://x"}]}), encoding="utf-8")
    assert cards.CardAI.from_file(str(tmp_path / "ai.json")) is None


class FakeAI:
    def __init__(self, before_return=None):
        self.calls, self.before_return = [], before_return

    def generate(self, title, summary):
        self.calls.append(summary)
        if self.before_return:
            self.before_return()
        return cards.clean_card(REPLY, title), "fake-model"


def test_upload_queues_an_ai_card_that_becomes_searchable(tmp_path):
    kb = _kb(tmp_path)
    kb.cards = server.CardWorker(kb, FakeAI())
    queued = []
    kb.cards.enqueue = queued.append  # run it by hand instead of in the thread
    result = kb.upload({"uploader": "沈涛", "group": "赛季组", "owner": "唐黎艳",
                        "files": [{"name": "浮桥用例集.md", "content": _b64(TABLE)}]})
    assert queued == ["赛季组/唐黎艳/浮桥用例集.md"]
    assert any("AI 在后台生成" in w for w in result["files"][0]["warnings"])
    assert kb.index.search("同盟可建造")["features"] == []

    assert kb.cards.process(queued[0]) == "written by fake-model"
    assert (tmp_path / "cards/赛季组/唐黎艳/浮桥用例集.md").read_text(encoding="utf-8").startswith("功能名：浮桥 用例集\n")
    assert kb.index.search("浮桥")["features"][0]["summary"] == "同盟可建造和拆除的浮桥。"
    assert "> 同盟可建造和拆除的浮桥。" in (tmp_path / "site/赛季组/唐黎艳/浮桥用例集.md").read_text(encoding="utf-8")


def test_card_is_dropped_when_the_case_changed_during_the_ai_call(tmp_path):
    kb = _kb(tmp_path)
    kb.upload({"uploader": "沈涛", "group": "赛季组", "owner": "唐黎艳",
               "files": [{"name": "浮桥用例集.md", "content": _b64(TABLE)}]})
    case = tmp_path / "cases/赛季组/唐黎艳/浮桥用例集.md"
    worker = server.CardWorker(kb, FakeAI(before_return=lambda: case.write_text(case.read_text(encoding="utf-8") + "\n新内容\n", encoding="utf-8")))
    assert worker.process("赛季组/唐黎艳/浮桥用例集.md").startswith("skipped: case changed")
    assert not (tmp_path / "cards/赛季组/唐黎艳/浮桥用例集.md").exists()
    assert worker.process("赛季组/唐黎艳/没有.md") == "skipped: case file gone"


def test_search_ranks_full_matches_and_falls_back_to_partial_bigrams(tmp_path):
    kb = _kb(tmp_path)
    kb.upload({"uploader": "沈涛", "group": "赛季组", "owner": "唐黎艳",
               "files": [{"name": "浮桥用例集.md", "content": _b64(TABLE)}]})
    assert kb.index.search("提示需要同盟")["rows"][0]["expected"] == "提示需要同盟"
    assert kb.index.search("需要同盟了吗")["rows"]  # 60% of the bigrams match
    assert kb.index.search("完全无关的词")["rows"] == []
