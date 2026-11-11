#!/usr/bin/env python3
"""Feature cards (design §3.2) written by an external AI after an upload; same rules as the first 133 cards.

The AI sees only a compact summary of the feature (module tree + at most 8 sample cases, P0 first) with every
sensitive value masked. Models live in /opt/team-kb/ai.json (chmod 600, never in git) and are tried in order:

    {"models": [{"base_url": "https://…/v1", "api_key": "…", "model": "…"}, …], "timeout_seconds": 120}

The team-kb service queues a card after every stored upload. One-off use (prints the cards; with --write stores and
commits them — restart team-kb afterwards so its search index picks them up):

    cards.py --root /opt/team-kb --ai-config /opt/team-kb/ai.json --missing --limit 3 [--write]

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site  # noqa: E402
import normalize  # noqa: E402

SKIP = re.compile(r"README|索引")
MAX_SAMPLES = 8
LEVEL_ORDER = {"P0": 0, "P1": 1, "P2": 2}
PROMPT = """你是游戏测试组的资料整理员。我会给你一个「功能用例摘要」，包含：功能名、组别、作者、用例数、模块结构、最多 8 条用例样例。
请输出一张「功能卡片」，供组内知识库检索使用。

严格规则：
1. 只根据给定内容写，不要补充你自己知道的游戏知识，不要编造数值、配置、ID、时间。
2. 不要改写或纠正用例内容；样例只用来理解功能。
3. 信息不足的字段写「（信息不足）」，不要猜。
4. 使用简体中文；术语保持原文写法（如「霸业」「剧本9」「军令」）。
5. 只输出卡片本身，不要任何解释、前言或总结，不要用代码块包裹。
6. 摘要里的 *** 是打码，不要还原或猜测被打码的内容。

卡片格式（字段名和顺序不能改）：

功能名：<与输入一致>
一句话：<这个功能是什么，30 字以内>
简介：<2–3 句：功能的玩法/机制，以及这份用例主要验证哪些方面>
测试关注点：
- <3–6 条，来自模块结构，每条一句>
关键词：<8–15 个，用顿号分隔；包含功能名、主要模块名、测试人员可能使用的同义说法>
典型问题：
- <3 条测试人员可能会问机器人、且能用这份用例回答的问题，例如「XX 开启条件是什么」「XX 有哪些 P0 用例」>
"""


class CardError(RuntimeError):
    pass


def card_input(stored: str) -> Optional[Tuple[str, str]]:
    """``(title, masked summary for the AI)`` of a stored case file, or None when it needs no card."""
    meta, body = build_site.parse_frontmatter(stored)
    title = meta.get("title") or ""
    # Rows split off a multi-line Excel cell (「2.停起服」 in 模块) would fill the module tree with noise.
    records = [r for r in normalize.case_records(body)
               if not normalize.SPILL.match(r.get("模块") or "") and not normalize.SPILL.match(r.get("二级模块") or "")]
    if not title or not records or SKIP.search(title):
        return None
    modules: "OrderedDict[str, OrderedDict]" = OrderedDict()
    for record in records:
        subs = modules.setdefault(record.get("模块") or "（未写模块）", OrderedDict())
        if record.get("二级模块"):
            subs[record["二级模块"]] = None
    levels = {k: v for k, v in (meta.get("levels") or {}).items() if k in normalize.LEVELS or k == "未分级"}
    lines = [
        "# 功能：" + title,
        "- 组别：{}；作者：{}；用例数：{}；分级：{}".format(
            meta.get("group", ""), meta.get("owner") or "—", int(meta.get("case_count") or 0) or len(records),
            "、".join("{} {}".format(k, v) for k, v in sorted(levels.items())) or "—"),
        "",
        "## 模块结构（模块：二级模块）",
    ]
    for module, subs in list(modules.items())[:25]:
        sub = "、".join(list(subs)[:12])
        lines.append("- {}：{}".format(module, sub) if sub else "- " + module)
    lines += ["", "## 用例样例（P0 优先，最多 {} 条）".format(MAX_SAMPLES)]
    seen = set()
    for record in sorted(records, key=lambda r: (LEVEL_ORDER.get(r.get("分级", ""), 3), not r.get("预期结果"))):
        item = record.get("测试项") or record.get("执行步骤") or record.get("前置条件") or ""
        line = "- [{}] {}｜{}｜预期：{}".format(record.get("分级", ""), record.get("二级模块") or record.get("模块", ""),
                                              item[:80], record.get("预期结果", "")[:80])
        if line not in seen:
            seen.add(line)
            lines.append(line)
        if len(seen) >= MAX_SAMPLES:
            break
    return title, normalize.mask_text("\n".join(lines))


def clean_card(content: str, title: str) -> Optional[str]:
    """The model's reply rebuilt in the fixed card layout, or None when a field is missing."""
    text = re.sub(r"```[A-Za-z]*", "", content or "")
    start = text.find("功能名：")
    if start < 0:
        return None
    card = build_site.parse_card(text[start:])
    if not all(card.get(field) for field in ("一句话", "简介", "测试关注点", "关键词")):
        return None
    lines = ["功能名：" + title, "一句话：" + card["一句话"], "简介：" + card["简介"], "测试关注点："]
    lines += ["- " + item for item in card["测试关注点"]]
    lines.append("关键词：" + card["关键词"])
    lines.append("典型问题：")
    lines += ["- " + item for item in card.get("典型问题") or []]
    return "\n".join(lines) + "\n"


