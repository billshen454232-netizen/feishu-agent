import json

from fastapi.testclient import TestClient

from src.admin_app import create_admin_app
from src.execution_report import ExecutionReportStore, compute_error_signature
from src.muliu_executor import MuliuExecutionResult, MuliuStepResult

HEADERS = {"X-Admin-Request": "1"}


def _store(tmp_path):
    store = ExecutionReportStore(tmp_path / "db.sqlite3")
    store.initialize()
    return store


def _add(store, log, message_id):
    result = MuliuExecutionResult(
        "合服",
        [MuliuStepResult(1, "合服", "/home/serverGeneralScript/exc_server_merge.sh", ["2213"], False, log)],
        failed_step_number=1,
        failure_reason="exit code 255",
    )
    return store.add_candidate(compute_error_signature(result), "诊断 " + log, message_id, "cn 2213")


def _client(store):
    return TestClient(create_admin_app(store), base_url="http://127.0.0.1:8901")


def test_rejects_non_loopback_host_and_missing_admin_header(tmp_path):
    store = _store(tmp_path)
    pattern = _add(store, "ERROR a", "om_1")
    client = _client(store)

    assert TestClient(create_admin_app(store), base_url="http://evil.example").get("/api/summary").status_code == 403
    assert client.post("/api/patterns/batch", json={"ids": [pattern.pattern_id], "action": "delete"}).status_code == 403
    assert store.get_pattern(pattern.pattern_id) is not None


def test_review_flow_approve_revert_edit_delete(tmp_path):
    store = _store(tmp_path)
    a = _add(store, "ERROR a", "om_1")
    b = _add(store, "ERROR b", "om_2")
    client = _client(store)

    assert "执行结果审批台" in client.get("/").text
    assert client.get("/api/summary").json() == {"counts": {"candidate": 2, "verified": 0}, "read_success_mode": "ai"}

    assert client.post("/api/patterns/batch", headers=HEADERS, json={"ids": [a.pattern_id, b.pattern_id], "action": "approve"}).json() == {"changed": 2}
    verified = client.get("/api/patterns?status=verified").json()["patterns"]
    assert {p["pattern_id"] for p in verified} == {a.pattern_id, b.pattern_id}

    client.post("/api/patterns/batch", headers=HEADERS, json={"ids": [b.pattern_id], "action": "revert"})
    assert [p["pattern_id"] for p in client.get("/api/patterns?status=candidate").json()["patterns"]] == [b.pattern_id]

    assert client.put("/api/patterns/{}".format(a.pattern_id), headers=HEADERS, json={"diagnosis": "人工修订"}).status_code == 200
    assert store.get_pattern(a.pattern_id).diagnosis == "人工修订"
    assert client.put("/api/patterns/{}".format(a.pattern_id), headers=HEADERS, json={"diagnosis": "  "}).status_code == 400

    client.post("/api/patterns/batch", headers=HEADERS, json={"ids": [a.pattern_id], "action": "delete"})
    assert store.get_pattern(a.pattern_id) is None
    assert client.post("/api/patterns/batch", headers=HEADERS, json={"ids": [b.pattern_id], "action": "drop"}).status_code == 400


def test_mode_switch_and_samples(tmp_path):
    store = _store(tmp_path)
    pattern = _add(store, "ERROR a", "om_1")
    store.record_artifact("om_1", "failed", "ai_new", "❌ 执行失败：合服")
    client = _client(store)

    assert client.put("/api/settings/read-success-mode", headers=HEADERS, json={"mode": "template"}).json() == {"read_success_mode": "template"}
    assert store.read_success_mode() == "template"
    assert client.put("/api/settings/read-success-mode", headers=HEADERS, json={"mode": "x"}).status_code == 400

    samples = client.get("/api/patterns/{}/samples".format(pattern.pattern_id)).json()["samples"]
    assert [s["message_id"] for s in samples] == ["om_1"]


def test_admin_server_binds_loopback_only(tmp_path):
    import socket
    import time

    import httpx

    from src.admin_app import ADMIN_HOST, AdminServer

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = AdminServer(create_admin_app(_store(tmp_path)), port=port)
    server.start()
    try:
        deadline = time.monotonic() + 5
        response = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get("http://127.0.0.1:{}/api/summary".format(port), trust_env=False)
                break
            except httpx.ConnectError:
                time.sleep(0.05)
        assert response is not None and response.status_code == 200
        assert ADMIN_HOST == "127.0.0.1"
        assert server._server.config.host == "127.0.0.1"
    finally:
        server.stop()


