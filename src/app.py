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
from .event_handler import FeishuEventHandler
from .feishu_client import FeishuClient
from .message_jobs import MessageJobStore
from .message_worker import MessageJobProcessor, MessageJobQueue
from .muliu_executor import MuliuExecutor
from .muliu_firewall import MuliuFirewall
from .muliu_intent import MuliuIntentRouter
from .muliu_plan import MuliuPlanError, parse_plan
from .muliu_planner import MuliuPlanner
from .muliu_script_catalog import MuliuScriptCatalog
from .muliu_worker import GatewayWorkerClient, MuliuWorker
from .operation_message import build_execution_status_card, format_execution_result
from .script_knowledge_base import ScriptKnowledgeBase
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
    conversations = ConversationStore(config.conversation.max_history_messages)
    ai_client = create_ai_client(config.ai)
    feishu_client = FeishuClient(config.feishu)
    job_queue = MessageJobQueue()

    script_catalog = MuliuScriptCatalog(config.muliu.script_catalog_path)
    operation_planner = MuliuPlanner(
        ai_client=ai_client,
        script_catalog=script_catalog,
        max_steps=config.muliu.max_steps,
    )
    muliu_firewall = MuliuFirewall(
        config.muliu,
        call_contracts=script_catalog.read_call_contracts(),
    )
    muliu_executor = MuliuExecutor(config.muliu) if config.runtime.role == "local" else None
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
        script_knowledge_base=script_knowledge_base,
        script_catalog=script_catalog,
        intent_router=MuliuIntentRouter(),
        conversation_log=conversation_log,
    )
    event_handler = FeishuEventHandler(config, job_store, job_queue, job_processor=job_processor)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
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
        try:
            yield
        finally:
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
        _add_gateway_worker_routes(app, config, job_store, job_processor)

    return app


def _create_worker_app(config: Any) -> FastAPI:
    """Run a worker as a process without exposing inbound operational endpoints."""
    script_catalog = MuliuScriptCatalog(config.muliu.script_catalog_path)
    firewall = MuliuFirewall(config.muliu, call_contracts=script_catalog.read_call_contracts())
    worker = MuliuWorker(
        config=config,
        firewall=firewall,
        executor=MuliuExecutor(config.muliu),
        gateway_client=GatewayWorkerClient(config),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(worker.run_forever())
        try:
            yield
        finally:
            worker.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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
) -> None:
    """Expose a minimal authenticated pull protocol for the intranet worker."""

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
        return {"message_id": job.message_id, "plan_json": job.plan_json}

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
        reply = format_execution_result(result)
        await job_processor.deliver_execution_result(
            pending_job,
            pending_job.confirmation_message_id,
            reply,
            status_card=build_execution_status_card(result),
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
        await job_processor.deliver_execution_result(
            job,
            job.confirmation_message_id,
            format_execution_result(result),
            status_card=build_execution_status_card(result),
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
        fallback = FastAPI(title="Feishu Muliu Server Operations Bot")

        @fallback.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "missing_config", "detail": detail}

        return fallback


app = _create_default_app()
