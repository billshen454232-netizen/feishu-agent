from __future__ import annotations

from typing import Protocol

import httpx

from .config import AIConfig


class AIClient(Protocol):
    async def generate(self, history: list[dict[str, str]], user_message: str) -> str:
        """Generate an assistant reply from conversation history and the new user message."""


class OpenAICompatibleClient:
    def __init__(self, config: AIConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client or httpx.AsyncClient(timeout=config.timeout_seconds)

    async def generate(self, history: list[dict[str, str]], user_message: str) -> str:
        messages = [{"role": "system", "content": self._config.system_prompt}, *history, {"role": "user", "content": user_message}]
        response = await self._client.post(
            f"{self._config.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._config.api_key}", "Content-Type": "application/json"},
            json={
                "model": self._config.model,
                "messages": messages,
                "temperature": self._config.temperature,
                "max_tokens": self._config.max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return str(data["choices"][0]["message"]["content"]).strip()


class ClaudeMessagesClient:
    def __init__(self, config: AIConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client or httpx.AsyncClient(timeout=config.timeout_seconds)

    async def generate(self, history: list[dict[str, str]], user_message: str) -> str:
        messages = [*history, {"role": "user", "content": user_message}]
        response = await self._client.post(
            f"{self._config.base_url}/messages",
            headers={
                "x-api-key": self._config.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": self._config.model,
                "system": self._config.system_prompt,
                "messages": messages,
                "max_tokens": self._config.max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        text_parts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        return "".join(text_parts).strip()


def create_ai_client(config: AIConfig, http_client: httpx.AsyncClient | None = None) -> AIClient:
    if config.provider == "openai_compatible":
        return OpenAICompatibleClient(config, http_client=http_client)
    if config.provider == "claude_messages":
        return ClaudeMessagesClient(config, http_client=http_client)
    raise ValueError(f"Unsupported provider: {config.provider}")