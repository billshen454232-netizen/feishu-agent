import time
import json

from fastapi.testclient import TestClient

from src.app import _create_default_app, create_app
from src.message_jobs import MessageJobStore
from src.message_worker import _plan_to_json
from src.muliu_executor import MuliuExecutionResult, MuliuStepResult
from src.muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
from src.worker_protocol import serialize_execution_result


def write_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret", "verification_token": "verify"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "conversation": {"max_history_messages": 4},
        "bot": {"name": "AI助手"},
        "job_queue": {"database_path": str(tmp_path / "jobs.sqlite3"), "recovery_window_seconds": 600}
    }), encoding="utf-8")
    return config_path


def test_health_endpoint_returns_ok(tmp_path):
    app = create_app(write_config(tmp_path))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_feishu_events_endpoint_handles_challenge(tmp_path):
    app = create_app(write_config(tmp_path))
    client = TestClient(app)

    response = client.post("/feishu/events", json={"challenge": "abc"})

    assert response.status_code == 200
    assert response.json() == {"challenge": "abc"}


def test_create_app_initializes_job_database(tmp_path):
    config_path = write_config(tmp_path)

    with TestClient(create_app(config_path)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert (tmp_path / "jobs.sqlite3").exists()


def test_default_app_reports_missing_config_without_crashing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app = _create_default_app()
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "missing_config"



def test_worker_role_exposes_only_worker_health_without_gateway_dependencies(tmp_path):
    catalog_path = tmp_path / "catalog.md"
    catalog_path.write_text(
        "# Muliu\n\n<!-- MULIU_CALL_CONTRACTS\n"
        "{\n"
        '  "max_plan_steps": 1,\n'
        '  "contracts": [\n'
        "    {\n"
        '      "name": "basic-server-info",\n'
        '      "path": "/home/serverGeneralScript/basic_info.sh",\n'
        '      "args": ["{server_id}"],\n'
        '      "variables": {"server_id": "[0-9]{3,8}"},\n'
        '      "runner": "bash",\n'
        '      "risk": "read"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "MULIU_CALL_CONTRACTS -->",
        encoding="utf-8",
    )
    config_path = tmp_path / "worker-config.json"
    config_path.write_text(json.dumps({
        "runtime": {
            "role": "worker",
            "worker_token": "shared-token",
            "worker_id": "jenkins-worker-1",
            "gateway_base_url": "https://gateway.example.com",
        },
        "feishu": {},
        "ai": {},
        "muliu": {
            "script_catalog_path": str(catalog_path),
            "routes": {
                "cn": {
                    "identifier": "route-cn",
                    "task_id": 101,
                    "base_url": "https://cn-muliu.internal",
                    "username": "cn-user",
                    "password": "cn-secret",
                },
                "overseas": {
                    "identifier": "route-overseas",
                    "task_id": 89,
                    "base_url": "https://overseas-muliu.internal",
                    "username": "overseas-user",
                    "password": "overseas-secret",
                },
            },
        },
    }), encoding="utf-8")

    app = create_app(config_path)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "worker"}
    assert all(route.path != "/feishu/events" for route in app.routes)
    assert not (tmp_path / "data" / "feishu_bot.sqlite3").exists()


