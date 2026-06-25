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


def test_rejects_unknown_ai_provider(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "id", "app_secret": "secret"},
        "ai": {"provider": "unknown", "base_url": "https://api.example.com", "api_key": "key", "model": "m"}
    }), encoding="utf-8")

    with pytest.raises(ConfigError, match="Unsupported ai.provider"):
        load_config(config_path)


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
