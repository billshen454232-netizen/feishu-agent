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
    assert config.ai.strategy == "fallback"
    assert [m.provider for m in config.ai.models] == ["openai_compatible"]
    assert config.conversation.max_history_messages == 6
    assert config.knowledge_base.enabled is True
    assert config.knowledge_base.vault_path == "D:/MySecondBrain/vault"
    assert config.script_knowledge.enabled is True
    assert config.script_knowledge.path == "knowledge"
    assert config.conversation_log.enabled is True
    assert config.conversation_log.directory == "logs/conversations"
    assert config.execution_interpretation.enabled is True
    assert config.execution_interpretation.background_timeout_seconds == 45
    assert config.execution_interpretation.max_concurrent == 2
    assert config.execution_interpretation.artifact_retention_days == 90
    assert config.admin.enabled is False
    assert config.admin.port == 8901
    assert config.execution_interpretation.server_environments_path == "config/server_environments.json"


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
        ("execution_interpretation", "background_timeout_seconds", 0),
        ("execution_interpretation", "max_concurrent", 0),
        ("admin", "port", 0),
        ("execution_interpretation", "max_execution_data_chars", 0),
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


def test_loads_execution_interpretation_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "execution_interpretation": {
            "enabled": False,
            "background_timeout_seconds": 3,
            "server_environments_path": "custom-environments.json",
            "max_execution_data_chars": 4567,
        },
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.execution_interpretation.enabled is False
    assert config.execution_interpretation.background_timeout_seconds == 3
    assert config.execution_interpretation.server_environments_path == "custom-environments.json"
    assert config.execution_interpretation.max_execution_data_chars == 4567


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
        "muliu": {
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

    config = load_config(config_path)

    assert config.runtime.role == "worker"
    assert config.feishu.app_id == ""
    assert config.ai.models == ()
    assert config.runtime.gateway_base_url == "https://feishu.example.com"
    assert config.muliu.routes["cn"].identifier == "route-cn"
    assert config.muliu.routes["cn"].task_id == 101
    assert config.muliu.routes["overseas"].identifier == "route-overseas"
    assert config.muliu.routes["overseas"].task_id == 89


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
        "muliu": {
            "routes": {
                "cn": {"identifier": "route-cn", "task_id": 101, "base_url": "https://cn", "username": "u", "password": "p"},
                "overseas": {"identifier": "route-overseas", "task_id": 89, "base_url": "https://overseas", "username": "u", "password": "p"},
            },
        },
    }), encoding="utf-8")

    with pytest.raises(ConfigError, match=pattern):
        load_config(config_path)


@pytest.mark.parametrize(
    "routes, pattern",
    [
        ({}, "muliu.routes 必须且只能包含 cn 和 overseas"),
        (
            {
                "cn": {"identifier": "cnmuliu", "task_id": 101, "base_url": "https://cn", "username": "u", "password": "p"},
                "overseas": {"identifier": "route-overseas", "task_id": 89, "base_url": "https://overseas", "username": "u", "password": "p"},
            },
            "identifier 必须为 route-cn",
        ),
        (
            {
                "cn": {"identifier": "route-cn", "task_id": 0, "base_url": "https://cn", "username": "u", "password": "p"},
                "overseas": {"identifier": "route-overseas", "task_id": 89, "base_url": "https://overseas", "username": "u", "password": "p"},
            },
            "task_id 必须是正整数",
        ),
    ],
)
def test_worker_rejects_incomplete_or_uncontrolled_route_profiles(tmp_path, routes, pattern):
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
        "muliu": {"routes": routes},
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


def _ai_model(base_url="https://api.example.com/v1", api_key="key", model="m", **extra):
    section = {
        "provider": "openai_compatible",
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }
    section.update(extra)
    return section


def test_ai_pool_defaults_to_fallback_strategy_and_single_model(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": _ai_model(),
    }), encoding="utf-8")

    pool = load_config(config_path).ai

    assert pool.strategy == "fallback"
    assert len(pool.models) == 1
    assert pool.models[0].model == "m"


def test_ai_pool_loads_multiple_models_and_strategy(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {
            "strategy": "round_robin",
            "models": [
                _ai_model(base_url="https://a.example.com/v1", model="model-a"),
                _ai_model(base_url="https://b.example.com/v1", model="model-b"),
            ],
        },
    }), encoding="utf-8")

    pool = load_config(config_path).ai

    assert pool.strategy == "round_robin"
    assert [m.model for m in pool.models] == ["model-a", "model-b"]
    assert [m.base_url for m in pool.models] == ["https://a.example.com/v1", "https://b.example.com/v1"]


