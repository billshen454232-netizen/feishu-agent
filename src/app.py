from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI

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
from .muliu_planner import MuliuPlanner
from .muliu_script_catalog import MuliuScriptCatalog
from .script_knowledge_base import ScriptKnowledgeBase

logger = logging.getLogger(__name__)


def create_app(config_path: str | Path = "config/config.json") -> FastAPI:
    config = load_config(config_path)
    conversations = ConversationStore(config.conversation.max_history_messages)
    ai_client = create_ai_client(config.ai)
    feishu_client = FeishuClient(config.feishu)
    job_store = MessageJobStore(config.job_queue.database_path)
    job_store.initialize()
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
    muliu_executor = MuliuExecutor(config.muliu)
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
        recoverable = job_store.list_recoverable_unfinished(None, config.job_queue.recovery_window_seconds)
        for job in recoverable:
            await job_queue.enqueue(job.message_id)
        job_processor.start()
        try:
            yield
        finally:
            await job_processor.stop()

    app = FastAPI(title="Feishu Muliu Server Operations Bot", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/feishu/events")
    async def feishu_events(payload: dict[str, Any]) -> dict[str, Any]:
        return await event_handler.handle(payload)

    return app


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
