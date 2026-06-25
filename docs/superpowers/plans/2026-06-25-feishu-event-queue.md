# Feishu Event Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Feishu message handling fast to acknowledge and idempotent across restarts using a SQLite-backed job table and in-process queue.

**Architecture:** The HTTP event handler validates Feishu callbacks, extracts message data, persists a queued job if the `message_id` is new, pushes the job into an in-process queue, and immediately returns `{"code": 0}`. A background worker reads queued jobs, builds the knowledge-base-augmented AI prompt, sends one Feishu reply, and updates persistent job state.

**Tech Stack:** Python 3.11+, FastAPI, standard-library `sqlite3`, standard-library `asyncio.Queue`, pytest, pytest-asyncio.

## Global Constraints

- Do not add Redis, RabbitMQ, Celery, or any new runtime dependency.
- Use SQLite with default database path `data/feishu_bot.sqlite3`.
- Use `message_id` as the primary idempotency key.
- Job statuses are exactly `queued`, `processing`, `done`, `failed`, and `expired`.
- Recovery window default is exactly `600` seconds.
- Incoming duplicate messages must return `{"code": 0}` and must not enqueue or reply again.
- The Feishu callback request path must not call AI generation or Feishu reply APIs.
- Unfinished jobs older than the recovery window must become `expired` on startup.
- First version does not automatically retry failed jobs.

---

## File Structure

- Create `src/message_jobs.py`
  - Owns the `MessageJob` dataclass and `MessageJobStore` SQLite persistence/idempotency logic.
- Create `src/message_worker.py`
  - Owns `MessageJobQueue` and `MessageJobProcessor`, including background worker loop and AI/reply processing.
- Modify `src/config.py`
  - Adds `JobQueueConfig` and parses optional `job_queue` config.
- Modify `src/event_handler.py`
  - Narrows responsibility to Feishu callback validation, message extraction, job enqueue, and quick ACK.
- Modify `src/app.py`
  - Wires store, queue, processor, startup recovery, worker startup, and worker shutdown.
- Add `tests/test_message_jobs.py`
  - Unit tests for SQLite state transitions, duplicate handling, and restart recovery.
- Add `tests/test_message_worker.py`
  - Unit tests for successful and failed worker processing.
- Modify `tests/test_event_handler.py`
  - Updates existing handler tests to assert queueing/quick ACK instead of synchronous AI/reply.
- Modify `tests/test_config.py`
  - Tests default and custom job queue config parsing.
- Modify `tests/test_app.py`
  - Keeps app creation tests passing with default job queue wiring.

---

### Task 1: Add Job Queue Configuration

**Files:**
- Modify: `src/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `JobQueueConfig(enabled: bool, database_path: str, recovery_window_seconds: int)`.
- Produces: `AppConfig.job_queue: JobQueueConfig`.
- Consumed by later tasks: `create_app()` uses `config.job_queue.database_path` and `config.job_queue.recovery_window_seconds`.

- [ ] **Step 1: Write failing config tests**

Add these tests to `tests/test_config.py`:

```python
def test_job_queue_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.job_queue.enabled is True
    assert config.job_queue.database_path == "data/feishu_bot.sqlite3"
    assert config.job_queue.recovery_window_seconds == 600


def test_job_queue_custom_values(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "feishu": {"app_id": "app", "app_secret": "secret"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "job_queue": {"enabled": False, "database_path": "tmp/jobs.sqlite3", "recovery_window_seconds": 120},
    }), encoding="utf-8")

    config = load_config(config_path)

    assert config.job_queue.enabled is False
    assert config.job_queue.database_path == "tmp/jobs.sqlite3"
    assert config.job_queue.recovery_window_seconds == 120
```

- [ ] **Step 2: Run config tests and verify failure**

Run:

```bash
python -m pytest tests/test_config.py -q
```

Expected: fails because `AppConfig` has no `job_queue` attribute.

- [ ] **Step 3: Implement minimal config support**

In `src/config.py`, add this dataclass after `BotConfig`:

```python
@dataclass(frozen=True)
class JobQueueConfig:
    enabled: bool = True
    database_path: str = "data/feishu_bot.sqlite3"
    recovery_window_seconds: int = 600
