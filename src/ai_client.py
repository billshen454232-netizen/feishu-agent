from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple, Protocol

import httpx

from .config import AIConfig

logger = logging.getLogger(__name__)

# Only transient upstream failures are worth retrying: Cloudflare 5xx, rate
# limits and network blips recover on their own. Auth and request-shape errors
# (the remaining 4xx) never do, so they fail fast and reach the failure card
# instead of making the requester wait out a pointless retry budget.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5


def is_retryable_ai_error(error: BaseException) -> bool:
    """Return True only for failures that a later attempt can plausibly fix."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUS_CODES
    return isinstance(error, httpx.TransportError)


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    **kwargs: object,
) -> httpx.Response:
    """POST once, retrying only transient failures with a bounded exponential backoff.

    The attempt budget is small on purpose: this runs on the request path and a
    Feishu requester is waiting, so total added latency stays under a couple of
    seconds for fast failures such as an upstream 5xx.
    """
    attempt = 0
    while True:
        try:
            response = await client.post(url, **kwargs)  # type: ignore[arg-type]
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if attempt >= max_retries or not is_retryable_ai_error(exc):
                raise
            delay = retry_backoff_seconds * (2**attempt)
            logger.warning(
                "AI call failed, retrying attempt=%s/%s delay=%.2fs error=%s",
                attempt + 1,
                max_retries,
                delay,
                exc,
            )
            attempt += 1
            await asyncio.sleep(delay)


class AIClient(Protocol):
    async def generate(self, history: list[dict[str, str]], user_message: str) -> str:
        """Generate an assistant reply from conversation history and the new user message."""


class AIGeneration(NamedTuple):
    """One model answer plus the provenance a presenter must not lose."""

    text: str
    model: str
    degraded: bool


async def generate_with_provenance(
    client: AIClient,
    history: list[dict[str, str]],
    user_message: str,
) -> AIGeneration:
    """Ask for an answer and where it came from, without changing ``generate()``.

    ``generate()`` keeps returning bare text because callers such as the planner
    parse it as a strict JSON plan; any marker appended there would corrupt the
    parse. Presentation code that needs to label the producing model calls this
    instead. Test doubles that only implement ``generate`` still work.
    """
    getter = getattr(client, "generate_with_provenance", None)
    if callable(getter):
        return await getter(history, user_message)
    return AIGeneration(await client.generate(history, user_message), "未知模型", False)


def degradation_notice(generation: AIGeneration) -> str:
    """Return a presentation-only marker, or an empty string for a primary answer.

    A fallback model is chosen because it is cheaper and weaker, so its answers
    must never pass for the primary model's. This text is appended to the reply
    shown to the requester only; it is never fed back into a prompt or plan.
    """
    if not generation.degraded:
        return ""
    return (
        "\n\n---\n\n⚠️ 本次回复由兜底模型「{}」生成（主模型暂不可用），"
        "能力弱于主模型，请谨慎采信其结论。".format(generation.model)
    )


class OpenAICompatibleClient:
    def __init__(
        self,
        config: AIConfig,
        http_client: httpx.AsyncClient | None = None,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self._config = config
        self._client = http_client or httpx.AsyncClient(timeout=config.timeout_seconds)
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    async def generate(self, history: list[dict[str, str]], user_message: str) -> str:
        messages = [{"role": "system", "content": self._config.system_prompt}, *history, {"role": "user", "content": user_message}]
        response = await _post_with_retry(
            self._client,
            f"{self._config.base_url}/chat/completions",
            max_retries=self._max_retries,
            retry_backoff_seconds=self._retry_backoff_seconds,
            headers={"Authorization": f"Bearer {self._config.api_key}", "Content-Type": "application/json"},
            json={
                "model": self._config.model,
                "messages": messages,
                "temperature": self._config.temperature,
                "max_tokens": self._config.max_tokens,
            },
        )
        data = response.json()
        return str(data["choices"][0]["message"]["content"]).strip()

    async def generate_with_provenance(
        self, history: list[dict[str, str]], user_message: str
    ) -> AIGeneration:
        return AIGeneration(await self.generate(history, user_message), self._config.model, False)


class ClaudeMessagesClient:
    def __init__(
        self,
        config: AIConfig,
        http_client: httpx.AsyncClient | None = None,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self._config = config
        self._client = http_client or httpx.AsyncClient(timeout=config.timeout_seconds)
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    async def generate(self, history: list[dict[str, str]], user_message: str) -> str:
        messages = [*history, {"role": "user", "content": user_message}]
        response = await _post_with_retry(
            self._client,
            f"{self._config.base_url}/messages",
            max_retries=self._max_retries,
            retry_backoff_seconds=self._retry_backoff_seconds,
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
        data = response.json()
        text_parts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        return "".join(text_parts).strip()

    async def generate_with_provenance(
        self, history: list[dict[str, str]], user_message: str
    ) -> AIGeneration:
        return AIGeneration(await self.generate(history, user_message), self._config.model, False)


class FallbackAIClient:
    """Try the primary model first, then a cheaper fallback when it fails.

    Degradation is never silent: the fallback's answers are tagged so a weaker
    model cannot pass for the primary one, and the switch is logged. The fallback
    runs after the primary's own retry budget is exhausted, so it is a genuine
    last resort rather than a load balancer.
    """

    def __init__(self, primary: AIClient, fallback: AIClient, *, primary_model: str, fallback_model: str) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_model = primary_model
        self._fallback_model = fallback_model

    async def generate(self, history: list[dict[str, str]], user_message: str) -> str:
        generation = await self.generate_with_provenance(history, user_message)
        return generation.text

    async def generate_with_provenance(
        self, history: list[dict[str, str]], user_message: str
    ) -> AIGeneration:
        try:
            return AIGeneration(await self._primary.generate(history, user_message), self._primary_model, False)
        except Exception as exc:  # noqa: BLE001 - fallback must absorb primary failures
            logger.warning(
                "primary AI model failed, degrading to fallback model=%s error=%s",
                self._fallback_model,
                exc,
            )
        return AIGeneration(await self._fallback.generate(history, user_message), self._fallback_model, True)


def create_ai_client(
    config: AIConfig,
    http_client: httpx.AsyncClient | None = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> AIClient:
    retry_kwargs = {
        "max_retries": max_retries,
        "retry_backoff_seconds": retry_backoff_seconds,
    }
    primary = _build_single_client(config, http_client=http_client, **retry_kwargs)
    if config.fallback is None:
        return primary
    fallback = _build_single_client(config.fallback, http_client=http_client, **retry_kwargs)
    return FallbackAIClient(
        primary,
        fallback,
        primary_model=config.model,
        fallback_model=config.fallback.model,
    )


def _build_single_client(
    config: AIConfig,
    http_client: httpx.AsyncClient | None = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> AIClient:
    retry_kwargs = {
        "max_retries": max_retries,
        "retry_backoff_seconds": retry_backoff_seconds,
    }
    if config.provider == "openai_compatible":
        return OpenAICompatibleClient(config, http_client=http_client, **retry_kwargs)
    if config.provider == "claude_messages":
        return ClaudeMessagesClient(config, http_client=http_client, **retry_kwargs)
    raise ValueError(f"Unsupported provider: {config.provider}")