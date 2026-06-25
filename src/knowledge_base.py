from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KnowledgeSource:
    kind: str
    path: str
    title: str
    score: int


@dataclass(frozen=True)
class KnowledgeSearchResult:
    hit: bool
    context: str
    sources: list[KnowledgeSource]


class DualChainVaultKnowledgeBase:
    def __init__(self, vault_path: str | Path, min_score: int = 3, max_results: int = 5, max_context_chars: int = 12000) -> None:
        self.vault_path = Path(vault_path)
        self.min_score = min_score
        self.max_results = max_results
        self.max_context_chars = max_context_chars
        self.articles = self._load_json("indexes/articles.json")
        self.concepts = self._load_json("indexes/concepts.json")
        self.qa = self._load_json("indexes/qa.json")
        self._articles_by_id = {str(item.get("id")): item for item in self.articles}

    def search(self, query: str) -> KnowledgeSearchResult:
        query = query.strip()
        if not query:
            return KnowledgeSearchResult(False, "", [])

        candidates: list[KnowledgeSource] = []
        candidates.extend(self._search_qa(query))
        candidates.extend(self._search_concepts(query))
        candidates.extend(self._search_articles(query))
        candidates = self._dedupe_sources(sorted(candidates, key=lambda source: source.score, reverse=True))
        candidates = [source for source in candidates if source.score >= self.min_score][: self.max_results]
        if not candidates:
            return KnowledgeSearchResult(False, "", [])

        context_parts: list[str] = []
        seen_paths: set[str] = set()
        for source in candidates:
            if source.path in seen_paths:
                continue
            seen_paths.add(source.path)
            text = self._read_text(source.path)
            if not text:
                continue
            context_parts.append(f"[来源: {source.path}]\n{text.strip()}")
            if len("\n\n".join(context_parts)) >= self.max_context_chars:
                break

        context = "\n\n".join(context_parts)[: self.max_context_chars]
        return KnowledgeSearchResult(bool(context), context, candidates)

    def _load_json(self, relative_path: str) -> list[dict[str, Any]]:
        path = self.vault_path / relative_path
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    def _search_qa(self, query: str) -> list[KnowledgeSource]:
        results: list[KnowledgeSource] = []
        for item in self.qa:
            if item.get("status", "active") != "active":
                continue
            text = " ".join(str(item.get(key, "")) for key in ("title", "question", "summary"))
            text += " " + " ".join(item.get("concepts", []))
            score = self._score(query, text) + 1
            if score > 1 and item.get("path"):
                results.append(KnowledgeSource("qa", str(item["path"]), str(item.get("title") or item.get("question") or item["path"]), score))
                for source_path in item.get("source_paths", []):
                    results.append(KnowledgeSource("article", str(source_path), str(source_path), score))
        return results

    def _search_concepts(self, query: str) -> list[KnowledgeSource]:
        results: list[KnowledgeSource] = []
        for item in self.concepts:
            names = [str(item.get("concept", "")), *[str(alias) for alias in item.get("aliases", [])]]
            score = max((self._score(query, name) for name in names), default=0) + 1
            if score <= 1:
                continue
            concept_path = f"concepts/{item.get('concept')}.md"
            results.append(KnowledgeSource("concept", concept_path, str(item.get("concept")), score))
            for article_id in item.get("article_ids", []):
                article = self._articles_by_id.get(str(article_id))
                if article and article.get("path"):
                    results.append(KnowledgeSource("article", str(article["path"]), str(article.get("title") or article["path"]), max(score - 1, 1)))
        return results

    def _search_articles(self, query: str) -> list[KnowledgeSource]:
        results: list[KnowledgeSource] = []
        for item in self.articles:
            if item.get("status", "active") != "active":
                continue
            text = " ".join(str(item.get(key, "")) for key in ("title", "summary"))
            text += " " + " ".join(item.get("concepts", []))
            score = self._score(query, text)
            if score > 0 and item.get("path"):
                results.append(KnowledgeSource("article", str(item["path"]), str(item.get("title") or item["path"]), score))
        return results

    @staticmethod
    def _score(query: str, text: str) -> int:
        if not text:
            return 0
        score = 0
        lowered_query = query.lower()
        lowered_text = text.lower()
        if lowered_query in lowered_text:
            score += 3
        compact_query = re.sub(r"[\s的是了]+", "", lowered_query)
        compact_text = re.sub(r"[\s的是了]+", "", lowered_text)
        if compact_query and compact_query in compact_text:
            score += 3
        stripped_text = compact_text.strip()
        if 2 <= len(stripped_text) <= 50 and stripped_text in compact_query:
            score += 2
        tokens = [token for token in re.split(r"[\s,，。！？；：:;()（）\[\]【】\-_/]+", query) if token]
        for token in tokens:
            token_lower = token.lower()
            if token_lower in lowered_text:
                score += 1
            elif len(token_lower) >= 4:
                overlap = sum(1 for char in set(token_lower) if char in lowered_text)
                if overlap >= min(4, len(set(token_lower))):
                    score += 1
        return score

    @staticmethod
    def _dedupe_sources(sources: list[KnowledgeSource]) -> list[KnowledgeSource]:
        best: dict[str, KnowledgeSource] = {}
        for source in sources:
            current = best.get(source.path)
            if current is None or source.score > current.score:
                best[source.path] = source
        return list(best.values())

    def _read_text(self, relative_path: str) -> str:
        path = (self.vault_path / relative_path).resolve()
        vault_root = self.vault_path.resolve()
        if vault_root not in path.parents and path != vault_root:
            return ""
        if not path.exists() or not path.is_file():
            return ""
        if path.suffix.lower() not in {".md", ".txt"}:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
