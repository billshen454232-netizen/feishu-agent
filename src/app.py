from __future__ import annotations

import asyncio
import hmac
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Header, HTTPException, Response

from .ai_client import create_ai_client
from .config import ConfigError, load_config
from .conversation import ConversationStore
from .conversation_log import ConversationLog
from .access_auth import CloudflareAccessVerifier
from .admin_app import AdminServer, create_admin_app
from .clear_notifier import ClearNotifier
from .plan_board import PlanStore
from .hotfix_thresholds import HotfixPlannerCatalog, ThresholdStore
from .execution_routes import ExecutionRouteStore
from .team_kb_reviews import ReviewError, TeamKbReviewStore, parse_review_item
from .baye_check import (
    BayeCheckCoordinator,
    build_followup_card,
    build_judge_prompt,
    parse_followup,
    parse_judgement,
    should_judge,
)
from .qa_library import QaService, QaStore
from .event_handler import FeishuEventHandler
from .execution_report import ExecutionReportStore
from .feishu_client import FeishuClient
from .message_jobs import MessageJobStore
from .message_worker import MessageJobProcessor, MessageJobQueue, result_card_detail
from .muliu_executor import MuliuExecutor
from .muliu_firewall import MuliuFirewall
from .muliu_intent import MuliuIntentRouter
from .muliu_plan import MuliuPlanError, parse_plan
from .muliu_routing import MuliuPlanRouteResolver
from .muliu_planner import MuliuPlanner
from .muliu_script_catalog import BACKEND_BAYE_CHECK, MuliuScriptCatalog
from .muliu_worker import GatewayWorkerClient, MuliuWorker
from .operation_message import build_execution_status_card, format_execution_result
from .script_knowledge_base import ScriptKnowledgeBase
from .utils.diagnostics import install_stack_dump_signals
from .worker_protocol import (
    WorkerProtocolError,
    parse_execution_result,
    validate_execution_result_matches_plan,
)

logger = logging.getLogger(__name__)


