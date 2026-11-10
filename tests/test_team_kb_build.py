import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "team_kb_build_site", Path(__file__).resolve().parents[1] / "tools/team_kb/build_site.py"
)
build_site = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_site)

CASE = """---
title: "亲卫队"
group: 系统组
owner: "张宇杰"
source: "nslggameknowledge/系统组用例/系统组用例/张宇杰/亲卫队.md"
case_count: 2
levels: {P0: 1, 未分级: 1}
modules: ["亲卫队"]
---
# 亲卫队
| 模块 | 二级模块 | 分级 | 预期结果 |
|---|---|---|---|
| 亲卫队 | 功能开启 | P0 | 开启 |
详见 [[索引页#二、分季|分季索引]] 与 [[不存在的页]]
"""
CARD = """功能名：亲卫队
一句话：按剧本与霸业条件开启的亲卫队。
简介：（信息不足）
测试关注点：
- 功能开启：剧本限制。
关键词：亲卫队、补兵
典型问题：
- 亲卫队有哪些 P0 用例？
"""


def _kb(tmp_path):
    (tmp_path / "cases/系统组/张宇杰").mkdir(parents=True)
    (tmp_path / "cases/系统组/张宇杰/亲卫队.md").write_text(CASE, encoding="utf-8")
    (tmp_path / "cases/国服").mkdir(parents=True)
    (tmp_path / "cases/国服/索引页.md").write_text("# 索引页\n正文\n", encoding="utf-8")
    (tmp_path / "cards/系统组/张宇杰").mkdir(parents=True)
    (tmp_path / "cards/系统组/张宇杰/亲卫队.md").write_text(CARD, encoding="utf-8")
    (tmp_path / "site/_assets").mkdir(parents=True)
    (tmp_path / "site/index.html").write_text("keep", encoding="utf-8")
    (tmp_path / "site/stale.md").write_text("old", encoding="utf-8")
    return tmp_path


def test_build_renders_card_cases_sidebar_home_and_index(tmp_path):
    kb = _kb(tmp_path)
    result = build_site.build(kb)

    assert result == {"pages": 2, "cards": 1, "cases": 2}
    page = (kb / "site/系统组/张宇杰/亲卫队.md").read_text(encoding="utf-8")
    assert page.startswith("# 亲卫队\n") and page.count("# 亲卫队") == 1
    assert "**测试**：张宇杰" in page and "P0 1、未分级 1" in page
    assert "> 按剧本与霸业条件开启的亲卫队。" in page
    assert "## 简介" not in page  # 「信息不足」 fields are hidden
    assert "[分季索引](国服/索引页.md)" in page and "不存在的页" in page and "[[" not in page
    assert "| 亲卫队 | 功能开启 | P0 | 开启 |" in page
    sidebar = (kb / "site/_sidebar.md").read_text(encoding="utf-8")
    assert "  - 张宇杰\n    - [亲卫队](系统组/张宇杰/亲卫队.md)" in sidebar
    assert sidebar.index("系统组") < sidebar.index("国服")
    assert "| 张宇杰 | 2 |" in (kb / "site/README.md").read_text(encoding="utf-8")
    assert "| 系统组/张宇杰/亲卫队.md | 亲卫队 | 系统组 | 张宇杰 | 2 |" in (kb / "index.md").read_text(encoding="utf-8")


def test_rebuild_keeps_assets_and_entry_but_drops_stale_pages(tmp_path):
    kb = _kb(tmp_path)
    build_site.build(kb)
    assert (kb / "site/index.html").read_text(encoding="utf-8") == "keep"
    assert (kb / "site/_assets").is_dir()
    assert not (kb / "site/stale.md").exists()
