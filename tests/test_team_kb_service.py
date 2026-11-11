import base64
import importlib.util
import sys
from pathlib import Path

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


def test_upload_rejects_bad_input_without_storing(tmp_path):
    kb = _kb(tmp_path)
    assert kb.upload({"uploader": "", "group": "赛季组", "files": [{"name": "a.md", "content": _b64("x")}]})["ok"] is False
    result = kb.upload({"uploader": "沈涛", "group": "赛季组", "files": [
        {"name": "../evil.md", "content": _b64("x")},
        {"name": "a.txt", "content": _b64("x")},
        {"name": "密码.md", "content": _b64("登录密码：hunter22")},
    ]})
    assert [f["status"] for f in result["files"]] == ["rejected"] * 3
    assert "敏感信息" in result["files"][2]["reasons"][0] and "hunter22" not in result["files"][2]["reasons"][0]
    assert list((tmp_path / "cases").rglob("*.md")) == []


def test_search_ranks_full_matches_and_falls_back_to_partial_bigrams(tmp_path):
    kb = _kb(tmp_path)
    kb.upload({"uploader": "沈涛", "group": "赛季组", "owner": "唐黎艳",
               "files": [{"name": "浮桥用例集.md", "content": _b64(TABLE)}]})
    assert kb.index.search("提示需要同盟")["rows"][0]["expected"] == "提示需要同盟"
    assert kb.index.search("需要同盟了吗")["rows"]  # 60% of the bigrams match
    assert kb.index.search("完全无关的词")["rows"] == []
