"""Search the curated, local Muliu script documentation only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .knowledge_base import KnowledgeSearchResult, KnowledgeSource


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

        ranked: list[tuple[int, _Document]] = []
        for document in self._load_documents():
            score = self._score(query, normalized_query, document.searchable_text)
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

        documents: list[_Document] = []
        for path in paths:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            relative_path = path.relative_to(self._knowledge_path).as_posix()
            documents.append(
                _Document(
                    path="knowledge/{}".format(relative_path),
                    title=self._title_from_text(text, path.stem),
                    text=text,
                    searchable_text=self._normalize(text),
                )
            )
        return documents

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
    def _score(query: str, normalized_query: str, searchable_text: str) -> int:
        if normalized_query in searchable_text:
            return 8

        terms = re.findall(r"[a-z0-9_.-]+|[一-鿿]{2,}", query.lower())
        score = 0
        for term in terms:
            normalized_term = ScriptKnowledgeBase._normalize(term)
            if len(normalized_term) >= 2 and normalized_term in searchable_text:
                score += 2
        return score
