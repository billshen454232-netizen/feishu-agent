from pathlib import Path

from tools.doc_fixes import apply


def _repo(tmp_path):
    docs = tmp_path / "knowledge" / "docs"
    docs.mkdir(parents=True)
    (docs / "start.md").write_bytes("# start\r\n\r\n默认启动 Zone。\r\n".encode("utf-8"))
    (docs / "dup.md").write_text("同一句\n同一句\n", encoding="utf-8")
    return tmp_path


def test_apply_replaces_unique_excerpt_and_keeps_crlf(tmp_path):
    repo = _repo(tmp_path)

    applied, problems = apply([
        {"suggestion_id": 1, "doc_path": "knowledge/docs/start.md", "original_excerpt": "默认启动 Zone。", "proposed_excerpt": "默认启动 Zone 与逻辑服。"},
    ], repo)

    assert applied == [1] and problems == []
    assert (repo / "knowledge/docs/start.md").read_bytes().decode("utf-8") == "# start\r\n\r\n默认启动 Zone 与逻辑服。\r\n"


def test_apply_refuses_ambiguous_missing_and_outside_paths(tmp_path):
    repo = _repo(tmp_path)

    applied, problems = apply([
        {"suggestion_id": 2, "doc_path": "knowledge/docs/dup.md", "original_excerpt": "同一句", "proposed_excerpt": "x"},
        {"suggestion_id": 3, "doc_path": "knowledge/docs/start.md", "original_excerpt": "不存在", "proposed_excerpt": "x"},
        {"suggestion_id": 4, "doc_path": "knowledge/scripts/basic_info.sh", "original_excerpt": "a", "proposed_excerpt": "b"},
        {"suggestion_id": 5, "doc_path": "knowledge/docs/../../etc.md", "original_excerpt": "a", "proposed_excerpt": "b"},
    ], repo)

    assert applied == [] and len(problems) == 4
    assert (repo / "knowledge/docs/dup.md").read_text(encoding="utf-8") == "同一句\n同一句\n"