def test_create_app_with_enabled_knowledge_base(tmp_path):
    vault = tmp_path / "vault"
    (vault / "indexes").mkdir(parents=True)
    (vault / "indexes" / "articles.json").write_text("[]", encoding="utf-8")
    (vault / "indexes" / "concepts.json").write_text("[]", encoding="utf-8")
    (vault / "indexes" / "qa.json").write_text("[]", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret", "verification_token": "verify"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "knowledge_base": {"enabled": True, "type": "dual_chain_vault", "vault_path": str(vault)},
    }), encoding="utf-8")

    app = create_app(config_path)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_gateway_worker_result_route_persists_and_interprets_patch_query(monkeypatch, tmp_path):
    catalog_path = tmp_path / "catalog.md"
    catalog_path.write_text(
        "# Muliu\n\n<!-- MULIU_CALL_CONTRACTS\n"
        "{\n"
        '  "max_plan_steps": 1,\n'
        '  "contracts": [\n'
        "    {\n"
        '      "name": "check-patch",\n'
        '      "path": "/home/serverGeneralScript/cc_patch.py",\n'
        '      "args": ["-s", "{server_id}", "-ck"],\n'
        '      "variables": {"server_id": "[0-9]{3,8}"},\n'
        '      "runner": "python3.7",\n'
        '      "risk": "read"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "MULIU_CALL_CONTRACTS -->",
        encoding="utf-8",
    )
    env_path = tmp_path / "server_environments.json"
    env_path.write_text(
        json.dumps({
            "regions": {"hmt": {"name": "港澳台繁体", "environments": {"weekly": ["5010"]}}},
            "groups": {},
        }),
        encoding="utf-8",
    )
    config_path = tmp_path / "gateway-config.json"
    config_path.write_text(json.dumps({
        "runtime": {
            "role": "gateway",
            "worker_token": "secret-token",
            "lease_timeout_seconds": 60,
        },
        "feishu": {"app_id": "app", "app_secret": "secret", "verification_token": "verify"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "job_queue": {"database_path": str(tmp_path / "jobs.sqlite3"), "recovery_window_seconds": 600},
        "muliu": {"script_catalog_path": str(catalog_path)},
        "execution_interpretation": {
            "enabled": True,
            "background_timeout_seconds": 2,
            "server_environments_path": str(env_path),
        },
    }), encoding="utf-8")

    class FakeInterpretationAI:
        def __init__(self):
            self.calls = []

        async def generate(self, history, prompt):
            self.calls.append((history, prompt))
            return "【存在】已在 5010 服找到该补丁"

    captured_feishu = []

    class FakeFeishu:
        def __init__(self, _cfg):
            self.replies = []
            self.card_updates = []
            captured_feishu.append(self)

        async def reply_text(self, message_id, text):
            self.replies.append((message_id, text))

        async def update_card(self, message_id, card):
            self.card_updates.append((message_id, card))

    fake_ai = FakeInterpretationAI()
    monkeypatch.setattr("src.app.create_ai_client", lambda _cfg: fake_ai)
    monkeypatch.setattr("src.app.FeishuClient", FakeFeishu)

    app = create_app(config_path)

    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = MuliuPlan(
        summary="查询 5010 服 Patch 列表",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/cc_patch.py",
                args=["-s", "5010", "-ck"],
                description="查询 5010 服 Patch",
            )
        ],
    )
    store.create_if_new(
        "om_worker_1",
        "evt_1",
        "oc_1",
        "ou_1",
        "5010 服务器是否存在 avatar_patch_79941 patch？",
    )
    store.mark_awaiting_confirmation(
        "om_worker_1",
        plan_json=_plan_to_json(plan),
        confirmation_message_id="card_1",
        confirmation_token="token_1",
        confirmation_method="card",
        route_region="overseas",
    )
    store.claim_card_confirmation(
        request_message_id="om_worker_1",
        confirmation_message_id="card_1",
        chat_id="oc_1",
        user_id="ou_1",
        confirmation_token="token_1",
        max_age_seconds=600,
    )
    store.queue_for_worker("om_worker_1")

    with TestClient(app) as client:
        claim_response = client.post(
            "/internal/worker/jobs/claim",
            headers={
                "Authorization": "Bearer secret-token",
                "X-Feishu-Worker-Id": "jenkins-worker-1",
            },
        )
        assert claim_response.status_code == 200
        assert claim_response.json() == {
            "message_id": "om_worker_1",
            "plan_json": _plan_to_json(plan),
            "route_region": "overseas",
        }

        exec_result = MuliuExecutionResult(
            summary="查询 5010 服 Patch 列表",
            step_results=[
                MuliuStepResult(
                    step_number=1,
                    description="查询 5010 服 Patch",
                    path="/home/serverGeneralScript/cc_patch.py",
                    args=["-s", "5010", "-ck"],
                    succeeded=True,
                    log='AI_OP_START\n{"file": "avatar_patch_79941.lua"}\nAI_OP_END status=success\nEND',
                )
            ],
        )
        payload = {"result": serialize_execution_result(exec_result)}
        response = client.post(
            "/internal/worker/jobs/om_worker_1/result",
            headers={
                "Authorization": "Bearer secret-token",
                "X-Feishu-Worker-Id": "jenkins-worker-1",
            },
            json=payload,
        )
        # The trusted card is delivered before AI runs; the reading lands later.
        feishu = captured_feishu[0]
        first_target, first_card = feishu.card_updates[0]
        assert first_target == "card_1"
        assert first_card["header"]["template"] == "green"
        assert "AI 正在后台读取查询结果" in first_card["elements"][2]["text"]["content"]
        deadline = time.monotonic() + 5
        while len(feishu.card_updates) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}

    job = store.get("om_worker_1")
    assert job.status == "done"

    assert len(fake_ai.calls) == 1
    assert "avatar_patch_79941.lua" in fake_ai.calls[0][1]

    assert len(captured_feishu) == 1
    assert feishu.replies == []

    assert len(feishu.card_updates) == 2
    card_target, card = feishu.card_updates[1]
    assert card_target == "card_1"
    assert card["header"]["template"] == "green"
    assert "【存在】已在 5010 服找到该补丁" in card["elements"][2]["text"]["content"]
    assert "执行明细：" not in card["elements"][2]["text"]["content"]


