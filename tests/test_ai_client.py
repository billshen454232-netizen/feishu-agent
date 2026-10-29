import json

import httpx
import pytest

from src.ai_client import (
    AIGeneration,
    PoolAIClient,
    create_ai_client,
    generate_with_provenance,
    is_retryable_ai_error,
)
from src.config import AIConfig, AIPoolConfig


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


def _model(base_url, model):
    return AIConfig(
        provider="openai_compatible",
        base_url=base_url,
        api_key="k",
        model=model,
        system_prompt="sys",
    )


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
async def test_pool_first_entry_succeeds_without_touching_others():
    first = _ScriptedClient("model-a", ["A 的回答"])
    second = _ScriptedClient("model-b", ["B 的回答"])
    pool = PoolAIClient([(first, "model-a"), (second, "model-b")], strategy="fallback")

    generation = await pool.generate_with_provenance([], "查一下")

    assert generation.text == "A 的回答"
    assert generation.model == "model-a"
    assert second.calls == 0


@pytest.mark.asyncio
async def test_pool_falls_through_to_next_model_on_failure():
    first = _ScriptedClient("model-a", [RuntimeError("A 挂了")])
    second = _ScriptedClient("model-b", ["B 的回答"])
    pool = PoolAIClient([(first, "model-a"), (second, "model-b")], strategy="fallback")

    generation = await pool.generate_with_provenance([], "查一下")

    assert generation.text == "B 的回答"
    assert generation.model == "model-b"
    assert first.calls == 1 and second.calls == 1


@pytest.mark.asyncio
async def test_pool_raises_last_error_when_every_model_fails():
    first = _ScriptedClient("model-a", [RuntimeError("A 挂了")])
    second = _ScriptedClient("model-b", [RuntimeError("B 也挂了")])
    pool = PoolAIClient([(first, "model-a"), (second, "model-b")], strategy="fallback")

    with pytest.raises(RuntimeError, match="B 也挂了"):
        await pool.generate([], "查一下")


def test_pool_rejects_empty_entries():
    with pytest.raises(ValueError, match="AI 池不能为空"):
        PoolAIClient([])


def test_round_robin_rotates_the_first_choice():
    first = _ScriptedClient("model-a", ["A"])
    second = _ScriptedClient("model-b", ["B"])
    pool = PoolAIClient([(first, "model-a"), (second, "model-b")], strategy="round_robin")

    orders = [entry.model for entry in pool._ordered_entries() for _ in range(0)] or None
    # 直接看两轮的首选顺序
    first_round = [c.model for c, _ in pool._ordered_entries()]
    second_round = [c.model for c, _ in pool._ordered_entries()]
    assert first_round[0] != second_round[0]
    assert sorted(first_round) == sorted(second_round) == ["model-a", "model-b"]


def test_fallback_strategy_always_prefers_first_entry():
    first = _ScriptedClient("model-a", ["A"])
    second = _ScriptedClient("model-b", ["B"])
    pool = PoolAIClient([(first, "model-a"), (second, "model-b")], strategy="fallback")

    for _ in range(3):
        assert [c.model for c, _ in pool._ordered_entries()] == ["model-a", "model-b"]


def test_random_strategy_spreads_across_the_pool():
    entries = [(_ScriptedClient("m{}".format(i), ["x"]), "m{}".format(i)) for i in range(4)]
    pool = PoolAIClient(entries, strategy="random")

    firsts = {pool._ordered_entries()[0][1] for _ in range(40)}
    assert len(firsts) > 1


@pytest.mark.asyncio
async def test_create_ai_client_builds_pool_from_config():
    handler, calls = _recording_handler([
        httpx.Response(520, text="origin error"),
        httpx.Response(200, json={"choices": [{"message": {"content": "第二个模型的回答"}}]}),
    ])
    pool_config = AIPoolConfig(
        strategy="fallback",
        models=(
            _model("https://a.example.com/v1", "model-a"),
            _model("https://b.example.com/v1", "model-b"),
        ),
    )
    client = create_ai_client(
        pool_config,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
        retry_backoff_seconds=0,
    )

    generation = await client.generate_with_provenance([], "查一下")

    assert generation.text == "第二个模型的回答"
    assert generation.model == "model-b"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_create_ai_client_accepts_single_model_config():
    handler, calls = _recording_handler([
        httpx.Response(200, json={"choices": [{"message": {"content": "单模型回答"}}]}),
    ])
    client = create_ai_client(
        _model("https://api.example.com/v1", "only-model"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_backoff_seconds=0,
    )

    assert await client.generate([], "查一下") == "单模型回答"
    assert len(calls) == 1
