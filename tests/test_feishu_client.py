import json

import httpx
import pytest

from src.config import FeishuConfig
from src.feishu_client import FeishuClient


@pytest.mark.asyncio
async def test_get_tenant_access_token_fetches_and_caches_token():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        body = json.loads(request.content.decode("utf-8"))
        assert body == {"app_id": "app", "app_secret": "secret"}
        return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})

    client = FeishuClient(
        FeishuConfig(app_id="app", app_secret="secret", base_url="https://open.feishu.cn"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    first = await client.get_tenant_access_token()
    second = await client.get_tenant_access_token()

    assert first == "tenant-token"
    assert second == "tenant-token"
    assert calls == ["https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"]


@pytest.mark.asyncio
async def test_reply_card_posts_interactive_message_reply():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})
        assert str(request.url) == "https://open.feishu.cn/open-apis/im/v1/messages/om_xxx/reply"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {
            "msg_type": "interactive",
            "content": json.dumps({"header": {"title": "待确认"}}, ensure_ascii=False),
        }
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_card"}})

    client = FeishuClient(
        FeishuConfig(app_id="app", app_secret="secret", base_url="https://open.feishu.cn"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    message_id = await client.reply_card("om_xxx", {"header": {"title": "待确认"}})

    assert message_id == "om_card"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_update_card_patches_interactive_message():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})
        assert request.method == "PATCH"
        assert str(request.url) == "https://open.feishu.cn/open-apis/im/v1/messages/om_card"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {
            "msg_type": "interactive",
            "content": json.dumps({"elements": []}, ensure_ascii=False),
        }
        return httpx.Response(200, json={"code": 0, "data": {}})

    client = FeishuClient(
        FeishuConfig(app_id="app", app_secret="secret", base_url="https://open.feishu.cn"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    await client.update_card("om_card", {"elements": []})

    assert len(requests) == 2


@pytest.mark.asyncio
async def test_reply_text_posts_text_message_reply():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})
        assert str(request.url) == "https://open.feishu.cn/open-apis/im/v1/messages/om_xxx/reply"
        assert request.headers.get("authorization") == "Bearer tenant-token"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {"msg_type": "text", "content": json.dumps({"text": "hello"}, ensure_ascii=False)}
        return httpx.Response(200, json={"code": 0, "data": {}})

    client = FeishuClient(
        FeishuConfig(app_id="app", app_secret="secret", base_url="https://open.feishu.cn"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    await client.reply_text("om_xxx", "hello")

    assert len(requests) == 2


@pytest.mark.asyncio
async def test_list_chat_member_names_follows_pages():
    member_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
        member_requests.append(dict(request.url.params))
        assert request.headers["Authorization"] == "Bearer t"
        if "page_token" not in request.url.params:
            return httpx.Response(200, json={"code": 0, "data": {
                "items": [{"member_id": "ou_1", "name": "沈涛"}, {"member_id": "ou_bot", "name": ""}],
                "has_more": True, "page_token": "p2"}})
        return httpx.Response(200, json={"code": 0, "data": {"items": [{"member_id": "ou_2", "name": "王一啸"}], "has_more": False}})

    client = FeishuClient(
        FeishuConfig(app_id="app", app_secret="secret", base_url="https://open.feishu.cn"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    names = await client.list_chat_member_names("oc_1")

    assert names == {"ou_1": "沈涛", "ou_2": "王一啸"}
    assert member_requests[0]["member_id_type"] == "open_id"
    assert member_requests[1]["page_token"] == "p2"
