import json

import httpx
import pytest

from src.ai_client import create_ai_client
from src.config import AIConfig


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
