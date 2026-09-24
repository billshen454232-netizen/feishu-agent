from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


class ConfigError(ValueError):
    """Raised when the JSON configuration is missing or invalid."""


Provider = Literal["openai_compatible", "claude_messages"]


def _require_positive_number(value: float, label: str) -> float:
    if value <= 0:
        raise ConfigError("{} 必须大于 0".format(label))
    return value


def _require_positive_integer(value: int, label: str) -> int:
    if value <= 0:
        raise ConfigError("{} 必须大于 0".format(label))
    return value


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    verification_token: str = ""
    encrypt_key: str = ""
    base_url: str = "https://open.feishu.cn"


@dataclass(frozen=True)
class AIConfig:
    provider: Provider
    base_url: str
    api_key: str
    model: str
    system_prompt: str = "你是一个飞书里的 AI 助手，请用简洁、准确的中文回答。"
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class ConversationConfig:
    max_history_messages: int = 12


@dataclass(frozen=True)
class KnowledgeBaseConfig:
    enabled: bool = False
    type: str = "dual_chain_vault"
    vault_path: str = ""
    min_score: int = 3
    max_results: int = 5
    max_context_chars: int = 12000
    fallback_when_miss: str = "answer_with_notice"


@dataclass(frozen=True)
class BotConfig:
    name: str = "AI助手"


@dataclass(frozen=True)
class JobQueueConfig:
    enabled: bool = True
    database_path: str = "data/feishu_bot.sqlite3"
    recovery_window_seconds: int = 600


@dataclass(frozen=True)
class MuliuConfig:
    enabled: bool = True
    base_url: str = ""
    username: str = ""
    password: str = ""
    task_id: int = 89
    step: int = 11
    script_index: int = 0
    parameter_index: int = 0
    # Interval between Task 89 log polls after a confirmed operation is submitted.
    log_wait_seconds: float = 5.0
    # Maximum time to wait for Task 89's AI_OP_END terminal marker.
    log_completion_timeout_seconds: float = 1800.0
    # Delay after start exits before checking whether game processes finished initializing.
    start_verification_wait_seconds: float = 60.0
    request_timeout_seconds: float = 20.0
    script_root: str = "/home/serverGeneralScript"
    script_catalog_path: str = "config/muliu_script_catalog.md"
    max_steps: int = 10
    max_arguments_per_step: int = 32
    max_argument_length: int = 1024
    confirmation_prefix: str = "确认执行"
    confirmation_timeout_seconds: int = 600
    blocked_keywords: tuple[str, ...] = field(default_factory=tuple)
    blocked_patterns: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ScriptKnowledgeConfig:
    enabled: bool = True
    path: str = "knowledge"
    min_score: int = 2
    max_results: int = 3
    max_context_chars: int = 10000


@dataclass(frozen=True)
class ConversationLogConfig:
    enabled: bool = True
    directory: str = "logs/conversations"
    max_text_chars: int = 12000


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    feishu: FeishuConfig
    ai: AIConfig
    conversation: ConversationConfig
    knowledge_base: KnowledgeBaseConfig
    bot: BotConfig
    job_queue: JobQueueConfig
    muliu: MuliuConfig = field(default_factory=MuliuConfig)
    script_knowledge: ScriptKnowledgeConfig = field(default_factory=ScriptKnowledgeConfig)
    conversation_log: ConversationLogConfig = field(default_factory=ConversationLogConfig)


def _require_str(data: dict[str, Any], key: str, section: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"Missing required {section}.{key}")
    return value


