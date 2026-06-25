# Feishu Event Queue and Idempotency Design

## Problem

The current Feishu event handler processes a message synchronously: it receives the event, calls the AI model, sends the reply, and only then returns success to Feishu. If AI generation, network calls, local tunneling, or the process itself is slow or unstable, Feishu may retry an old event. The current duplicate guard is an in-memory `event_id` set, so it is lost on restart. This can cause the bot to reply again to an old message such as the first test message.

## Goal

Make Feishu event handling idempotent and fast to acknowledge:

- Return success to Feishu quickly after validating and enqueueing the event.
- Reply to each Feishu `message_id` at most once across service restarts.
- Recover unfinished work after restart only if the message is still recent.
- Expire old unfinished work so the bot does not unexpectedly answer stale messages much later.

## Recommended Approach

Use a SQLite-backed job state table plus an in-process `asyncio.Queue`.

This keeps the first production-ready version simple: no Redis, RabbitMQ, Celery, or external worker service is required. SQLite persists idempotency state across restarts, while the in-process queue keeps runtime processing straightforward.

## Runtime Flow

1. `POST /feishu/events` receives a Feishu callback.
2. The handler keeps existing validation behavior:
   - challenge response;
   - verification token check;
   - supported event type check;
   - text message check;
   - group mention filtering.
3. The handler extracts `event_id`, `message_id`, `chat_id`, sender/user id when available, and normalized user text.
4. The handler calls a job store operation such as `enqueue_if_new(...)`.
5. If `message_id` is already known, the handler returns `{"code": 0}` without enqueueing or replying again.
6. If `message_id` is new, the handler persists a `queued` job, pushes the `message_id` into `asyncio.Queue`, and immediately returns `{"code": 0}`.
7. A background worker consumes queued jobs.
8. The worker marks a job `processing`, builds the AI user text with knowledge-base context, calls the AI client, replies through Feishu OpenAPI, then marks the job `done`.
9. On failure, the worker records `failed`, increments `attempts`, and stores `last_error`.

## Job State Table

SQLite database path should be configurable, with a default such as:

```text
data/feishu_bot.sqlite3
```

Table: `message_jobs`

```text
message_id TEXT PRIMARY KEY
event_id TEXT
chat_id TEXT
user_id TEXT
text TEXT
status TEXT              -- queued | processing | done | failed | expired
attempts INTEGER
created_at INTEGER       -- unix seconds
updated_at INTEGER       -- unix seconds
last_error TEXT
```

`message_id` is the primary idempotency key because the same user message must not be answered twice. `event_id` remains useful for logs and debugging Feishu retries.

## Restart Recovery Policy

Use the selected policy C: recover only recent unfinished jobs.

On application startup:

1. Find jobs with `status in ('queued', 'processing')` and `created_at >= now - recovery_window_seconds`.
2. Requeue those jobs.
3. Find jobs with `status in ('queued', 'processing')` and `created_at < now - recovery_window_seconds`.
4. Mark those jobs `expired` and do not reply.

Initial default:

```text
recovery_window_seconds = 600
```

This means unfinished messages are resumed for up to 10 minutes. Older unfinished messages are expired so the bot does not reply unexpectedly long after the user asked.

## Duplicate Handling

For any incoming event whose `message_id` already exists in SQLite:

- `queued`: return success, do not enqueue again.
- `processing`: return success, do not enqueue again.
- `done`: return success, do not reply again.
- `failed`: return success, do not auto-retry in the callback path.
- `expired`: return success, do not reply.

This makes Feishu retries safe.

## Retry Policy

The first implementation should not automatically retry failed jobs. This avoids accidentally sending duplicate replies if the failure happens after Feishu accepted the reply but before local state was updated.

A future retry design can distinguish failure phases, for example:

- AI generation failed before any Feishu reply attempt: retry may be safe.
- Feishu reply call timed out or returned ambiguous network status: retry may duplicate a reply, so it needs extra safeguards.

## Logging

Add structured log lines for event and job state transitions:

```text
received event_id=... message_id=... chat_id=... text_len=...
job queued message_id=...
job duplicate message_id=... status=...
job processing message_id=...
job done message_id=...
job failed message_id=... error=...
job expired message_id=...
```

These logs are required for diagnosing future duplicate replies.

## Component Boundaries

### FeishuEventHandler

Owns Feishu callback validation and message extraction. It should no longer call AI or Feishu reply directly in the request path. It should enqueue jobs and return quickly.

### MessageJobStore

Owns SQLite persistence and idempotency decisions. It exposes operations such as:

- initialize schema;
- create queued job if new;
- get job by message id;
- mark processing;
- mark done;
- mark failed;
- expire stale unfinished jobs;
- list recoverable unfinished jobs.

### MessageJobQueue / Worker

Owns in-process queueing and background processing. It consumes `message_id` values, loads full job data from the store, builds the AI prompt, calls AI, sends the Feishu reply, and updates state.

## Testing Strategy

Add automated tests before implementation:

1. Duplicate callback for the same `message_id` only creates one queued job.
2. The HTTP handler returns success before AI/reply processing is required.
3. A `done` job is not enqueued or replied again.
4. Startup recovery requeues unfinished jobs newer than 10 minutes.
5. Startup recovery expires unfinished jobs older than 10 minutes.
6. Worker marks successful jobs `done` after sending a reply.
7. Worker marks failed jobs `failed` and records the error.

## Runtime Verification

After implementation, verify through the real Feishu surface:

1. Start the app.
2. Send one message to the bot.
3. Observe one reply.
4. Send or replay the same Feishu event payload twice if possible.
5. Observe that the duplicate is acknowledged but does not produce a second reply.
6. Restart the service and confirm old `done` messages still do not reply again.

## Non-Goals

This design does not add Redis, Celery, RabbitMQ, multiple worker processes, a web admin UI, or advanced retry logic. Those can be added later if the bot becomes a larger team service.
