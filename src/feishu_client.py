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

    async def reply_text(self, message_id: str, text: str) -> None:
        token = await self.get_tenant_access_token()
        response = await self._client.post(
            f"{self._config.base_url}/open-apis/im/v1/messages/{message_id}/reply",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"Failed to reply message: {data}")
