from src.script_knowledge_base import ScriptKnowledgeBase


def make_knowledge(tmp_path):
    knowledge = tmp_path / "knowledge"
    docs = knowledge / "docs"
    scripts = knowledge / "scripts"
    references = knowledge / "references"
    docs.mkdir(parents=True)
    scripts.mkdir()
    references.mkdir()

    (knowledge / "index.md").write_text(
        "# 测试服脚本知识索引\n\n"
        "|脚本|关键词|说明文档|\n"
        "|---|---|---|\n"
        "|`cc_patch.py`|Patch、补丁列表|[cc_patch.md](docs/cc_patch.md)|\n",
        encoding="utf-8",
    )
    (docs / "cc_patch.md").write_text(
        "# cc_patch.py\n\n"
        "## 查询单个服务器 Patch 列表\n\n"
        "使用 `-s <server_id> -ck` 检查 Patch 列表。\n",
        encoding="utf-8",
    )
    (scripts / "sensitive.py").write_text("password = secret-value", encoding="utf-8")
    (references / "manual.md").write_text("internal-only reference", encoding="utf-8")
    return knowledge


def test_search_reads_index_and_docs_only(tmp_path):
    knowledge = make_knowledge(tmp_path)
    kb = ScriptKnowledgeBase(knowledge, min_score=2, max_results=3, max_context_chars=4000)

    result = kb.search("怎么查询 Patch 列表")

    assert result.hit is True
    assert "Patch 列表" in result.context
    assert "knowledge/docs/cc_patch.md" in [source.path for source in result.sources]
    assert "secret-value" not in result.context
    assert "internal-only" not in result.context
    assert all("scripts/" not in source.path for source in result.sources)
    assert all("references/" not in source.path for source in result.sources)


def test_search_miss_does_not_read_raw_directories(tmp_path):
    knowledge = make_knowledge(tmp_path)
    kb = ScriptKnowledgeBase(knowledge, min_score=2)

    result = kb.search("secret-value")

    assert result.hit is False
    assert result.context == ""
    assert result.sources == []


def test_search_respects_context_limit(tmp_path):
    knowledge = make_knowledge(tmp_path)
    kb = ScriptKnowledgeBase(knowledge, min_score=2, max_context_chars=80)

    result = kb.search("Patch")

    assert result.hit is True
    assert len(result.context) <= 80
