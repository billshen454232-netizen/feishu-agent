from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class ConfigError(ValueError):
    """Raised when the JSON configuration is missing or invalid."""


Provider = Literal["openai_compatible", "claude_messages"]


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
class AppConfig:
    server: ServerConfig
    feishu: FeishuConfig
    ai: AIConfig
    conversation: ConversationConfig
    knowledge_base: KnowledgeBaseConfig
    bot: BotConfig
    job_queue: JobQueueConfig


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

    if not isinstance(server_raw, dict) or not isinstance(feishu_raw, dict) or not isinstance(ai_raw, dict):
        raise ConfigError("server, feishu, and ai sections must be JSON objects")

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
            timeout_seconds=float(ai_raw.get("timeout_seconds", 60)),
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
            recovery_window_seconds=int(job_queue_raw.get("recovery_window_seconds", 600)) if isinstance(job_queue_raw, dict) else 600,
        ),
    )
