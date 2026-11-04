import json

import pytest

from src.qa_library import (
    QaService,
    QaStore,
    exact_archive,
    rank_archive,
)
from src.knowledge_base import KnowledgeSearchResult, KnowledgeSource
from src.utils import text_match


# ---------------------------------------------------------------- text matching


def test_terms_use_chinese_bigrams_and_ascii_words():
    terms = text_match.terms("对外开服跟 control_is_pub.sh 有啥区别")

    assert {"对外", "外开", "开服", "control_is_pub.sh"} <= terms
    assert "区别" not in terms  # stop bigram
    assert text_match.normalize("对外 开服？") == "对外开服"


def test_coverage_is_share_of_query_terms():
    assert text_match.coverage({"对外", "开服"}, {"对外", "开服", "脚本"}) == 1.0
    assert text_match.coverage({"对外", "开服"}, {"清档"}) == 0.0
    assert text_match.coverage(set(), {"x"}) == 0.0


# ---------------------------------------------------------------- retrieval

ARCHIVE = [
    {"archive_id": 1, "canonical_question": "对外开服脚本和普通开服脚本有什么区别", "keywords": "对外, 外部体验, op, control_is_pub",
     "scripts": "start, control_is_pub.sh", "category": "开服", "answer": "A1", "hit_count": 0},
    {"archive_id": 2, "canonical_question": "清档会删除哪些数据", "keywords": "清档, 清服, 数据库",
     "scripts": "clear", "category": "清档", "answer": "A2", "hit_count": 5},
]


def test_rank_archive_matches_different_phrasing_and_ignores_unrelated():
    ranked = rank_archive("外部体验开服跟普通开服啥不一样", ARCHIVE)

    assert [m.archive["archive_id"] for m in ranked] == [1]
    assert rank_archive("合服失败怎么办", ARCHIVE) == []


def test_script_mention_alone_can_match():
    assert [m.archive["archive_id"] for m in rank_archive("control_is_pub 的 re 参数是什么意思", ARCHIVE)] == [1]


def test_exact_archive_ignores_punctuation_and_spaces():
    assert exact_archive("清档会删除哪些数据？", ARCHIVE)["archive_id"] == 2
    assert exact_archive("清档会删哪些数据", ARCHIVE) is None


# ---------------------------------------------------------------- store


def _store(tmp_path):
    store = QaStore(tmp_path / "db.sqlite3")
    store.initialize()
    return store


def test_record_revision_archive_and_fix_lifecycle(tmp_path):
    store = _store(tmp_path)
    store.record("om_1", "问 password=hunter2", "答", sources={"docs": ["knowledge/docs/start.md"]}, model="luna", elapsed_ms=900)

    record = store.get_record("om_1")
    assert record["status"] == "pending" and "hunter2" not in record["question"]
    assert store.add_revision("om_1", "纠正1", "新答1", model="terra", elapsed_ms=1, operator="me") == 1
    assert store.add_revision("om_1", "纠正2", "新答2", model="terra", elapsed_ms=1, operator="me") == 2
    assert store.get_record("om_1")["status"] == "correcting"
    assert [r["round"] for r in store.list_revisions("om_1")] == [1, 2]

    archive_id = store.add_archive(source_message_id="om_1", canonical_question="Q", answer="A", category="不存在的类别",
                                   keywords="k", scripts="start", catalog_digest="d", operator="me")
    assert store.get_archive(archive_id)["category"] == "通用"
    store.record_hits([archive_id])
    assert store.get_archive(archive_id)["hit_count"] == 1

    fix_id = store.add_doc_fix(archive_id, "knowledge/docs/start.md", "old", "new")
    store.update_doc_fix(fix_id, status="accepted")
    assert store.mark_fixes_applied([fix_id]) == 1
    assert store.get_archive(archive_id)["written_back"] == 1
    assert store.count_doc_fixes()["applied"] == 1

    store.update_archive(archive_id, {"status": "retired"})
    assert store.active_archive() == []
    with pytest.raises(ValueError):
        store.update_archive(archive_id, {"category": "x"})


# ---------------------------------------------------------------- service