def test_recent_executions_view_joins_request_and_reading(tmp_path):
    from src.message_jobs import MessageJobStore

    jobs = MessageJobStore(tmp_path / "db.sqlite3")
    jobs.initialize()
    jobs.create_if_new("om_q", "evt_q", "oc", "ou", "查询2007服务器状态 password=hunter2")
    store = _store(tmp_path)
    store.record_artifact("om_q", "done", "ai_read", "✅ 执行完成：查询 2007 服")
    store.enqueue_interpretation("om_q", "read_success", {"summary": "s", "step_results": []})
    store.claim_interpretation("om_q")
    store.finish_interpretation("om_q", "done", summary="2007 已开服", model_name="gpt-5.6-terra", elapsed_ms=6731)
    store.record_artifact("om_w", "done", "template", "✅ 执行完成：启服")

    rows = _client(store).get("/api/executions").json()["executions"]

    by_id = {row["message_id"]: row for row in rows}
    assert by_id["om_q"]["request_text"].startswith("查询2007服务器状态")
    assert "hunter2" not in by_id["om_q"]["request_text"]
    assert by_id["om_q"]["model_name"] == "gpt-5.6-terra"
    assert by_id["om_q"]["elapsed_ms"] == 6731
    assert by_id["om_q"]["ai_summary"] == "2007 已开服"
    assert by_id["om_w"]["report_source"] == "template" and by_id["om_w"]["model_name"] == ""
    assert "最近执行" in _client(store).get("/").text


def test_outcome_verdict_endpoint(tmp_path):
    store = _store(tmp_path)
    pattern = _add(store, "ERROR a", "om_1")
    client = _client(store)

    assert client.put("/api/patterns/{}/outcome".format(pattern.pattern_id), headers=HEADERS, json={"outcome": "warning"}).json() == {"outcome": "warning"}
    assert store.get_pattern(pattern.pattern_id).outcome == "warning"
    assert client.put("/api/patterns/{}/outcome".format(pattern.pattern_id), headers=HEADERS, json={"outcome": "ok"}).status_code == 400
    assert client.put("/api/patterns/999/outcome", headers=HEADERS, json={"outcome": "failure"}).status_code == 404


def test_recent_executions_include_planning_model_and_requester(tmp_path):
    from src.message_jobs import MessageJobStore

    jobs = MessageJobStore(tmp_path / "db.sqlite3")
    jobs.initialize()
    jobs.create_if_new("om_p", "evt_p", "oc", "ou_abc123", "查询7020服务器信息")
    jobs.record_planning("om_p", "gpt-6-luna", 7100, 2)
    jobs.set_user_name("om_p", "沈涛")
    store = _store(tmp_path)
    store.record_artifact("om_p", "done", "template", "✅ 执行完成")

    row = _client(store).get("/api/executions").json()["executions"][0]

    assert row["user_id"] == "ou_abc123"
    assert row["user_name"] == "沈涛"
    assert (row["planning_model"], row["planning_ms"], row["planning_calls"]) == ("gpt-6-luna", 7100, 2)
    assert "需求分析" in _client(store).get("/").text


def test_patterns_and_executions_are_paginated(tmp_path):
    store = _store(tmp_path)
    for index in range(5):
        _add(store, "ERROR {}".format(index), "om_{}".format(index))
        store.record_artifact("om_{}".format(index), "failed", "template", "x", now=1000 + index)
    client = _client(store)

    first = client.get("/api/patterns?status=candidate&page=1&page_size=2").json()
    last = client.get("/api/patterns?status=candidate&page=3&page_size=2").json()
    runs = client.get("/api/executions?page=2&page_size=2").json()

    assert (first["total"], len(first["patterns"]), len(last["patterns"])) == (5, 2, 1)
    assert {p["pattern_id"] for p in first["patterns"]}.isdisjoint({p["pattern_id"] for p in last["patterns"]})
    assert runs["total"] == 5 and [r["message_id"] for r in runs["executions"]] == ["om_2", "om_1"]
    assert client.get("/api/executions?page=0&page_size=999").json()["page_size"] == 100