def create_app(config_path: str | Path = "config/config.json") -> FastAPI:
    config = load_config(config_path)
    if config.runtime.role == "worker":
        return _create_worker_app(config)

    job_store = MessageJobStore(config.job_queue.database_path)
    job_store.initialize()
    report_store = ExecutionReportStore(config.job_queue.database_path)
    report_store.initialize()
    qa_store = QaStore(config.job_queue.database_path)
    qa_store.initialize()
    conversations = ConversationStore(config.conversation.max_history_messages)
    ai_client, planning_ai_client, interpretation_ai_client = _create_role_ai_clients(config)
    feishu_client = FeishuClient(config.feishu)
    job_queue = MessageJobQueue()

    threshold_store = ThresholdStore(config.job_queue.database_path)
    threshold_store.initialize()
    # The Planner sees hotfix keys from the live registry; executed plans use the static executable contracts.
    script_catalog = HotfixPlannerCatalog(config.muliu.script_catalog_path, threshold_store)
    executable_contracts = MuliuScriptCatalog(config.muliu.script_catalog_path).read_call_contracts()
    route_store = ExecutionRouteStore(config.job_queue.database_path)
    route_store.initialize()
    team_kb_reviews = TeamKbReviewStore(config.job_queue.database_path)
    team_kb_reviews.initialize()
    operation_planner = MuliuPlanner(
        ai_client=planning_ai_client,
        script_catalog=script_catalog,
        max_steps=config.muliu.max_steps,
        plan_expander=threshold_store.expand_plan,
    )
    muliu_firewall = MuliuFirewall(
        config.muliu,
        call_contracts=executable_contracts,
    )
    muliu_executor = MuliuExecutor(config.muliu) if config.runtime.role == "local" else None
    route_resolver = MuliuPlanRouteResolver(
        config.execution_interpretation.server_environments_path,
        executable_contracts,
    )
    script_knowledge_base = None
    if config.script_knowledge.enabled:
        script_knowledge_base = ScriptKnowledgeBase(
            config.script_knowledge.path,
            min_score=config.script_knowledge.min_score,
            max_results=config.script_knowledge.max_results,
            max_context_chars=config.script_knowledge.max_context_chars,
        )
    conversation_log = None
    if config.conversation_log.enabled:
        conversation_log = ConversationLog(
            config.conversation_log.directory,
            max_text_chars=config.conversation_log.max_text_chars,
        )
    job_processor = MessageJobProcessor(
        config=config,
        store=job_store,
        conversations=conversations,
        ai_client=ai_client,
        feishu_client=feishu_client,
        queue=job_queue,
        operation_planner=operation_planner,
        muliu_firewall=muliu_firewall,
        muliu_executor=muliu_executor,
        route_resolver=route_resolver,
        script_knowledge_base=script_knowledge_base,
        script_catalog=script_catalog,
        intent_router=MuliuIntentRouter(),
        conversation_log=conversation_log,
        report_store=report_store,
        interpretation_ai_client=interpretation_ai_client,
        clear_notifier=ClearNotifier(config.clear_notification) if config.clear_notification.enabled else None,
        qa_service=QaService(
            answer_client=ai_client,
            curation_client=interpretation_ai_client,
            knowledge_base=script_knowledge_base,
            capability_context=script_catalog.read_capability_context,
            catalog_text=script_catalog.read,
            store=qa_store,
            knowledge_root=config.script_knowledge.path,
        ),
    )
    event_handler = FeishuEventHandler(config, job_store, job_queue, job_processor=job_processor)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        install_stack_dump_signals()
        expired = job_store.expire_stale_unfinished(None, config.job_queue.recovery_window_seconds)
        for job in expired:
            logger.warning("job expired message_id=%s", job.message_id)
        lease_task: asyncio.Task[None] | None = None
        if config.runtime.role == "gateway":
            await _deliver_expired_worker_leases(
                job_store,
                job_processor,
                config.runtime.lease_timeout_seconds,
            )
            lease_task = asyncio.create_task(
                _watch_worker_leases(job_store, job_processor, config.runtime.lease_timeout_seconds)
            )
        recoverable = job_store.list_recoverable_unfinished(None, config.job_queue.recovery_window_seconds)
        for job in recoverable:
            await job_queue.enqueue(job.message_id)
        job_processor.start()
        admin_server: AdminServer | None = None
        if config.admin.enabled:
            verifier = (
                CloudflareAccessVerifier(config.admin.access_team_domain, config.admin.access_aud)
                if config.admin.access_team_domain
                else None
            )
            admin_qa = QaService(
                # Separate clients: the review page runs its own event loop in another thread.
                answer_client=create_ai_client(config.ai_for("chat")),
                curation_client=create_ai_client(config.ai_for("interpretation")),
                knowledge_base=script_knowledge_base,
                capability_context=script_catalog.read_capability_context,
                catalog_text=script_catalog.read,
                store=qa_store,
                knowledge_root=config.script_knowledge.path,
            )
            plan_store = PlanStore(config.job_queue.database_path)
            plan_store.initialize()
            admin_server = AdminServer(
                create_admin_app(
                    report_store,
                    access_verifier=verifier,
                    qa_service=admin_qa,
                    plan_store=plan_store,
                    threshold_store=threshold_store,
                    review_client=create_ai_client(config.ai_for("interpretation")),
                    route_store=route_store,
                    route_contracts=executable_contracts,
                    team_kb_reviews=team_kb_reviews,
                ),
                port=config.admin.port,
            )
            admin_server.start()
        try:
            yield
        finally:
            if admin_server is not None:
                admin_server.stop()
            if lease_task is not None:
                lease_task.cancel()
                await asyncio.gather(lease_task, return_exceptions=True)
            await job_processor.stop()

    app = FastAPI(title="Feishu Muliu Server Operations Bot", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/feishu/events")
    async def feishu_events(payload: dict[str, Any]) -> dict[str, Any]:
        return await event_handler.handle(payload)

    if config.runtime.role == "gateway":
        _add_gateway_worker_routes(
            app, config, job_store, job_processor, route_store, executable_contracts,
            feishu_client=feishu_client, judge_ai_client=interpretation_ai_client, team_kb_reviews=team_kb_reviews,
        )

    return app


def _create_role_ai_clients(config: Any) -> tuple[Any, Any, Any]:
    """Build (chat, planning, interpretation) clients; roles sharing a pool share a client."""
    clients: dict[int, Any] = {}

    def client_for(role: str) -> Any:
        pool = config.ai_for(role)
        if id(pool) not in clients:
            clients[id(pool)] = create_ai_client(pool)
        return clients[id(pool)]

    return client_for("chat"), client_for("planning"), client_for("interpretation")


def _create_worker_app(config: Any) -> FastAPI:
    """Run a worker as a process without exposing inbound operational endpoints."""
    script_catalog = MuliuScriptCatalog(config.muliu.script_catalog_path)
    call_contracts = script_catalog.read_call_contracts()
    firewall = MuliuFirewall(config.muliu, call_contracts=call_contracts)
    gateway_client = GatewayWorkerClient(config)
    baye_checks = (
        BayeCheckCoordinator(config.baye_check, gateway_client.submit_followup) if config.baye_check.enabled else None
    )
    worker = MuliuWorker(
        config=config,
        firewall=firewall,
        gateway_client=gateway_client,
        route_resolver=MuliuPlanRouteResolver(
            config.execution_interpretation.server_environments_path,
            call_contracts,
        ),
        call_contracts=call_contracts,
        baye_checks=baye_checks,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if install_stack_dump_signals():
            logger.warning("stack dumps enabled: kill -USR1 (threads) / -USR2 (asyncio tasks) pid")
        task = asyncio.create_task(worker.run_forever())
        if baye_checks is not None:
            resumed = baye_checks.resume()
            if resumed:
                logger.warning("baye checks resumed after restart: %s", resumed)
        try:
            yield
        finally:
            worker.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if baye_checks is not None:
                await baye_checks.stop()

    app = FastAPI(title="Feishu Muliu Intranet Worker", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": "worker"}

    return app


def _add_gateway_worker_routes(
    app: FastAPI,
    config: Any,
    job_store: MessageJobStore,
    job_processor: MessageJobProcessor,
    route_store: Optional[ExecutionRouteStore] = None,
    executable_contracts: Any = None,
    *,
    feishu_client: Any = None,
    judge_ai_client: Any = None,
    team_kb_reviews: Optional[TeamKbReviewStore] = None,
) -> None:
    """Expose a minimal authenticated pull protocol for the intranet worker."""
    delivered_followups: set[str] = set()
    judge_tasks: set[asyncio.Task[None]] = set()

    def require_worker(
        authorization: str | None,
        worker_id: str | None,
    ) -> str:
        expected = "Bearer {}".format(config.runtime.worker_token)
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid worker authorization")
        if not worker_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", worker_id):
            raise HTTPException(status_code=400, detail="invalid worker id")
        return worker_id

    @app.post("/internal/worker/jobs/claim", include_in_schema=False)
    async def claim_worker_job(
        authorization: Optional[str] = Header(default=None),
        x_feishu_worker_id: Optional[str] = Header(default=None),
    ) -> Any:
        worker_id = require_worker(authorization, x_feishu_worker_id)
        await _deliver_expired_worker_leases(
            job_store,
            job_processor,
            config.runtime.lease_timeout_seconds,
        )
        job = job_store.claim_next_worker_job(worker_id)
        if job is None:
            return Response(status_code=204)
        step_routes: list[str] = []
        try:
            # Per-step Muliu / Server Manager route from the admin switches at claim time; the Worker re-checks.
            if route_store is not None and executable_contracts is not None:
                step_routes = route_store.step_routes(
                    parse_plan(job.plan_json, max_steps=config.muliu.max_steps), job.route_region, executable_contracts
                )
        except Exception:  # noqa: BLE001 - an unreadable plan is rejected by the Worker's own validation
            logger.warning("step routes unavailable message_id=%s", job.message_id, exc_info=True)
            step_routes = []
        return {
            "message_id": job.message_id,
            "plan_json": job.plan_json,
            "route_region": job.route_region,
            "step_routes": step_routes,
        }

    @app.post("/internal/worker/jobs/{message_id}/heartbeat", include_in_schema=False)
    async def heartbeat_worker_job(
        message_id: str,
        authorization: Optional[str] = Header(default=None),
        x_feishu_worker_id: Optional[str] = Header(default=None),
    ) -> dict[str, str]:
        worker_id = require_worker(authorization, x_feishu_worker_id)
        if not job_store.renew_worker_lease(message_id, worker_id):
            raise HTTPException(status_code=404, detail="worker lease not found")
        return {"status": "ok"}

    @app.post("/internal/worker/jobs/{message_id}/result", include_in_schema=False)
    async def finish_worker_job(
        message_id: str,
        payload: dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        x_feishu_worker_id: Optional[str] = Header(default=None),
    ) -> dict[str, str]:
        worker_id = require_worker(authorization, x_feishu_worker_id)
        leased_job = job_store.get(message_id)
        if (
            leased_job is None
            or leased_job.status != "leased"
            or leased_job.worker_id != worker_id
        ):
            raise HTTPException(status_code=404, detail="worker lease not found")
        try:
            result = parse_execution_result(payload.get("result"), max_steps=config.muliu.max_steps)
            plan = parse_plan(leased_job.plan_json, max_steps=config.muliu.max_steps)
            validate_execution_result_matches_plan(result, plan)
        except (MuliuPlanError, WorkerProtocolError) as exc:
            logger.warning("worker result rejected message_id=%s worker_id=%s reason=%s", message_id, worker_id, exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result.succeeded:
            status = "done"
            error = ""
        elif result.result_unknown:
            status = "submitted_result_unknown"
            error = result.result_unknown_reason
        else:
            status = "failed"
            error = result.failure_reason
        pending_job = job_store.complete_worker_job(
            message_id,
            worker_id,
            status=status,
            error=error,
        )
        if pending_job is None:
            raise HTTPException(status_code=404, detail="worker lease not found")
        await job_processor.present_execution_result(
            pending_job,
            result,
            pending_job.confirmation_message_id,
        )
        return {"status": "accepted"}

    async def judge_and_reply(message_id: str, followup: dict[str, Any], image_keys: list[tuple[str, str]]) -> None:
        judgement = None
        judge_error = ""
        try:
            text = await asyncio.wait_for(
                judge_ai_client.generate([], build_judge_prompt(followup)),
                timeout=config.execution_interpretation.background_timeout_seconds,
            )
            judgement = parse_judgement(text)
        except asyncio.TimeoutError:
            judge_error = "AI 超时"
        except Exception as exc:  # noqa: BLE001 - the screenshots still go out
            logger.warning("baye judge failed message_id=%s", message_id, exc_info=True)
            judge_error = "AI 读取失败：{}".format(type(exc).__name__)
        try:
            card = build_followup_card(followup, image_keys, judgement=judgement, judge_error=judge_error)
            await feishu_client.reply_card(message_id, card)
            logger.warning(
                "baye follow-up delivered message_id=%s check_id=%s status=%s judged=%s",
                message_id, followup["check_id"], followup["status"], judgement is not None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("baye follow-up reply failed message_id=%s check_id=%s", message_id, followup["check_id"])

    # Team knowledge base (intranet team-kb service): held uploads go to the admin page, decisions come back.
    def require_team_kb(authorization: str | None, worker_id: str | None) -> TeamKbReviewStore:
        require_worker(authorization, worker_id)
        if team_kb_reviews is None:
            raise HTTPException(status_code=503, detail="team kb reviews unavailable")
        return team_kb_reviews

    @app.post("/internal/team-kb/reviews", include_in_schema=False)
    async def report_team_kb_reviews(
        payload: dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        x_feishu_worker_id: Optional[str] = Header(default=None),
    ) -> dict[str, int]:
        store = require_team_kb(authorization, x_feishu_worker_id)
        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or len(raw_items) > 100:
            raise HTTPException(status_code=422, detail="items must be a list of at most 100")
        try:
            items = [parse_review_item(raw) for raw in raw_items]
        except ReviewError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        inserted = store.report(items)
        if inserted:
            logger.warning("team kb reviews reported inserted=%s", inserted)
        return {"accepted": len(items), "inserted": inserted}

    @app.get("/internal/team-kb/decisions", include_in_schema=False)
    async def team_kb_decisions(
        authorization: Optional[str] = Header(default=None),
        x_feishu_worker_id: Optional[str] = Header(default=None),
    ) -> dict[str, Any]:
        return {"items": require_team_kb(authorization, x_feishu_worker_id).decisions_to_apply()}

    @app.post("/internal/team-kb/reviews/{review_id}/applied", include_in_schema=False)
    async def team_kb_applied(
        review_id: str,
        payload: dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        x_feishu_worker_id: Optional[str] = Header(default=None),
    ) -> dict[str, str]:
        store = require_team_kb(authorization, x_feishu_worker_id)
        if not store.mark_applied(review_id, ok=bool(payload.get("ok")), message=str(payload.get("message") or "")):
            raise HTTPException(status_code=404, detail="review not found or not decided")
        logger.warning("team kb review applied id=%s ok=%s", review_id, bool(payload.get("ok")))
        return {"status": "ok"}

    @app.post("/internal/worker/jobs/{message_id}/followup", include_in_schema=False)
    async def worker_job_followup(
        message_id: str,
        payload: dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        x_feishu_worker_id: Optional[str] = Header(default=None),
    ) -> dict[str, str]:
        """A background result (开服配置检查 screenshots) for a job the same Worker already finished."""
        worker_id = require_worker(authorization, x_feishu_worker_id)
        job = job_store.get(message_id)
        if job is None or job.worker_id != worker_id:
            raise HTTPException(status_code=404, detail="worker job not found")
        try:
            plan = parse_plan(job.plan_json, max_steps=config.muliu.max_steps)
            followup = parse_followup(payload)
        except (MuliuPlanError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if executable_contracts is None or executable_contracts.backend_for(plan.steps[0]) != BACKEND_BAYE_CHECK:
            raise HTTPException(status_code=422, detail="job does not accept follow-ups")
        if followup["check_id"] in delivered_followups:
            return {"status": "duplicate"}
        if feishu_client is None:
            raise HTTPException(status_code=503, detail="feishu client unavailable")
        image_keys: list[tuple[str, str]] = []
        for image in followup["images"]:
            try:
                key = await feishu_client.upload_image(image["data"], "{}.png".format(image["name"] or "screenshot"))
                image_keys.append((image["label"], key))
            except Exception:  # noqa: BLE001 - still deliver the text result
                logger.warning("baye follow-up image upload failed message_id=%s", message_id, exc_info=True)
        delivered_followups.add(followup["check_id"])
        if judge_ai_client is not None and should_judge(followup):
            # AI may outlast the Worker's 30 s request timeout: accept now, judge and reply in the background.
            task = asyncio.create_task(judge_and_reply(message_id, followup, image_keys))
            judge_tasks.add(task)
            task.add_done_callback(judge_tasks.discard)
            return {"status": "accepted"}
        try:
            await feishu_client.reply_card(message_id, build_followup_card(followup, image_keys))
        except Exception:
            delivered_followups.discard(followup["check_id"])  # let the Worker retry
            raise
        logger.warning(
            "baye follow-up delivered message_id=%s check_id=%s status=%s images=%s",
            message_id, followup["check_id"], followup["status"], len(image_keys),
        )
        return {"status": "accepted"}


def _expire_worker_leases(job_store: MessageJobStore, lease_timeout_seconds: float) -> list[Any]:
    expired = job_store.expire_worker_leases(lease_timeout_seconds)
    for job in expired:
        logger.warning("worker lease expired message_id=%s worker_id=%s", job.message_id, job.worker_id)
    return expired


async def _watch_worker_leases(
    job_store: MessageJobStore,
    job_processor: MessageJobProcessor,
    lease_timeout_seconds: float,
) -> None:
    """Turn a lost lease into an explicit card/result-unknown notification."""
    interval_seconds = min(max(1.0, lease_timeout_seconds / 3), 30.0)
    while True:
        await asyncio.sleep(interval_seconds)
        await _deliver_expired_worker_leases(
            job_store,
            job_processor,
            lease_timeout_seconds,
        )


async def _deliver_expired_worker_leases(
    job_store: MessageJobStore,
    job_processor: MessageJobProcessor,
    lease_timeout_seconds: float,
) -> None:
    for job in _expire_worker_leases(job_store, lease_timeout_seconds):
        result = _worker_lease_unknown_result(job)
        reply = format_execution_result(result)
        await job_processor.deliver_execution_result(
            job,
            job.confirmation_message_id,
            reply,
            status_card=build_execution_status_card(result, detail=result_card_detail(reply, result)),
        )


def _worker_lease_unknown_result(job: Any):
    from .muliu_executor import MuliuExecutionResult

    try:
        summary = parse_plan(job.plan_json).summary
    except MuliuPlanError:
        summary = job.text
    return MuliuExecutionResult(
        summary=summary,
        step_results=[],
        result_unknown_reason=job.last_error,
    )


def _create_default_app() -> FastAPI:
    try:
        return create_app()
    except ConfigError as exc:
        detail = str(exc)
        # Loud on purpose: the fallback app starts "successfully" but serves no Feishu / Worker traffic.
        logger.error("CONFIG ERROR — service is NOT running (only /health is served): %s", detail)
        fallback = FastAPI(title="Feishu Muliu Server Operations Bot")

        @fallback.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "missing_config", "detail": detail}

        return fallback


app = _create_default_app()