def test_ai_pool_models_inherit_shared_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {
            "strategy": "random",
            "defaults": {
                "system_prompt": "共享提示词",
                "temperature": 0.4,
                "max_tokens": 777,
                "timeout_seconds": 33,
            },
            "models": [
                _ai_model(base_url="https://a.example.com/v1", model="model-a"),
                _ai_model(base_url="https://b.example.com/v1", model="model-b"),
            ],
        },
    }), encoding="utf-8")

    pool = load_config(config_path).ai

    for model in pool.models:
        assert model.system_prompt == "共享提示词"
        assert model.temperature == 0.4
        assert model.max_tokens == 777
        assert model.timeout_seconds == 33
    # 两个模型连接信息各自独立，不因继承而混同
    assert [m.model for m in pool.models] == ["model-a", "model-b"]


def test_ai_pool_second_model_may_override_inherited_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {
            "models": [
                _ai_model(),
                _ai_model(
                    provider="claude_messages",
                    base_url="https://b.example.com/v1",
                    model="model-b",
                    system_prompt="兜底专用提示词",
                    max_tokens=512,
                ),
            ],
        },
    }), encoding="utf-8")

    second = load_config(config_path).ai.models[1]

    assert second.provider == "claude_messages"
    assert second.system_prompt == "兜底专用提示词"
    assert second.max_tokens == 512


@pytest.mark.parametrize("ai_section, match", [
    ({"models": []}, "ai.models"),
    ({"strategy": "nope", "models": [_ai_model()]}, "ai.strategy"),
    ({"models": [{"provider": "openai_compatible", "base_url": "https://x", "api_key": "k"}]}, r"ai\.models\[0\]\.model"),
    ({"models": [{"base_url": "https://x", "api_key": "k", "model": "m"}]}, r"ai\.models\[0\]\.provider"),
    ({"models": [{"provider": "unknown", "base_url": "https://x", "api_key": "k", "model": "m"}]},
     r"Unsupported ai\.models\[0\]\.provider"),
])
def test_ai_pool_rejects_incomplete_or_unknown_shapes(tmp_path, ai_section, match):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": ai_section,
    }), encoding="utf-8")

    with pytest.raises(ConfigError, match=match):
        load_config(config_path)


def _write_ai(tmp_path, ai_section):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": ai_section,
    }), encoding="utf-8")
    return config_path


def test_ai_role_pools_route_each_role_and_merge_pool_defaults(tmp_path):
    config = load_config(_write_ai(tmp_path, {
        "defaults": {"temperature": 0.3, "timeout_seconds": 60},
        "pools": {
            "fast": {
                "strategy": "random",
                "defaults": {"timeout_seconds": 20},
                "models": [_ai_model(model="flash"), _ai_model(model="luna")],
            },
            "strong": {"strategy": "fallback", "models": [_ai_model(model="terra")]},
        },
        "routing": {"planning": "fast", "chat": "fast", "interpretation": "strong"},
    }))

    assert [m.model for m in config.ai_for("planning").models] == ["flash", "luna"]
    assert config.ai_for("chat") is config.ai_for("planning")
    assert [m.model for m in config.ai_for("interpretation").models] == ["terra"]
    assert config.ai_for("planning").strategy == "random"
    assert all(m.timeout_seconds == 20 and m.temperature == 0.3 for m in config.ai_for("planning").models)
    assert config.ai_for("interpretation").models[0].timeout_seconds == 60
    # 未在 routing 中写 default 时，默认池为第一个池
    assert config.ai is config.ai_for("planning")


def test_ai_role_pools_unrouted_roles_use_default(tmp_path):
    config = load_config(_write_ai(tmp_path, {
        "pools": {
            "fast": {"models": [_ai_model(model="luna")]},
            "strong": {"models": [_ai_model(model="terra")]},
        },
        "routing": {"default": "strong", "planning": "fast"},
    }))

    assert config.ai_for("planning").models[0].model == "luna"
    assert config.ai_for("chat").models[0].model == "terra"
    assert config.ai_for("interpretation").models[0].model == "terra"
    assert config.ai.models[0].model == "terra"


def test_legacy_ai_config_serves_every_role(tmp_path):
    config = load_config(_write_ai(tmp_path, _ai_model(model="terra")))

    assert config.ai_routes == {}
    for role in ("planning", "chat", "interpretation"):
        assert config.ai_for(role) is config.ai


@pytest.mark.parametrize("ai_section, match", [
    ({"pools": {}}, "ai.pools"),
    ({"pools": {"a": {"models": [_ai_model()]}}, "models": [_ai_model()]}, "不能同时配置"),
    ({"pools": {"a": {"models": [_ai_model()]}}, "routing": {"planning": "missing"}}, "不存在的池"),
    ({"pools": {"a": {"models": [_ai_model()]}}, "routing": {"report": "a"}}, "不支持的用途"),
    ({"pools": {"a": {"models": []}}}, r"ai\.pools\.a\.models"),
    ({"pools": {"a": {"strategy": "nope", "models": [_ai_model()]}}}, r"ai\.pools\.a\.strategy"),
])
def test_ai_role_pools_reject_invalid_shapes(tmp_path, ai_section, match):
    with pytest.raises(ConfigError, match=match):
        load_config(_write_ai(tmp_path, ai_section))