```

Update `AppConfig`:

```python
@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    feishu: FeishuConfig
    ai: AIConfig
    conversation: ConversationConfig
    knowledge_base: KnowledgeBaseConfig
    bot: BotConfig
    job_queue: JobQueueConfig
```

Inside `load_config()`, add:

```python
job_queue_raw = raw.get("job_queue", {})
```

And add this argument to the returned `AppConfig`:

```python
job_queue=JobQueueConfig(
    enabled=bool(job_queue_raw.get("enabled", True)) if isinstance(job_queue_raw, dict) else True,
    database_path=str(job_queue_raw.get("database_path", "data/feishu_bot.sqlite3")) if isinstance(job_queue_raw, dict) else "data/feishu_bot.sqlite3",
    recovery_window_seconds=int(job_queue_raw.get("recovery_window_seconds", 600)) if isinstance(job_queue_raw, dict) else 600,
),
```

- [ ] **Step 4: Run config tests and verify pass**

Run:

```bash
python -m pytest tests/test_config.py -q
```

Expected: all config tests pass.

---

### Task 2: Implement SQLite MessageJobStore

**Files:**
- Create: `src/message_jobs.py`
- Add: `tests/test_message_jobs.py`

**Interfaces:**
- Produces: `MessageJob` dataclass.
- Produces: `MessageJobStore(database_path: str | Path)`.
- Produces: `MessageJobStore.initialize() -> None`.
- Produces: `MessageJobStore.create_if_new(...) -> tuple[MessageJob, bool]` where `bool` is `True` only for newly-created jobs.
- Produces: `MessageJobStore.get(message_id: str) -> MessageJob | None`.
- Produces: `mark_processing(message_id: str)`, `mark_done(message_id: str)`, `mark_failed(message_id: str, error: str)`, `expire_stale_unfinished(now: int, recovery_window_seconds: int) -> list[MessageJob]`, and `list_recoverable_unfinished(now: int, recovery_window_seconds: int) -> list[MessageJob]`.
- Consumed by later tasks: event handler calls `create_if_new`; app startup calls recovery methods; worker calls state transition methods.

- [ ] **Step 1: Write failing store tests**

Create `tests/test_message_jobs.py`:

```python
from src.message_jobs import MessageJobStore


