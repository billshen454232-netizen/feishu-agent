# Dify Private Knowledge Agent POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a self-hosted Dify POC and validate it as the RAG/Agent core for the existing Feishu knowledge bot project.

**Architecture:** Dify runs as the knowledge base, RAG, workflow, and agent orchestration layer. The existing FastAPI Feishu bot remains the enterprise integration layer, and CLIProxyAPI remains the model/embedding proxy layer. The POC first validates Dify independently, then connects it to the Feishu bot through Dify API.

**Tech Stack:** Dify, Docker Compose, PostgreSQL/Redis/vector services bundled by Dify Compose, existing FastAPI Feishu bot, existing CLIProxyAPI-compatible model service.

## Global Constraints

- Do not replace the existing FastAPI Feishu bot; keep it as the Feishu event callback and integration gateway.
- Do not self-build a complete RAG platform; use Dify as the open-source RAG/Agent platform.
- Prefer private/self-hosted deployment suitable for intranet use.
- Validate Embedding availability before uploading large knowledge bases.
- POC acceptance must include Markdown upload, retrieval with sources, missed-query refusal, stable container restart, and backup path identification.
- Official Dify Docker Compose baseline from README: `cd dify/docker`, `cp .env.example .env`, `docker compose up -d`, then open `http://localhost/install`.

---

## File Structure

This POC primarily creates operational documentation and leaves application code unchanged until Dify API is validated.

- Create: `docs/dify-poc/deployment-notes.md` — records server-specific deployment values, ports, model provider settings, and troubleshooting notes.
- Create: `docs/dify-poc/acceptance-checklist.md` — tracks POC verification results.
- Modify later only after API validation: existing Feishu bot configuration files for Dify API URL/key integration.

---

### Task 1: Clone and Start Dify with Docker Compose

**Files:**
- Create: `docs/dify-poc/deployment-notes.md`

**Interfaces:**
- Consumes: Docker and Docker Compose installed on target host.
- Produces: Running Dify web console reachable at `http://<server-ip>/install` or mapped host port.

- [ ] **Step 1: Choose deployment directory**

Use one directory outside this project repository so Dify source code does not get mixed with the Feishu bot repo:

```bash
cd /opt
```

If testing on Windows workstation instead of Linux server, use a sibling directory of this repo, for example:

```bash
cd /d/otherPrograms
```

- [ ] **Step 2: Clone official Dify repository**

```bash
git clone https://github.com/langgenius/dify.git
cd dify/docker
```

If GitHub requires a local proxy on this machine, run:

```bash
HTTPS_PROXY=http://127.0.0.1:7897 HTTP_PROXY=http://127.0.0.1:7897 git clone https://github.com/langgenius/dify.git
cd dify/docker
```

Expected: the `docker` directory contains `.env.example` and `docker-compose.yaml` or Compose files used by Dify.

- [ ] **Step 3: Create Dify environment file**

```bash
cp .env.example .env
```

Expected: `.env` now exists in the Dify `docker` directory.

- [ ] **Step 4: Start Dify services**

```bash
docker compose up -d
```

Expected: Docker pulls images and starts Dify services in the background.

- [ ] **Step 5: Inspect service status**

```bash
docker compose ps
```

Expected: core services are `running` or `healthy`. If a service exits, stop and record the failing service logs instead of guessing.

- [ ] **Step 6: Open initial setup page**

Open:

```text
http://localhost/install
```

For a remote server, open:

```text
http://<server-ip>/install
```

Expected: Dify setup page is displayed.

- [ ] **Step 7: Record deployment values**

Create `docs/dify-poc/deployment-notes.md` in this Feishu bot repo with this content:

```markdown
# Dify POC Deployment Notes

## Deployment Host

- Host:
- OS:
- Dify source path:
- Dify Docker path:
- Public/Internal URL:
- Initial setup URL:

## Compose Commands

```bash
cd <dify-source>/docker
docker compose ps
docker compose logs --tail=100 api
docker compose logs --tail=100 web
docker compose restart
```

## Model Provider

- Chat model provider:
- Chat model base URL:
- Chat model name:
- Embedding provider:
- Embedding base URL:
- Embedding model name:

## Data and Backup Notes

- Docker volumes:
- Database volume:
- Vector database volume:
- File storage volume:
- Backup command tested:

## Issues Encountered

- None yet.
```
```

- [ ] **Step 8: Commit documentation**

```bash
git add docs/dify-poc/deployment-notes.md
git commit -m "docs: add dify poc deployment notes"
```

---

### Task 2: Configure Model and Embedding Provider

**Files:**
- Modify: `docs/dify-poc/deployment-notes.md`

**Interfaces:**
- Consumes: Running Dify web console from Task 1.
- Produces: Dify can call a chat model and an embedding model.

- [ ] **Step 1: Configure chat model provider in Dify UI**

In Dify console, configure the OpenAI-compatible provider or other provider supported by your model proxy.

Use these values if CLIProxyAPI exposes an OpenAI-compatible endpoint:

```text
Provider type: OpenAI-compatible
Base URL: <CLIProxyAPI base URL>/v1
API Key: <proxy key>
Model: <chat model name exposed by proxy>
```

Expected: Dify model test succeeds.

- [ ] **Step 2: Configure embedding model provider**

Use one of these options:

```text
Option A - Recommended long term:
Provider type: OpenAI-compatible
Base URL: <CLIProxyAPI base URL>/v1
API Key: <proxy key>
Embedding model: <embedding model exposed by proxy>
```

```text
Option B - Fully intranet:
Deploy local embedding model such as bge-m3 and expose it through an OpenAI-compatible /v1/embeddings endpoint.
```

```text
Option C - External provider:
Use a provider reachable from the server network, such as Tongyi, Zhipu, Jina, or SiliconFlow.
```

Expected: Dify embedding model test succeeds. If it fails, do not upload knowledge documents yet.

- [ ] **Step 3: Record working model settings**

Update `docs/dify-poc/deployment-notes.md`:

```markdown
## Model Provider

