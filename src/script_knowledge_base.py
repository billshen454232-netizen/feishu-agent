"""Search the curated, local Muliu script documentation only."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .knowledge_base import KnowledgeSearchResult, KnowledgeSource
from .utils import text_match


@dataclass(frozen=True)
class _Document:
    path: str
    title: str
    text: str
    searchable_text: str


class ScriptKnowledgeBase:
    """Small keyword retriever for knowledge/index.md and knowledge/docs/*.md.

    The source allowlist is intentional: raw script copies and reference spreadsheets
    can contain credentials or internal environment information and must never be
    read by this retriever.
    """

    def __init__(
        self,
        knowledge_path: str | Path,
        min_score: int = 2,
        max_results: int = 3,
        max_context_chars: int = 10000,
    ) -> None:
        self._knowledge_path = Path(knowledge_path)
        self._min_score = min_score
        self._max_results = max_results
        self._max_context_chars = max_context_chars

    def search(self, query: str) -> KnowledgeSearchResult:
        normalized_query = self._normalize(query)
        if not normalized_query:
            return KnowledgeSearchResult(False, "", [])

        documents = self._load_documents()
        weights = self._term_weights(query, documents)
        ranked: list[tuple[int, _Document]] = []
        for document in documents:
            score = self._score(query, normalized_query, document.searchable_text, weights, document.title)
            if score >= self._min_score:
                ranked.append((score, document))

        ranked.sort(key=lambda item: (-item[0], item[1].path))
        selected = ranked[: self._max_results]
        if not selected:
            return KnowledgeSearchResult(False, "", [])

        sources = [
            KnowledgeSource(
                kind="script_knowledge",
                path=document.path,
                title=document.title,
                score=score,
            )
            for score, document in selected
        ]
        context_parts: list[str] = []
        for _score, document in selected:
            part = "[来源: {}]\n{}".format(document.path, document.text.strip())
            candidate = "\n\n".join([*context_parts, part])
            if len(candidate) > self._max_context_chars:
                remaining = self._max_context_chars - len("\n\n".join(context_parts))
                if remaining > 0:
                    context_parts.append(part[:remaining])
                break
            context_parts.append(part)

        context = "\n\n".join(context_parts)
        return KnowledgeSearchResult(bool(context), context, sources)

    def _load_documents(self) -> list[_Document]:
        index_path = self._knowledge_path / "index.md"
        docs_path = self._knowledge_path / "docs"
        paths = [index_path]
        if docs_path.is_dir():
            paths.extend(sorted(docs_path.glob("*.md")))

        aliases = self._index_aliases(index_path)
        documents: list[_Document] = []
        for path in paths:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative_path = path.relative_to(self._knowledge_path).as_posix()
            # index.md 的「关键词」列是该文档的同义说法：并入标题（命中加倍计分）和检索文本。
            title = " ".join(filter(None, [self._title_from_text(text, path.stem), aliases.get(path.name, "")]))
            documents.append(
                _Document(
                    path="knowledge/{}".format(relative_path),
                    title=title,
                    text=text,
                    searchable_text=self._normalize("{} {}".format(title, text)),
                )
            )
        return documents

    @staticmethod
    def _index_aliases(index_path: Path) -> dict[str, str]:
        """``{"control_is_pub.md": "<用途> <关键词>"}`` from the index table rows that link to docs/."""
        if not index_path.is_file():
            return {}
        aliases: dict[str, str] = {}
        for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
            link = re.search(r"\(docs/([^)]+\.md)\)", line)
            if not link or not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            aliases[link.group(1)] = " ".join(cell for cell in cells[:-1] if cell)
        return aliases

    @staticmethod
    def _title_from_text(text: str, fallback: str) -> str:
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return fallback

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[\s`*_#|>\[\](){}]+", "", text.lower())

    @staticmethod
    def _term_weights(query: str, documents: list[_Document]) -> dict[str, float]:
        """Rarer terms count more: weight = ln((N + 1) / (df + 1)) + 0.1, so ubiquitous words like 脚本 barely count."""
        total = len(documents)
        weights: dict[str, float] = {}
        for term in text_match.terms(query):
            normalized = text_match.normalize(term)
            df = sum(1 for document in documents if normalized in document.searchable_text)
            weights[term] = math.log((total + 1) / (df + 1)) + 0.1
        return weights

    @staticmethod
    def _score(
        query: str,
        normalized_query: str,
        searchable_text: str,
        weights: dict[str, float] | None = None,
        title: str = "",
    ) -> int:
        """Whole-query hit scores 8; otherwise matched terms weighted by rarity (×4), title hits doubled.

        Chinese is matched by character bigrams (see ``utils.text_match``) so a
        conversational question shares terms with documents that phrase it differently.
        """
        if normalized_query in searchable_text:
            return 8
        normalized_title = text_match.normalize(title)
        score = 0.0
        for term in text_match.terms(query):
            normalized = text_match.normalize(term)
            if normalized in searchable_text:
                weight = (weights or {}).get(term, 0.5)
                score += weight * (2 if normalized in normalized_title else 1)
        return int(round(score * 4))
