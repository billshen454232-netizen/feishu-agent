#!/usr/bin/env python3
"""Build the read-only browsing site of the team knowledge base (design: docs/superpowers/specs/2026-11-10-team-knowledge-base.md).

    build_site.py <kb_root>

<kb_root>/cases/<组别>/<作者>/<功能>.md   normalized test cases (frontmatter + tables)  — source of truth
<kb_root>/cards/<same relative path>.md  feature card (功能名/一句话/简介/测试关注点/关键词/典型问题), optional
Generates <kb_root>/site/ (docsify pages, README.md home, _sidebar.md) and <kb_root>/index.md (one line per
feature, for retrieval). site/ is fully regenerated; site/_assets and site/index.html are left alone.
Standard library only (runs with the system python3 on the Worker).
"""

from __future__ import annotations

import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

GROUP_ORDER = ["系统组", "赛季组", "国服"]
CARD_FIELDS = ("功能名", "一句话", "简介", "测试关注点", "关键词", "典型问题")
KEEP_IN_SITE = {"_assets", "index.html", ".nojekyll"}
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")  # Obsidian [[page#heading|label]]


def write(path: Path, text: str) -> None:
    """LF line endings on every platform (Path.write_text has no ``newline`` before Python 3.10)."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta: dict = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if key == "levels":
            meta[key] = {k.strip(): int(v) for k, v in re.findall(r"([^{},:]+):\s*(\d+)", value)}
        elif key == "modules":
            meta[key] = re.findall(r'"([^"]*)"', value)
        else:
            meta[key.strip()] = value.strip('"')
    return meta, text[end + 5:]


def parse_card(text: str) -> dict:
    card: dict = {}
    current = None
    for line in text.splitlines():
        head = next((f for f in CARD_FIELDS if line.startswith(f + "：")), None)
        if head:
            current = head
            value = line[len(head) + 1:].strip()
            card[head] = value if head not in ("测试关注点", "典型问题") else []
            if value and head in ("测试关注点", "典型问题"):
                card[head].append(value)
        elif current in ("测试关注点", "典型问题") and line.startswith("- "):
            card[current].append(line[2:].strip())
        elif current == "简介" and line.strip():
            card["简介"] += line.strip()
    return card


def missing(value: str) -> bool:
    return not value or "信息不足" in value


def page_for(rel: Path, meta: dict, card: dict, body: str, stems: dict[str, str]) -> str:
    title = meta.get("title") or rel.stem
    levels = meta.get("levels") or {}
    level_text = "、".join(f"{k} {v}" for k, v in sorted(levels.items())) or "—"
    lines = [f"# {title}", "", f"**组别**：{meta.get('group', '')}　**测试**：{meta.get('owner') or '—'}　"
             f"**用例数**：{meta.get('case_count', '0')}　**分级**：{level_text}", ""]
    if card:
        if not missing(card.get("一句话", "")):
            lines += [f"> {card['一句话']}", ""]
        if not missing(card.get("简介", "")):
            lines += ["## 简介", "", card["简介"], ""]
        focus = [f for f in card.get("测试关注点", []) if not missing(f)]
        if focus:
            lines += ["## 测试关注点", ""] + [f"- {f}" for f in focus] + [""]
        if not missing(card.get("关键词", "")):
            lines += [f"**关键词**：{card['关键词']}", ""]
        questions = [q for q in card.get("典型问题", []) if not missing(q)]
        if questions:
            lines += ["<details><summary>可以这样问</summary>", ""] + [f"- {q}" for q in questions] + ["", "</details>", ""]
    lines += ["## 用例", ""]
    body = re.sub(r"\A\s*# [^\n]*\n", "", body, count=1)  # title already shown above

    def link(match: re.Match) -> str:
        target, label = match.group(1).strip(), (match.group(2) or match.group(1)).strip()
        found = stems.get(target)
        # docsify resolves links from the site root (relativePath: false); unknown targets become plain text
        return f"[{label}]({found})" if found else label

    return "\n".join(lines) + WIKILINK.sub(link, body).rstrip() + "\n"


def build(root: Path) -> dict:
    cases, cards, site = root / "cases", root / "cards", root / "site"
    site.mkdir(exist_ok=True)
    for child in site.iterdir():
        if child.name not in KEEP_IN_SITE:
            shutil.rmtree(child) if child.is_dir() else child.unlink()

    entries = []
    for path in sorted(cases.rglob("*.md")):
        rel = path.relative_to(cases)
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        card_path = cards / rel
        card = parse_card(card_path.read_text(encoding="utf-8")) if card_path.is_file() else {}
        entries.append((rel, meta, card, body))
    stems = {rel.stem: rel.as_posix() for rel, *_ in entries}

    for rel, meta, card, body in entries:
        target = site / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        write(target, page_for(rel, meta, card, body, stems))

    tree: dict = defaultdict(lambda: defaultdict(list))
    for rel, meta, card, _ in entries:
        group = rel.parts[0]
        owner = rel.parts[1] if len(rel.parts) > 2 else ""
        tree[group][owner].append((rel, meta, card))
    groups = sorted(tree, key=lambda g: (GROUP_ORDER.index(g) if g in GROUP_ORDER else 99, g))

    sidebar = ["- [首页](README.md)"]
    for group in groups:
        sidebar.append(f"- {group}")
        for owner in sorted(tree[group]):
            indent = "  "
            if owner:
                sidebar.append(f"  - {owner}")
                indent = "    "
            for rel, meta, _ in sorted(tree[group][owner], key=lambda e: e[0].stem):
                sidebar.append(f"{indent}- [{meta.get('title') or rel.stem}]({rel.as_posix()})")
    write(site / "_sidebar.md", "\n".join(sidebar) + "\n")

    total_cases = sum(int(m.get("case_count") or 0) for _, m, _, _ in entries)
    per_owner = Counter()
    for rel, meta, _, _ in entries:
        if len(rel.parts) > 2:
            per_owner[rel.parts[1]] += int(meta.get("case_count") or 0)
    home = [
        "# 组内项目知识库",
        "",
        "测试组整理的功能用例与经验。左侧按 组别 → 测试 → 功能 浏览，左上角搜索框可全文搜索（第一次搜索需要加载全部内容，稍等几秒）。",
        "",
        f"共 **{len(entries)}** 份资料、**{total_cases}** 条用例。各功能页顶部是摘要卡片（由 AI 根据用例生成，仅供快速了解，以用例原文为准）。",
        "",
        "## 测试与用例数",
        "",
        "| 测试 | 用例数 |",
        "| --- | --- |",
    ] + [f"| {o} | {n} |" for o, n in per_owner.most_common()] + ["", "## 功能一览", ""]
    for group in groups:
        home += [f"### {group}", "", "| 功能 | 测试 | 用例数 | 一句话 |", "| --- | --- | --- | --- |"]
        rows = [e for owner in tree[group].values() for e in owner]
        for rel, meta, card in sorted(rows, key=lambda e: e[0].stem):
            one = card.get("一句话", "")
            home.append(f"| [{meta.get('title') or rel.stem}]({rel.as_posix()}) | {meta.get('owner') or '—'} | "
                        f"{meta.get('case_count', '0')} | {'' if missing(one) else one} |")
        home.append("")
    write(site / "README.md", "\n".join(home))

    index = ["# 组内知识库索引（自动生成，勿手改）", "", "| 路径 | 功能 | 组别 | 测试 | 用例数 | 一句话 | 关键词 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for rel, meta, card, _ in entries:
        index.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            rel.as_posix(), meta.get("title") or rel.stem, rel.parts[0], meta.get("owner") or "",
            meta.get("case_count", "0"), "" if missing(card.get("一句话", "")) else card["一句话"],
            "" if missing(card.get("关键词", "")) else card["关键词"]))
    write(root / "index.md", "\n".join(index) + "\n")
    return {"pages": len(entries), "cards": sum(1 for e in entries if e[2]), "cases": total_cases}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2].strip())
        sys.exit(2)
    print(build(Path(sys.argv[1])))