def test_create_if_new_persists_queued_job(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()

    job, created = store.create_if_new(
        message_id="om_1",
        event_id="evt_1",
        chat_id="oc_1",
        user_id="ou_1",
        text="你好",
        now=1000,
    )

    assert created is True
    assert job.message_id == "om_1"
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.created_at == 1000
    assert store.get("om_1") == job


def test_create_if_new_returns_existing_job_for_duplicate_message_id(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    first, created_first = store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "first", now=1000)

    second, created_second = store.create_if_new("om_1", "evt_2", "oc_1", "ou_1", "duplicate", now=1005)

    assert created_first is True
    assert created_second is False
    assert second == first
    assert store.get("om_1").text == "first"


def test_state_transitions_update_status_attempts_and_error(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "你好", now=1000)

    store.mark_processing("om_1", now=1001)
    processing = store.get("om_1")
    assert processing.status == "processing"
    assert processing.updated_at == 1001

    store.mark_failed("om_1", "AI timeout", now=1002)
    failed = store.get("om_1")
    assert failed.status == "failed"
    assert failed.attempts == 1
    assert failed.last_error == "AI timeout"
    assert failed.updated_at == 1002

    store.mark_done("om_1", now=1003)
    done = store.get("om_1")
    assert done.status == "done"
    assert done.updated_at == 1003


def test_recovery_lists_recent_unfinished_and_expires_old_unfinished(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("recent", "evt_recent", "oc_1", "ou_1", "recent text", now=950)
    store.create_if_new("old", "evt_old", "oc_1", "ou_1", "old text", now=300)
    store.mark_processing("old", now=301)
    store.create_if_new("done", "evt_done", "oc_1", "ou_1", "done text", now=900)
    store.mark_done("done", now=901)

    recoverable = store.list_recoverable_unfinished(now=1000, recovery_window_seconds=600)
    expired = store.expire_stale_unfinished(now=1000, recovery_window_seconds=600)

    assert [job.message_id for job in recoverable] == ["recent"]
    assert [job.message_id for job in expired] == ["old"]
    assert store.get("old").status == "expired"
    assert store.get("done").status == "done"
```

- [ ] **Step 2: Run store tests and verify failure**

Run:

```bash
python -m pytest tests/test_message_jobs.py -q
```

Expected: import failure because `src.message_jobs` does not exist.

- [ ] **Step 3: Implement `src/message_jobs.py`**

Create `src/message_jobs.py`:

```python
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

JobStatus = Literal["queued", "processing", "done", "failed", "expired"]


@dataclass(frozen=True)
class MessageJob:
    message_id: str
    event_id: str
    chat_id: str
    user_id: str
    text: str
    status: str
    attempts: int
    created_at: int
    updated_at: int
    last_error: str


class MessageJobStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS message_jobs (
                    message_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_error TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create_if_new(
        self,
        message_id: str,
        event_id: str,
        chat_id: str,
        user_id: str,
        text: str,
        now: int | None = None,
    ) -> tuple[MessageJob, bool]:
        current = self.get(message_id)
        if current is not None:
            return current, False
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO message_jobs (message_id, event_id, chat_id, user_id, text, status, attempts, created_at, updated_at, last_error)
                VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, '')
                """,
                (message_id, event_id, chat_id, user_id, text, timestamp, timestamp),
            )
            conn.commit()
        job = self.get(message_id)
        if job is None:
            raise RuntimeError(f"Failed to create message job: {message_id}")
        return job, True

    def get(self, message_id: str) -> MessageJob | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT message_id, event_id, chat_id, user_id, text, status, attempts, created_at, updated_at, last_error
                FROM message_jobs
                WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def mark_processing(self, message_id: str, now: int | None = None) -> None:
        self._update_status(message_id, "processing", self._now(now))

    def mark_done(self, message_id: str, now: int | None = None) -> None:
        self._update_status(message_id, "done", self._now(now))

    def mark_failed(self, message_id: str, error: str, now: int | None = None) -> None:
        timestamp = self._now(now)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE message_jobs
                SET status = 'failed', attempts = attempts + 1, last_error = ?, updated_at = ?
                WHERE message_id = ?
                """,
                (error, timestamp, message_id),
            )
            conn.commit()

    def list_recoverable_unfinished(self, now: int | None, recovery_window_seconds: int) -> list[MessageJob]:
        timestamp = self._now(now)
        minimum_created_at = timestamp - recovery_window_seconds
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, event_id, chat_id, user_id, text, status, attempts, created_at, updated_at, last_error
                FROM message_jobs
                WHERE status IN ('queued', 'processing') AND created_at >= ?
                ORDER BY created_at ASC
                """,
                (minimum_created_at,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def expire_stale_unfinished(self, now: int | None, recovery_window_seconds: int) -> list[MessageJob]:
        timestamp = self._now(now)
        maximum_created_at = timestamp - recovery_window_seconds
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, event_id, chat_id, user_id, text, status, attempts, created_at, updated_at, last_error
                FROM message_jobs
                WHERE status IN ('queued', 'processing') AND created_at < ?
                ORDER BY created_at ASC
                """,
                (maximum_created_at,),
            ).fetchall()
            conn.execute(
                """
                UPDATE message_jobs
                SET status = 'expired', updated_at = ?
                WHERE status IN ('queued', 'processing') AND created_at < ?
                """,
                (timestamp, maximum_created_at),
            )
            conn.commit()
        return [self._row_to_job(row) for row in rows]

    def _update_status(self, message_id: str, status: JobStatus, timestamp: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE message_jobs SET status = ?, updated_at = ? WHERE message_id = ?",
                (status, timestamp, message_id),
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now(now: int | None) -> int:
        return int(time.time()) if now is None else now

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> MessageJob:
        return MessageJob(
            message_id=str(row["message_id"]),
            event_id=str(row["event_id"]),
            chat_id=str(row["chat_id"]),
            user_id=str(row["user_id"]),
            text=str(row["text"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            last_error=str(row["last_error"]),
        )
```

- [ ] **Step 4: Run store tests and verify pass**

Run:

```bash
python -m pytest tests/test_message_jobs.py -q
```

Expected: all store tests pass.

---

### Task 3: Add MessageJobQueue and MessageJobProcessor

**Files:**
- Create: `src/message_worker.py`
- Add: `tests/test_message_worker.py`

**Interfaces:**
- Consumes: `MessageJobStore.get`, `mark_processing`, `mark_done`, `mark_failed` from Task 2.
- Produces: `MessageJobQueue.enqueue(message_id: str) -> None`.
- Produces: `MessageJobQueue.get() -> Awaitable[str]`.
- Produces: `MessageJobProcessor.process_one(message_id: str) -> Awaitable[None]`.
- Produces: `MessageJobProcessor.start() -> None` and `stop() -> Awaitable[None]`.
- Later consumed by `src.app.create_app()` and `src.event_handler.FeishuEventHandler`.

- [ ] **Step 1: Write failing worker tests**

Create `tests/test_message_worker.py`:

```python
import pytest

from src.config import AIConfig, AppConfig, BotConfig, ConversationConfig, FeishuConfig, KnowledgeBaseConfig, ServerConfig, JobQueueConfig
from src.conversation import ConversationStore
from src.knowledge_base import KnowledgeSearchResult, KnowledgeSource
from src.message_jobs import MessageJobStore
from src.message_worker import MessageJobProcessor, MessageJobQueue


class FakeAIClient:
    def __init__(self):
        self.calls = []
        self.error = None

    async def generate(self, history, user_message):
        self.calls.append((history, user_message))
        if self.error:
            raise self.error
        return f"AI:{user_message}"


class FakeFeishuClient:
    def __init__(self):
        self.replies = []

    async def reply_text(self, message_id, text):
        self.replies.append((message_id, text))


class FakeKnowledgeBase:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self.result


def make_config():
    return AppConfig(
        server=ServerConfig(),
        feishu=FeishuConfig(app_id="app", app_secret="secret", verification_token="verify"),
        ai=AIConfig(provider="openai_compatible", base_url="https://api.example.com/v1", api_key="key", model="m"),
        conversation=ConversationConfig(max_history_messages=4),
        knowledge_base=KnowledgeBaseConfig(enabled=False),
        bot=BotConfig(name="AI助手"),
        job_queue=JobQueueConfig(),
    )


@pytest.mark.asyncio
async def test_process_one_replies_and_marks_done(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "你好", now=1000)
    ai = FakeAIClient()
    feishu = FakeFeishuClient()
    conversations = ConversationStore(4)
    processor = MessageJobProcessor(make_config(), store, conversations, ai, feishu)

    await processor.process_one("om_1")

    assert store.get("om_1").status == "done"
    assert ai.calls == [([], "你好")]
    assert feishu.replies == [("om_1", "AI:你好")]
    assert conversations.get("oc_1") == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "AI:你好"},
    ]


@pytest.mark.asyncio
async def test_process_one_uses_knowledge_base_context(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "本地问题", now=1000)
    ai = FakeAIClient()
    feishu = FakeFeishuClient()
    kb = FakeKnowledgeBase(KnowledgeSearchResult(
        hit=True,
        context="[来源: articles/a.md]\n本地知识内容",
        sources=[KnowledgeSource(kind="article", path="articles/a.md", title="A", score=5)],
    ))
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), ai, feishu, knowledge_base=kb)

    await processor.process_one("om_1")

    assert kb.queries == ["本地问题"]
    assert "请优先依据以下本地知识库资料回答" in ai.calls[0][1]
    assert "本地知识内容" in ai.calls[0][1]
    assert "用户问题：本地问题" in ai.calls[0][1]


