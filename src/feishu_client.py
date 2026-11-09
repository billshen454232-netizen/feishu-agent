from __future__ import annotations

import json
import time

import httpx

from .config import FeishuConfig


class FeishuAPIError(RuntimeError):
    """Raised when Feishu OpenAPI returns a non-zero code or bad HTTP status."""


class FeishuClient:
    def __init__(self, config: FeishuConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client or httpx.AsyncClient(timeout=30)
        self._tenant_access_token: str | None = None
        self._tenant_token_expires_at = 0.0

    async def get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._tenant_token_expires_at:
            return self._tenant_access_token

        response = await self._client.post(
            f"{self._config.base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self._config.app_id, "app_secret": self._config.app_secret},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"Failed to get tenant_access_token: {data}")

        token = str(data["tenant_access_token"])
        expire = int(data.get("expire", 7200))
        self._tenant_access_token = token
        self._tenant_token_expires_at = now + max(expire - 120, 60)
        return token

    async def reply_text(self, message_id: str, text: str) -> str:
        return await self._reply_message(message_id, "text", {"text": text})

    async def reply_card(self, message_id: str, card: dict[str, object]) -> str:
        """Reply with an interactive CardKit card owned by this application."""
        return await self._reply_message(message_id, "interactive", card)

    async def update_card(self, message_id: str, card: dict[str, object]) -> None:
        """Replace a card sent by this bot with a non-actionable status card."""
        token = await self.get_tenant_access_token()
        response = await self._client.patch(
            f"{self._config.base_url}/open-apis/im/v1/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"Failed to update card message: {data}")

    async def upload_image(self, image: bytes, filename: str = "image.png") -> str:
        """Upload an image for use in messages / cards and return its ``image_key`` (needs im:resource)."""
        token = await self.get_tenant_access_token()
        response = await self._client.post(
            f"{self._config.base_url}/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": "message"},
            files={"image": (filename, image, "image/png")},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"Failed to upload image: code={data.get('code')} msg={data.get('msg')}")
        return str(data["data"]["image_key"])

    async def list_chat_member_names(self, chat_id: str) -> dict[str, str]:
        """Return ``open_id -> display name`` of a chat's members (needs im:chat.members:read)."""
        token = await self.get_tenant_access_token()
        names: dict[str, str] = {}
        page_token = ""
        for _page in range(20):
            params: dict[str, object] = {"member_id_type": "open_id", "page_size": 100}
            if page_token:
                params["page_token"] = page_token
            response = await self._client.get(
                f"{self._config.base_url}/open-apis/im/v1/chats/{chat_id}/members",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                raise FeishuAPIError(f"Failed to list chat members: {data.get('code')} {data.get('msg')}")
            body = data.get("data") or {}
            for item in body.get("items") or []:
                member_id = str(item.get("member_id") or "")
                name = str(item.get("name") or "")
                if member_id and name:
                    names[member_id] = name
            page_token = str(body.get("page_token") or "")
            if not body.get("has_more") or not page_token:
                break
        return names

    async def _reply_message(self, message_id: str, msg_type: str, content: dict[str, object]) -> str:
        token = await self.get_tenant_access_token()
        response = await self._client.post(
            f"{self._config.base_url}/open-apis/im/v1/messages/{message_id}/reply",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"Failed to reply message: {data}")
        return str(data.get("data", {}).get("message_id", ""))
