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
