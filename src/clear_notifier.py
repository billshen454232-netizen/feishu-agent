"""Clear-data (清档) events: built by the Gateway, relayed by the intranet Worker.

Two events per request, decided by the plan's exact script paths (never by AI):

* ``clear_prepared`` — a plan containing clear steps was generated and is
  waiting for confirmation;
* ``clear_executing`` — that plan was confirmed and handed to execution.

The Gateway only writes events to its outbox (:class:`OutboundEventStore`).
The Worker pulls them over its authenticated channel and POSTs them to the
user's intranet endpoint, which decides whether and where to forward.
Delivery is best-effort and never affects the operation; ``event_id`` lets the
receiver de-duplicate.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Any, Protocol

import httpx

from .config import ClearNotificationConfig
from .muliu_plan import MuliuPlan, MuliuStep
from .operation_message import mask_sensitive_operation_data
from .outbound_events import OutboundEventStore
from .utils.time_text import BEIJING

logger = logging.getLogger(__name__)

EVENT_PREPARED = "clear_prepared"
EVENT_EXECUTING = "clear_executing"
_EVENT_VERBS = {EVENT_PREPARED: "准备清档", EVENT_EXECUTING: "正在操作清档"}


class ClearEventPublisher:
    """Gateway side: detect clear steps, build the payload, record it in the outbox."""

    def __init__(self, config: ClearNotificationConfig, store: OutboundEventStore) -> None:
        self._config = config
        self._store = store

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def clear_steps(self, plan: MuliuPlan) -> list[MuliuStep]:
        triggers = set(self._config.trigger_paths)
        return [step for step in plan.steps if step.path in triggers]

    def build_payload(
        self,
        event: str,
        *,
        message_id: str,
        plan: MuliuPlan,
        clear_steps: list[MuliuStep],
        server_ids: list[str],
        route_region: str,
        request_text: str,
        operator: dict[str, str],
        requester: dict[str, str],
        now: float | None = None,
    ) -> dict[str, Any]:
        occurred_at = int(time.time() if now is None else now)
        operator_label = operator.get("name") or operator.get("open_id") or "未知成员"
        servers_label = "、".join(server_ids) if server_ids else "（未识别服号）"
        return {
            "event": event,
            "event_id": "{}:{}".format(message_id, event),
            "occurred_at": occurred_at,
            "occurred_at_text": datetime.fromtimestamp(occurred_at, BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
            "text": "内网：{} {} {} 服".format(operator_label, _EVENT_VERBS.get(event, event), servers_label),
            "operator": operator,
            "requester": requester,
            "server_ids": server_ids,
            "route_region": route_region,
            "clear_steps": [
                {
                    "script": step.path.rsplit("/", 1)[-1],
                    "path": step.path,
                    "args": list(step.args),
                    "description": mask_sensitive_operation_data(step.description),
                }
                for step in clear_steps
            ],
            "plan_summary": mask_sensitive_operation_data(plan.summary),
            "request_text": mask_sensitive_operation_data(request_text),
            "message_id": message_id,
        }

    def publish(self, payload: dict[str, Any]) -> bool:
        added = self._store.add(str(payload["event_id"]), str(payload["event"]), payload)
        logger.info("clear event queued event_id=%s new=%s", payload["event_id"], added)
        return added


class WebhookSender:
    """Worker side: POST one event to the intranet endpoint (optional HMAC signature)."""

    def __init__(self, config: ClearNotificationConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client or httpx.AsyncClient(timeout=config.timeout_seconds)

    async def send(self, payload: dict[str, Any]) -> tuple[bool, str]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self._config.secret:
            timestamp = str(int(time.time()))
            signature = hmac.new(
                self._config.secret.encode("utf-8"),
                timestamp.encode("utf-8") + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            headers.update({"X-Clear-Timestamp": timestamp, "X-Clear-Signature": signature})
        try:
            response = await self._client.post(self._config.webhook_url, content=body, headers=headers)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - delivery is best-effort
            return False, type(exc).__name__
        return True, ""


class EventSource(Protocol):
    async def claim_events(self) -> list[dict[str, Any]]: ...

    async def report_event(self, event_id: str, delivered: bool, error: str) -> None: ...


class LocalEventSource:
    """``runtime.role=local``: relay straight from the in-process outbox."""

    def __init__(self, store: OutboundEventStore, *, max_age_seconds: int) -> None:
        self._store = store
        self._max_age_seconds = max_age_seconds

    async def claim_events(self) -> list[dict[str, Any]]:
        return self._store.claim("local", max_age_seconds=self._max_age_seconds)

    async def report_event(self, event_id: str, delivered: bool, error: str) -> None:
        self._store.report(event_id, "local", delivered=delivered, error=error)


class ClearEventRelay:
    """Pull events, deliver them, report outcomes; back off when the source fails."""

    def __init__(self, source: EventSource, sender: WebhookSender, *, poll_interval_seconds: float) -> None:
        self._source = source
        self._sender = sender
        self._poll_interval = poll_interval_seconds
        self._stopping = False

    async def run_once(self) -> int:
        events = await self._source.claim_events()
        for event in events:
            delivered, error = await self._sender.send(event["payload"])
            if delivered:
                logger.info("clear event delivered event_id=%s", event["event_id"])
            else:
                logger.warning("clear event delivery failed event_id=%s error=%s", event["event_id"], error)
            await self._source.report_event(event["event_id"], delivered, error)
        return len(events)

    async def run_forever(self) -> None:
        delay = self._poll_interval
        while not self._stopping:
            try:
                await self.run_once()
                delay = self._poll_interval
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep relaying after transient gateway errors
                delay = min(max(delay * 2, self._poll_interval), 60.0)
                logger.warning("clear event relay error=%s retry_in=%.0fs", exc, delay)
            await asyncio.sleep(delay)

    def stop(self) -> None:
        self._stopping = True