def load_config(path: str | Path = "config/config.json") -> AppConfig:
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON config: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a JSON object")

    server_raw = raw.get("server", {})
    feishu_raw = raw.get("feishu", {})
    ai_raw = raw.get("ai", {})
    conversation_raw = raw.get("conversation", {})
    knowledge_base_raw = raw.get("knowledge_base", {})
    bot_raw = raw.get("bot", {})
    job_queue_raw = raw.get("job_queue", {})
    muliu_raw = raw.get("muliu", {})
    script_knowledge_raw = raw.get("script_knowledge", {})
    conversation_log_raw = raw.get("conversation_log", {})

    if not isinstance(server_raw, dict) or not isinstance(feishu_raw, dict) or not isinstance(ai_raw, dict):
        raise ConfigError("server, feishu, and ai sections must be JSON objects")
    if not isinstance(muliu_raw, dict):
        raise ConfigError("muliu section must be a JSON object")
    if not isinstance(script_knowledge_raw, dict):
        raise ConfigError("script_knowledge section must be a JSON object")
    if not isinstance(conversation_log_raw, dict):
        raise ConfigError("conversation_log section must be a JSON object")

    provider = _require_str(ai_raw, "provider", "ai")
    if provider not in ("openai_compatible", "claude_messages"):
        raise ConfigError(f"Unsupported ai.provider: {provider}")

    return AppConfig(
        server=ServerConfig(
            host=str(server_raw.get("host", "127.0.0.1")),
            port=int(server_raw.get("port", 8000)),
        ),
        feishu=FeishuConfig(
            app_id=_require_str(feishu_raw, "app_id", "feishu"),
            app_secret=_require_str(feishu_raw, "app_secret", "feishu"),
            verification_token=str(feishu_raw.get("verification_token", "")),
            encrypt_key=str(feishu_raw.get("encrypt_key", "")),
            base_url=str(feishu_raw.get("base_url", "https://open.feishu.cn")).rstrip("/"),
        ),
        ai=AIConfig(
            provider=provider,  # type: ignore[arg-type]
            base_url=_require_str(ai_raw, "base_url", "ai").rstrip("/"),
            api_key=_require_str(ai_raw, "api_key", "ai"),
            model=_require_str(ai_raw, "model", "ai"),
            system_prompt=str(ai_raw.get("system_prompt", "你是一个飞书里的 AI 助手，请用简洁、准确的中文回答。")),
            temperature=float(ai_raw.get("temperature", 0.2)),
            max_tokens=int(ai_raw.get("max_tokens", 2048)),
            timeout_seconds=_require_positive_number(
                float(ai_raw.get("timeout_seconds", 60)),
                "ai.timeout_seconds",
            ),
        ),
        conversation=ConversationConfig(
            max_history_messages=int(conversation_raw.get("max_history_messages", 12))
            if isinstance(conversation_raw, dict)
            else 12
        ),
        knowledge_base=KnowledgeBaseConfig(
            enabled=bool(knowledge_base_raw.get("enabled", False)) if isinstance(knowledge_base_raw, dict) else False,
            type=str(knowledge_base_raw.get("type", "dual_chain_vault")) if isinstance(knowledge_base_raw, dict) else "dual_chain_vault",
            vault_path=str(knowledge_base_raw.get("vault_path", "")) if isinstance(knowledge_base_raw, dict) else "",
            min_score=int(knowledge_base_raw.get("min_score", 3)) if isinstance(knowledge_base_raw, dict) else 3,
            max_results=int(knowledge_base_raw.get("max_results", 5)) if isinstance(knowledge_base_raw, dict) else 5,
            max_context_chars=int(knowledge_base_raw.get("max_context_chars", 12000)) if isinstance(knowledge_base_raw, dict) else 12000,
            fallback_when_miss=str(knowledge_base_raw.get("fallback_when_miss", "answer_with_notice")) if isinstance(knowledge_base_raw, dict) else "answer_with_notice",
        ),
        bot=BotConfig(
            name=str(bot_raw.get("name", "AI助手")) if isinstance(bot_raw, dict) else "AI助手"
        ),
        job_queue=JobQueueConfig(
            enabled=bool(job_queue_raw.get("enabled", True)) if isinstance(job_queue_raw, dict) else True,
            database_path=str(job_queue_raw.get("database_path", "data/feishu_bot.sqlite3")) if isinstance(job_queue_raw, dict) else "data/feishu_bot.sqlite3",
            recovery_window_seconds=_require_positive_integer(
                int(job_queue_raw.get("recovery_window_seconds", 600)) if isinstance(job_queue_raw, dict) else 600,
                "job_queue.recovery_window_seconds",
            ),
        ),
        muliu=MuliuConfig(
            enabled=bool(muliu_raw.get("enabled", True)),
            base_url=str(muliu_raw.get("base_url", "")).rstrip("/"),
            username=str(muliu_raw.get("username", "")),
            password=str(muliu_raw.get("password", "")),
            task_id=int(muliu_raw.get("task_id", 89)),
            step=int(muliu_raw.get("step", 11)),
            script_index=int(muliu_raw.get("script_index", 0)),
            parameter_index=int(muliu_raw.get("parameter_index", 0)),
            log_wait_seconds=_require_positive_number(
                float(muliu_raw.get("log_wait_seconds", 5)),
                "muliu.log_wait_seconds",
            ),
            log_completion_timeout_seconds=_require_positive_number(
                float(muliu_raw.get("log_completion_timeout_seconds", 1800)),
                "muliu.log_completion_timeout_seconds",
            ),
            start_verification_wait_seconds=_require_positive_number(
                float(muliu_raw.get("start_verification_wait_seconds", 60)),
                "muliu.start_verification_wait_seconds",
            ),
            request_timeout_seconds=_require_positive_number(
                float(muliu_raw.get("request_timeout_seconds", 20)),
                "muliu.request_timeout_seconds",
            ),
            script_root=str(muliu_raw.get("script_root", "/home/serverGeneralScript")),
            script_catalog_path=str(muliu_raw.get("script_catalog_path", "config/muliu_script_catalog.md")),
            max_steps=int(muliu_raw.get("max_steps", 10)),
            max_arguments_per_step=int(muliu_raw.get("max_arguments_per_step", 32)),
            max_argument_length=int(muliu_raw.get("max_argument_length", 1024)),
            confirmation_prefix=str(muliu_raw.get("confirmation_prefix", "确认执行")),
            confirmation_timeout_seconds=_require_positive_integer(
                int(muliu_raw.get("confirmation_timeout_seconds", 600)),
                "muliu.confirmation_timeout_seconds",
            ),
            blocked_keywords=tuple(str(item) for item in muliu_raw.get("blocked_keywords", [])),
            blocked_patterns=tuple(str(item) for item in muliu_raw.get("blocked_patterns", [])),
        ),
        script_knowledge=ScriptKnowledgeConfig(
            enabled=bool(script_knowledge_raw.get("enabled", True)),
            path=str(script_knowledge_raw.get("path", "knowledge")),
            min_score=int(script_knowledge_raw.get("min_score", 2)),
            max_results=int(script_knowledge_raw.get("max_results", 3)),
            max_context_chars=int(script_knowledge_raw.get("max_context_chars", 10000)),
        ),
        conversation_log=ConversationLogConfig(
            enabled=bool(conversation_log_raw.get("enabled", True)),
            directory=str(conversation_log_raw.get("directory", "logs/conversations")),
            max_text_chars=int(conversation_log_raw.get("max_text_chars", 12000)),
        ),
    )
