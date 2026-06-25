from __future__ import annotations

import json
import logging
from typing import Any

from .config import AppConfig

logger = logging.getLogger(__name__)


class FeishuEventHandler:
    def __init__(self, config: AppConfig, job_store: Any, job_queue: Any) -> None:
        self._config = config
        self._job_store = job_store
        self._job_queue = job_queue

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "challenge" in payload:
            return {"challenge": payload["challenge"]}

        header = payload.get("header", {})
        event_id = str(header.get("event_id", ""))
        event_type = str(header.get("event_type", ""))
        if self._config.feishu.verification_token:
            token = header.get("token")
            if token != self._config.feishu.verification_token:
                logger.warning("ignored event_id=%s reason=invalid_token", event_id)
                return {"code": 0, "ignored": "invalid_token"}

        if event_type != "im.message.receive_v1":
            logger.warning("ignored event_id=%s reason=unsupported_event event_type=%s", event_id, event_type)
            return {"code": 0, "ignored": "unsupported_event"}

        event = payload.get("event", {})
        message = event.get("message", {})
        if message.get("message_type") != "text":
            logger.warning("ignored event_id=%s reason=unsupported_message_type message_type=%s", event_id, message.get("message_type"))
            return {"code": 0, "ignored": "unsupported_message_type"}

        chat_type = str(message.get("chat_type", ""))
        raw_text = self._extract_text(str(message.get("content", "{}")))
        mentions = message.get("mentions", []) or []
        user_text = raw_text

        logger.warning("message event_id=%s chat_type=%s message_type=%s mentions=%s", event_id, chat_type, message.get("message_type"), len(mentions))

        if chat_type != "p2p":
            if not mentions:
                logger.warning("ignored event_id=%s reason=not_mentioned chat_type=%s", event_id, chat_type)
                return {"code": 0, "ignored": "not_mentioned"}
            user_text = self._remove_mentions(raw_text, mentions)
            if not user_text:
                logger.warning("ignored event_id=%s reason=empty_after_mention", event_id)
                return {"code": 0, "ignored": "empty_after_mention"}

        chat_id = str(message.get("chat_id", ""))
        message_id = str(message.get("message_id", ""))
        sender = event.get("sender", {})
        sender_id = sender.get("sender_id", {}) if isinstance(sender, dict) else {}
        user_id = ""
        if isinstance(sender_id, dict):
            user_id = str(sender_id.get("open_id") or sender_id.get("user_id") or sender_id.get("union_id") or "")

        logger.warning(
            "received event_id=%s message_id=%s chat_id=%s text_len=%s",
            event_id,
            message_id,
            chat_id,
            len(user_text),
        )
        job, created = self._job_store.create_if_new(message_id, event_id, chat_id, user_id, user_text)
        if not created:
            logger.warning("job duplicate message_id=%s status=%s", message_id, job.status)
            return {"code": 0}

        await self._job_queue.enqueue(message_id)
        logger.warning("job queued message_id=%s", message_id)
        return {"code": 0}

    @staticmethod
    def _extract_text(content: str) -> str:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return content.strip()
        return str(data.get("text", "")).strip()

    @staticmethod
    def _remove_mentions(text: str, mentions: list[dict[str, Any]]) -> str:
        cleaned = text
        for mention in mentions:
            key = str(mention.get("key", ""))
            name = str(mention.get("name", ""))
            if key:
                cleaned = cleaned.replace(key, "")
            if name:
                cleaned = cleaned.replace(f"@{name}", "")
        return cleaned.strip()