@pytest.mark.asyncio
async def test_process_one_marks_failed_when_ai_raises(tmp_path):
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_1", "evt_1", "oc_1", "ou_1", "你好", now=1000)
    ai = FakeAIClient()
    ai.error = RuntimeError("model unavailable")
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(make_config(), store, ConversationStore(4), ai, feishu)

    await processor.process_one("om_1")

    job = store.get("om_1")
    assert job.status == "failed"
    assert job.attempts == 1
    assert "model unavailable" in job.last_error
    assert feishu.replies == []


@pytest.mark.asyncio
async def test_queue_puts_and_gets_message_ids():
    queue = MessageJobQueue()

    await queue.enqueue("om_1")
    message_id = await queue.get()

    assert message_id == "om_1"
```

- [ ] **Step 2: Run worker tests and verify failure**

Run:

```bash
python -m pytest tests/test_message_worker.py -q
```

Expected: import failure because `src.message_worker` does not exist.

- [ ] **Step 3: Implement worker and processor**

Create `src/message_worker.py`:

```python
from __future__ import annotations

import asyncio
import logging
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

        logger.warning("job processing message_id=%s", message_id)
        self._store.mark_processing(message_id)
        try:
            history = self._conversations.get(job.chat_id)
            ai_user_text = self._build_ai_user_text(job.text)
            reply = await self._ai_client.generate(history, ai_user_text)
            self._conversations.append(job.chat_id, "user", job.text)
            self._conversations.append(job.chat_id, "assistant", reply)
            await self._feishu_client.reply_text(message_id, reply)
            self._store.mark_done(message_id)
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

    def _build_ai_user_text(self, user_text: str) -> str:
        if self._knowledge_base is None:
            return user_text
        result = self._knowledge_base.search(user_text)
        if result.hit:
            sources = "\n".join(f"- {source.path}" for source in result.sources)
            return (
                "请优先依据以下本地知识库资料回答；如果资料不足，请明确说明不足之处，再结合通用知识补充。\n\n"
                f"{result.context}\n\n"
                f"来源列表：\n{sources}\n\n"
                f"用户问题：{user_text}"
            )
        return (
            "本地知识库没有找到足够相关的资料。请先在回答开头说明这一点，"
            "然后再基于通用知识或可用外部能力进行推理回答。\n\n"
            f"用户问题：{user_text}"
        )
