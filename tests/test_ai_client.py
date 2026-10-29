import json

import httpx
import pytest

from src.ai_client import (
    AIGeneration,
    FallbackAIClient,
    create_ai_client,
    degradation_notice,
    generate_with_provenance,
    is_retryable_ai_error,
)
from src.config import AIConfig


def _openai_config() -> AIConfig:
    return AIConfig(
        provider="openai_compatible",
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="gpt-test",
        system_prompt="sys",
    )


def _claude_config() -> AIConfig:
    return AIConfig(
        provider="claude_messages",
        base_url="https://api.anthropic.com/v1",
        api_key="sk-test",
        model="claude-test",
        system_prompt="sys",
        max_tokens=456,
    )


def _recording_handler(responses):
    """Serve ``responses`` in order and record every request attempt."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        outcome = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return handler, calls


@pytest.mark.asyncio
async def test_openai_compatible_client_sends_chat_completion_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "你好"}}]})

    client = create_ai_client(
        AIConfig(
            provider="openai_compatible",
            base_url="https://api.example.com/v1",
            api_key="secret",
            model="gpt-test",
            system_prompt="sys",
            temperature=0.3,
            max_tokens=123,
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await client.generate([{"role": "user", "content": "旧消息"}], "新消息")

    assert result == "你好"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer secret"
    assert captured["json"]["model"] == "gpt-test"
    assert captured["json"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "旧消息"},
        {"role": "user", "content": "新消息"},
    ]


@pytest.mark.asyncio
async def test_claude_messages_client_sends_messages_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")
        captured["version"] = request.headers.get("anthropic-version")
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "收到"}]})

    client = create_ai_client(
        AIConfig(
            provider="claude_messages",
            base_url="https://api.anthropic.com/v1",
            api_key="sk-test",
            model="claude-test",
            system_prompt="sys",
            max_tokens=456,
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await client.generate([{"role": "assistant", "content": "旧回复"}], "新问题")

    assert result == "收到"
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["api_key"] == "sk-test"
    assert captured["version"] == "2023-06-01"
    assert captured["json"]["system"] == "sys"
    assert captured["json"]["messages"] == [
        {"role": "assistant", "content": "旧回复"},
        {"role": "user", "content": "新问题"},
    ]


@pytest.mark.asyncio
async def test_retries_transient_5xx_then_succeeds():
    handler, calls = _recording_handler([
        httpx.Response(520, text="origin error"),
        httpx.Response(520, text="origin error"),
        httpx.Response(200, json={"choices": [{"message": {"content": "重试后成功"}}]}),
    ])
    client = create_ai_client(
        _openai_config(),
        retry_backoff_seconds=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    result = await client.generate([], "查一下")

    assert result == "重试后成功"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_exhausted_retries_raise_the_last_error():
    handler, calls = _recording_handler([httpx.Response(520, text="origin error")])
    client = create_ai_client(
        _openai_config(),
        retry_backoff_seconds=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.generate([], "查一下")

    assert len(calls) == 3


@pytest.mark.asyncio
async def test_client_errors_do_not_retry():
    handler, calls = _recording_handler([httpx.Response(401, text="bad key")])
    client = create_ai_client(
        _openai_config(),
        retry_backoff_seconds=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.generate([], "查一下")

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_is_retried():
    handler, calls = _recording_handler([
        httpx.Response(429, text="slow down"),
        httpx.Response(200, json={"choices": [{"message": {"content": "好了"}}]}),
    ])
    client = create_ai_client(
        _openai_config(),
        retry_backoff_seconds=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await client.generate([], "查一下") == "好了"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_network_errors_are_retried():
    handler, calls = _recording_handler([
        httpx.ConnectError("connection refused"),
        httpx.Response(200, json={"choices": [{"message": {"content": "连上了"}}]}),
    ])
    client = create_ai_client(
        _openai_config(),
        retry_backoff_seconds=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await client.generate([], "查一下") == "连上了"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_claude_messages_client_also_retries():
    handler, calls = _recording_handler([
        httpx.Response(503, text="upstream busy"),
        httpx.Response(200, json={"content": [{"type": "text", "text": "重试后收到"}]}),
    ])
    client = create_ai_client(
        _claude_config(),
        retry_backoff_seconds=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await client.generate([], "新问题") == "重试后收到"
    assert len(calls) == 2


def test_retryability_classification():
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    assert is_retryable_ai_error(httpx.HTTPStatusError("boom", request=request, response=httpx.Response(520, request=request)))
    assert is_retryable_ai_error(httpx.HTTPStatusError("boom", request=request, response=httpx.Response(429, request=request)))
    assert not is_retryable_ai_error(httpx.HTTPStatusError("boom", request=request, response=httpx.Response(401, request=request)))
    assert not is_retryable_ai_error(httpx.HTTPStatusError("boom", request=request, response=httpx.Response(400, request=request)))
    assert is_retryable_ai_error(httpx.ReadTimeout("timed out"))
    assert is_retryable_ai_error(httpx.ConnectError("refused"))
    assert not is_retryable_ai_error(ValueError("not a transport error"))


def _fallback_config(**overrides):
    base = dict(
        provider="openai_compatible",
        base_url="https://fallback.example.com/v1",
        api_key="fb-key",
        model="minimax-3.1",
        system_prompt="sys",
    )
    base.update(overrides)
    return AIConfig(**base)


class _ScriptedClient:
    """Test double returning queued outcomes without touching the network."""

    def __init__(self, model, outcomes):
        self.model = model
        self._outcomes = list(outcomes)
        self.calls = 0

    async def generate(self, history, user_message):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def generate_with_provenance(self, history, user_message):
        text = await self.generate(history, user_message)
        return AIGeneration(text, self.model, False)


@pytest.mark.asyncio
async def test_primary_success_does_not_degrade():
    primary = _ScriptedClient("gpt-test", ["主力回答"])
    fallback = _ScriptedClient("minimax-3.1", ["兜底回答"])
    client = FallbackAIClient(primary, fallback, primary_model="gpt-test", fallback_model="minimax-3.1")

    generation = await client.generate_with_provenance([], "查一下")

    assert generation.text == "主力回答"
    assert generation.model == "gpt-test"
    assert generation.degraded is False
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_primary_failure_falls_back_and_is_marked_degraded():
    primary = _ScriptedClient("gpt-test", [RuntimeError("primary down")])
    fallback = _ScriptedClient("minimax-3.1", ["兜底回答"])
    client = FallbackAIClient(primary, fallback, primary_model="gpt-test", fallback_model="minimax-3.1")

    generation = await client.generate_with_provenance([], "查一下")

    assert generation.text == "兜底回答"
    assert generation.model == "minimax-3.1"
    assert generation.degraded is True
    assert primary.calls == 1
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_both_models_failing_raises():
    primary = _ScriptedClient("gpt-test", [RuntimeError("primary down")])
    fallback = _ScriptedClient("minimax-3.1", [RuntimeError("fallback down")])
    client = FallbackAIClient(primary, fallback, primary_model="gpt-test", fallback_model="minimax-3.1")

    with pytest.raises(RuntimeError, match="fallback down"):
        await client.generate([], "查一下")


def test_degradation_notice_is_empty_for_primary_answers():
    assert degradation_notice(AIGeneration("答", "gpt-test", False)) == ""


def test_degradation_notice_names_the_fallback_model():
    notice = degradation_notice(AIGeneration("答", "minimax-3.1", True))

    assert "minimax-3.1" in notice
    assert "兜底模型" in notice
    assert "谨慎采信" in notice


@pytest.mark.asyncio
async def test_plain_test_double_gets_provenance_without_crashing():
    class PlainDouble:
        async def generate(self, history, user_message):
            return "只实现了 generate"

    generation = await generate_with_provenance(PlainDouble(), [], "查一下")

    assert generation.text == "只实现了 generate"
    assert generation.degraded is False


@pytest.mark.asyncio
async def test_create_ai_client_wraps_fallback_when_configured():
    handler, calls = _recording_handler([
        httpx.Response(520, text="origin error"),
        httpx.Response(200, json={"choices": [{"message": {"content": "兜底回答"}}]}),
    ])
    config = AIConfig(
        provider="openai_compatible",
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="gpt-test",
        system_prompt="sys",
        fallback=_fallback_config(),
    )
    client = create_ai_client(
        config,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
        retry_backoff_seconds=0,
    )

    generation = await client.generate_with_provenance([], "查一下")

    assert generation.text == "兜底回答"
    assert generation.model == "minimax-3.1"
    assert generation.degraded is True
    assert len(calls) == 2
