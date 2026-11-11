import json

import pytest
from fastapi.testclient import TestClient

from src.admin_app import create_admin_app
from src.app import create_app
from src.execution_report import ExecutionReportStore
from src.team_kb_reviews import ReviewError, TeamKbReviewStore, parse_review_item

ITEM = {
    "id": "20261111-120000-abcdef12", "uploader": "沈涛", "group": "赛季组", "owner": "唐黎艳", "name": "浮桥.md",
    "path": "赛季组/唐黎艳/浮桥.md", "title": "浮桥", "cases": 3, "uploaded_at": 1793000000,
    "hits": [{"kind": "IP 地址", "line": 4, "context": "测试服 10.***.40"}],
    "view_url": "http://intranet.example:8090/api/pending/20261111-120000-abcdef12",
}


def _store(tmp_path):
    store = TeamKbReviewStore(tmp_path / "db.sqlite3")
    store.initialize()
    return store


def test_report_is_idempotent_and_decisions_flow_back_once(tmp_path):
    store = _store(tmp_path)
    assert store.report([parse_review_item(ITEM)]) == 1
    assert store.report([parse_review_item(ITEM)]) == 0
    assert store.count_pending() == 1
    items, total = store.list_reviews("pending")
    assert total == 1 and items[0]["hits"][0]["context"] == "测试服 10.***.40"
    assert store.decisions_to_apply() == []

    store.decide(ITEM["id"], "approve", operator="boss@example.com", note="测试服 IP 无妨")
    with pytest.raises(ReviewError):
        store.decide(ITEM["id"], "reject", operator="boss@example.com")  # already decided
    assert store.decisions_to_apply() == [{"id": ITEM["id"], "action": "approve", "operator": "boss@example.com", "note": "测试服 IP 无妨"}]
    store.report([parse_review_item(ITEM)])  # a late re-report must not reset the decision
    assert store.list_reviews("approved")[1] == 1

    assert store.mark_applied(ITEM["id"], ok=True, message="已入库")
    assert store.decisions_to_apply() == []
    assert store.list_reviews("approved")[0][0]["apply_message"] == "已入库"


@pytest.mark.parametrize("bad", [{**ITEM, "id": "../x"}, {**ITEM, "hits": []}, {**ITEM, "view_url": "javascript:alert(1)"}, "x"])
def test_parse_rejects_bad_items(bad):
    with pytest.raises(ReviewError):
        parse_review_item(bad)


def test_admin_page_lists_and_decides_with_operator(tmp_path):
    store = _store(tmp_path)
    store.report([parse_review_item(ITEM)])
    reports = ExecutionReportStore(tmp_path / "db.sqlite3")
    reports.initialize()
    client = TestClient(create_admin_app(reports, team_kb_reviews=store), base_url="http://127.0.0.1:8901")
    headers = {"X-Admin-Request": "1", "X-Admin-Operator": "claude-code"}

    assert client.get("/api/summary").json()["team_kb_pending"] == 1
    assert client.get("/api/team-kb/reviews?status=pending").json()["items"][0]["review_id"] == ITEM["id"]
    assert client.post(f"/api/team-kb/reviews/{ITEM['id']}/decision", json={"action": "approve"}).status_code == 403
    response = client.post(f"/api/team-kb/reviews/{ITEM['id']}/decision", json={"action": "reject", "note": "真密码"}, headers=headers)
    assert response.json() == {"review_id": ITEM["id"], "status": "rejected"}
    assert store.list_reviews("rejected")[0][0]["decided_by"] == "claude-code"
    assert client.post(f"/api/team-kb/reviews/{ITEM['id']}/decision", json={"action": "approve"}, headers=headers).status_code == 400


def test_gateway_internal_endpoints_need_worker_token(monkeypatch, tmp_path):
    config_path = tmp_path / "gateway-config.json"
    config_path.write_text(json.dumps({
        "runtime": {"role": "gateway", "worker_token": "secret-token", "lease_timeout_seconds": 60},
        "feishu": {"app_id": "app", "app_secret": "secret", "verification_token": "verify"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "job_queue": {"database_path": str(tmp_path / "jobs.sqlite3"), "recovery_window_seconds": 600},
    }), encoding="utf-8")
    monkeypatch.setattr("src.app.create_ai_client", lambda _cfg: object())
    headers = {"Authorization": "Bearer secret-token", "X-Feishu-Worker-Id": "team-kb"}
    with TestClient(create_app(config_path)) as client:
        assert client.post("/internal/team-kb/reviews", json={"items": [ITEM]}).status_code == 401
        assert client.post("/internal/team-kb/reviews", json={"items": [ITEM]}, headers=headers).json() == {"accepted": 1, "inserted": 1}
        assert client.post("/internal/team-kb/reviews", json={"items": [{**ITEM, "id": "bad/id"}]}, headers=headers).status_code == 422
        assert client.get("/internal/team-kb/decisions", headers=headers).json() == {"items": []}
        store = TeamKbReviewStore(tmp_path / "jobs.sqlite3")
        store.decide(ITEM["id"], "reject", operator="boss")
        assert client.get("/internal/team-kb/decisions", headers=headers).json()["items"][0]["action"] == "reject"
        assert client.post(f"/internal/team-kb/reviews/{ITEM['id']}/applied", json={"ok": True, "message": "已拒绝"}, headers=headers).status_code == 200
        assert client.get("/internal/team-kb/decisions", headers=headers).json() == {"items": []}