```

- [ ] **Step 4: Run worker tests and verify pass**

Run:

```bash
python -m pytest tests/test_message_worker.py -q
```

Expected: all worker tests pass.

---

### Task 4: Refactor FeishuEventHandler to Queue Jobs and ACK Quickly

**Files:**
- Modify: `src/event_handler.py`
- Modify: `tests/test_event_handler.py`

**Interfaces:**
- Consumes: `MessageJobStore.create_if_new(...)`.
- Consumes: `MessageJobQueue.enqueue(message_id)`.
- Produces: `FeishuEventHandler(config, job_store, job_queue)`.
- Preserves: challenge, invalid token, unsupported event, unsupported message type, and group mention behavior.

- [ ] **Step 1: Update event handler tests to use fake job store/queue**

In `tests/test_event_handler.py`, replace synchronous AI/Feishu expectations with queue expectations. Add these fakes near the top:

```python
class FakeJobStore:
    def __init__(self):
        self.jobs = {}
        self.create_calls = []

    def create_if_new(self, message_id, event_id, chat_id, user_id, text, now=None):
        self.create_calls.append((message_id, event_id, chat_id, user_id, text))
        if message_id in self.jobs:
            return self.jobs[message_id], False
        job = type("Job", (), {
            "message_id": message_id,
            "event_id": event_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "text": text,
            "status": "queued",
        })()
        self.jobs[message_id] = job
        return job, True


