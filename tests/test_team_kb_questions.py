import json

import pytest
from fastapi.testclient import TestClient

from src.app import _deliver_expired_team_kb_questions, create_app
from src.conversation import ConversationStore
from src.message_jobs import MessageJobStore
from src.message_worker import MessageJobProcessor
from src.muliu_plan import MuliuPlan, MuliuPlanKind, parse_plan
from src.muliu_planner import MuliuPlanGeneration, MuliuPlanner
from src.team_kb_questions import (
    FAILED_REPLY, UNAVAILABLE_REPLY, QuestionError, TeamKbQuestionStore, format_reply, parse_answer, strip_prefix,
)
from tests.test_message_worker import FakeAIClient, FakeFeishuClient, make_config

ANSWER = {"ok": True, "text": "浮桥建造需要同盟（浮桥 用例集 · 金阳）。", "model": "m",
          "sources": [{"title": "浮桥 用例集", "group": "赛季组", "owner": "金阳", "path": "赛季组/金阳/浮桥用例集.md"}]}


def _store(tmp_path):
    store = TeamKbQuestionStore(tmp_path / "db.sqlite3")
    store.initialize()
    return store


def test_store_claims_once_answers_only_claimed_and_expires_slow_questions(tmp_path):
    store = _store(tmp_path)
    assert store.ask("om_1", "oc_1", "浮桥谁测的", now=1000) and not store.ask("om_1", "oc_1", "again", now=1000)
    assert store.finish("om_1", parse_answer(ANSWER), now=1001) is None  # not claimed yet
    assert store.claim(now=1002) == [{"id": "om_1", "text": "浮桥谁测的", "asked_at": 1000, "references": []}]
    assert store.claim(now=1003) == []
    done = store.finish("om_1", parse_answer(ANSWER), now=1010)
    assert done["status"] == "answered" and done["sources"][0]["owner"] == "金阳" and done["chat_id"] == "oc_1"
    assert store.finish("om_1", parse_answer(ANSWER), now=1011) is None  # answered twice

    store.ask("om_2", "oc_1", "领了没答", now=2000)
    store.ask("om_3", "oc_1", "没人领", now=2001)
    assert [q["id"] for q in store.claim(limit=1, now=2001)] == ["om_2"]  # oldest first
    assert store.expire(now=2060) == []
    unclaimed = store.expire(now=2061)
    assert [q["question_id"] for q in unclaimed] == ["om_3"] and unclaimed[0]["error"] == "timeout: nobody claimed it"
    assert store.expire(now=2120) == []
    late = store.expire(now=2121)
    assert [q["question_id"] for q in late] == ["om_2"] and late[0]["error"] == "timeout: no answer in time"
    assert store.finish("om_2", parse_answer(ANSWER), now=2122) is None  # a late answer is dropped


def test_prefix_answer_validation_and_reply_format():
    assert strip_prefix("问题 浮桥谁测的") == "浮桥谁测的" and strip_prefix(" 问题：浮桥") == "浮桥"
    assert strip_prefix("问题, 浮桥") == "浮桥" and strip_prefix("问题") == "" and strip_prefix("问题：") == ""
    assert strip_prefix("问题是 5000 服起不来") is None and strip_prefix("浮桥的问题 谁测的") is None
    assert format_reply(parse_answer(ANSWER)) == (
        "📒 组内知识库\n\n浮桥建造需要同盟（浮桥 用例集 · 金阳）。\n\n来源：浮桥 用例集（赛季组 · 金阳）")
    assert format_reply(parse_answer({"ok": True, "text": "没找到"})) == "📒 组内知识库\n\n没找到"
    assert format_reply(parse_answer({"ok": False, "reason": "AI 挂了"})) == FAILED_REPLY
    with pytest.raises(QuestionError):
        parse_answer({"ok": True, "text": ""})
    with pytest.raises(QuestionError):
        parse_answer("x")


def test_planner_accepts_the_team_kb_kind_without_steps():
    plan = parse_plan('{"kind":"team_kb","summary":"浮桥谁测的","steps":[]}', allow_legacy_kind=False)
    assert plan.kind is MuliuPlanKind.TEAM_KB
    with pytest.raises(Exception):
        parse_plan('{"kind":"team_kb","summary":"x","steps":[{"path":"/a","args":[],"description":"d"}]}')
    assert "`team_kb`" in MuliuPlanner._build_prompt("浮桥谁测的", "catalog")


class _Planner:
    def __init__(self, kind):
        self.kind, self.calls = kind, []

    async def create_plan_with_trace(self, text):
        self.calls.append(text)
        return MuliuPlanGeneration(plan=MuliuPlan(summary="浮桥谁测的", steps=[], kind=self.kind), raw_responses=())


def _processor(tmp_path, planner, questions):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), FakeAIClient(), feishu,
                                    operation_planner=planner, team_kb_questions=questions)
    return processor, store, feishu


@pytest.mark.asyncio
async def test_planner_team_kb_and_prefix_queue_the_question_and_finish_the_job(tmp_path):
    questions = _store(tmp_path)
    planner = _Planner(MuliuPlanKind.TEAM_KB)
    processor, store, feishu = _processor(tmp_path, planner, questions)
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "浮桥之前谁测的", now=1000)
    store.create_if_new("om_2", "evt_2", "oc_1", "ou_1", "问题 重启 5000 服要注意什么", now=1000)
    await processor.process_one("om_1")
    await processor.process_one("om_2")
    assert planner.calls == ["浮桥之前谁测的"]  # the prefixed message never reaches the planner
    assert [q["text"] for q in questions.claim(now=1001)] == ["浮桥之前谁测的", "重启 5000 服要注意什么"]
    assert store.get("om_1").status == "done" and feishu.replies == []

    await processor.deliver_team_kb_answer(questions.finish("om_1", parse_answer(ANSWER)), format_reply(parse_answer(ANSWER)))
    assert feishu.replies[0][0] == "om_1" and feishu.replies[0][1].startswith("📒 组内知识库\n\n浮桥建造需要同盟")