def test_admin_server_does_not_touch_process_logging(tmp_path):
    import logging

    from src.admin_app import AdminServer

    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    before = {name: (logging.getLogger(name).level, list(logging.getLogger(name).handlers)) for name in names}

    AdminServer(create_admin_app(_store(tmp_path)), port=1)

    assert {name: (logging.getLogger(name).level, list(logging.getLogger(name).handlers)) for name in names} == before


# ---------------------------------------------------------------- Q&A review


class _QaAI:
    def __init__(self):
        self.prompts = []

    async def generate(self, history, prompt):
        self.prompts.append(prompt)
        if "输出一个 JSON 对象" in prompt:
            return json.dumps({
                "canonical_question": "对外开服和普通开服的区别", "category": "开服", "keywords": ["对外", "op"],
                "scripts": ["control_is_pub.sh"],
                "doc_fixes": [{"doc_path": "knowledge/docs/control_is_pub.md", "original_excerpt": "op：外部体验开服。",
                               "proposed_excerpt": "op：对外开服。", "reason": "补充说法"}],
            }, ensure_ascii=False)
        return "修正后的回答"


def _qa_client(tmp_path):
    from src.qa_library import QaService, QaStore

    docs = tmp_path / "kb" / "docs"
    docs.mkdir(parents=True)
    (docs / "control_is_pub.md").write_text("# control_is_pub.sh\n\nop：外部体验开服。\n", encoding="utf-8")
    qa_store = QaStore(tmp_path / "db.sqlite3")
    qa_store.initialize()
    qa_store.record("om_q", "对外和开服啥区别", "错误回答", sources={"docs": ["knowledge/docs/control_is_pub.md"], "archives": []},
                    model="luna", elapsed_ms=800)
    ai = _QaAI()
    service = QaService(answer_client=ai, curation_client=ai, capability_context=lambda: "注册表", catalog_text=lambda: "c",
                        store=qa_store, knowledge_root=tmp_path / "kb")
    return _client_with(create_admin_app(_store(tmp_path), qa_service=service)), qa_store, ai


def _client_with(app):
    return TestClient(app, base_url="http://127.0.0.1:8901")


def test_qa_review_correct_archive_and_doc_fix_flow(tmp_path):
    client, qa_store, ai = _qa_client(tmp_path)

    summary = client.get("/api/summary").json()
    assert summary["qa"]["pending"] == 1 and summary["doc_fixes"]["pending"] == 0
    listed = client.get("/api/qa?status=pending").json()
    assert listed["total"] == 1 and listed["items"][0]["answer"] == "错误回答"

    corrected = client.post("/api/qa/om_q/correct", headers=HEADERS, json={"correction": "对外是 op 模式"}).json()
    assert corrected["round"] == 1 and corrected["answer"] == "修正后的回答"
    assert "对外是 op 模式" in ai.prompts[-1]
    assert client.get("/api/qa?status=correcting").json()["items"][0]["revisions"][0]["answer"] == "修正后的回答"

    draft = client.post("/api/qa/om_q/draft", headers=HEADERS).json()
    assert draft["answer"] == "修正后的回答" and len(draft["doc_fixes"]) == 1

    archived = client.post("/api/qa/om_q/archive", headers=HEADERS, json=dict(draft, doc_fixes=draft["doc_fixes"])).json()
    assert archived == {"archive_id": 1, "doc_fixes": 1}
    assert qa_store.get_record("om_q")["status"] == "archived"
    library = client.get("/api/qa-archive?status=active").json()
    assert library["items"][0]["canonical_question"] == "对外开服和普通开服的区别" and library["items"][0]["stale"] is False

    fix = client.get("/api/doc-fixes?status=pending").json()["items"][0]
    assert client.put("/api/doc-fixes/{}".format(fix["suggestion_id"]), headers=HEADERS,
                      json={"status": "accepted", "proposed_excerpt": "op：对外（外部体验）开服。"}).status_code == 200
    assert client.put("/api/doc-fixes/{}".format(fix["suggestion_id"]), headers=HEADERS, json={"status": "applied"}).status_code == 400
    assert client.get("/api/doc-fixes?status=accepted").json()["items"][0]["proposed_excerpt"] == "op：对外（外部体验）开服。"

    assert client.put("/api/qa-archive/1", headers=HEADERS, json={"status": "retired"}).status_code == 200
    assert client.get("/api/qa-archive?status=retired").json()["total"] == 1
    assert client.delete("/api/qa-archive/1", headers=HEADERS).status_code == 200


