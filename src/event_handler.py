from __future__ import annotations

import json
import logging
import re
from typing import Any

from .config import AppConfig

logger = logging.getLogger(__name__)


class FeishuEventHandler:
    def __init__(self, config: AppConfig, job_store: Any, job_queue: Any, job_processor: Any | None = None) -> None:
        self._config = config
        self._job_store = job_store
        self._job_queue = job_queue
        self._job_processor = job_processor

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

        if event_type == "card.action.trigger":
            return await self._handle_card_action(payload, event_id)
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
            confirmation_text = self._remove_mentions(raw_text, mentions) if mentions else raw_text
            if await self._try_enqueue_confirmation(
                chat_type,
                str(message.get("chat_id", "")),
                str(message.get("message_id", "")),
                confirmation_text,
            ):
                return {"code": 0}
            if not mentions:
                logger.warning("ignored event_id=%s reason=not_mentioned chat_type=%s", event_id, chat_type)
                return {"code": 0, "ignored": "not_mentioned"}
            user_text = confirmation_text
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
        job, created = self._job_store.create_if_new(
            message_id,
            event_id,
            chat_id,
            user_id,
            user_text,
            chat_type=chat_type,
        )
        if not created:
            logger.warning("job duplicate message_id=%s status=%s", message_id, job.status)
            return {"code": 0}

        await self._job_queue.enqueue(message_id)
        logger.warning("job queued message_id=%s", message_id)
        return {"code": 0}

    async def _handle_card_action(self, payload: dict[str, Any], event_id: str) -> dict[str, Any]:
        """Confirm or cancel a stored plan from a CardKit callback.

        A callback must finish within three seconds. The processor claims the local
        plan atomically and starts its existing background execution task; this
        handler never runs Muliu directly and never consumes card-supplied argv.
        """
        if self._job_processor is None:
            logger.warning("ignored card event_id=%s reason=processor_unavailable", event_id)
            return _card_action_response("机器人暂未就绪，请稍后重新发起操作。", "warning")

        event = payload.get("event", {})
        if not isinstance(event, dict):
            return _card_action_response("卡片事件格式无效。", "warning")
        action = event.get("action", {})
        context = event.get("context", {})
        operator = event.get("operator", {})
        if not isinstance(action, dict) or not isinstance(context, dict) or not isinstance(operator, dict):
            return _card_action_response("卡片事件格式无效。", "warning")
        value = action.get("value", {})
        if not isinstance(value, dict):
            return _card_action_response("卡片操作数据无效。", "warning")

        action_name = str(value.get("action", ""))
        request_message_id = str(value.get("request_message_id", ""))
        confirmation_token = str(value.get("confirmation_token", ""))
        confirmation_message_id = str(context.get("open_message_id", ""))
        chat_id = str(context.get("open_chat_id", ""))
        user_id = str(operator.get("open_id") or operator.get("user_id") or operator.get("union_id") or "")
        if not all((action_name, request_message_id, confirmation_token, confirmation_message_id, chat_id, user_id)):
            logger.warning("ignored card event_id=%s reason=missing_callback_identity", event_id)
            return _card_action_response("卡片操作信息不完整，未执行。", "warning")

        card = await self._job_processor.process_card_action(
            action=action_name,
            request_message_id=request_message_id,
            confirmation_message_id=confirmation_message_id,
            chat_id=chat_id,
            user_id=user_id,
            confirmation_token=confirmation_token,
        )
        if card is None:
            logger.warning(
                "ignored card event_id=%s action=%s request_message_id=%s reason=not_pending_or_not_owner",
                event_id,
                action_name,
                request_message_id,
            )
            return _card_action_response("该操作已处理、已过期，或你不是原请求人。", "warning")
        logger.warning(
            "card action accepted event_id=%s action=%s request_message_id=%s",
            event_id,
            action_name,
            request_message_id,
        )
        return {
            "toast": {"type": "success", "content": "操作已受理"},
            "card": {"type": "raw", "data": card},
        }

    async def _try_enqueue_confirmation(
        self,
        chat_type: str,
        chat_id: str,
        message_id: str,
        text: str,
    ) -> bool:
        if chat_type == "p2p" or self._job_processor is None:
            return False
        token = self._extract_confirmation_token(text)
        if token is None:
            return False
        await self._job_queue.enqueue_confirmation(message_id, chat_id, token)
        logger.warning(
            "confirmation queued message_id=%s chat_id=%s token=%s",
            message_id,
            chat_id,
            token,
        )
        return True

    def _extract_confirmation_token(self, text: str) -> str | None:
        """Read the exact confirmation instruction at the end of a group message.

        Feishu may preserve a visible bot mention in ``content`` even when the
        corresponding mention key is absent or differs from the text. Matching
        the final instruction lets ``@bot 确认执行 ABC123`` work without treating
        surrounding mention text as part of the confirmation command.
        """
        prefix = self._config.muliu.confirmation_prefix.strip()
        if not prefix:
            return None
        match = re.search(
            r"(?:^|\s){}\s+([A-Za-z0-9]{{6}})\s*$".format(re.escape(prefix)),
            text,
        )
        return match.group(1).upper() if match else None


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
                cleaned = cleaned.replace("@{}".format(name), "")
        return cleaned.strip()


def _card_action_response(content: str, toast_type: str) -> dict[str, Any]:
    return {"toast": {"type": toast_type, "content": content}}