- Chat model provider: OpenAI-compatible through CLIProxyAPI
- Chat model base URL: <actual-base-url>/v1
- Chat model name: <actual-chat-model>
- Embedding provider: <actual-provider>
- Embedding base URL: <actual-embedding-base-url>
- Embedding model name: <actual-embedding-model>
```

- [ ] **Step 4: Commit model configuration notes**

```bash
git add docs/dify-poc/deployment-notes.md
git commit -m "docs: record dify model provider settings"
```

---

### Task 3: Validate Knowledge Base RAG Behavior

**Files:**
- Create: `docs/dify-poc/acceptance-checklist.md`

**Interfaces:**
- Consumes: Working chat and embedding provider from Task 2.
- Produces: Verified Dify knowledge base behavior for Markdown documents.

- [ ] **Step 1: Create a small Markdown test document**

Use this exact content as the first knowledge document:

```markdown
# Feishu Bot Test Knowledge

## Project Purpose

The Feishu bot project is an enterprise internal knowledge assistant. It receives Feishu messages, queries a local knowledge source or RAG system, and replies with controlled answers.

## Refusal Policy

If the answer is not found in the configured knowledge base, the assistant should say that the current knowledge base does not contain enough information instead of inventing an answer.

## Integration Architecture

Feishu messages enter through a FastAPI callback service. The service handles message deduplication, queues work, calls the AI/RAG backend, and sends replies back to Feishu.
```

- [ ] **Step 2: Upload the Markdown document to Dify knowledge base**

In Dify UI:

```text
Knowledge Base -> Create Knowledge -> Upload file -> select Markdown file -> process/index
```

Expected: document processing completes without embedding error.

- [ ] **Step 3: Create an acceptance checklist**

Create `docs/dify-poc/acceptance-checklist.md`:

```markdown
# Dify POC Acceptance Checklist

## Knowledge Upload

- [ ] Markdown file uploads successfully.
- [ ] Document indexing completes successfully.
- [ ] No embedding provider error appears.

## Retrieval and Answering

- [ ] Query: "飞书机器人项目的用途是什么？"
- [ ] Expected: answer says it is an enterprise internal knowledge assistant.
- [ ] Actual:
- [ ] Source/citation shown: yes/no

## Refusal Behavior

- [ ] Query: "这个项目的 Kubernetes Helm chart 怎么配置？"
- [ ] Expected: answer refuses or says the knowledge base lacks enough information.
- [ ] Actual:

## Stability

- [ ] `docker compose restart` completed.
- [ ] Dify web console is still accessible after restart.
- [ ] Knowledge base data still exists after restart.

## Backup

- [ ] Docker volumes identified.
- [ ] Database/vector/file storage backup approach recorded in deployment notes.
```

- [ ] **Step 4: Run the first in-knowledge query**

Ask in Dify app/chat preview:

```text
飞书机器人项目的用途是什么？
```

Expected: Dify answers based on the Markdown document and shows a source/citation if configured.

- [ ] **Step 5: Run the missed-knowledge query**

Ask:

```text
这个项目的 Kubernetes Helm chart 怎么配置？
```

Expected: Dify should not fabricate a Helm chart. If it fabricates, adjust the Dify app prompt to require refusal when retrieved context is insufficient.

- [ ] **Step 6: Commit acceptance checklist**

```bash
git add docs/dify-poc/acceptance-checklist.md
git commit -m "docs: add dify poc acceptance checklist"
```

---

### Task 4: Decide Integration Path with Feishu Bot

**Files:**
- Modify later: Feishu bot environment/config files after Dify API is validated.
- Modify later: Feishu bot AI client module after Dify API is validated.

**Interfaces:**
- Consumes: Dify app API endpoint and API key.
- Produces: A decision record for whether Feishu bot should call Dify API directly.

- [ ] **Step 1: Create a Dify application API key**

In Dify UI:

```text
App -> API Access -> Create API Key
```

Record:

```text
DIFY_BASE_URL=http://<server-ip>/v1
DIFY_API_KEY=<created-key>
DIFY_APP_ID=<if needed>
```

- [ ] **Step 2: Test Dify API independently with curl**

Use Dify's API docs shown in the app console. The request shape may differ by app type; use the exact example generated by the Dify UI.

Expected: API returns a valid answer before any Feishu bot code changes.

- [ ] **Step 3: Decide whether to integrate now**

Proceed to Feishu bot code changes only if all are true:

```text
- Dify UI knowledge base query works.
- Dify missed-query behavior is acceptable.
- Dify API call works outside Feishu.
- Dify response latency is acceptable for Feishu callback/queue flow.
```

- [ ] **Step 4: If integration is approved, write a separate implementation plan**

Create a new plan file instead of mixing deployment and code integration:

```text
docs/superpowers/plans/YYYY-MM-DD-feishu-dify-api-integration.md
```

Expected: Dify deployment remains independently testable, and Feishu code changes are planned separately.

---

## Self-Review

- Spec coverage: The plan covers official Dify Docker Compose deployment, model/embedding setup, Markdown knowledge upload, source/refusal validation, restart persistence, backup discovery, and a separate Feishu integration decision gate.
- Placeholder scan: No task uses TBD/TODO/fill-in-later as an implementation substitute. Server-specific values are intentionally recorded during execution because they depend on the target host.
- Type consistency: No code interfaces are introduced in this deployment POC. The only later integration interface is `DIFY_BASE_URL` and `DIFY_API_KEY`, deferred to a separate plan after API validation.