def test_gateway_worker_result_with_logs_replies_with_execution_detail(monkeypatch, tmp_path):
    catalog_path = tmp_path / "catalog.md"
    catalog_path.write_text(
        "# Muliu\n\n<!-- MULIU_CALL_CONTRACTS\n"
        "{\n"
        '  "max_plan_steps": 1,\n'
        '  "contracts": [\n'
        "    {\n"
        '      "name": "basic-server-info",\n'
        '      "path": "/home/serverGeneralScript/basic_info.sh",\n'
        '      "args": ["{server_id}"],\n'
        '      "variables": {"server_id": "[0-9]{3,8}"},\n'
        '      "runner": "bash",\n'
        '      "risk": "read"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "MULIU_CALL_CONTRACTS -->",
        encoding="utf-8",
    )
    config_path = tmp_path / "gateway-config.json"
    config_path.write_text(json.dumps({
        "runtime": {"role": "gateway", "worker_token": "secret-token", "lease_timeout_seconds": 60},
        "feishu": {"app_id": "app", "app_secret": "secret", "verification_token": "verify"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "job_queue": {"database_path": str(tmp_path / "jobs.sqlite3"), "recovery_window_seconds": 600},
        "muliu": {"script_catalog_path": str(catalog_path)},
        "execution_interpretation": {"enabled": False},
    }), encoding="utf-8")

    captured_feishu = []

    class FakeFeishu:
        def __init__(self, _cfg):
            self.replies = []
            self.card_updates = []
            captured_feishu.append(self)

        async def reply_text(self, message_id, text):
            self.replies.append((message_id, text))

        async def update_card(self, message_id, card):
            self.card_updates.append((message_id, card))

    monkeypatch.setattr("src.app.FeishuClient", FakeFeishu)
    app = create_app(config_path)
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = MuliuPlan(
        summary="查询 6001 服基础信息",
        kind=MuliuPlanKind.OPERATION,
        steps=[MuliuStep("/home/serverGeneralScript/basic_info.sh", ["6001"], "查询基础信息")],
    )
    store.create_if_new("om_worker_logs", "evt_logs", "oc_1", "ou_1", "查询 6001 服")
    store.mark_awaiting_confirmation(
        "om_worker_logs",
        plan_json=_plan_to_json(plan),
        confirmation_message_id="card_logs",
        confirmation_token="token_logs",
        confirmation_method="card",
        route_region="overseas",
    )
    store.claim_card_confirmation(
        "om_worker_logs", "card_logs", "oc_1", "ou_1", "token_logs", 600,
        include_execution_logs=True,
    )
    store.queue_for_worker("om_worker_logs")

    with TestClient(app) as client:
        client.post(
            "/internal/worker/jobs/claim",
            headers={"Authorization": "Bearer secret-token", "X-Feishu-Worker-Id": "jenkins-worker-1"},
        )
        result = MuliuExecutionResult(
            summary="查询 6001 服基础信息",
            step_results=[MuliuStepResult(1, "查询基础信息", "/home/serverGeneralScript/basic_info.sh", ["6001"], True, "开服成功")],
        )
        response = client.post(
            "/internal/worker/jobs/om_worker_logs/result",
            headers={"Authorization": "Bearer secret-token", "X-Feishu-Worker-Id": "jenkins-worker-1"},
            json={"result": serialize_execution_result(result)},
        )

    assert response.status_code == 200
    feishu = captured_feishu[0]
    assert len(feishu.replies) == 1
    assert feishu.replies[0][0] == "card_logs"
    assert "开服成功" in feishu.replies[0][1]
    assert "开服成功" not in feishu.card_updates[0][1]["elements"][2]["text"]["content"]


def test_role_ai_clients_share_clients_for_shared_pools(monkeypatch, tmp_path):
    from src.app import _create_role_ai_clients
    from src.config import load_config

    model = {"provider": "openai_compatible", "base_url": "https://x/v1", "api_key": "k"}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {
            "pools": {
                "fast": {"models": [dict(model, model="luna")]},
                "strong": {"models": [dict(model, model="terra")]},
            },
            "routing": {"planning": "fast", "chat": "fast", "interpretation": "strong"},
        },
    }), encoding="utf-8")
    built = []
    monkeypatch.setattr("src.app.create_ai_client", lambda pool: built.append(pool.models[0].model) or pool.models[0].model)

    chat, planning, interpretation = _create_role_ai_clients(load_config(config_path))

    assert (chat, planning, interpretation) == ("luna", "luna", "terra")
    assert built == ["luna", "terra"]