def test_qa_dismiss_restore_and_validation(tmp_path):
    client, qa_store, _ai = _qa_client(tmp_path)

    assert client.post("/api/qa/om_q/correct", headers=HEADERS, json={"correction": " "}).status_code == 400
    assert client.post("/api/qa/om_x/correct", headers=HEADERS, json={"correction": "x"}).status_code == 404
    client.post("/api/qa/om_q/dismiss", headers=HEADERS)
    assert qa_store.get_record("om_q")["status"] == "dismissed"
    assert client.post("/api/qa/om_q/correct", headers=HEADERS, json={"correction": "x"}).status_code == 409
    client.post("/api/qa/om_q/restore", headers=HEADERS)
    assert qa_store.get_record("om_q")["status"] == "pending"
    assert client.post("/api/qa/om_q/archive", headers=HEADERS, json={"canonical_question": "", "answer": "a"}).status_code == 400
    assert "问答" in client.get("/").text and "文档修正" in client.get("/").text


def test_plan_board_page_and_api(tmp_path):
    from src.plan_board import PlanStore

    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({
        "meta": {"title": "飞书机器人 · 项目计划", "principle": "计划即备忘"},
        "areas": ["执行链", "其他"],
        "steps": [
            {"id": "01", "status": "active", "area": "执行链", "title": "A", "desc": "d", "done": ["x"], "current": ["y"], "remaining": []},
            {"id": "02", "status": "todo", "area": "其他", "title": "B", "desc": "", "done": [], "current": [], "remaining": ["z"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    plans = PlanStore(tmp_path / "db.sqlite3")
    plans.initialize(seed)
    client = TestClient(create_admin_app(_store(tmp_path), plan_store=plans), base_url="http://127.0.0.1:8901")

    page = client.get("/plan")
    assert page.status_code == 200 and "/api/plan" in page.text and page.headers["cache-control"] == "no-cache"
    data = client.get("/api/plan").json()
    assert data["total"] == 2 and data["counts"] == {"done": 0, "verifying": 0, "active": 1, "todo": 1}
    queue = client.get("/api/plan?status=active,todo").json()
    assert [step["id"] for step in queue["steps"]] == ["01", "02"] and queue["total"] == 2

    tunnel = dict(HEADERS, **{"X-Admin-Operator": "claude-code"})
    updated = client.put("/api/plan/steps/01", headers=tunnel, json={"status": "done", "note": "上线验证通过"}).json()
    assert updated["status"] == "done" and updated["updated_by"] == "claude-code"
    verifying = client.put("/api/plan/steps/02", headers=tunnel, json={"status": "verifying"}).json()
    assert verifying["status"] == "verifying"
    assert [step["id"] for step in client.get("/api/plan?status=verifying").json()["steps"]] == ["02"]
    assert client.get("/api/plan").json()["counts"]["verifying"] == 1
    assert "待验收" in page.text
    assert client.put("/api/plan/steps/01", headers=HEADERS, json={"status": "obsolete"}).status_code == 400
    assert client.put("/api/plan/steps/99", headers=HEADERS, json={"status": "done"}).status_code == 404
    created = client.post("/api/plan/steps", headers=HEADERS, json={"title": "新想法", "area": "其他"}).json()
    assert created["id"] == "03" and created["status"] == "todo"
    assert client.delete("/api/plan/steps/02", headers=HEADERS).status_code == 200
    events = client.get("/api/plan/events?step_id=01").json()["events"]
    assert {e["field"] for e in events} == {"status", "note"}

    migrated = client.post("/api/plan/migrate-local", headers=HEADERS, json={
        "overrides": {"03": "active", "01": "done", "77": "done"}, "notes": {"03": {"note": "旧批注", "question": ""}},
    }).json()
    assert migrated == {"statuses": 1, "notes": 1}
    assert plans.get_step("03")["note"] == "旧批注"

    assert client.get("/api/plan", headers={"X-Admin-Proxy": "cloudflare-access"}).status_code == 403
    assert TestClient(create_admin_app(_store(tmp_path)), base_url="http://127.0.0.1:8901").get("/plan").status_code == 404
    assert 'href="/plan"' in client.get("/").text


def test_rewrite_diagnosis_returns_a_draft_around_reviewer_notes(tmp_path):
    store = _store(tmp_path)
    pattern = _add(store, "ERROR src_zone_id对应的服未配置name", "om_rw")

    class FakeAI:
        prompts = []

        async def generate(self, history, prompt):
            self.prompts.append(prompt)
            return "现象：合服报 src_zone_id 未配置 name。\n原因：GMT 系统未注册对应战区服信息。\n处理办法：在 GMT 服务器列表与开服系统中加上 zone。"

    ai = FakeAI()
    client = TestClient(create_admin_app(store, review_client=ai), base_url="http://127.0.0.1:8901")
    notes = "旧推测\n服务端恢复报错原因是GMT系统未注册对应战区服信息\n开服系统 导入逻辑服 手动注册基础信息注册到ServerInfo中 加上zone"

    response = client.post(f"/api/patterns/{pattern.pattern_id}/rewrite", headers=HEADERS, json={"diagnosis": notes})

    assert response.status_code == 200 and "GMT 系统未注册" in response.json()["diagnosis"]
    assert "已确认事实" in ai.prompts[0] and "GMT系统未注册对应战区服信息" in ai.prompts[0]
    assert "src_zone_id对应的服未配置name" in ai.prompts[0]  # key error lines are part of the context
    assert store.get_pattern(pattern.pattern_id).diagnosis == "诊断 ERROR src_zone_id对应的服未配置name"  # not saved
    assert client.post("/api/patterns/999/rewrite", headers=HEADERS, json={"diagnosis": "x"}).status_code == 404
    assert client.post(f"/api/patterns/{pattern.pattern_id}/rewrite", headers=HEADERS, json={"diagnosis": " "}).status_code == 400
    no_ai = TestClient(create_admin_app(store), base_url="http://127.0.0.1:8901")
    assert no_ai.post(f"/api/patterns/{pattern.pattern_id}/rewrite", headers=HEADERS, json={"diagnosis": notes}).status_code == 503
    assert "AI 按人工说明重写" in client.get("/").text


def test_reviewer_marks_and_lists_unsatisfied_answers(tmp_path):
    client, qa_store, _ = _qa_client(tmp_path)
    assert client.post("/api/qa/om_q/feedback", json={"vote": "down"}).status_code == 403  # admin header required
    assert client.post("/api/qa/om_q/feedback", headers=HEADERS, json={"vote": "up"}).status_code == 400
    assert client.post("/api/qa/om_missing/feedback", headers=HEADERS, json={"vote": "down"}).status_code == 404
    assert client.post("/api/qa/om_q/feedback", headers=HEADERS, json={"vote": "down"}).json() == {"feedback": "down"}
    listed = client.get("/api/qa?status=unsatisfied").json()
    assert listed["total"] == 1 and listed["items"][0]["feedback"] == "down"
    assert client.get("/api/summary").json()["qa"]["unsatisfied"] == 1
    client.post("/api/qa/om_q/feedback", headers=HEADERS, json={"vote": ""})
    assert client.get("/api/qa?status=unsatisfied").json()["total"] == 0


def test_team_kb_record_archives_into_its_own_domain(tmp_path):
    client, qa_store, _ = _qa_client(tmp_path)
    qa_store.record("om_kb", "浮桥谁测的", "金阳", sources={"team_kb": [{"title": "浮桥 用例集", "group": "赛季组", "owner": "金阳"}]},
                    model="m", elapsed_ms=1, domain="team_kb")
    archived = client.post("/api/qa/om_kb/archive", headers=HEADERS, json={
        "canonical_question": "浮桥由谁测试", "answer": "唐黎艳", "category": "知识库", "keywords": "浮桥", "scripts": "浮桥 用例集"}).json()
    entry = qa_store.get_archive(archived["archive_id"])
    assert entry["domain"] == "team_kb" and entry["catalog_digest"] == "" and qa_store.active_archive() == []