class FakeJobQueue:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, message_id):
        self.enqueued.append(message_id)
```

Update the private message test body to:

```python
@pytest.mark.asyncio
async def test_private_message_is_queued_without_calling_ai_or_feishu():
    store = FakeJobStore()
    queue = FakeJobQueue()
    handler = FeishuEventHandler(make_config(), store, queue)

    result = await handler.handle(message_event(chat_type="p2p", text="你好"))

    assert result == {"code": 0}
    assert store.create_calls == [("om_1", "evt-1", "oc_1", "", "你好")]
    assert queue.enqueued == ["om_1"]
```

Update the duplicate test to:

```python
@pytest.mark.asyncio
async def test_duplicate_message_id_is_acknowledged_without_requeueing():
    store = FakeJobStore()
    queue = FakeJobQueue()
    handler = FeishuEventHandler(make_config(), store, queue)

    await handler.handle(message_event(event_id="evt-first"))
    result = await handler.handle(message_event(event_id="evt-retry"))

    assert result == {"code": 0}
    assert len(store.create_calls) == 2
    assert queue.enqueued == ["om_1"]
```

Update group mention test expected queueing:

```python
assert store.create_calls == [("om_1", "evt-1", "oc_1", "", "帮我总结")]
assert queue.enqueued == ["om_1"]
```

Update invalid/no mention tests to assert `queue.enqueued == []`.

Remove event-handler tests for knowledge-base augmentation from this file because that behavior moves to `tests/test_message_worker.py`.

- [ ] **Step 2: Run event handler tests and verify failure**

Run:

```bash
python -m pytest tests/test_event_handler.py -q
```

Expected: failures because `FeishuEventHandler` still expects conversations, AI, and Feishu clients and still calls AI synchronously.

- [ ] **Step 3: Implement queued event handler**

Rewrite `src/event_handler.py` constructor and main path:

```python
class FeishuEventHandler:
    def __init__(self, config: AppConfig, job_store: Any, job_queue: Any) -> None:
        self._config = config
        self._job_store = job_store
        self._job_queue = job_queue
```

In `handle()`, preserve challenge/token/type/text/mention checks. Replace AI/reply logic with:

```python
chat_id = str(message.get("chat_id", ""))
message_id = str(message.get("message_id", ""))
sender = payload.get("event", {}).get("sender", {})
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
job, created = self._job_store.create_if_new(message_id, event_id, chat_id, user_id, user_text)
if not created:
    logger.warning("job duplicate message_id=%s status=%s", message_id, job.status)
    return {"code": 0}

