import json

import pytest

from src.config import ConfigError, load_config


def test_loads_openai_compatible_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "server": {"host": "127.0.0.1", "port": 8000},
        "feishu": {
            "app_id": "cli_xxx",
            "app_secret": "secret",
            "verification_token": "verify",
            "encrypt_key": ""
        },
        "ai": {
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "api_key": "key",
            "model": "test-model",
            "system_prompt": "You are helpful.",
            "temperature": 0.2,
            "max_tokens": 1024,
            "timeout_seconds": 30
        },
        "conversation": {"max_history_messages": 6},
        "knowledge_base": {
            "enabled": True,
            "type": "dual_chain_vault",
            "vault_path": "D:/MySecondBrain/vault",
            "min_score": 3,
            "max_results": 5,
            "max_context_chars": 12000,
            "fallback_when_miss": "answer_with_notice"
        },
        "bot": {"name": "AI助手"}
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.feishu.app_id == "cli_xxx"
    assert config.ai.provider == "openai_compatible"
    assert config.conversation.max_history_messages == 6
    assert config.knowledge_base.enabled is True
    assert config.knowledge_base.vault_path == "D:/MySecondBrain/vault"
    assert config.script_knowledge.enabled is True
    assert config.script_knowledge.path == "knowledge"
    assert config.conversation_log.enabled is True
    assert config.conversation_log.directory == "logs/conversations"


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("ai", "timeout_seconds", 0),
        ("job_queue", "recovery_window_seconds", 0),
        ("muliu", "log_wait_seconds", 0),
        ("muliu", "log_completion_timeout_seconds", 0),
        ("muliu", "start_verification_wait_seconds", 0),
        ("muliu", "request_timeout_seconds", 0),
        ("muliu", "confirmation_timeout_seconds", 0),
    ],
)
def test_rejects_non_positive_operation_timeouts(tmp_path, section, field, value):
    config_path = tmp_path / "config.json"
    config = {
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "api_key": "key",
            "model": "m",
        },
    }
    config.setdefault(section, {})[field] = value
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ConfigError, match="必须大于 0"):
        load_config(config_path)


def test_loads_muliu_defaults_for_a_config_without_a_muliu_section(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.job_queue.recovery_window_seconds == 600
    assert config.muliu.log_completion_timeout_seconds == 1800
    assert config.muliu.start_verification_wait_seconds == 60


def test_rejects_unknown_ai_provider(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "id", "app_secret": "secret"},
        "ai": {"provider": "unknown", "base_url": "https://api.example.com", "api_key": "key", "model": "m"}
    }), encoding="utf-8")

    with pytest.raises(ConfigError, match="Unsupported ai.provider"):
        load_config(config_path)


def test_loads_script_knowledge_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "script_knowledge": {
            "enabled": False,
            "path": "custom-knowledge",
            "min_score": 4,
            "max_results": 2,
            "max_context_chars": 5000,
        },
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.script_knowledge.enabled is False
    assert config.script_knowledge.path == "custom-knowledge"
    assert config.script_knowledge.min_score == 4
    assert config.script_knowledge.max_results == 2
    assert config.script_knowledge.max_context_chars == 5000


def test_loads_conversation_log_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "conversation_log": {
            "enabled": False,
            "directory": "custom-logs",
            "max_text_chars": 4567,
        },
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.conversation_log.enabled is False
    assert config.conversation_log.directory == "custom-logs"
    assert config.conversation_log.max_text_chars == 4567


def test_job_queue_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.job_queue.enabled is True
    assert config.job_queue.database_path == "data/feishu_bot.sqlite3"
    assert config.job_queue.recovery_window_seconds == 600


def test_job_queue_custom_values(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "job_queue": {"enabled": False, "database_path": "tmp/jobs.sqlite3", "recovery_window_seconds": 120},
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.job_queue.enabled is False
    assert config.job_queue.database_path == "tmp/jobs.sqlite3"
    assert config.job_queue.recovery_window_seconds == 120


def test_gateway_requires_only_worker_token_beyond_normal_gateway_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "runtime": {"role": "gateway", "worker_token": "shared-worker-token"},
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "muliu": {"base_url": "", "username": "", "password": ""},
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.runtime.role == "gateway"
    assert config.runtime.worker_token == "shared-worker-token"
    assert config.muliu.base_url == ""


def test_worker_allows_empty_feishu_and_ai_but_requires_gateway_and_identity(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "runtime": {
            "role": "worker",
            "worker_token": "shared-worker-token",
            "worker_id": "jenkins-worker-1",
            "gateway_base_url": "https://feishu.example.com",
        },
        "feishu": {},
        "ai": {},
        "muliu": {"base_url": "http://muliu.internal", "username": "user", "password": "secret"},
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.runtime.role == "worker"
    assert config.feishu.app_id == ""
    assert config.ai.api_key == ""
    assert config.runtime.gateway_base_url == "https://feishu.example.com"


@pytest.mark.parametrize(
    "runtime, pattern",
    [
        ({"role": "gateway"}, "worker_token"),
        ({"role": "worker", "worker_token": "token"}, "worker_id"),
        ({"role": "worker", "worker_token": "token", "worker_id": "id", "gateway_base_url": "http://gateway"}, "HTTPS"),
        ({"role": "worker", "worker_token": "token", "worker_id": "id", "gateway_base_url": "https://gateway", "heartbeat_interval_seconds": 180, "lease_timeout_seconds": 180}, "必须小于"),
    ],
)
def test_rejects_invalid_runtime_configuration(tmp_path, runtime, pattern):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "runtime": runtime,
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
    }), encoding="utf-8")

    with pytest.raises(ConfigError, match=pattern):
        load_config(config_path)


def test_example_configurations_are_valid():
    from pathlib import Path
    root = Path(__file__).parents[1]
    gateway_config = load_config(root / "config" / "config.gateway.example.json")
    worker_config = load_config(root / "config" / "config.worker.example.json")

    assert gateway_config.runtime.role == "gateway"
    assert worker_config.runtime.role == "worker"
