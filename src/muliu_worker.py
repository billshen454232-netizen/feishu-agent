"""Long-lived intranet worker that claims gateway-approved Muliu plans."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import AppConfig
from .muliu_executor import MuliuExecutionError, MuliuExecutionResult, MuliuExecutor, MuliuSubmittedError
from .muliu_firewall import MuliuFirewall
from .muliu_plan import MuliuPlanError, parse_plan
from .worker_protocol import serialize_execution_result

logger = logging.getLogger(__name__)


class GatewayWorkerClientError(RuntimeError):
    """Raised when the worker cannot safely use the gateway's internal API."""


@dataclass(frozen=True)
class ClaimedWorkerJob:
    message_id: str
    plan_json: str


class GatewayWorkerClient:
    """Authenticated client for the two gateway-only worker endpoints."""

    def __init__(self, config: AppConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = http_client or httpx.AsyncClient(timeout=30)
        self._owns_client = http_client is None

    async def close(self) -> None:
        """Close only the HTTP client created by this protocol client."""
        if self._owns_client:
            await self._client.aclose()

    async def claim(self) -> ClaimedWorkerJob | None:
        response = await self._request("POST", "/internal/worker/jobs/claim", {})
        if response.status_code == 204:
            return None
        data = self._response_json(response)
        message_id = data.get("message_id")
        plan_json = data.get("plan_json")
        if not isinstance(message_id, str) or not message_id or not isinstance(plan_json, str) or not plan_json:
            raise GatewayWorkerClientError("gateway 返回的领取任务格式无效")
        return ClaimedWorkerJob(message_id=message_id, plan_json=plan_json)

    async def heartbeat(self, message_id: str) -> bool:
        response = await self._request(
            "POST",
            "/internal/worker/jobs/{}/heartbeat".format(message_id),
            {},
        )
        if response.status_code == 404:
            return False
        self._response_json(response)
        return True

    async def submit_result(self, message_id: str, result: MuliuExecutionResult) -> bool:
        response = await self._request(
            "POST",
            "/internal/worker/jobs/{}/result".format(message_id),
            {"result": serialize_execution_result(result)},
        )
        if response.status_code == 404:
            return False
        self._response_json(response)
        return True

    async def _request(self, method: str, path: str, payload: dict[str, object]) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                "{}{}".format(self._config.runtime.gateway_base_url, path),
                headers={
                    "Authorization": "Bearer {}".format(self._config.runtime.worker_token),
                    "X-Feishu-Worker-ID": self._config.runtime.worker_id,
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise GatewayWorkerClientError("连接 gateway 失败：{}".format(exc)) from exc
        if response.status_code in {401, 403}:
            raise GatewayWorkerClientError("gateway 拒绝 Worker 身份认证")
        if response.status_code >= 500:
            raise GatewayWorkerClientError("gateway 服务端错误：HTTP {}".format(response.status_code))
        return response

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        if not 200 <= response.status_code < 300:
            raise GatewayWorkerClientError("gateway 返回 HTTP {}".format(response.status_code))
        try:
            value = response.json()
        except ValueError as exc:
            raise GatewayWorkerClientError("gateway 返回非 JSON 响应") from exc
        if not isinstance(value, dict):
            raise GatewayWorkerClientError("gateway 返回 JSON 格式无效")
        return value


class MuliuWorker:
    """Sequential worker. It never receives user text or free-form commands."""

    def __init__(
        self,
        config: AppConfig,
        firewall: MuliuFirewall,
        executor: MuliuExecutor,
        gateway_client: GatewayWorkerClient,
    ) -> None:
        if config.runtime.role != "worker":
            raise ValueError("MuliuWorker 只能在 runtime.role=worker 时运行")
        self._config = config
        self._firewall = firewall
        self._executor = executor
        self._gateway_client = gateway_client
        self._stopping = False

    async def run_forever(self) -> None:
        self._stopping = False
        try:
            while not self._stopping:
                try:
                    job = await self._gateway_client.claim()
                    if job is None:
                        await asyncio.sleep(self._config.runtime.poll_interval_seconds)
                        continue
                    await self._execute_claimed_job(job)
                except asyncio.CancelledError:
                    raise
                except GatewayWorkerClientError:
                    logger.exception("gateway worker request failed")
                    await asyncio.sleep(self._config.runtime.poll_interval_seconds)
                except Exception:  # noqa: BLE001 - never let one malformed remote item kill the service
                    logger.exception("unexpected Muliu worker loop error")
                    await asyncio.sleep(self._config.runtime.poll_interval_seconds)
        finally:
            await self._gateway_client.close()

    def stop(self) -> None:
        self._stopping = True

    async def _execute_claimed_job(self, job: ClaimedWorkerJob) -> None:
        try:
            plan = parse_plan(job.plan_json, max_steps=self._config.muliu.max_steps)
            self._firewall.validate(plan)
        except (MuliuPlanError, ValueError) as exc:
            result = MuliuExecutionResult(
                summary=_plan_summary_or_request_text(job.plan_json),
                step_results=[],
                failed_step_number=1,
                failure_reason="Worker 本地重新校验失败：{}".format(exc),
            )
            accepted = await self._gateway_client.submit_result(job.message_id, result)
            if not accepted:
                logger.error("gateway no longer accepts invalid-plan result message_id=%s", job.message_id)
            return

        execution_task = asyncio.create_task(self._executor.execute_plan(plan))
        heartbeat_task = asyncio.create_task(self._heartbeat_until_done(job.message_id, execution_task))
        try:
            result = await execution_task
        except MuliuSubmittedError as exc:
            if exc.submitted:
                result = MuliuExecutionResult(
                    summary=plan.summary,
                    step_results=[],
                    result_unknown_reason=str(exc),
                )
            else:
                result = MuliuExecutionResult(
                    summary=plan.summary,
                    step_results=[],
                    failed_step_number=1,
                    failure_reason=str(exc),
                )
        except MuliuExecutionError as exc:
            result = MuliuExecutionResult(
                summary=plan.summary,
                step_results=[],
                failed_step_number=1,
                failure_reason=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - execution may have reached Muliu
            result = MuliuExecutionResult(
                summary=plan.summary,
                step_results=[],
                result_unknown_reason="内网 Worker 执行时发生异常：{}；请勿重复确认或重试".format(exc),
            )
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

        accepted = await self._gateway_client.submit_result(job.message_id, result)
        if not accepted:
            logger.error("gateway no longer accepts result message_id=%s", job.message_id)

    async def _heartbeat_until_done(self, message_id: str, execution_task: asyncio.Task[MuliuExecutionResult]) -> None:
        while not execution_task.done():
            await asyncio.sleep(self._config.runtime.heartbeat_interval_seconds)
            if execution_task.done():
                return
            try:
                active = await self._gateway_client.heartbeat(message_id)
            except GatewayWorkerClientError:
                logger.exception("worker heartbeat request failed message_id=%s", message_id)
                return
            if not active:
                logger.error("worker lease is no longer active message_id=%s", message_id)
                return


def _plan_summary_or_request_text(plan_json: str) -> str:
    """Keep an invalid-plan result bindable to its original stored summary."""
    try:
        import json

        value = json.loads(plan_json)
    except json.JSONDecodeError:
        return "已确认计划"
    if isinstance(value, dict) and isinstance(value.get("summary"), str) and value["summary"].strip():
        return value["summary"].strip()[:120]
    return "已确认计划"