await self._job_queue.enqueue(message_id)
logger.warning("job queued message_id=%s", message_id)
return {"code": 0}
```

Keep `_extract_text()` and `_remove_mentions()` unchanged.

- [ ] **Step 4: Run event handler tests and verify pass**

Run:

```bash
python -m pytest tests/test_event_handler.py -q
```

Expected: all event handler tests pass.

---

### Task 5: Wire Queue, Store, Worker, and Recovery into FastAPI App

**Files:**
- Modify: `src/app.py`
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: `config.job_queue.database_path`, `config.job_queue.recovery_window_seconds`, `MessageJobStore`, `MessageJobQueue`, `MessageJobProcessor`, and `FeishuEventHandler(config, store, queue)`.
- Produces: FastAPI startup initializes SQLite, expires stale unfinished jobs, requeues recoverable unfinished jobs, and starts worker.
- Produces: FastAPI shutdown stops worker.

- [ ] **Step 1: Write app integration test for temporary job queue database**

Update `tests/test_app.py::write_config` to include a temp job database path:

```python
"job_queue": {"database_path": str(tmp_path / "jobs.sqlite3"), "recovery_window_seconds": 600},
```

Add this test:

```python
def test_create_app_initializes_job_database(tmp_path):
    config_path = write_config(tmp_path)

    with TestClient(create_app(config_path)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert (tmp_path / "jobs.sqlite3").exists()
```

- [ ] **Step 2: Run app tests and verify failure**

Run:

```bash
python -m pytest tests/test_app.py -q
```

Expected: failure because `create_app()` does not initialize the job database yet and still constructs the old event handler.

- [ ] **Step 3: Implement app wiring**

Modify `src/app.py` imports:

```python
from .message_jobs import MessageJobStore
from .message_worker import MessageJobProcessor, MessageJobQueue
```

Inside `create_app()`, after knowledge-base setup:

```python
job_store = MessageJobStore(config.knowledge_base.vault_path and config.job_queue.database_path or config.job_queue.database_path)
job_store.initialize()
job_queue = MessageJobQueue()
job_processor = MessageJobProcessor(config, job_store, conversations, ai_client, feishu_client, knowledge_base=knowledge_base, queue=job_queue)
event_handler = FeishuEventHandler(config, job_store, job_queue)
```

Use this simpler line instead if editing directly:

```python
job_store = MessageJobStore(config.job_queue.database_path)
```

After `app = FastAPI(...)`, add lifecycle hooks:

```python
@app.on_event("startup")
async def startup() -> None:
    expired = job_store.expire_stale_unfinished(None, config.job_queue.recovery_window_seconds)
    for job in expired:
        logger.warning("job expired message_id=%s", job.message_id)
    recoverable = job_store.list_recoverable_unfinished(None, config.job_queue.recovery_window_seconds)
    for job in recoverable:
        await job_queue.enqueue(job.message_id)
    job_processor.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await job_processor.stop()
```

Add `import logging` and `logger = logging.getLogger(__name__)` to `src/app.py`.

- [ ] **Step 4: Run app tests and verify pass**

Run:

```bash
python -m pytest tests/test_app.py -q
```

Expected: all app tests pass.

---

### Task 6: Full Regression and Runtime Smoke Verification

**Files:**
- No required source files.
- May modify `README.md` only if runtime instructions need to mention `job_queue` config.

**Interfaces:**
- Consumes all earlier tasks.
- Produces verified implementation ready for real Feishu runtime testing.

- [ ] **Step 1: Run full automated suite**

Run:

```bash
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Start the app with real config**

Run:

```bash
python -m uvicorn src.app:app --host 127.0.0.1 --port 8000
```

Expected: app starts and `/health` returns `{"status":"ok"}`.

- [ ] **Step 3: Runtime verify through Feishu**

In Feishu, send the bot one private message or mention it in the configured group:

```text
双链增强怎么工作
```

Expected: exactly one bot reply.

- [ ] **Step 4: Observe logs**

Expected log sequence includes:

```text
received event_id=... message_id=... chat_id=... text_len=...
job queued message_id=...
job processing message_id=...
job done message_id=...
```

- [ ] **Step 5: Duplicate safety spot check**

If the original Feishu event payload is available, POST it to `/feishu/events` twice. If not available, send a synthetic callback with the same `message_id` twice against a test config.

Expected: first callback queues one job; second callback logs duplicate and returns `{"code": 0}` without a second Feishu reply.

- [ ] **Step 6: Do not commit unless explicitly asked**

The user did not ask to commit. Report changed files, verification commands, and runtime findings instead.

---

## Self-Review Notes

- Spec coverage: Task 1 covers configurability; Task 2 covers SQLite persistence, idempotency, statuses, and recovery queries; Task 3 covers worker processing and failure marking; Task 4 covers fast ACK and duplicate handling; Task 5 covers startup recovery/expiration; Task 6 covers automated and Feishu runtime verification.
- Placeholder scan: no TBD/TODO placeholders are intentionally left in this plan.
- Type consistency: `MessageJobStore`, `MessageJobQueue`, and `MessageJobProcessor` signatures are consistent across tasks.
