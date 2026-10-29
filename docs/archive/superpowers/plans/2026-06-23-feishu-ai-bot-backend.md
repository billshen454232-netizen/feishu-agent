# Feishu AI Bot Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-version Python FastAPI backend that receives Feishu bot message events, calls a configurable AI provider, and replies in private chats or group mentions.

**Architecture:** The service exposes `/feishu/events` for Feishu event callbacks and `/health` for deployment checks. Event handling verifies Feishu challenge payloads, deduplicates events, filters private chats and group mentions, stores short in-memory chat history by `chat_id`, calls a provider-selected AI client, and replies through Feishu OpenAPI. Runtime secrets and provider details live in JSON configuration files ignored by git.

**Tech Stack:** Python 3.10+, FastAPI, Uvicorn, httpx, pytest.

## Global Constraints

- Repository starts empty except for git metadata.
- Backend language is Python.
- Model configuration must be JSON-based and user-editable.
- AI providers supported in v1: `openai_compatible` and `claude_messages`.
- Bot responds in private chats and in group chats only when mentioned.
- Conversation context is in-memory, keyed by `chat_id`, keeping the latest configured number of messages.
- Feishu replies use OpenAPI, not `lark-cli` subprocesses.
- Real secrets must not be committed; `config/config.json` is ignored.

---

## File Structure

- `requirements.txt` — runtime and test dependencies.
- `.gitignore` — ignores virtualenvs, caches, and real local config.
- `README.md` — local run, config, Feishu platform setup, and testing instructions.
- `config/config.example.json` — documented example configuration.
- `src/config.py` — loads and validates JSON config into dataclasses.
- `src/conversation.py` — small in-memory chat history store.
- `src/ai_client.py` — common AI interface plus OpenAI-compatible and Claude Messages implementations.
- `src/feishu_client.py` — tenant token retrieval and message reply calls.
- `src/event_handler.py` — Feishu challenge handling, event dedupe, message parsing/filtering, and orchestration.
- `src/app.py` — FastAPI app factory and endpoints.
- `tests/` — unit tests for config, conversation, AI payloads, and event behavior.

---

### Task 1: Project skeleton and configuration

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `config/config.example.json`
- Create: `src/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `load_config(path: str | Path = "config/config.json") -> AppConfig`
- Produces dataclasses: `AppConfig`, `FeishuConfig`, `AIConfig`, `ServerConfig`

**Steps:**
- [ ] Write tests for loading valid JSON and rejecting invalid provider names.
- [ ] Implement dataclass config loader.
- [ ] Add example JSON config with Feishu and AI provider sections.
- [ ] Run `pytest tests/test_config.py -v` and expect all tests to pass.

---

### Task 2: Conversation memory

**Files:**
- Create: `src/conversation.py`
- Test: `tests/test_conversation.py`

**Interfaces:**
- Produces: `ConversationStore(max_messages: int)`
- Produces: `append(chat_id: str, role: Literal["user", "assistant"], content: str) -> None`
- Produces: `get(chat_id: str) -> list[dict[str, str]]`
- Produces: `clear(chat_id: str) -> None`

**Steps:**
- [ ] Write tests for append, trim, and clear.
- [ ] Implement bounded in-memory store using `collections.deque`.
- [ ] Run `pytest tests/test_conversation.py -v` and expect all tests to pass.

---

### Task 3: AI clients

**Files:**
- Create: `src/ai_client.py`
- Test: `tests/test_ai_client.py`

**Interfaces:**
- Consumes: `AIConfig`
- Produces: `AIClient.generate(history: list[dict[str, str]], user_message: str) -> str`
- Produces: `create_ai_client(config: AIConfig) -> AIClient`

**Steps:**
- [ ] Write tests using mocked `httpx.AsyncClient` transport for OpenAI-compatible and Claude payloads.
- [ ] Implement provider switching.
- [ ] Implement OpenAI-compatible `/chat/completions` call.
- [ ] Implement Claude `/messages` call.
- [ ] Run `pytest tests/test_ai_client.py -v` and expect all tests to pass.

---

### Task 4: Feishu client

**Files:**
- Create: `src/feishu_client.py`
- Test: `tests/test_feishu_client.py`

**Interfaces:**
- Consumes: `FeishuConfig`
- Produces: `FeishuClient.get_tenant_access_token() -> str`
- Produces: `FeishuClient.reply_text(message_id: str, text: str) -> None`

**Steps:**
- [ ] Write tests with mocked Feishu token and reply endpoints.
- [ ] Implement token caching until expiry.
- [ ] Implement message reply API call using `POST /open-apis/im/v1/messages/{message_id}/reply`.
- [ ] Run `pytest tests/test_feishu_client.py -v` and expect all tests to pass.

---

### Task 5: Event handling and FastAPI endpoint

**Files:**
- Create: `src/event_handler.py`
- Create: `src/app.py`
- Test: `tests/test_event_handler.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `ConversationStore`, `AIClient`, `FeishuClient`, `AppConfig`
- Produces: `FeishuEventHandler.handle(payload: dict[str, Any]) -> dict[str, Any]`
- Produces: `create_app(config_path: str | Path = "config/config.json") -> FastAPI`

**Steps:**
- [ ] Write tests for URL challenge response.
- [ ] Write tests for duplicate event ignoring.
- [ ] Write tests for private message response.
- [ ] Write tests for group message ignored unless the bot is mentioned.
- [ ] Implement message text extraction from Feishu event content JSON.
- [ ] Implement mention filtering and mention cleanup.
- [ ] Wire FastAPI `/health` and `/feishu/events` endpoints.
- [ ] Run `pytest tests/test_event_handler.py tests/test_app.py -v` and expect all tests to pass.

---

### Task 6: Documentation and smoke verification

**Files:**
- Create: `README.md`
- Modify: any files found during verification if needed.

**Steps:**
- [ ] Document setup: `python -m venv .venv`, dependency install, config copy, run server.
- [ ] Document Feishu event subscription URL and required event `im.message.receive_v1`.
- [ ] Document model config for both providers.
- [ ] Run full test suite: `pytest -v`.
- [ ] Run import check: `python -m compileall src tests`.
- [ ] Run local server startup check if dependencies are installed: `python -m uvicorn src.app:app --host 127.0.0.1 --port 8000`.

---

## Self-Review

- Spec coverage: plan covers Python backend, JSON model config, both provider protocols, Feishu OpenAPI replies, private/group mention filtering, and in-memory history.
- Placeholder scan: no TBD/TODO placeholders remain in the plan.
- Type consistency: function and class names are stable across tasks.
