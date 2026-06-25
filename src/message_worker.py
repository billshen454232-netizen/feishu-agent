from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .config import AppConfig
from .conversation import ConversationStore
from .message_jobs import MessageJobStore

logger = logging.getLogger(__name__)


class MessageJobQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def enqueue(self, message_id: str) -> None:
        await self._queue.put(message_id)

    async def get(self) -> str:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()


class MessageJobProcessor:
    def __init__(
        self,
        config: AppConfig,
        store: MessageJobStore,
        conversations: ConversationStore,
        ai_client: Any,
        feishu_client: Any,
        knowledge_base: Any | None = None,
        queue: MessageJobQueue | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._conversations = conversations
        self._ai_client = ai_client
        self._feishu_client = feishu_client
        self._knowledge_base = knowledge_base
        self._queue = queue
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        if self._queue is None:
            raise RuntimeError("Cannot start MessageJobProcessor without a queue")
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def process_one(self, message_id: str) -> None:
        job = self._store.get(message_id)
        if job is None:
            logger.warning("job missing message_id=%s", message_id)
            return
        if job.status in {"done", "failed", "expired"}:
            logger.warning("job skipped message_id=%s status=%s", message_id, job.status)
            return

        total_started = time.perf_counter()
        knowledge_ms = 0
        ai_ms = 0
        feishu_reply_ms = 0
        local_knowledge_hit = False
        logger.warning("job processing message_id=%s", message_id)
        self._store.mark_processing(message_id)
        try:
            history = self._conversations.get(job.chat_id)
            knowledge_started = time.perf_counter()
            ai_user_text, source_paths, local_knowledge_hit = self._build_ai_user_text(job.text)
            knowledge_ms = self._elapsed_ms(knowledge_started)
            if ai_user_text is None:
                reply = "📚 本地知识库没有找到与该问题相关的内容。当前已关闭外部/通用知识回答。"
            else:
                ai_started = time.perf_counter()
                reply = await self._ai_client.generate(history, ai_user_text)
                ai_ms = self._elapsed_ms(ai_started)
                if local_knowledge_hit:
                    reply = self._format_local_knowledge_reply(reply, source_paths)
            self._conversations.append(job.chat_id, "user", job.text)
            self._conversations.append(job.chat_id, "assistant", reply)
            feishu_reply_started = time.perf_counter()
            await self._feishu_client.reply_text(message_id, reply)
            feishu_reply_ms = self._elapsed_ms(feishu_reply_started)
            self._store.mark_done(message_id)
            logger.warning(
                "job timings message_id=%s knowledge_ms=%s ai_ms=%s feishu_reply_ms=%s total_ms=%s local_knowledge_hit=%s fallback=%s",
                message_id,
                knowledge_ms,
                ai_ms,
                feishu_reply_ms,
                self._elapsed_ms(total_started),
                local_knowledge_hit,
                self._config.knowledge_base.fallback_when_miss,
            )
            logger.warning("job done message_id=%s", message_id)
        except Exception as exc:  # noqa: BLE001 - store operational failure details for diagnosis
            self._store.mark_failed(message_id, str(exc))
            logger.exception("job failed message_id=%s error=%s", message_id, exc)

    async def _run(self) -> None:
        if self._queue is None:
            return
        while not self._stopping:
            message_id = await self._queue.get()
            try:
                await self.process_one(message_id)
            finally:
                self._queue.task_done()

    def _build_ai_user_text(self, user_text: str) -> tuple[str | None, list[str], bool]:
        if self._knowledge_base is None:
            return user_text, [], False
        result = self._knowledge_base.search(user_text)
        if result.hit:
            source_paths = [source.path for source in result.sources]
            sources = "\n".join(f"- {path}" for path in source_paths)
            return (
                "请优先依据以下本地知识库资料回答；如果资料不足，请明确说明不足之处，再结合通用知识补充。\n\n"
                f"{result.context}\n\n"
                f"来源列表：\n{sources}\n\n"
                f"用户问题：{user_text}",
                source_paths,
                True,
            )
        if self._config.knowledge_base.fallback_when_miss == "local_only":
            return None, [], False
        return (
            "本地知识库没有找到足够相关的资料。请先在回答开头说明这一点，"
            "然后再基于通用知识或可用外部能力进行推理回答。\n\n"
            f"用户问题：{user_text}",
            [],
            False,
        )

    @staticmethod
    def _format_local_knowledge_reply(reply: str, source_paths: list[str]) -> str:
        sources = "\n".join(f"- {path}" for path in source_paths)
        return f"📚 以下回答基于本地知识库资料生成。\n\n{reply}\n\n来源：\n{sources}"

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