class FakeAI:
    def __init__(self, replies=None):
        self.prompts = []
        self.replies = list(replies or [])

    async def generate(self, history, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "AI 回答"


class FakeKB:
    def search(self, query):
        return KnowledgeSearchResult(True, "[来源: knowledge/docs/control_is_pub.md]\n资料内容",
                                     [KnowledgeSource("script_knowledge", "knowledge/docs/control_is_pub.md", "t", 8)])


def _service(tmp_path, ai, *, store=None, root=None):
    return QaService(answer_client=ai, curation_client=ai, knowledge_base=FakeKB(),
                     capability_context=lambda: "注册表摘要", catalog_text=lambda: "catalog", store=store,
                     knowledge_root=root or tmp_path)


def _seed_archive(store):
    return store.add_archive(source_message_id="om_0", canonical_question=ARCHIVE[0]["canonical_question"], answer="已审核答案",
                             category="开服", keywords=ARCHIVE[0]["keywords"], scripts=ARCHIVE[0]["scripts"],
                             catalog_digest="", operator="me")


@pytest.mark.asyncio
async def test_answer_injects_matching_archive_and_counts_hits(tmp_path):
    store = _store(tmp_path)
    archive_id = _seed_archive(store)
    ai = FakeAI()

    answer = await _service(tmp_path, ai, store=store).answer("外部体验开服跟普通开服啥不一样")

    assert answer.archive_ids == [archive_id] and answer.reused_archive_id is None
    assert "已审核问答 #{}".format(archive_id) in ai.prompts[0] and "已审核答案" in ai.prompts[0]
    assert "不得生成执行计划" in ai.prompts[0] and "注册表摘要" in ai.prompts[0]
    assert answer.doc_paths == ["knowledge/docs/control_is_pub.md"]
    assert store.get_archive(archive_id)["hit_count"] == 1


@pytest.mark.asyncio
async def test_identical_question_reuses_archive_without_ai(tmp_path):
    store = _store(tmp_path)
    archive_id = _seed_archive(store)
    ai = FakeAI()

    answer = await _service(tmp_path, ai, store=store).answer("对外开服脚本和普通开服脚本有什么区别？")

    assert ai.prompts == []
    assert answer.reused_archive_id == archive_id and answer.text == "已审核答案"


@pytest.mark.asyncio
async def test_reanswer_includes_all_corrections_and_previous_answer(tmp_path):
    ai = FakeAI(["修正后的回答"])
    record = {"question": "Q", "answer": "原答", "revisions": [{"answer": "第1轮答", "correction": "c1"}]}

    result = await _service(tmp_path, ai).reanswer(record, ["c1", "c2"])

    assert result.text == "修正后的回答"
    assert "1. c1" in ai.prompts[0] and "2. c2" in ai.prompts[0] and "第1轮答" in ai.prompts[0]


@pytest.mark.asyncio
async def test_draft_archive_keeps_only_valid_doc_fixes(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "control_is_pub.md").write_text("# control_is_pub.sh\n\nop 模式：外部体验开服。\nre 模式：发布模式。\n", encoding="utf-8")
    reply = json.dumps({
        "canonical_question": "对外开服和普通开服的区别",
        "category": "开服",
        "keywords": ["对外", "外部体验", "op"],
        "scripts": ["control_is_pub.sh", "start"],
        "doc_fixes": [
            {"doc_path": "knowledge/docs/control_is_pub.md", "original_excerpt": "op 模式：外部体验开服。",
             "proposed_excerpt": "op 模式：对外（外部体验）开服，走 public_game.sh。", "reason": "补充对外说法"},
            {"doc_path": "knowledge/docs/control_is_pub.md", "original_excerpt": "并不存在的原文", "proposed_excerpt": "x"},
            {"doc_path": "knowledge/docs/start.md", "original_excerpt": "x", "proposed_excerpt": "y"},
        ],
    }, ensure_ascii=False)
    ai = FakeAI(["```json\n" + reply + "\n```"])
    record = {"question": "对外和开服啥区别", "answer": "错", "sources": {"docs": ["knowledge/docs/control_is_pub.md"]},
              "revisions": [{"correction": "对外是 op 模式", "answer": "对"}]}

    draft = await _service(tmp_path, ai).draft_archive(record, "对", corrected=True)

    assert draft["category"] == "开服" and draft["keywords"] == "对外, 外部体验, op"
    assert draft["scripts"] == "control_is_pub.sh, start"
    assert [f["original_excerpt"] for f in draft["doc_fixes"]] == ["op 模式：外部体验开服。"]
    assert "op 模式：外部体验开服。" in ai.prompts[0]


@pytest.mark.asyncio
async def test_draft_archive_without_correction_sends_no_documents(tmp_path):
    ai = FakeAI(['{"canonical_question": "Q", "category": "乱写", "keywords": "a, b", "scripts": [], "doc_fixes": []}'])
    record = {"question": "Q", "answer": "A", "sources": {"docs": []}, "revisions": []}

    draft = await _service(tmp_path, ai).draft_archive(record, "A", corrected=False)

    assert draft["category"] == "通用" and draft["doc_fixes"] == []
    assert "未提供文档" in ai.prompts[0]
