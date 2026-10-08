import json

from fastapi.testclient import TestClient

from src.app import _create_default_app, create_app


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
            "base_url": "http://muliu.internal",
            "username": "user",
            "password": "secret",
            "script_catalog_path": str(catalog_path),
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
