"""Long-lived intranet worker that claims gateway-approved Muliu plans."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from .config import AppConfig, ConfigError, muliu_config_for_route
from .muliu_executor import MuliuExecutionError, MuliuExecutionResult, MuliuExecutor, MuliuSubmittedError
from .muliu_firewall import MuliuFirewall
from .muliu_plan import MuliuPlanError, parse_plan
from .muliu_routing import MuliuPlanRouteResolver, MuliuRouteSnapshot, MuliuRoutingError
from .server_manager import ROUTE_API, ROUTES, ServerManagerClient, ServerManagerStepRunner, translate
from .baye_check import BayeCheckCoordinator, BayeCheckError, BayeCheckSubmitUnknown
from .muliu_executor import MuliuStepResult
from .muliu_script_catalog import BACKEND_BAYE_CHECK
from .worker_protocol import serialize_execution_result

logger = logging.getLogger(__name__)


class GatewayWorkerClientError(RuntimeError):
    """Raised when the worker cannot safely use the gateway's internal API."""


@dataclass(frozen=True)
class ClaimedWorkerJob:
    message_id: str
    plan_json: str
    route_region: str
    # Gateway's per-step route (muliu / api) from the admin switches; empty = all Muliu.
    step_routes: tuple[str, ...] = field(default_factory=tuple)


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
        route_region = data.get("route_region")
        if (
            not isinstance(message_id, str)
            or not message_id
            or not isinstance(plan_json, str)
            or not plan_json
            or not isinstance(route_region, str)
        ):
            raise GatewayWorkerClientError("gateway 返回的领取任务格式无效")
        raw_routes = data.get("step_routes") or []
        if not isinstance(raw_routes, list) or not all(item in ROUTES for item in raw_routes):
            raise GatewayWorkerClientError("gateway 返回的执行路线格式无效")
        return ClaimedWorkerJob(
            message_id=message_id,
            plan_json=plan_json,
            route_region=route_region,
            step_routes=tuple(raw_routes),
        )

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

    async def submit_followup(self, message_id: str, payload: dict[str, object]) -> bool:
        """Post a later result for an already finished job (e.g. a background 开服配置检查)."""
        response = await self._request(
            "POST",
            "/internal/worker/jobs/{}/followup".format(message_id),
            payload,
        )
        if response.status_code == 404:
            return True  # job unknown to the Gateway: nothing to deliver to, do not retry forever
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
            try:
                detail = str(response.json().get("detail") or "")[:300]
            except (ValueError, AttributeError):
                detail = ""
            raise GatewayWorkerClientError(
                "gateway 返回 HTTP {}{}".format(response.status_code, "：" + detail if detail else "")
            )
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
        gateway_client: GatewayWorkerClient,
        route_resolver: MuliuPlanRouteResolver,
        call_contracts: Any | None = None,
        baye_checks: Optional[BayeCheckCoordinator] = None,
    ) -> None:
        if config.runtime.role != "worker":
            raise ValueError("MuliuWorker 只能在 runtime.role=worker 时运行")
        self._config = config
        self._firewall = firewall
        self._gateway_client = gateway_client
        self._route_resolver = route_resolver
        self._call_contracts = call_contracts
        self._baye_checks = baye_checks
        self._stopping = False
        self._sm_clients: dict[str, ServerManagerClient] = {}
        self._sm_inventory: dict[str, tuple[float, set[str]]] = {}

    async def run_forever(self) -> None:
        self._stopping = False
        logger.warning(
            "worker polling started worker_id=%s gateway=%s",
            self._config.runtime.worker_id,
            self._config.runtime.gateway_base_url,
        )
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
            gateway_snapshot = MuliuRouteSnapshot.from_region(job.route_region)
            local_snapshot = self._route_resolver.resolve(plan)
            if local_snapshot != gateway_snapshot:
                raise MuliuRoutingError("Gateway 路由分类与 Worker 本地服号归属不一致")
            routed_config = muliu_config_for_route(
                self._config.muliu,
                local_snapshot.region,
                local_snapshot.identifier,
            )
        except (ConfigError, MuliuPlanError, MuliuRoutingError, ValueError) as exc:
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

        if self._call_contracts is not None and self._call_contracts.backend_for(plan.steps[0]) == BACKEND_BAYE_CHECK:
            await self._submit_baye_check(job, plan)
            return

        api_steps, api_runner = await self._api_route(plan, job, local_snapshot.region)
        routed_executor = MuliuExecutor(routed_config)
        execution_task = asyncio.create_task(
            self._execute_with_read_only_retry(routed_executor, plan, job.message_id, api_steps, api_runner)
        )
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
            logger.exception("worker execution crashed message_id=%s", job.message_id)
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

    async def _submit_baye_check(self, job: ClaimedWorkerJob, plan: Any) -> None:
        """Submit the UI-automation check and report "submitted" at once; the result follows in the same thread."""
        step = plan.steps[0]
        try:
            if self._baye_checks is None:
                raise BayeCheckError("这台 Worker 未启用开服配置检查（config.json 的 baye_check）")
            expected_open_time = int(step.args[1]) if len(step.args) > 1 else 0  # {timestamp}, checked by the contract
            check_id = await self._baye_checks.submit(job.message_id, step.args[0], expected_open_time)
            result = MuliuExecutionResult(
                summary=plan.summary,
                step_results=[MuliuStepResult(
                    step_number=1, description=step.description, path=step.path, args=list(step.args), succeeded=True,
                    log="[开服配置检查] 已提交到真机自动化（任务 {}），通常 3–10 分钟完成；完成后会在本话题追加结果和两张截图。\n"
                        "AI_OP_END status=success".format(check_id),
                )],
            )
        except BayeCheckSubmitUnknown as exc:
            result = MuliuExecutionResult(
                summary=plan.summary, step_results=[],
                result_unknown_reason="{}；是否已开始检查未知。接口没有防重复机制，请勿重复确认，先在执行机控制台查看最近记录".format(exc),
            )
        except BayeCheckError as exc:
            result = MuliuExecutionResult(summary=plan.summary, step_results=[], failed_step_number=1,
                                          failure_reason="开服配置检查未开始：{}".format(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("baye check submit crashed message_id=%s", job.message_id)
            result = MuliuExecutionResult(summary=plan.summary, step_results=[],
                                          result_unknown_reason="提交开服配置检查时发生异常：{}；请勿重复确认".format(type(exc).__name__))
        if not await self._gateway_client.submit_result(job.message_id, result):
            logger.error("gateway no longer accepts result message_id=%s", job.message_id)

    async def _api_route(
        self, plan: Any, job: ClaimedWorkerJob, region: str
    ) -> tuple[Optional[list[Any]], Optional[ServerManagerStepRunner]]:
        """Per-step Server Manager tasks; any doubt keeps the step on Muliu (never an error)."""
        settings = self._config.server_manager
        if ROUTE_API not in job.step_routes:
            return None, None
        if not settings.enabled or region not in settings.regions or self._call_contracts is None:
            logger.warning("api route requested but unavailable on this worker message_id=%s region=%s", job.message_id, region)
            return None, None
        if len(job.step_routes) != len(plan.steps):
            logger.warning("step route count mismatch message_id=%s", job.message_id)
            return None, None
        client = self._sm_client(region)
        try:
            inventory = await self._sm_servers(region, client)
        except Exception:  # noqa: BLE001 - inventory unknown: stay on Muliu
            logger.warning("server manager inventory unavailable region=%s; using muliu", region, exc_info=True)
            return None, None
        api_steps: list[Any] = []
        for step, route in zip(plan.steps, job.step_routes):
            tasks = None
            if route == ROUTE_API:
                matching = [contract for contract in self._call_contracts.contracts if contract.matches(step)]
                tasks = translate(matching[0].name, step) if len(matching) == 1 else None
                if tasks and not all(target in inventory for task in tasks for target in task.targets):
                    logger.warning("api route skipped: target not in inventory message_id=%s step=%s", job.message_id, step.args)
                    tasks = None
            api_steps.append(tasks)
        if not any(api_steps):
            return None, None
        logger.warning("api route message_id=%s steps=%s", job.message_id, [bool(item) for item in api_steps])
        return api_steps, ServerManagerStepRunner(client, job.message_id)

    def _sm_client(self, region: str) -> ServerManagerClient:
        if region not in self._sm_clients:
            settings = self._config.server_manager
            self._sm_clients[region] = ServerManagerClient(
                settings.regions[region],
                settings.env,
                poll_interval_seconds=settings.poll_interval_seconds,
                task_timeout_seconds=settings.task_timeout_seconds,
                request_timeout_seconds=settings.request_timeout_seconds,
            )
        return self._sm_clients[region]

    async def _sm_servers(self, region: str, client: ServerManagerClient) -> set[str]:
        cached = self._sm_inventory.get(region)
        if cached and time.monotonic() - cached[0] < self._config.server_manager.inventory_ttl_seconds:
            return cached[1]
        servers = await asyncio.to_thread(client.server_ids)
        self._sm_inventory[region] = (time.monotonic(), servers)
        return servers

    async def _execute_with_read_only_retry(
        self,
        executor: MuliuExecutor,
        plan: Any,
        message_id: str,
        api_steps: Optional[list[Any]] = None,
        api_runner: Optional[ServerManagerStepRunner] = None,
    ) -> MuliuExecutionResult:
        """Read-only plans that failed on Muliu transport trouble are re-run once (no side effects)."""
        if api_steps:
            # API-routed plans are never re-run here: phase-1 API capabilities are all writes.
            return await executor.execute_plan(plan, api_steps=api_steps, api_runner=api_runner)
        result = await executor.execute_plan(plan)
        if (
            not result.succeeded
            and result.transient_error
            and self._call_contracts is not None
            and self._call_contracts.is_read_only(plan)
        ):
            logger.warning(
                "read-only plan failed transiently, retrying once message_id=%s reason=%s",
                message_id,
                result.failure_reason or result.result_unknown_reason,
            )
            result = await executor.execute_plan(plan)
        return result

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