def test_gateway_event_relay_endpoints(monkeypatch, tmp_path):
    from src.outbound_events import OutboundEventStore

    config_path = tmp_path / "gateway-config.json"
    config_path.write_text(json.dumps({
        "runtime": {"role": "gateway", "worker_token": "secret-token", "lease_timeout_seconds": 60},
        "feishu": {"app_id": "app", "app_secret": "secret", "verification_token": "verify"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "job_queue": {"database_path": str(tmp_path / "jobs.sqlite3")},
        "clear_notification": {"enabled": True},
    }), encoding="utf-8")
    monkeypatch.setattr("src.app.create_ai_client", lambda _cfg: object())
    app = create_app(config_path)
    events = OutboundEventStore(tmp_path / "jobs.sqlite3")
    events.add("om_1:clear_prepared", "clear_prepared", {"event_id": "om_1:clear_prepared", "text": "内网：沈涛 准备清档 2213 服"})
    worker = {"Authorization": "Bearer secret-token", "X-Feishu-Worker-Id": "jenkins-worker-1"}

    with TestClient(app) as client:
        assert client.post("/internal/worker/events/claim", headers={"Authorization": "Bearer wrong", "X-Feishu-Worker-Id": "w"}).status_code == 401
        claimed = client.post("/internal/worker/events/claim", headers=worker).json()["events"]
        other = {"Authorization": "Bearer secret-token", "X-Feishu-Worker-Id": "other-worker"}
        rejected = client.post("/internal/worker/events/result", headers=other, json={"event_id": "om_1:clear_prepared", "delivered": True})
        accepted = client.post("/internal/worker/events/result", headers=worker, json={"event_id": "om_1:clear_prepared", "delivered": True, "error": ""})
        invalid = client.post("/internal/worker/events/result", headers=worker, json={"event_id": 1, "delivered": "yes"})

    assert [e["payload"]["text"] for e in claimed] == ["内网：沈涛 准备清档 2213 服"]
    assert rejected.status_code == 404
    assert accepted.json() == {"status": "ok"}
    assert invalid.status_code == 422
    assert events.get("om_1:clear_prepared")["status"] == "delivered"
