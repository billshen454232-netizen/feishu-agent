"""Clear-data (清档) notices pushed from the Gateway to a WeCom group robot.

Two notices per request, decided by the plan's exact script paths (never by AI):

* ``clear_prepared`` — a plan containing clear steps was generated and is
  waiting for confirmation;
* ``clear_executing`` — that plan was confirmed and handed to execution.

Delivery is best-effort: failures are logged and never affect the operation.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from .config import ClearNotificationConfig
from .muliu_plan import MuliuPlan, MuliuStep
from .operation_message import mask_sensitive_operation_data
from .utils.time_text import BEIJING

logger = logging.getLogger(__name__)

EVENT_PREPARED = "clear_prepared"
EVENT_EXECUTING = "clear_executing"
_EVENT_VERBS = {EVENT_PREPARED: "准备清档", EVENT_EXECUTING: "正在操作清档"}
_EVENT_NOTES = {EVENT_PREPARED: "（已生成计划，等待确认）", EVENT_EXECUTING: "（已确认，开始执行）"}


class ClearNotifier:
    def __init__(self, config: ClearNotificationConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client or httpx.AsyncClient(timeout=config.timeout_seconds)

    @property
    def enabled(self) -> bool:
        return self._config.enabled and bool(
            self._config.webhook_url or self._config.region_webhooks or self._config.cc_webhooks
        )

    @property
    def cc_webhooks(self) -> tuple[str, ...]:
        return self._config.cc_webhooks

    def webhook_for_region(self, region_key: str | None) -> str:
        """Region group if configured, else the fallback group ("" means: do not send)."""
        if region_key and region_key in self._config.region_webhooks:
            return self._config.region_webhooks[region_key]
        return self._config.webhook_url

    def clear_steps(self, plan: MuliuPlan) -> list[MuliuStep]:
        triggers = set(self._config.trigger_paths)
        return [step for step in plan.steps if step.path in triggers]

    @staticmethod
    def build_text(
        event: str,
        *,
        operator_name: str,
        server_ids: list[str],
        plan_summary: str,
        region_name: str = "",
        target: str | None = None,
        now: float | None = None,
    ) -> str:
        """``target`` overrides the "<region> <servers>" part (used for combined cc notices)."""
        occurred_at = datetime.fromtimestamp(time.time() if now is None else now, BEIJING)
        if target is None:
            servers = "、".join(server_ids) if server_ids else "（未识别服号）"
            target = "{} {}".format(region_name, servers) if region_name else servers
        return "\n".join([
            "内网：{} {} {} 服{}".format(
                operator_name or "未知成员", _EVENT_VERBS.get(event, event), target, _EVENT_NOTES.get(event, "")
            ),
            "计划：{}".format(mask_sensitive_operation_data(plan_summary)),
            "时间：{}".format(occurred_at.strftime("%Y-%m-%d %H:%M:%S")),
        ])

    async def send(self, text: str, webhook_url: str | None = None) -> bool:
        """Post a WeCom group-robot text message, optionally @all."""
        url = self._config.webhook_url if webhook_url is None else webhook_url
        if not self.enabled or not url:
            return False
        content: dict[str, object] = {"content": text}
        if self._config.mention_all:
            content["mentioned_mobile_list"] = ["@all"]
        try:
            response = await self._client.post(url, json={"msgtype": "text", "text": content})
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - notification must never affect the operation
            logger.warning("clear notification failed error=%s", type(exc).__name__)
            return False
        if body.get("errcode") != 0:
            logger.warning("clear notification rejected errcode=%s errmsg=%s", body.get("errcode"), body.get("errmsg"))
            return False
        logger.info("clear notification sent")
        return True