@pytest.mark.asyncio
async def test_without_a_gateway_store_the_asker_is_told_it_is_off(tmp_path):
    processor, store, feishu = _processor(tmp_path, _Planner(MuliuPlanKind.TEAM_KB), None)
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "浮桥之前谁测的", now=1000)
    await processor.process_one("om_1")
    assert feishu.replies == [("om_1", "📒 组内知识库问答未启用。")]


@pytest.mark.asyncio
async def test_expired_questions_get_the_unavailable_reply(tmp_path):
    questions = _store(tmp_path)
    processor, store, feishu = _processor(tmp_path, _Planner(MuliuPlanKind.TEAM_KB), questions)
    questions.ask("om_9", "oc_1", "浮桥", now=1)
    await _deliver_expired_team_kb_questions(questions, processor)
    assert feishu.replies == [("om_9", UNAVAILABLE_REPLY)]


def test_gateway_question_endpoints_long_poll_and_deliver(monkeypatch, tmp_path):
    config_path = tmp_path / "gateway-config.json"
    config_path.write_text(json.dumps({
        "runtime": {"role": "gateway", "worker_token": "secret-token", "lease_timeout_seconds": 60},
        "feishu": {"app_id": "app", "app_secret": "secret", "verification_token": "verify"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "job_queue": {"database_path": str(tmp_path / "jobs.sqlite3"), "recovery_window_seconds": 600},
    }), encoding="utf-8")
    feishu = FakeFeishuClient()
    monkeypatch.setattr("src.app.create_ai_client", lambda _cfg: object())
    monkeypatch.setattr("src.app.FeishuClient", lambda _cfg: feishu)
    headers = {"Authorization": "Bearer secret-token", "X-Feishu-Worker-Id": "team-kb"}
    with TestClient(create_app(config_path)) as client:
        assert client.get("/internal/team-kb/questions").status_code == 401
        assert client.get("/internal/team-kb/questions?wait=0", headers=headers).json() == {"items": []}
        TeamKbQuestionStore(tmp_path / "jobs.sqlite3").ask("om_1", "oc_1", "浮桥谁测的")
        assert client.get("/internal/team-kb/questions?wait=5", headers=headers).json()["items"][0]["id"] == "om_1"
        assert client.post("/internal/team-kb/questions/om_1/answer", json={"ok": True}, headers=headers).status_code == 422
        assert client.post("/internal/team-kb/questions/om_1/answer", json=ANSWER, headers=headers).json() == {"status": "ok"}
        assert client.post("/internal/team-kb/questions/om_1/answer", json=ANSWER, headers=headers).status_code == 404
    assert feishu.replies == [("om_1", format_reply(parse_answer(ANSWER)))]


def _qa_processor(tmp_path, questions):
    from src.qa_library import QaService, QaStore

    qa_store = QaStore(tmp_path / "jobs.sqlite3")
    qa_store.initialize()
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), FakeAIClient(), feishu,
                                    operation_planner=_Planner(MuliuPlanKind.TEAM_KB), team_kb_questions=questions,
                                    qa_service=QaService(answer_client=FakeAIClient(), store=qa_store))
    return processor, store, feishu, qa_store


@pytest.mark.asyncio
async def test_reviewed_answers_are_reused_or_sent_along_and_new_answers_go_to_review(tmp_path):
    questions = _store(tmp_path)
    processor, store, feishu, qa_store = _qa_processor(tmp_path, questions)
    kb_id = qa_store.add_archive(source_message_id="om_0", canonical_question="浮桥之前是谁测的", answer="唐黎艳测的",
                                 category="知识库", keywords="浮桥, 谁测, 测试人", scripts="", catalog_digest="",
                                 operator="me", domain="team_kb")

    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "浮桥之前是谁测的？", now=1000)
    await processor.process_one("om_1")
    assert feishu.replies == [("om_1", "📒 组内知识库（已审核答案，问答 #{}）\n\n唐黎艳测的".format(kb_id))]
    assert questions.claim() == [] and qa_store.get_record("om_1")["status"] == "reused"

    store.create_if_new("om_2", "evt_2", "oc_1", "ou_1", "浮桥是谁测的", now=1000)
    await processor.process_one("om_2")
    claimed = questions.claim()
    assert claimed[0]["references"] == [{"id": kb_id, "question": "浮桥之前是谁测的", "answer": "唐黎艳测的"}]

    answer = parse_answer({**ANSWER, "text": "浮桥由唐黎艳测试（浮桥 用例集 · 唐黎艳）"})
    asked = questions.get("om_2")["asked_at"]
    await processor.deliver_team_kb_answer(questions.finish("om_2", answer, now=asked + 5), format_reply(answer), answer=answer)
    record = qa_store.get_record("om_2")
    assert record["domain"] == "team_kb" and record["status"] == "pending" and record["answer_ms"] == 5000
    assert record["sources"] == {"team_kb": ANSWER["sources"], "archives": [kb_id]}
