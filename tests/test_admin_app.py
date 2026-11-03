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

    assert "执行结果模板审批" in client.get("/").text
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
