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


# ---------------------------------------------------------------- team knowledge base domain (§5.8 of the team-kb design)


def test_old_databases_get_the_domain_column_and_archives_stay_separate(tmp_path):
    import sqlite3

    db = tmp_path / "db.sqlite3"
    with sqlite3.connect(db) as conn:  # a database from before the team-kb domain existed
        conn.execute("CREATE TABLE qa_records (message_id TEXT PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL, "
                     "sources_json TEXT NOT NULL DEFAULT '{}', answer_model TEXT NOT NULL DEFAULT '', "
                     "answer_ms INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, created_at INTEGER NOT NULL, reviewed_at INTEGER)")
        conn.execute("INSERT INTO qa_records (message_id, question, answer, status, created_at) VALUES ('om_old', 'q', 'a', 'pending', 1)")
    store = QaStore(db)
    store.initialize()
    assert store.get_record("om_old")["domain"] == "script"

    store.record("om_kb", "浮桥谁测的", "金阳", sources={"team_kb": [{"title": "浮桥"}]}, model="m", elapsed_ms=5, domain="team_kb")
    assert store.get_record("om_kb")["domain"] == "team_kb"
    script_id = _seed_archive(store)
    kb_id = store.add_archive(source_message_id="om_kb", canonical_question="浮桥之前是谁测的", answer="金阳", category="知识库",
                              keywords="浮桥", scripts="浮桥 用例集", catalog_digest="", operator="me", domain="team_kb")
    assert [a["archive_id"] for a in store.active_archive()] == [script_id]
    assert [a["archive_id"] for a in store.active_archive("team_kb")] == [kb_id]


@pytest.mark.asyncio
async def test_script_answers_never_see_team_kb_archives(tmp_path):
    store = _store(tmp_path)
    store.add_archive(source_message_id="om_kb", canonical_question="对外开服脚本和普通开服脚本有什么区别？", answer="知识库答案",
                      category="知识库", keywords="", scripts="", catalog_digest="", operator="me", domain="team_kb")
    ai = FakeAI()
    answer = await _service(tmp_path, ai, store=store).answer("对外开服脚本和普通开服脚本有什么区别？")
    assert answer.reused_archive_id is None and "知识库答案" not in ai.prompts[0]


def test_team_kb_lookup_returns_the_identical_question_or_similar_references(tmp_path):
    store = _store(tmp_path)
    kb_id = store.add_archive(source_message_id="om_kb", canonical_question="浮桥之前是谁测的", answer="金阳测的", category="知识库",
                              keywords="浮桥, 测试人, 谁测", scripts="", catalog_digest="", operator="me", domain="team_kb")
    _seed_archive(store)
    service = _service(tmp_path, FakeAI(), store=store)
    exact, similar = service.team_kb_lookup("浮桥之前是谁测的？")
    assert exact["archive_id"] == kb_id and similar == []
    exact, similar = service.team_kb_lookup("浮桥是谁测的")
    assert exact is None and [item["archive_id"] for item in similar] == [kb_id]
    assert service.team_kb_lookup("对外开服脚本和普通开服脚本有什么区别？") == (None, [])  # script archive is not used
    assert store.get_archive(kb_id)["hit_count"] == 2


def test_exact_reuse_also_matches_the_original_question_when_the_canonical_one_was_rewritten(tmp_path):
    store = _store(tmp_path)
    store.record("om_kb", "安全锁是什么功能 在哪打开", "AI 回答", sources={"team_kb": [{"title": "安全锁"}]}, model="m",
                 elapsed_ms=5, domain="team_kb")
    kb_id = store.add_archive(source_message_id="om_kb", canonical_question="安全锁功能的位置、开启方式及忘记密码处理流程",
                              answer="安全锁在个人信息-设置-隐私-安全锁页签", category="知识库", keywords="安全锁", scripts="安全锁",
                              catalog_digest="", operator="me", domain="team_kb")
    service = _service(tmp_path, FakeAI(), store=store)
    assert service.team_kb_lookup("安全锁是什么功能，在哪打开？")[0]["archive_id"] == kb_id
    assert service.team_kb_lookup("安全锁功能的位置、开启方式及忘记密码处理流程")[0]["archive_id"] == kb_id
    assert service.team_kb_lookup("安全锁忘记密码怎么办")[0] is None


@pytest.mark.asyncio
async def test_team_kb_correction_and_archive_draft_use_their_own_prompts(tmp_path):
    ai = FakeAI(["修正后：浮桥是唐黎艳测的", '{"canonical_question": "浮桥由谁测试", "keywords": ["浮桥", "测试人"]}'])
    record = {"question": "浮桥谁测的", "answer": "金阳", "domain": "team_kb", "revisions": [],
              "sources": {"team_kb": [{"title": "浮桥 用例集", "group": "赛季组", "owner": "金阳"}]}}
    service = _service(tmp_path, ai)
    result = await service.reanswer(record, ["是唐黎艳测的"])
    assert result.text == "修正后：浮桥是唐黎艳测的"
    assert "组内知识库助手" in ai.prompts[0] and "1. 是唐黎艳测的" in ai.prompts[0] and "浮桥 用例集（赛季组 · 金阳）" in ai.prompts[0]
    assert "注册表摘要" not in ai.prompts[0]
    draft = await service.draft_archive(record, result.text, corrected=True)
    assert draft == {"canonical_question": "浮桥由谁测试", "answer": result.text, "category": "知识库",
                     "keywords": "浮桥, 测试人", "scripts": "浮桥 用例集", "doc_fixes": []}


def test_feedback_votes_and_the_unsatisfied_filter(tmp_path):
    store = _store(tmp_path)
    for mid in ("om_a", "om_b", "om_c"):
        store.record(mid, "问" + mid, "答", sources={}, model="m", elapsed_ms=1)
    assert store.set_feedback("om_a", "down", user_id="ou_1", user_name="沈涛")
    assert store.set_feedback("om_b", "up", user_id="ou_2")
    store.set_feedback("om_c", "down")
    store.set_record_status("om_c", "archived")  # fixed already: no longer 「不满意」
    assert not store.set_feedback("om_missing", "down")
    with pytest.raises(ValueError):
        store.set_feedback("om_a", "meh")
    items, total = store.list_records("unsatisfied", limit=10, offset=0)
    assert total == 1 and items[0]["message_id"] == "om_a" and items[0]["feedback_name"] == "沈涛"
    assert store.count_records()["unsatisfied"] == 1
    store.set_feedback("om_a", "")
    assert store.list_records("unsatisfied", limit=10, offset=0)[1] == 0


def test_stale_team_kb_archives_are_not_replayed_but_sent_as_outdated_references(tmp_path):
    from src.team_kb_questions import TeamKbQuestionStore

    store = _store(tmp_path)
    store.record("om_kb", "浮桥之前是谁测的", "金阳", sources={"team_kb": [{"path": "赛季组/金阳/浮桥.md"}]},
                 model="m", elapsed_ms=1, domain="team_kb")
    store.add_archive(source_message_id="om_kb", canonical_question="浮桥之前是谁测的", answer="金阳测的", category="知识库",
                      keywords="浮桥", scripts="", catalog_digest="", operator="me", domain="team_kb", now=1000)
    features = TeamKbQuestionStore(tmp_path / "db.sqlite3")
    features.initialize()
    features.replace_features([{"path": "赛季组/金阳/浮桥.md", "updated_at": 5000}])
    exact, similar = _service(tmp_path, FakeAI(), store=store).team_kb_lookup("浮桥之前是谁测的")
    assert exact is None and similar[0]["stale"] is True and similar[0]["answer"] == "金阳测的"
