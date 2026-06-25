from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI

from .ai_client import create_ai_client
from .config import ConfigError, load_config
from .conversation import ConversationStore
from .event_handler import FeishuEventHandler
from .feishu_client import FeishuClient
from .knowledge_base import DualChainVaultKnowledgeBase
from .message_jobs import MessageJobStore
from .message_worker import MessageJobProcessor, MessageJobQueue

logger = logging.getLogger(__name__)


def create_app(config_path: str | Path = "config/config.json") -> FastAPI:
    config = load_config(config_path)
    conversations = ConversationStore(config.conversation.max_history_messages)
    ai_client = create_ai_client(config.ai)
    feishu_client = FeishuClient(config.feishu)
    knowledge_base = None
    if config.knowledge_base.enabled and config.knowledge_base.type == "dual_chain_vault":
        knowledge_base = DualChainVaultKnowledgeBase(
            vault_path=config.knowledge_base.vault_path,
            min_score=config.knowledge_base.min_score,
            max_results=config.knowledge_base.max_results,
            max_context_chars=config.knowledge_base.max_context_chars,
        )

    job_store = MessageJobStore(config.job_queue.database_path)
    job_store.initialize()
    job_queue = MessageJobQueue()
    job_processor = MessageJobProcessor(config, job_store, conversations, ai_client, feishu_client, knowledge_base=knowledge_base, queue=job_queue)
    event_handler = FeishuEventHandler(config, job_store, job_queue)

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

    app = FastAPI(title="Feishu AI Bot Backend", lifespan=lifespan)

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
        fallback = FastAPI(title="Feishu AI Bot Backend")

        @fallback.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "missing_config", "detail": detail}

        return fallback


app = _create_default_app()
