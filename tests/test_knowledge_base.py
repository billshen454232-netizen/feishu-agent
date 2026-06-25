import json

from src.knowledge_base import DualChainVaultKnowledgeBase


def make_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "indexes").mkdir(parents=True)
    (vault / "articles").mkdir()
    (vault / "concepts").mkdir()
    (vault / "qa").mkdir()
    (vault / "articles" / "llm.md").write_text("# LLM Wiki\n正文：双链增强通过概念和文章互链提升检索。", encoding="utf-8")
    (vault / "concepts" / "双链增强.md").write_text("# 双链增强\n概念页内容。", encoding="utf-8")
    (vault / "qa" / "双链问答.md").write_text("# 双链问答\n问：双链增强怎么工作？\n答：先命中概念，再找相关文章。", encoding="utf-8")
    (vault / "indexes" / "articles.json").write_text(json.dumps([
        {"id": "a1", "path": "articles/llm.md", "title": "LLM Wiki", "summary": "双链增强通过概念和文章互链提升检索", "concepts": ["双链增强"], "status": "active"}
    ], ensure_ascii=False), encoding="utf-8")
    (vault / "indexes" / "concepts.json").write_text(json.dumps([
        {"concept": "双链增强", "aliases": ["双向链接增强"], "article_ids": ["a1"], "article_count": 1}
    ], ensure_ascii=False), encoding="utf-8")
    (vault / "indexes" / "qa.json").write_text(json.dumps([
        {"id": "q1", "path": "qa/双链问答.md", "question": "这个 wiki 的双链增强是怎么工作的？", "summary": "先命中概念，再找相关文章。", "concepts": ["双链增强"], "source_paths": ["articles/llm.md"], "status": "active"}
    ], ensure_ascii=False), encoding="utf-8")
    return vault


def test_search_hits_qa_and_reads_sources(tmp_path):
    kb = DualChainVaultKnowledgeBase(vault_path=make_vault(tmp_path), min_score=2, max_results=3, max_context_chars=2000)

    result = kb.search("双链增强怎么工作")

    assert result.hit is True
    assert result.sources[0].path == "qa/双链问答.md"
    assert "先命中概念" in result.context
    assert "articles/llm.md" in [source.path for source in result.sources]


def test_search_hits_when_concept_is_part_of_question(tmp_path):
    kb = DualChainVaultKnowledgeBase(vault_path=make_vault(tmp_path), min_score=3, max_results=3, max_context_chars=2000)

    result = kb.search("双链增强如何发挥作用")

    assert result.hit is True
    assert "concepts/双链增强.md" in [source.path for source in result.sources]
    assert "articles/llm.md" in [source.path for source in result.sources]


def test_search_miss_returns_empty_context(tmp_path):
    kb = DualChainVaultKnowledgeBase(vault_path=make_vault(tmp_path), min_score=3, max_results=3, max_context_chars=2000)

    result = kb.search("完全无关的问题")

    assert result.hit is False
    assert result.context == ""
    assert result.sources == []
