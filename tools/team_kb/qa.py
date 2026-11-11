#!/usr/bin/env python3
"""Answer Feishu questions from the team knowledge base (design: docs/superpowers/specs/2026-11-10-team-knowledge-base.md §5).

Retrieval is deterministic (no AI): Chinese bigrams + ASCII words with IDF weights over each feature's title, card
keywords and card text, then the case rows of the best features. Only the answer is written by the external AI, from
a masked context. Questions naming a tester list that tester's features without AI.

    qa.py --root /opt/team-kb --eval                       # retrieval hit rate on the cards' 典型问题
    qa.py --root /opt/team-kb --ask "亲卫队是谁测的" [--ai-config /opt/team-kb/ai.json]

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cards  # noqa: E402
import normalize  # noqa: E402

QUESTION_WORDS = re.compile(
    r"是谁|谁测|谁负责|测的|测过|写的|什么|怎么|如何|哪些|哪个|有没有|是否|需要|注意|之前|一下|可以|能否|会不会|"
    r"分别|用例集|用例|相关|功能|情况|时候|是不是|吗|呢|吧|请问|帮我|查一下|知识库")
FIELD_WEIGHTS = {"title": 4.0, "keywords": 2.0, "body": 1.0}
MAX_FEATURES = 3
RELATIVE_CUTOFF = 0.4
MIN_SCORE_RATIO = 0.9  # of the rarest bigram's IDF: about one distinctive word in the card text
ROW_COVERAGE = 0.6  # row fallback: share of the question's (row-IDF) weight one case row must contain
ROWS_PER_FEATURE = 6
MAX_ROWS = 12
MAX_CONTEXT_CHARS = 6000
MAX_OWNER_LIST = 30
LEVEL_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

ANSWER_PROMPT = """你是游戏测试组的组内知识库助手，根据「资料」回答组员在飞书里的提问。

