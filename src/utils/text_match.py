"""Lightweight lexical matching for short Chinese/English questions.

Chinese has no spaces, so whole runs of Chinese characters are useless as
terms ("对外开服跟普通开服啥不一样" never appears verbatim in a document). Text is
instead split into ASCII words plus Chinese character bigrams, and matching
counts how many of the query's terms a target contains.
"""

from __future__ import annotations

import re

_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9_.\-]*[a-z0-9]|[a-z0-9]")
_CHINESE_RUN = re.compile(r"[一-鿿]+")
_STRIP = re.compile(r"[\s`*_#|>\[\](){}，。、；：？！,.;:?!\"'“”‘’（）【】《》<>~～-]+")

# Bigrams that appear in almost every ops question and carry no topic signal.
STOP_BIGRAMS = frozenset({
    "什么", "怎么", "一下", "可以", "这个", "那个", "现在", "需要", "是否", "如何",
    "服务", "务器", "器的", "的区", "区别", "有什", "么区", "吗", "是不", "不是",
})


def normalize(text: str) -> str:
    """Lower-case and drop whitespace/punctuation (used for exact-question matching)."""
    return _STRIP.sub("", (text or "").lower())


def terms(text: str) -> set[str]:
    """ASCII words (len >= 2) and Chinese character bigrams, minus stop bigrams."""
    lowered = (text or "").lower()
    result = {word for word in _ASCII_WORD.findall(lowered) if len(word) >= 2}
    for run in _CHINESE_RUN.findall(lowered):
        if len(run) == 1:
            continue
        result.update(run[index:index + 2] for index in range(len(run) - 1))
    return result - STOP_BIGRAMS


def coverage(query_terms: set[str], target_terms: set[str]) -> float:
    """Share of the query's terms found in the target (0.0 – 1.0)."""
    if not query_terms:
        return 0.0
    return len(query_terms & target_terms) / len(query_terms)