class CardAI:
    """OpenAI-compatible chat models from ai.json, tried in order (cards and Feishu answers); errors never contain the key."""

    def __init__(self, models: List[Dict], timeout: float = 120.0) -> None:
        self.models, self.timeout = models, timeout

    @classmethod
    def from_file(cls, path: str) -> Optional["CardAI"]:
        if not path or not Path(path).is_file():
            return None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        models = [m for m in data.get("models") or [] if isinstance(m, dict) and m.get("api_key") and m.get("model")
                  and str(m.get("base_url") or "").startswith(("http://", "https://"))]
        return cls(models, float(data.get("timeout_seconds") or 120)) if models else None

    def complete(self, system: str, user: str, max_tokens: int = 800) -> Tuple[str, str]:
        """``(reply text, model name)`` from the first model that answers; raises CardError when none does."""
        problems = []
        for model in self.models:
            try:
                text = self._chat(model, user, system=system, max_tokens=max_tokens).strip()
            except CardError as exc:
                problems.append("{}: {}".format(model["model"], exc))
                continue
            if text:
                return text, model["model"]
            problems.append("{}: 空回复".format(model["model"]))
        raise CardError("；".join(problems))

    def generate(self, title: str, summary: str) -> Tuple[str, str]:
        """``(card text, model name)``; raises CardError when every model fails."""
        problems = []
        for model in self.models:
            try:
                card = clean_card(self._chat(model, summary), title)
            except CardError as exc:
                problems.append("{}: {}".format(model["model"], exc))
                continue
            if card:
                return card, model["model"]
            problems.append("{}: 输出格式不符".format(model["model"]))
        raise CardError("；".join(problems))

    def _chat(self, model: Dict, user: str, system: str = PROMPT, max_tokens: int = 1500) -> str:
        payload = {"model": model["model"], "temperature": 0.2, "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        request = Request(
            str(model["base_url"]).rstrip("/") + "/chat/completions", method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            # Own User-Agent: Cloudflare-fronted relays reject the default "Python-urllib/x.y" (error 1010).
            headers={"Authorization": "Bearer " + str(model["api_key"]), "Content-Type": "application/json",
                     "User-Agent": "team-kb/1.0"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return str(data["choices"][0]["message"]["content"] or "")
        except HTTPError as exc:
            raise CardError("HTTP {}".format(exc.code)) from None
        except (URLError, OSError) as exc:
            raise CardError(str(getattr(exc, "reason", exc))[:120]) from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise CardError("返回内容无法解析") from None


def commit(root: Path, rels: List[str], model: str) -> None:
    message = "card: {} ({})".format(", ".join(rels[:5]) + (" …" if len(rels) > 5 else ""), model)
    subprocess.run(["git", "add", "-A", "cards", "index.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=team-kb", "-c", "user.email=team-kb@localhost", "commit", "-q",
                    "--author", "AI 卡片 <ai-card@team-kb>", "-m", message], cwd=root, check=True, capture_output=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True)
    parser.add_argument("--ai-config", required=True)
    parser.add_argument("paths", nargs="*", help="case paths under cases/, e.g. 赛季组/张三/浮桥.md")
    parser.add_argument("--missing", action="store_true", help="every case without a card")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--write", action="store_true", help="store the cards, rebuild the site and commit")
    args = parser.parse_args()
    root = Path(args.root)
    ai = CardAI.from_file(args.ai_config)
    if ai is None:
        sys.exit("no usable model in " + args.ai_config)
    rels = list(args.paths)
    if args.missing:
        rels += [p.relative_to(root / "cases").as_posix() for p in sorted((root / "cases").rglob("*.md"))
                 if not (root / "cards" / p.relative_to(root / "cases")).is_file()]
    rels = rels[:args.limit] if args.limit else rels
    written = []
    for rel in rels:
        source = card_input((root / "cases" / rel).read_text(encoding="utf-8"))
        if source is None:
            print("SKIP (no case table / index page):", rel)
            continue
        try:
            card, model = ai.generate(*source)
        except CardError as exc:
            print("FAIL", rel, exc)
            continue
        print("== {} ({})\n{}".format(rel, model, card))
        if args.write:
            target = root / "cards" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            build_site.write(target, card)
            written.append(rel)
    if written:
        build_site.build(root)
        commit(root, written, "cards.py")
        print("written", len(written), "— restart team-kb to reload the search index")


if __name__ == "__main__":
    main()