规则：
1. 只依据资料回答；资料里没有的内容直接说「知识库里没有相关记录」，不要用你自己的游戏知识补充。
2. 第一句直接给结论（不要写「结论：」），再列要点；300 字以内；不要用表格和 Markdown 标题，列表用「- 」开头。
3. 提到具体结论或用例时，在句末注明出处，格式「（功能名 · 测试人姓名）」，例如「（浮桥 用例集 · 金阳）」。
4. 不编造人员、配置、数值、时间；资料里的 *** 是打码，不要还原或猜测。
5. 问「谁测的 / 谁负责」时，回答资料里的测试（作者）；策划、程序信息知识库里没有，直接说明。
6. 资料来自多个功能时，只用和问题相关的部分；都不相关就说没有找到。
7. 资料开头的「已审核答案」是人工确认过的标准答案：与问题相关时优先依据它，与用例资料冲突时以它为准；不相关就忽略。"""

NOT_FOUND = "组内知识库里没有找到和这个问题相关的功能。可以换个说法（带上功能名会更准），或到内网知识库站点搜索。"


def grams(text: str) -> List[str]:
    text = QUESTION_WORDS.sub(" ", (text or "").lower())
    out: List[str] = []
    for run in re.findall(r"[a-z0-9]+|[一-鿿]+", text):
        if run[0].isascii():
            out.append(run)
        elif len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


class QAIndex:
    """Feature-level and row-level retrieval over what SearchIndex already loaded."""

    def __init__(self, features: List[Dict], rows: List[Dict]) -> None:
        self.features = features
        self._grams = []
        df: Counter = Counter()
        for feature in features:
            card = feature.get("card") or {}
            fields = {
                "title": feature["title"].replace("用例集", ""),
                "keywords": card.get("关键词", ""),
                "body": " ".join([card.get("一句话", ""), card.get("简介", ""), " ".join(card.get("测试关注点") or []),
                                  " ".join(feature.get("modules") or [])]),
            }
            field_grams = {name: set(grams(value)) for name, value in fields.items()}
            self._grams.append(field_grams)
            df.update(set().union(*field_grams.values()))
        count = len(features)
        self._idf = {g: math.log((count + 1) / (c + 0.5)) for g, c in df.items()}
        self._min_score = MIN_SCORE_RATIO * math.log((count + 1) / 1.5)
        self._index_by_path = {f["path"]: i for i, f in enumerate(features)}
        self._all_rows = rows
        self._rows: Dict[str, List[Dict]] = {}
        for row in rows:
            self._rows.setdefault(row["path"], []).append(row)
        self.owners = sorted({f["owner"] for f in features if f.get("owner")}, key=len, reverse=True)

    def rank(self, question: str, candidates: Optional[List[int]] = None) -> List[tuple]:
        """``[(score, feature index)]`` best first."""
        query = set(grams(question))
        scored = []
        for index in candidates if candidates is not None else range(len(self.features)):
            fields = self._grams[index]
            score = 0.0
            for g in query:
                weight = max((FIELD_WEIGHTS[name] for name, values in fields.items() if g in values), default=0.0)
                score += self._idf.get(g, 0.0) * weight
            if score:
                scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], self.features[item[1]]["path"]))
        return scored

    def retrieve(self, question: str) -> Dict:
        owner = next((o for o in self.owners if o in question), "")
        rest = question.replace(owner, " ") if owner else question
        candidates = [i for i, f in enumerate(self.features) if f.get("owner") == owner] if owner else None
        ranked = self.rank(rest, candidates)
        best = ranked[0][0] if ranked else 0.0
        picked = [i for score, i in ranked[:MAX_FEATURES] if score >= self._min_score and score >= best * RELATIVE_CUTOFF]
        if not picked:  # details that only the case rows mention (「补兵额度用完后显示什么」)
            ranked = self._row_search(rest, None if candidates is None else {self.features[i]["path"] for i in candidates})
            best = ranked[0][0] if ranked else 0.0
            picked = [i for score, i in ranked[:MAX_FEATURES] if score >= best * RELATIVE_CUTOFF]
        if not picked:
            if owner:
                own = sorted((f for f in self.features if f.get("owner") == owner and f.get("cases")),
                             key=lambda f: f["path"])
                return {"kind": "owner", "owner": owner, "features": own}
            return {"kind": "none", "features": []}
        features = [self.features[i] for i in picked]
        return {"kind": "features", "features": features, "rows": self._pick_rows(rest, features)}

    def _row_search(self, question: str, paths: Optional[set] = None) -> List[tuple]:
        """``[(score, feature index)]`` of features owning a case row that covers most of the question (IDF over rows)."""
        query = {g for g in grams(question) if len(g) > 1}  # single characters (「服」) match almost anything
        rows = self._all_rows if paths is None else [r for r in self._all_rows if r["path"] in paths]
        if not query or not rows:
            return []
        hits = {g: [i for i, row in enumerate(rows) if g in row["_text"]] for g in query}
        weights = {g: math.log((len(rows) + 1) / (len(ids) + 0.5)) for g, ids in hits.items() if ids}
        total = sum(weights.values())  # bigrams that occur nowhere (「后界」) carry no information
        scores: Counter = Counter()
        matched: Counter = Counter()
        for g, ids in hits.items():
            for i in ids:
                scores[i] += weights.get(g, 0.0)
                matched[i] += 1
        need = max(2, math.ceil(len(query) / 2))  # and at least half of the question's words in the same row
        best: Dict[str, float] = {}
        for i, score in scores.items():
            if total and score >= ROW_COVERAGE * total and matched[i] >= need:
                path = rows[i]["path"]
                best[path] = max(best.get(path, 0.0), score)
        ranked = [(score, self._index_by_path[path]) for path, score in best.items() if path in self._index_by_path]
        ranked.sort(key=lambda item: (-item[0], self.features[item[1]]["path"]))
        return ranked

    def _pick_rows(self, question: str, features: List[Dict]) -> List[Dict]:
        query = set(grams(question))
        chosen: List[Dict] = []
        for feature in features:
            rows = self._rows.get(feature["path"], [])
            scored = []
            for row in rows:
                text = row["_text"]
                score = sum(self._idf.get(g, 1.0) for g in query if g in text)
                scored.append((score, LEVEL_ORDER.get(row.get("level", ""), 9), row))
            hits = [item for item in scored if item[0] > 0]
            if hits:  # rows matching the question, P0 first among equals
                hits.sort(key=lambda item: (-item[0], item[1]))
            else:  # a general question about the feature: its most important cases
                hits = sorted(scored, key=lambda item: item[1])
            chosen.extend(row for _, _, row in hits[:ROWS_PER_FEATURE])
        return chosen[:MAX_ROWS]


def build_context(retrieval: Dict) -> str:
    parts = []
    for number, feature in enumerate(retrieval["features"], 1):
        card = feature.get("card") or {}
        lines = ["【功能 {}】{}（{} · 测试 {} · {} 条用例）".format(
            number, feature["title"], feature.get("group", ""), feature.get("owner") or "—", feature.get("cases", 0))]
        for name in ("一句话", "简介", "关键词"):
            if card.get(name) and "信息不足" not in card[name]:
                lines.append("{}：{}".format(name, card[name]))
        if card.get("测试关注点"):
            lines.append("测试关注点：" + "；".join(card["测试关注点"]))
        rows = [r for r in retrieval["rows"] if r["path"] == feature["path"]]
        if rows:
            lines.append("相关用例：")
            for row in rows:
                lines.append("- [{}] {}｜{}｜预期：{}".format(row.get("level") or "未分级", row.get("module", ""),
                                                          row.get("item", "")[:120], row.get("expected", "")[:120]))
        parts.append("\n".join(lines))
    return normalize.mask_text("\n\n".join(parts))[:MAX_CONTEXT_CHARS]


def source(feature: Dict) -> Dict:
    return {k: feature.get(k, "") for k in ("title", "group", "owner", "path")}


def reference_context(references: List[Dict]) -> str:
    """Reviewed answers the Gateway sent along (admin-corrected 「补丁」, design §5.8)."""
    return normalize.mask_text("\n\n".join(
        "【已审核答案 #{}{}】\n问：{}\n答：{}".format(
            r.get("id", ""), "（归档后相关用例又更新过，可能过时：与下面的用例冲突时以用例为准）" if r.get("stale") else "",
            r.get("question", ""), r.get("answer", ""))
        for r in references))[:3000]


def answer(question: str, index: QAIndex, ai: Optional["cards.CardAI"], references: Optional[List[Dict]] = None) -> Dict:
    """``{ok, text, sources, model, elapsed_ms}`` for the Gateway; text is already masked."""
    started = time.perf_counter()
    question = (question or "").strip()[:500]
    references = [r for r in (references or []) if isinstance(r, dict) and r.get("answer")][:3]
    retrieval = index.retrieve(question)
    result: Dict = {"ok": True, "sources": [], "model": ""}
    if references and retrieval["kind"] != "features":  # only reviewed answers fit: let the AI judge their relevance
        retrieval = {"kind": "features", "features": [], "rows": []}
    if retrieval["kind"] == "none":
        result["text"] = NOT_FOUND
    elif retrieval["kind"] == "owner":
        own = retrieval["features"]
        lines = ["{} 在知识库里有 {} 份用例：".format(retrieval["owner"], len(own))]
        lines += ["- {}（{}，{} 条）".format(f["title"], f.get("group", ""), f.get("cases", 0)) for f in own[:MAX_OWNER_LIST]]
        if len(own) > MAX_OWNER_LIST:
            lines.append("- …另有 {} 份，可在内网知识库站点按作者查看".format(len(own) - MAX_OWNER_LIST))
        result["text"] = "\n".join(lines)
    else:
        result["sources"] = [source(f) for f in retrieval["features"]]
        context = "\n\n".join(part for part in (reference_context(references), build_context(retrieval)) if part)
        text = ""
        if ai is not None:
            try:
                text, result["model"] = ai.complete(ANSWER_PROMPT, "资料：\n{}\n\n问题：{}".format(context, question))
            except cards.CardError as exc:
                result["error"] = str(exc)[:300]
        if text:  # list only the features the answer actually used
            used = [s for s in result["sources"] if s["title"].replace("用例集", "").strip() in text]
            result["sources"] = used or result["sources"]
        if not text and references and not retrieval["features"]:
            text = "AI 暂时不可用，以下是最接近的已审核答案（问答 #{}）：\n{}".format(references[0].get("id", ""),
                                                                    references[0]["answer"])
        elif not text:  # no AI or it failed: still point to the right features
            text = "AI 暂时不可用，先列出最相关的功能：\n" + "\n".join(
                "- {}（{} · {}）{}".format(f["title"], f.get("group", ""), f.get("owner") or "—",
                                          "：" + f["card"]["一句话"] if (f.get("card") or {}).get("一句话") else "")
                for f in retrieval["features"])
        result["text"] = text
    result["text"] = normalize.mask_text(result["text"])[:3000]
    result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def evaluate(index: QAIndex) -> Dict:
    total = top1 = top3 = 0
    misses = []
    for feature in index.features:
        for question in (feature.get("card") or {}).get("典型问题") or []:
            total += 1
            got = index.retrieve(question)
            paths = [f["path"] for f in got["features"]] if got["kind"] == "features" else []
            top1 += bool(paths) and paths[0] == feature["path"]
            top3 += feature["path"] in paths
            if feature["path"] not in paths:
                misses.append((question, feature["path"], paths[:2]))
    return {"questions": total, "top1": top1 / max(total, 1), "top3": top3 / max(total, 1), "misses": misses}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True)
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--ask", action="append", default=[])
    parser.add_argument("--ai-config", default="")
    args = parser.parse_args()
    import server  # noqa: E402 - the same loader the service uses
    search = server.SearchIndex(Path(args.root))
    search.load()
    if args.eval:
        report = evaluate(search.qa)
        print("questions {questions}  top1 {top1:.1%}  top3 {top3:.1%}".format(**report))
        for question, want, got in report["misses"][:20]:
            print("MISS", question, "| want", want, "| got", got)
    ai = cards.CardAI.from_file(args.ai_config) if args.ai_config else None
    for question in args.ask:
        result = answer(question, search.qa, ai)
        print("Q:", question)
        print(result["text"])
        print("sources:", [s["title"] for s in result["sources"]], "model:", result["model"], "ms:", result["elapsed_ms"])
        print()


if __name__ == "__main__":
    main()
