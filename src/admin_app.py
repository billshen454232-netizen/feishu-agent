"""Template review page for the execution report library.

Served by a second uvicorn server bound to 127.0.0.1 only, separate from the
public Feishu/Worker listener. Reach it through ``ssh -L``. Mutating requests
must carry ``X-Admin-Request: 1`` (blocks cross-site form posts against
localhost) and the Host header must be a loopback name (blocks DNS rebinding).
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .access_auth import AccessDenied, CloudflareAccessVerifier
from .hotfix_thresholds import ThresholdError, ThresholdStore, review_threshold
from .plan_board import PlanError, PlanStore
from .plan_page import PLAN_HTML
from .threshold_page import THRESHOLDS_HTML
from .qa_library import (
    ARCHIVE_ACTIVE,
    ARCHIVE_STATUSES,
    CATEGORIES,
    FIX_ACCEPTED,
    FIX_IGNORED,
    FIX_PENDING,
    FIX_STATUSES,
    QA_ARCHIVED,
    QA_DISMISSED,
    QA_PENDING,
    QA_STATUSES,
    QaService,
)
from .execution_report import (
    PATTERN_CANDIDATE,
    PATTERN_OUTCOMES,
    PATTERN_STATUSES,
    PATTERN_VERIFIED,
    READ_SUCCESS_MODES,
    ExecutionReportStore,
)

logger = logging.getLogger(__name__)

ADMIN_HOST = "127.0.0.1"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
_MAX_DIAGNOSIS_CHARS = 4000


# nginx sets this header on every request it forwards (clients cannot strip it).
# Requests carrying it came from the public site and must hold a valid Access JWT;
# requests without it reached 127.0.0.1 directly (SSH tunnel).
PROXY_HEADER = "x-admin-proxy"
PROXY_HEADER_VALUE = "cloudflare-access"


def create_admin_app(
    store: ExecutionReportStore,
    *,
    access_verifier: CloudflareAccessVerifier | None = None,
    qa_service: QaService | None = None,
    plan_store: PlanStore | None = None,
    threshold_store: ThresholdStore | None = None,
    review_client: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="执行结果模板审批", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Any:
        if _request_host(request) not in _LOOPBACK_HOSTS:
            return PlainTextResponse("forbidden host", status_code=403)
        request.state.operator = "ssh-tunnel"
        self_reported = request.headers.get("x-admin-operator", "")
        if request.headers.get(PROXY_HEADER) is None and re.fullmatch(r"[\w.@-]{1,40}", self_reported):
            # Loopback callers (SSH tunnel, Claude Code via MCP) may name themselves for the audit trail.
            request.state.operator = self_reported
        if request.headers.get(PROXY_HEADER) is not None:
            if request.headers.get(PROXY_HEADER) != PROXY_HEADER_VALUE or access_verifier is None:
                return PlainTextResponse("public access is not configured", status_code=403)
            token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization", "")
            try:
                claims = await access_verifier.verify(token)
            except AccessDenied as exc:
                logger.warning("review page access denied reason=%s", exc)
                return PlainTextResponse("access denied", status_code=403)
            request.state.operator = str(claims.get("email") or claims.get("sub") or "cloudflare-access")
        if request.method not in {"GET", "HEAD"} and request.headers.get("x-admin-request") != "1":
            return PlainTextResponse("missing admin header", status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _INDEX_HTML

    @app.get("/plan", response_class=HTMLResponse)
    async def plan_board() -> HTMLResponse:
        if plan_store is None:
            return HTMLResponse("项目计划未启用", status_code=404)
        return HTMLResponse(PLAN_HTML, headers={"Cache-Control": "no-cache"})

    @app.get("/thresholds", response_class=HTMLResponse)
    async def thresholds_page() -> HTMLResponse:
        if threshold_store is None:
            return HTMLResponse("热更阈值注册表未启用", status_code=404)
        return HTMLResponse(THRESHOLDS_HTML, headers={"Cache-Control": "no-cache"})

    @app.get("/api/summary")
    async def summary() -> dict[str, Any]:
        result: dict[str, Any] = {"counts": store.count_patterns(), "read_success_mode": store.read_success_mode()}
        if qa_service is not None and qa_service.store is not None:
            result["qa"] = qa_service.store.count_records()
            result["qa_archive"] = qa_service.store.count_archive()
            result["doc_fixes"] = qa_service.store.count_doc_fixes()
        return result

    @app.get("/api/patterns")
    async def list_patterns(status: str = PATTERN_CANDIDATE, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        if status not in PATTERN_STATUSES:
            raise HTTPException(status_code=400, detail="unknown status")
        page, page_size = _paging(page, page_size)
        total = store.count_patterns().get(status, 0)
        patterns = store.list_patterns(status, limit=page_size, offset=(page - 1) * page_size)
        return {
            "patterns": [pattern.as_jsonable() for pattern in patterns],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @app.get("/api/executions")
    async def executions(page: int = 1, page_size: int = 20) -> dict[str, Any]:
        page, page_size = _paging(page, page_size)
        return {
            "executions": store.list_recent_executions(page_size, (page - 1) * page_size),
            "total": store.count_executions(),
            "page": page,
            "page_size": page_size,
        }

    @app.get("/api/patterns/{pattern_id}/samples")
    async def samples(pattern_id: int) -> dict[str, Any]:
        pattern = store.get_pattern(pattern_id)
        if pattern is None:
            raise HTTPException(status_code=404, detail="not found")
        return {"samples": store.list_artifacts(pattern.sample_message_ids)}

    @app.put("/api/patterns/{pattern_id}")
    async def edit(pattern_id: int, payload: dict[str, Any], request: Request) -> dict[str, str]:
        diagnosis = payload.get("diagnosis")
        if not isinstance(diagnosis, str) or not diagnosis.strip() or len(diagnosis) > _MAX_DIAGNOSIS_CHARS:
            raise HTTPException(status_code=400, detail="diagnosis must be 1-{} chars".format(_MAX_DIAGNOSIS_CHARS))
        if not store.update_diagnosis(pattern_id, diagnosis.strip()):
            raise HTTPException(status_code=404, detail="not found")
        logger.info("template review edit pattern=%s operator=%s", pattern_id, request.state.operator)
        return {"status": "ok"}

    @app.put("/api/patterns/{pattern_id}/outcome")
    async def set_outcome(pattern_id: int, payload: dict[str, Any], request: Request) -> dict[str, str]:
        outcome = payload.get("outcome")
        if outcome not in PATTERN_OUTCOMES:
            raise HTTPException(status_code=400, detail="unknown outcome")
        if not store.set_pattern_outcome(pattern_id, outcome):
            raise HTTPException(status_code=404, detail="not found")
        logger.info("template review pattern=%s outcome=%s operator=%s", pattern_id, outcome, request.state.operator)
        return {"outcome": outcome}

    @app.post("/api/patterns/batch")
    async def batch(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        ids = payload.get("ids")
        action = payload.get("action")
        if not isinstance(ids, list) or not all(isinstance(item, int) for item in ids) or not ids:
            raise HTTPException(status_code=400, detail="ids must be a non-empty integer list")
        if action == "approve":
            changed = store.set_pattern_status(ids, PATTERN_VERIFIED)
        elif action == "revert":
            changed = store.set_pattern_status(ids, PATTERN_CANDIDATE)
        elif action == "delete":
            changed = store.delete_patterns(ids)
        else:
            raise HTTPException(status_code=400, detail="unknown action")
        logger.info("template review action=%s ids=%s changed=%s operator=%s", action, ids, changed, request.state.operator)
        return {"changed": changed}

    @app.put("/api/settings/read-success-mode")
    async def set_mode(payload: dict[str, Any], request: Request) -> dict[str, str]:
        mode = payload.get("mode")
        if mode not in READ_SUCCESS_MODES:
            raise HTTPException(status_code=400, detail="unknown mode")
        store.set_read_success_mode(mode)
        logger.info("template review read_success_mode=%s operator=%s", mode, request.state.operator)
        return {"read_success_mode": mode}

    if qa_service is not None and qa_service.store is not None:
        _add_qa_routes(app, qa_service)
    if plan_store is not None:
        _add_plan_routes(app, plan_store)
    if threshold_store is not None:
        _add_threshold_routes(app, threshold_store, review_client)

    return app


def _add_threshold_routes(app: FastAPI, thresholds: ThresholdStore, review_client: Any | None) -> None:
    """Hotfix threshold registry: edits take effect on the next Feishu request (the Planner reads it live)."""

    @app.get("/api/thresholds")
    async def list_thresholds(state: Optional[str] = None) -> dict[str, Any]:
        items = thresholds.list_thresholds()
        if state:
            items = [item for item in items if item["state"] == state]
        return {"thresholds": items}

    @app.post("/api/thresholds")
    async def create_threshold(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            item = thresholds.create(payload, operator=request.state.operator)
        except ThresholdError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info("threshold created key=%s operator=%s", item["key"], request.state.operator)
        return item

    @app.put("/api/thresholds/{key}")
    async def update_threshold(key: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        payload.pop("key", None)
        try:
            item = thresholds.update(key, payload, operator=request.state.operator)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except ThresholdError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info("threshold updated key=%s fields=%s operator=%s", key, sorted(payload), request.state.operator)
        return item

    @app.delete("/api/thresholds/{key}")
    async def delete_threshold(key: str, request: Request) -> dict[str, str]:
        try:
            deleted = thresholds.delete(key, operator=request.state.operator)
        except ThresholdError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="not found")
        logger.info("threshold deleted key=%s operator=%s", key, request.state.operator)
        return {"status": "deleted"}

    @app.get("/api/thresholds/events")
    async def threshold_events(key: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
        return {"events": thresholds.list_events(key, max(1, min(limit, 200)))}

    @app.post("/api/thresholds/review")
    async def review(payload: dict[str, Any]) -> dict[str, Any]:
        if review_client is None:
            raise HTTPException(status_code=503, detail="AI 审核未配置")
        try:
            return await review_threshold(review_client, payload, thresholds.list_thresholds())
        except Exception as exc:  # noqa: BLE001 - advisory only; surface the reason on the page
            logger.warning("threshold review failed", exc_info=True)
            raise HTTPException(status_code=502, detail="AI 审核失败：{}".format(type(exc).__name__)) from exc


def _add_plan_routes(app: FastAPI, plans: PlanStore) -> None:
    """Server-backed project plan board. ``GET /api/plan`` is also the read API for AI assistants."""

    @app.get("/api/plan")
    async def get_plan(status: Optional[str] = None) -> dict[str, Any]:
        """``status=active,todo`` narrows ``steps`` (counts stay global) — e.g. an assistant's work queue."""
        plan = plans.get_plan()
        if status:
            wanted = {item.strip() for item in status.split(",") if item.strip()}
            plan["steps"] = [step for step in plan["steps"] if step["status"] in wanted]
        return plan

    @app.put("/api/plan/steps/{step_id}")
    async def update_step(step_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            step = plans.update_step(step_id, payload, operator=request.state.operator)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except PlanError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info("plan step updated id=%s fields=%s operator=%s", step_id, sorted(payload), request.state.operator)
        return step

    @app.post("/api/plan/steps")
    async def create_step(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            step = plans.create_step(payload, operator=request.state.operator)
        except PlanError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info("plan step created id=%s operator=%s", step["id"], request.state.operator)
        return step

    @app.delete("/api/plan/steps/{step_id}")
    async def delete_step(step_id: str, request: Request) -> dict[str, str]:
        if not plans.delete_step(step_id, operator=request.state.operator):
            raise HTTPException(status_code=404, detail="not found")
        logger.info("plan step deleted id=%s operator=%s", step_id, request.state.operator)
        return {"status": "deleted"}

    @app.get("/api/plan/events")
    async def events(step_id: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
        return {"events": plans.list_events(step_id, max(1, min(limit, 200)))}

    @app.post("/api/plan/migrate-local")
    async def migrate_local(payload: dict[str, Any], request: Request) -> dict[str, int]:
        overrides = payload.get("overrides") or {}
        notes = payload.get("notes") or {}
        if not isinstance(overrides, dict) or not isinstance(notes, dict):
            raise HTTPException(status_code=400, detail="invalid payload")
        try:
            return plans.migrate_local(overrides, notes, operator=request.state.operator)
        except PlanError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


def _add_qa_routes(app: FastAPI, qa: QaService) -> None:
    """Q&A review: list, correct (AI re-answer), draft + confirm archive, archive library, doc fixes."""
    qa_store = qa.store

    def record_or_404(message_id: str) -> dict[str, Any]:
        record = qa_store.get_record(message_id)
        if record is None:
            raise HTTPException(status_code=404, detail="not found")
        record["revisions"] = qa_store.list_revisions(message_id)
        return record

    @app.get("/api/qa")
    async def list_qa(status: str = QA_PENDING, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        if status not in QA_STATUSES:
            raise HTTPException(status_code=400, detail="unknown status")
        page, page_size = _paging(page, page_size)
        items, total = qa_store.list_records(status, limit=page_size, offset=(page - 1) * page_size)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @app.post("/api/qa/{message_id}/correct")
    async def correct(message_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        correction = payload.get("correction")
        if not isinstance(correction, str) or not correction.strip() or len(correction) > 2000:
            raise HTTPException(status_code=400, detail="纠正说明需为 1-2000 字")
        record = record_or_404(message_id)
        if record["status"] not in {QA_PENDING, "correcting"}:
            raise HTTPException(status_code=409, detail="该问答当前状态不能纠正")
        corrections = [revision["correction"] for revision in record["revisions"]] + [correction.strip()]
        try:
            answer = await qa.reanswer(record, corrections)
        except Exception as exc:  # noqa: BLE001 - surface AI trouble to the reviewer
            logger.warning("qa reanswer failed message_id=%s error=%s", message_id, type(exc).__name__)
            raise HTTPException(status_code=502, detail="AI 重新回答失败：{}".format(type(exc).__name__)) from exc
        round_number = qa_store.add_revision(
            message_id, correction.strip(), answer.text,
            model=answer.model, elapsed_ms=answer.elapsed_ms, operator=request.state.operator,
        )
        logger.info("qa corrected message_id=%s round=%s operator=%s", message_id, round_number, request.state.operator)
        return {"round": round_number, "answer": answer.text, "model": answer.model, "elapsed_ms": answer.elapsed_ms}

    @app.post("/api/qa/{message_id}/draft")
    async def draft(message_id: str) -> dict[str, Any]:
        record = record_or_404(message_id)
        final_answer = record["revisions"][-1]["answer"] if record["revisions"] else record["answer"]
        try:
            return await qa.draft_archive(record, final_answer, corrected=bool(record["revisions"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("qa archive draft failed message_id=%s error=%s", message_id, type(exc).__name__)
            raise HTTPException(status_code=502, detail="AI 生成归档信息失败：{}".format(type(exc).__name__)) from exc

    @app.post("/api/qa/{message_id}/archive")
    async def archive(message_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        record = record_or_404(message_id)
        question = str(payload.get("canonical_question", "")).strip()
        answer = str(payload.get("answer", "")).strip()
        category = str(payload.get("category", "通用"))
        if not question or not answer or len(question) > 300 or len(answer) > 8000:
            raise HTTPException(status_code=400, detail="标准问法与答案不能为空")
        if category not in CATEGORIES:
            raise HTTPException(status_code=400, detail="unknown category")
        archive_id = qa_store.add_archive(
            source_message_id=message_id,
            canonical_question=question,
            answer=answer,
            category=category,
            keywords=str(payload.get("keywords", ""))[:500],
            scripts=str(payload.get("scripts", ""))[:300],
            catalog_digest=qa.catalog_digest(),
            operator=request.state.operator,
        )
        cited = set(record.get("sources", {}).get("docs", []))
        fixes = 0
        for fix in payload.get("doc_fixes") or []:
            if not isinstance(fix, dict):
                continue
            path, original = str(fix.get("doc_path", "")), str(fix.get("original_excerpt", ""))
            doc = qa._doc_file(path) if path in cited else None
            if not doc or not original or doc.read_text(encoding="utf-8", errors="replace").count(original) != 1:
                continue
            qa_store.add_doc_fix(archive_id, path, original, str(fix.get("proposed_excerpt", "")),
                                 str(fix.get("reason", ""))[:300])
            fixes += 1
        qa_store.set_record_status(message_id, QA_ARCHIVED)
        logger.info("qa archived message_id=%s archive=%s fixes=%s operator=%s",
                    message_id, archive_id, fixes, request.state.operator)
        return {"archive_id": archive_id, "doc_fixes": fixes}

    @app.post("/api/qa/{message_id}/dismiss")
    async def dismiss(message_id: str) -> dict[str, str]:
        record_or_404(message_id)
        qa_store.set_record_status(message_id, QA_DISMISSED)
        return {"status": QA_DISMISSED}

    @app.post("/api/qa/{message_id}/restore")
    async def restore(message_id: str) -> dict[str, str]:
        record_or_404(message_id)
        qa_store.set_record_status(message_id, QA_PENDING)
        return {"status": QA_PENDING}

    @app.get("/api/qa-archive")
    async def list_archive(status: str = ARCHIVE_ACTIVE, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        if status not in ARCHIVE_STATUSES:
            raise HTTPException(status_code=400, detail="unknown status")
        page, page_size = _paging(page, page_size)
        items, total = qa_store.list_archive(status, limit=page_size, offset=(page - 1) * page_size)
        digest = qa.catalog_digest()
        for item in items:
            item["stale"] = bool(digest and item.get("catalog_digest") and item["catalog_digest"] != digest)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @app.put("/api/qa-archive/{archive_id}")
    async def edit_archive(archive_id: int, payload: dict[str, Any], request: Request) -> dict[str, str]:
        try:
            changed = qa_store.update_archive(archive_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not changed:
            raise HTTPException(status_code=404, detail="not found")
        logger.info("qa archive edited id=%s fields=%s operator=%s", archive_id, sorted(payload), request.state.operator)
        return {"status": "ok"}

    @app.delete("/api/qa-archive/{archive_id}")
    async def delete_archive(archive_id: int, request: Request) -> dict[str, str]:
        if not qa_store.delete_archive(archive_id):
            raise HTTPException(status_code=404, detail="not found")
        logger.info("qa archive deleted id=%s operator=%s", archive_id, request.state.operator)
        return {"status": "deleted"}

    @app.get("/api/doc-fixes")
    async def list_doc_fixes(status: str = FIX_PENDING, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        if status not in FIX_STATUSES:
            raise HTTPException(status_code=400, detail="unknown status")
        page, page_size = _paging(page, page_size)
        items, total = qa_store.list_doc_fixes(status, limit=page_size, offset=(page - 1) * page_size)
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @app.put("/api/doc-fixes/{suggestion_id}")
    async def edit_doc_fix(suggestion_id: int, payload: dict[str, Any], request: Request) -> dict[str, str]:
        status = payload.get("status")
        proposed = payload.get("proposed_excerpt")
        if status is not None and status not in {FIX_PENDING, FIX_ACCEPTED, FIX_IGNORED}:
            raise HTTPException(status_code=400, detail="已回写状态只能由回写工具设置")
        if proposed is not None and (not isinstance(proposed, str) or len(proposed) > 8000):
            raise HTTPException(status_code=400, detail="invalid proposed_excerpt")
        if not qa_store.update_doc_fix(suggestion_id, status=status, proposed_excerpt=proposed):
            raise HTTPException(status_code=404, detail="not found")
        logger.info("doc fix updated id=%s status=%s operator=%s", suggestion_id, status, request.state.operator)
        return {"status": "ok"}


def _paging(page: int, page_size: int) -> tuple[int, int]:
    return max(1, page), max(1, min(page_size, 100))


def _request_host(request: Request) -> str:
    raw = request.headers.get("host") or ""
    if raw.startswith("["):
        return raw.split("]", 1)[0] + "]"
    return raw.rsplit(":", 1)[0]


class AdminServer:
    """Run the review app on 127.0.0.1 in a daemon thread (no signal handlers)."""

    def __init__(self, app: FastAPI, *, port: int) -> None:
        import uvicorn

        # log_config/log_level=None: never reconfigure the process-wide uvicorn loggers.
        # Doing so once silenced the main server's INFO logs ("Application startup complete").
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=ADMIN_HOST,
                port=port,
                lifespan="off",
                log_config=None,
                log_level=None,
                access_log=False,
            )
        )
        self._thread: threading.Thread | None = None
        self.port = port

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.run, name="admin-server", daemon=True)
        self._thread.start()
        logger.info("template review page listening on %s:%s", ADMIN_HOST, self.port)

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


_INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>执行结果审批台</title>
<style>
  :root {
    --paper: #f3f5f7;
    --panel: #ffffff;
    --ink: #1f2a36;
    --ink-2: #4b5866;
    --ink-3: #7a8693;
    --line: #dfe4e9;
    --accent: #2f5d8a;
    --accent-soft: #e8eff6;
    --ok: #2e7d4f;
    --bad: #b8322a;
    --unknown: #a86a12;
    --verdict: #c2620e;
    --radius: 6px;
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  html, body { margin: 0; }
  body {
    background: var(--paper);
    color: var(--ink);
    font: 14px/1.6 "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", "Noto Sans CJK SC", system-ui, sans-serif;
    font-variant-numeric: tabular-nums;
  }
  button, input, textarea { font: inherit; color: inherit; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  /* ---------- header ---------- */
  .masthead { background: var(--panel); border-bottom: 1px solid var(--line); }
  .masthead-inner {
    max-width: 1240px; margin: 0 auto; padding: 18px 28px 0;
    display: grid; grid-template-columns: 1fr auto; gap: 8px 32px; align-items: start;
  }
  .masthead h1 { font-size: 20px; font-weight: 650; margin: 0; letter-spacing: .02em; }
  .plan-link { margin-left: 12px; font-size: 13px; font-weight: 500; color: var(--accent); text-decoration: none;
    border: 1px solid var(--line); border-radius: var(--radius); padding: 3px 10px; vertical-align: 3px; }
  .plan-link:hover { border-color: var(--accent); }
  .masthead .lede { margin: 2px 0 0; color: var(--ink-2); font-size: 13px; max-width: 62ch; }
  nav.tabs { grid-column: 1 / -1; display: flex; gap: 4px; margin-top: 10px; }
  nav.tabs a {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 9px 14px 10px; color: var(--ink-2); text-decoration: none;
    border-bottom: 3px solid transparent; font-weight: 500;
  }
  nav.tabs a[aria-current="page"] { color: var(--ink); border-bottom-color: var(--accent); }
  nav.tabs a:hover { color: var(--ink); }
  .count {
    min-width: 22px; padding: 0 7px; border-radius: 11px; font-size: 12px; line-height: 20px; text-align: center;
    background: var(--paper); color: var(--ink-2);
  }
  nav.tabs a[aria-current="page"] .count { background: var(--accent-soft); color: var(--accent); }

  /* mode switch: a setting, so it lives apart from navigation */
  fieldset.mode { border: 0; margin: 0; padding: 0; min-width: 0; }
  fieldset.mode legend { font-size: 12px; color: var(--ink-3); padding: 0; margin-bottom: 6px; }
  .segmented { display: inline-flex; border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
  .segmented label { position: relative; cursor: pointer; }
  .segmented input { position: absolute; opacity: 0; inset: 0; margin: 0; cursor: pointer; }
  .segmented span { display: block; padding: 6px 14px; font-size: 13px; color: var(--ink-2); background: var(--panel); }
  .segmented label + label span { border-left: 1px solid var(--line); }
  .segmented input:checked + span { background: var(--accent); color: #fff; }
  .segmented input:focus-visible + span { outline: 2px solid var(--accent); outline-offset: -2px; }
  .mode-hint { margin: 6px 0 0; font-size: 12px; color: var(--ink-3); max-width: 46ch; }

  /* ---------- main ---------- */
  main { max-width: 1240px; margin: 0 auto; padding: 20px 28px 48px; }
  .toolbar {
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px;
    margin-bottom: 14px;
  }
  .toolbar .summary { font-weight: 600; margin-right: 8px; }
  .toolbar .spacer { flex: 1; }
  .stamp { color: var(--ink-3); font-size: 12px; }
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 12px; border-radius: var(--radius); border: 1px solid var(--line);
    background: var(--panel); cursor: pointer; font-size: 13px;
  }
  .btn:hover:not(:disabled) { border-color: var(--ink-3); }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn.danger { color: var(--bad); }
  .btn.danger:hover:not(:disabled) { border-color: var(--bad); }
  .check { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; color: var(--ink-2); font-size: 13px; }

  /* ---------- templates ---------- */
  .tpl {
    background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
    padding: 16px 18px; margin-bottom: 12px;
  }
  .tpl[data-selected="true"] { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
  .tpl-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 18px; }
  .tpl-head .script { font-size: 15px; font-weight: 650; }
  .tag { font-size: 12px; padding: 1px 8px; border-radius: 4px; background: var(--paper); color: var(--ink-2); }
  dl.facts { display: flex; flex-wrap: wrap; gap: 4px 20px; margin: 0; font-size: 12px; color: var(--ink-3); }
  dl.facts div { display: flex; gap: 6px; }
  dl.facts dt { margin: 0; }
  dl.facts dd { margin: 0; color: var(--ink-2); }
  .reason { margin: 10px 0 6px; font-size: 13px; color: var(--ink-2); }
  pre.lines {
    margin: 0 0 12px; padding: 10px 12px; background: #f6f8fa; border-radius: 4px; border-left: 3px solid var(--line);
    font: 12px/1.6 Consolas, "Cascadia Mono", "Sarasa Mono SC", monospace; white-space: pre-wrap; word-break: break-all;
  }
  .field-label { display: flex; justify-content: space-between; font-size: 12px; color: var(--ink-3); margin-bottom: 4px; }
  .dirty { color: var(--verdict); }
  textarea.diagnosis {
    width: 100%; min-height: 96px; resize: vertical; padding: 8px 10px;
    border: 1px solid var(--line); border-radius: 4px; background: #fff; line-height: 1.65;
  }
  textarea.diagnosis[data-dirty="true"] { border-color: var(--verdict); }
  .tpl-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; margin-top: 12px; }
  .verdict { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--ink-2); }
  .verdict .segmented span { padding: 4px 12px; font-size: 12px; }
  .verdict .segmented input[value="warning"]:checked + span { background: var(--verdict); }
  .verdict-note { font-size: 12px; color: var(--ink-3); }
  .tpl-foot .actions { margin-left: auto; display: flex; gap: 8px; }
  details.more { margin-top: 12px; border-top: 1px dashed var(--line); padding-top: 10px; }
  details.more summary { cursor: pointer; color: var(--accent); font-size: 13px; }
  details.more h4 { margin: 10px 0 4px; font-size: 12px; color: var(--ink-3); font-weight: 500; }
  .sample { margin-bottom: 10px; }
  .sample .meta { font-size: 12px; color: var(--ink-3); margin-bottom: 4px; }

  /* ---------- executions ---------- */
  .table-wrap { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow-x: auto; }
  table.exec { width: 100%; border-collapse: collapse; min-width: 880px; }
  table.exec th {
    text-align: left; font-weight: 500; font-size: 12px; color: var(--ink-3);
    padding: 10px 12px; border-bottom: 1px solid var(--line); white-space: nowrap;
  }
  table.exec td { padding: 11px 12px; border-bottom: 1px solid var(--line); vertical-align: top; font-size: 13px; }
  table.exec tr.row { cursor: pointer; }
  table.exec tr.row:hover td { background: #fafbfc; }
  /* the one signature element: a thin terminal-state signal bar per run */
  tr.row { --signal: var(--line); }
  tr.row[data-state="done"] { --signal: var(--ok); }
  tr.row[data-state="failed"] { --signal: var(--bad); }
  tr.row[data-state="submitted_result_unknown"] { --signal: var(--unknown); }
  tr.row[data-source="verified_warning"] { --signal: var(--verdict); }
  tr.row td:first-child, table.exec th:first-child { padding-left: 18px; }
  tr.row td:first-child { box-shadow: inset 4px 0 0 var(--signal); }
  tr.detail td { box-shadow: inset 4px 0 0 var(--line); }
  .state { font-weight: 600; white-space: nowrap; }
  .state.done { color: var(--ok); }
  .state.failed { color: var(--bad); }
  .state.submitted_result_unknown { color: var(--unknown); }
  .sub { display: block; color: var(--ink-3); font-size: 12px; }
  .request { max-width: 40ch; }
  .when { white-space: nowrap; color: var(--ink-2); }
  tr.detail td { background: #fafbfc; padding: 0 12px 14px 18px; }
  tr.detail h4 { margin: 12px 0 4px; font-size: 12px; color: var(--ink-3); font-weight: 500; }
  .chev { color: var(--ink-3); font-size: 12px; white-space: nowrap; }

  /* ---------- pager / states ---------- */
  .pager { display: flex; align-items: center; justify-content: center; gap: 14px; margin-top: 16px; color: var(--ink-2); font-size: 13px; }
  .state-box {
    background: var(--panel); border: 1px dashed var(--line); border-radius: var(--radius);
    padding: 40px 24px; text-align: center; color: var(--ink-2);
  }
  .state-box p { margin: 0 0 4px; }
  .state-box .hint { color: var(--ink-3); font-size: 13px; }
  .state-box.error { border-style: solid; border-color: #efc6c2; }
  /* ---------- filters / Q&A / doc fixes ---------- */
  .filters { display: flex; flex-wrap: wrap; gap: 6px; margin: -4px 0 14px; }
  .chip {
    padding: 4px 12px; border-radius: 14px; border: 1px solid var(--line); background: var(--panel);
    color: var(--ink-2); font-size: 13px; cursor: pointer;
  }
  .chip[aria-pressed="true"] { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }
  .qa-question { font-size: 15px; font-weight: 600; margin: 8px 0 6px; }
  .label { font-size: 12px; color: var(--ink-3); margin: 10px 0 4px; }
  .prose {
    white-space: pre-wrap; word-break: break-word; line-height: 1.7; font-size: 13px;
    background: #f8fafb; border-radius: 4px; padding: 10px 12px; max-height: 360px; overflow: auto;
  }
  .rev { border-left: 3px solid var(--verdict); padding-left: 12px; margin-top: 12px; }
  .rev blockquote { margin: 0; padding: 6px 10px; background: #fff7ed; border-radius: 4px; font-size: 13px; white-space: pre-wrap; }
  .sources { font-size: 12px; color: var(--ink-3); margin-top: 6px; }
  .badge { font-size: 12px; padding: 1px 8px; border-radius: 4px; }
  .badge.ok { background: #e8f5ee; color: var(--ok); }
  .badge.warn { background: #fff4e5; color: var(--verdict); }
  .form-grid { display: grid; grid-template-columns: 110px 1fr; gap: 8px 12px; align-items: center; margin-top: 10px; }
  .form-grid label { font-size: 12px; color: var(--ink-3); }
  .form-grid input, .form-grid select, .panel-box textarea, .form-grid textarea {
    width: 100%; padding: 6px 8px; border: 1px solid var(--line); border-radius: 4px; background: #fff;
  }
  .form-grid textarea, .panel-box textarea { min-height: 90px; resize: vertical; line-height: 1.6; }
  .panel-box { margin-top: 12px; padding: 12px 14px; border: 1px dashed var(--line); border-radius: var(--radius); background: #fcfcfd; }
  .panel-box .actions { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  .diff-old { background: #fdecea; border-left: 3px solid var(--bad); }
  .diff-new { background: #e8f5ee; border-left: 3px solid var(--ok); }
  .fix-item { margin-top: 10px; }
  textarea.fix-proposed, textarea.fix-new, textarea.correction {
    width: 100%; min-height: 90px; resize: vertical; padding: 8px 10px; border: 1px solid var(--line); border-radius: 4px; background: #fff; line-height: 1.6;
  }
  .fix-item .fix-head { display: flex; gap: 8px; align-items: center; font-size: 13px; }
  .row-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  .toast {
    position: fixed; right: 24px; bottom: 24px; max-width: 360px;
    background: var(--ink); color: #fff; padding: 10px 14px; border-radius: var(--radius); font-size: 13px;
    opacity: 0; transform: translateY(6px); transition: opacity .18s, transform .18s; pointer-events: none;
  }
  .toast.show { opacity: 1; transform: none; }
  .toast.error { background: var(--bad); }
  @media (prefers-reduced-motion: reduce) { .toast { transition: none; } }
  @media (max-width: 760px) {
    .masthead-inner { grid-template-columns: 1fr; padding: 14px 16px 0; }
    main { padding: 16px; }
    .tpl-foot .actions { margin-left: 0; }
  }
</style>
</head>
<body>
<header class="masthead">
  <div class="masthead-inner">
    <div>
      <h1>执行结果审批台 <a class="plan-link" href="/plan" target="_blank" rel="noopener">项目计划</a><a class="plan-link" href="/thresholds" target="_blank" rel="noopener">热更阈值</a></h1>
      <p class="lede">失败经 AI 分析后会收进「待审核」。通过的模板会被同类错误直接复用，不再调用 AI；改错了可以退回或删除。</p>
    </div>
    <fieldset class="mode" id="mode">
      <legend>查询类操作成功后</legend>
      <div class="segmented" role="radiogroup">
        <label><input type="radio" name="mode" value="ai"><span>AI 解读日志</span></label>
        <label><input type="radio" name="mode" value="template"><span>只发程序结果</span></label>
      </div>
      <p class="mode-hint" id="mode-hint"></p>
    </fieldset>
    <nav class="tabs" aria-label="页面">
      <a href="#tab=candidate" data-tab="candidate">待审核 <span class="count" id="n-candidate">–</span></a>
      <a href="#tab=verified" data-tab="verified">可用模板 <span class="count" id="n-verified">–</span></a>
      <a href="#tab=executions" data-tab="executions">最近执行</a>
      <a href="#tab=qa" data-tab="qa">问答 <span class="count" id="n-qa">–</span></a>
      <a href="#tab=docfix" data-tab="docfix">文档修正 <span class="count" id="n-docfix">–</span></a>
    </nav>
  </div>
</header>

<main>
  <div class="toolbar">
    <span class="summary" id="summary"></span>
    <label class="check" id="select-all-wrap"><input type="checkbox" id="select-all"> 全选本页</label>
    <button class="btn primary" id="batch-approve" disabled>通过所选</button>
    <button class="btn" id="batch-revert" disabled>退回所选</button>
    <button class="btn danger" id="batch-delete" disabled>删除所选</button>
    <span class="spacer"></span>
    <span class="stamp" id="stamp"></span>
    <button class="btn" id="refresh" title="重新加载当前列表">刷新</button>
  </div>
  <div class="filters" id="filters" hidden></div>
  <div id="content" aria-live="polite"></div>
  <div class="pager" id="pager" hidden>
    <button class="btn" id="prev">上一页</button>
    <span id="page-info"></span>
    <button class="btn" id="next">下一页</button>
  </div>
</main>
<div class="toast" id="toast" role="status"></div>

<script>
const PAGE_SIZE = 20;
const STATUS = { done: "执行完成", failed: "执行失败", submitted_result_unknown: "结果未核验" };
const SOURCE = {
  template: "程序模板", ai_pending: "AI 分析中", ai_read: "AI 解读", ai_new: "AI 新分析",
  candidate_template: "复用待审核模板", verified_template: "复用可用模板",
  verified_warning: "复用可用模板（判定状态存疑）",
};
const MODE_HINT = {
  ai: "先发程序结果卡，AI 在后台读日志后更新同一张卡片。",
  template: "只发程序结果卡，不调用 AI；查询结论需自己看日志判断。",
};
const EMPTY = {
  candidate: ["没有待审核的模板。", "新的失败经 AI 分析后会自动出现在这里。"],
  verified: ["还没有可用模板。", "在「待审核」里通过的模板会出现在这里。"],
  executions: ["还没有执行记录。", "在飞书群里执行一次操作后，这里会出现记录。"],
  "qa:pending": ["没有待评审的问答。", "组员在群里问脚本、能力相关的问题后，回答会出现在这里。"],
  "qa:correcting": ["没有纠正中的问答。", "在「待评审」里标记回答错误后会进入这里。"],
  "qa:archive": ["归档库还是空的。", "评审确认或纠正后的问答会归档到这里，之后类似问题会参考它们回答。"],
  "qa:retired": ["没有已下线的归档。", "下线的归档不再参与回答，可以随时恢复。"],
  "qa:dismissed": ["没有标记为不归档的问答。", ""],
  "qa:reused": ["还没有直接复用归档答案的问答。", "提问与归档的标准问法完全相同时，会直接回复归档答案。"],
  "docfix:pending": ["没有待处理的文档修正建议。", "纠正过的问答归档时，会为它引用过的资料生成修改建议。"],
  "docfix:accepted": ["没有已采纳、待回写的建议。", "采纳的建议由开发机回写到仓库，见下方说明。"],
  "docfix:applied": ["还没有已回写的建议。", ""],
  "docfix:ignored": ["没有已忽略的建议。", ""],
};
const QA_FILTERS = [["pending", "待评审"], ["correcting", "纠正中"], ["archive", "归档库"], ["retired", "已下线"], ["dismissed", "不归档"], ["reused", "直接复用"]];
const FIX_FILTERS = [["pending", "待处理"], ["accepted", "已采纳"], ["applied", "已回写"], ["ignored", "已忽略"]];
const CATEGORIES = ["开服", "关服", "清档", "合服", "热更", "配置", "查询", "通用"];
const TABS = ["candidate", "verified", "executions", "qa", "docfix"];
const DEFAULT_FILTER = { qa: "pending", docfix: "pending" };

const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const fmt = (ts) => {
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const clock = () => { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };

let state = { tab: "candidate", filter: "", page: 1 };
let total = 0;
const dirty = new Set();
// Client cache: switching tabs / pages reuses data until 刷新 or a change invalidates it.
const cache = new Map();          // "tab:page" -> { data, at }
let summaryCache = null;          // { data, at }
const cacheKey = () => `${state.tab}:${state.filter}:${state.page}`;
function invalidateTemplates() {
  for (const key of [...cache.keys()]) if (key.startsWith("candidate:") || key.startsWith("verified:")) cache.delete(key);
  summaryCache = null;
}
function invalidateQa() {
  for (const key of [...cache.keys()]) if (key.startsWith("qa:") || key.startsWith("docfix:")) cache.delete(key);
  summaryCache = null;
}
function invalidateAll() { cache.clear(); summaryCache = null; }

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const tab = params.get("tab");
  state.tab = TABS.includes(tab) ? tab : "candidate";
  const filters = state.tab === "qa" ? QA_FILTERS : state.tab === "docfix" ? FIX_FILTERS : [];
  const filter = params.get("filter");
  state.filter = filters.some(([key]) => key === filter) ? filter : (DEFAULT_FILTER[state.tab] || "");
  state.page = Math.max(1, parseInt(params.get("page") || "1", 10) || 1);
}
function writeHash() {
  const hash = `#tab=${state.tab}` + (state.filter ? `&filter=${state.filter}` : "") + (state.page > 1 ? `&page=${state.page}` : "");
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-Admin-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { detail = (await res.text()) || detail; }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json();
}

let toastTimer;
function toast(message, isError = false) {
  const t = $("#toast");
  t.textContent = message;
  t.classList.toggle("error", isError);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), isError ? 4000 : 1800);
}

function confirmDiscard() {
  return dirty.size === 0 || confirm(`有 ${dirty.size} 条诊断改动还没保存，确定放弃？`);
}

async function load({ force = false } = {}) {
  writeHash();
  document.querySelectorAll("nav.tabs a").forEach((a) => {
    if (a.dataset.tab === state.tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  const isTemplates = state.tab === "candidate" || state.tab === "verified";
  ["#select-all-wrap", "#batch-approve", "#batch-delete"].forEach((s) => { $(s).hidden = !isTemplates; });
  renderFilters();
  $("#batch-approve").hidden = state.tab !== "candidate";
  $("#batch-revert").hidden = state.tab !== "verified";
  $("#select-all").checked = false;
  if (force) { cache.delete(cacheKey()); summaryCache = null; }
  const cached = cache.get(cacheKey());
  if (!cached) $("#content").innerHTML = '<div class="state-box"><p>正在加载…</p></div>';
  $("#refresh").disabled = true;
  try {
    if (!summaryCache) summaryCache = { data: await api("GET", "/api/summary"), at: clock() };
    const summary = summaryCache.data;
    $("#n-candidate").textContent = summary.counts.candidate;
    $("#n-verified").textContent = summary.counts.verified;
    $("#n-qa").textContent = summary.qa ? summary.qa.pending : "–";
    $("#n-docfix").textContent = summary.doc_fixes ? summary.doc_fixes.pending : "–";
    setMode(summary.read_success_mode);
    let entry = cached;
    if (!entry) {
      entry = { data: await api("GET", listUrl()), at: clock() };
      const items = entry.data.patterns || entry.data.executions || entry.data.items || [];
      if (!items.length && state.page > 1) { state.page -= 1; return load(); }
      cache.set(cacheKey(), entry);
    }
    total = entry.data.total;
    if (isTemplates) {
      dirty.clear();
      renderTemplates(entry.data.patterns);
      $("#summary").textContent = `${state.tab === "candidate" ? "待审核" : "可用模板"} ${total} 条`;
    } else if (state.tab === "executions") {
      renderExecutions(entry.data.executions);
      $("#summary").textContent = `最近执行 ${total} 条`;
    } else if (state.tab === "qa") {
      if (state.filter === "archive" || state.filter === "retired") renderArchive(entry.data.items); else renderQa(entry.data.items);
      $("#summary").textContent = `问答：${QA_FILTERS.find(([k]) => k === state.filter)[1]} ${total} 条`;
    } else {
      renderDocFixes(entry.data.items);
      $("#summary").textContent = `文档修正：${FIX_FILTERS.find(([k]) => k === state.filter)[1]} ${total} 条`;
    }
    renderPager();
    $("#stamp").textContent = `数据更新于 ${entry.at}`;
  } catch (err) {
    $("#content").innerHTML = `<div class="state-box error"><p>加载失败：${esc(err.message)}</p>
      <p class="hint">检查 SSH 隧道是否仍然连接，然后重试。</p><p><button class="btn" id="retry">重试</button></p></div>`;
    $("#retry").onclick = () => load({ force: true });
    $("#pager").hidden = true;
  } finally {
    $("#refresh").disabled = false;
    if (isTemplates && !document.querySelector(".tpl")) {
      ["#select-all-wrap", "#batch-approve", "#batch-revert", "#batch-delete"].forEach((sel) => { $(sel).hidden = true; });
    }
    updateBatchButtons();
  }
}

function listUrl() {
  const paging = `page=${state.page}&page_size=${PAGE_SIZE}`;
  if (state.tab === "candidate" || state.tab === "verified") return `/api/patterns?status=${state.tab}&${paging}`;
  if (state.tab === "executions") return `/api/executions?${paging}`;
  if (state.tab === "qa") {
    if (state.filter === "archive") return `/api/qa-archive?status=active&${paging}`;
    if (state.filter === "retired") return `/api/qa-archive?status=retired&${paging}`;
    return `/api/qa?status=${state.filter}&${paging}`;
  }
  return `/api/doc-fixes?status=${state.filter}&${paging}`;
}

function renderFilters() {
  const filters = state.tab === "qa" ? QA_FILTERS : state.tab === "docfix" ? FIX_FILTERS : null;
  const box = $("#filters");
  box.hidden = !filters;
  if (!filters) return;
  box.innerHTML = filters.map(([key, label]) =>
    `<button class="chip" data-filter="${key}" aria-pressed="${key === state.filter}">${label}</button>`).join("");
}

function emptyFor(key) {
  const [title, hint] = EMPTY[key] || ["暂无内容。", ""];
  return `<div class="state-box"><p>${title}</p>${hint ? `<p class="hint">${hint}</p>` : ""}</div>`;
}

const secs = (ms) => ms ? `${(ms / 1000).toFixed(1)} 秒` : "";

function renderQa(items) {
  if (!items.length) { $("#content").innerHTML = emptyFor(`qa:${state.filter}`); return; }
  $("#content").innerHTML = items.map((r) => {
    const sources = [
      ...(r.sources.archives || []).map((id) => `已审核问答 #${id}`),
      ...(r.sources.docs || []).map((path) => path.replace("knowledge/", "")),
    ];
    const revisions = (r.revisions || []).map((v) => `
      <div class="rev">
        <div class="label">第 ${v.round} 轮纠正${v.operator ? `（${esc(v.operator)}）` : ""}</div>
        <blockquote>${esc(v.correction)}</blockquote>
        <div class="label">AI 重新回答${v.model ? `：${esc(v.model)}，${secs(v.elapsed_ms)}` : ""}</div>
        <div class="prose">${esc(v.answer)}</div>
      </div>`).join("");
    let actions = "";
    if (r.status === "pending") {
      actions = `<button class="btn primary" data-qa="draft">回答正确，去归档</button>
        <button class="btn" data-qa="open-correct">回答错误</button>
        <button class="btn" data-qa="dismiss">不归档</button>`;
    } else if (r.status === "correcting") {
      actions = `<button class="btn primary" data-qa="draft">这次对了，去归档</button>
        <button class="btn" data-qa="open-correct">继续纠正</button>
        <button class="btn" data-qa="dismiss">不归档</button>`;
    } else if (r.status === "dismissed") {
      actions = `<button class="btn" data-qa="restore">恢复为待评审</button>`;
    }
    return `
    <article class="tpl qa" data-mid="${esc(r.message_id)}">
      <div class="tpl-head">
        <span class="script">${esc(r.user_name || r.user_id) || "未知成员"}</span>
        <dl class="facts">
          <div><dt>时间</dt><dd>${fmt(r.created_at)}</dd></div>
          ${r.planning_model ? `<div><dt>需求分析</dt><dd>${esc(r.planning_model)}，${secs(r.planning_ms)}</dd></div>` : ""}
          ${r.answer_model ? `<div><dt>回答</dt><dd>${esc(r.answer_model)}，${secs(r.answer_ms)}</dd></div>` : ""}
        </dl>
      </div>
      <p class="qa-question">${esc(r.question)}</p>
      <div class="label">${r.status === "reused" ? "直接复用的已审核答案" : "机器人回答"}</div>
      <div class="prose">${esc(r.answer)}</div>
      ${sources.length ? `<p class="sources">引用来源：${esc(sources.join("，"))}</p>` : ""}
      ${revisions}
      ${actions ? `<div class="row-actions">${actions}</div>` : ""}
      <div class="panel-box correct-box" hidden>
        <div class="label">哪里错了？写清楚正确的说法即可，AI 会据此重新回答（新答案只在这里显示，不发回群里）</div>
        <textarea class="correction" maxlength="2000"></textarea>
        <div class="actions"><button class="btn primary" data-qa="correct">让 AI 重新回答</button><button class="btn" data-qa="close-correct">取消</button></div>
      </div>
      <div class="panel-box archive-box" hidden></div>
    </article>`;
  }).join("");
}

function renderArchiveForm(box, d) {
  const fixes = (d.doc_fixes || []).map((f, i) => `
    <div class="fix-item" data-i="${i}">
      <div class="fix-head"><input type="checkbox" class="fix-pick" checked aria-label="采用这条修改建议">
        <strong>${esc(f.doc_path.replace("knowledge/", ""))}</strong><span class="sources">${esc(f.reason)}</span></div>
      <div class="label">原文</div><pre class="lines diff-old">${esc(f.original_excerpt)}</pre>
      <div class="label">建议改为</div><textarea class="fix-new">${esc(f.proposed_excerpt)}</textarea>
      <input type="hidden" class="fix-path" value="${esc(f.doc_path)}"><textarea class="fix-old" hidden>${esc(f.original_excerpt)}</textarea>
    </div>`).join("");
  box.innerHTML = `
    <div class="label">AI 已生成归档信息，确认或修改后归档。之后类似问题会参考这条答案。</div>
    <div class="form-grid">
      <label>标准问法</label><input class="a-question" value="${esc(d.canonical_question)}" maxlength="300">
      <label>类别</label><select class="a-category">${CATEGORIES.map((c) => `<option ${c === d.category ? "selected" : ""}>${c}</option>`).join("")}</select>
      <label>关键词与同义词</label><input class="a-keywords" value="${esc(d.keywords)}" placeholder="用逗号分隔">
      <label>涉及脚本</label><input class="a-scripts" value="${esc(d.scripts)}" placeholder="如 start, control_is_pub.sh">
      <label>答案</label><textarea class="a-answer">${esc(d.answer)}</textarea>
    </div>
    ${fixes ? `<div class="label" style="margin-top:14px">资料修改建议（勾选的会进入「文档修正」等待你采纳）</div>${fixes}` : ""}
    <div class="actions"><button class="btn primary" data-qa="archive">确认归档</button><button class="btn" data-qa="close-archive">取消</button></div>`;
  box.hidden = false;
}

function renderArchive(items) {
  if (!items.length) { $("#content").innerHTML = emptyFor(`qa:${state.filter}`); return; }
  $("#content").innerHTML = items.map((a) => `
    <article class="tpl arch" data-aid="${a.archive_id}">
      <div class="tpl-head">
        <span class="script">问答 #${a.archive_id}</span>
        <span class="tag">${esc(a.category)}</span>
        <dl class="facts">
          <div><dt>被引用</dt><dd>${a.hit_count} 次</dd></div>
          <div><dt>最近引用</dt><dd>${fmt(a.last_hit_at)}</dd></div>
          <div><dt>归档于</dt><dd>${fmt(a.created_at)}</dd></div>
          ${a.operator ? `<div><dt>归档人</dt><dd>${esc(a.operator)}</dd></div>` : ""}
        </dl>
        ${a.written_back ? '<span class="badge ok">资料已回写</span>' : ""}
        ${a.stale ? '<span class="badge warn" title="执行注册表在归档后有变化，答案可能过时">注册表已变化，请复核</span>' : ""}
      </div>
      <div class="form-grid">
        <label>标准问法</label><input class="a-question" value="${esc(a.canonical_question)}" maxlength="300">
        <label>类别</label><select class="a-category">${CATEGORIES.map((c) => `<option ${c === a.category ? "selected" : ""}>${c}</option>`).join("")}</select>
        <label>关键词与同义词</label><input class="a-keywords" value="${esc(a.keywords)}">
        <label>涉及脚本</label><input class="a-scripts" value="${esc(a.scripts)}">
        <label>答案</label><textarea class="a-answer">${esc(a.answer)}</textarea>
      </div>
      <div class="row-actions">
        <button class="btn" data-qa="save-archive">保存修改</button>
        ${a.status === "active" ? '<button class="btn" data-qa="retire">下线</button>' : '<button class="btn" data-qa="reactivate">恢复上线</button>'}
        <button class="btn danger" data-qa="delete-archive">删除</button>
      </div>
    </article>`).join("");
}

function renderDocFixes(items) {
  const note = state.filter === "accepted"
    ? `<div class="state-box" style="text-align:left;margin-bottom:12px"><p>回写方式</p>
       <p class="hint">采纳的建议需要在开发机上回写仓库：在 Gateway 执行 <code>.venv/bin/python tools/doc_fixes.py export &gt; fixes.json</code> 导出，
       在本机仓库执行 <code>python tools/doc_fixes.py apply fixes.json</code> 修改资料并提交推送，Gateway <code>git pull</code> 后执行
       <code>.venv/bin/python tools/doc_fixes.py mark-applied &lt;编号…&gt;</code>。也可以直接让 Claude Code 代办。</p></div>` : "";
  if (!items.length) { $("#content").innerHTML = note + emptyFor(`docfix:${state.filter}`); return; }
  $("#content").innerHTML = note + items.map((f) => {
    const editable = f.status === "pending";
    const actions = {
      pending: '<button class="btn primary" data-qa="fix-accept">采纳</button><button class="btn" data-qa="fix-ignore">忽略</button>',
      accepted: '<button class="btn" data-qa="fix-reopen">退回待处理</button>',
      ignored: '<button class="btn" data-qa="fix-reopen">恢复待处理</button>',
    }[f.status] || "";
    return `
    <article class="tpl fix" data-fid="${f.suggestion_id}">
      <div class="tpl-head">
        <span class="script">${esc(f.doc_path.replace("knowledge/", ""))}</span>
        <span class="tag">建议 #${f.suggestion_id}</span>
        <dl class="facts"><div><dt>来自归档</dt><dd>#${f.archive_id} ${esc(f.canonical_question)}</dd></div>
          <div><dt>生成于</dt><dd>${fmt(f.created_at)}</dd></div></dl>
      </div>
      ${f.reason ? `<p class="reason">原因：${esc(f.reason)}</p>` : ""}
      <div class="label">原文</div><pre class="lines diff-old">${esc(f.original_excerpt)}</pre>
      <div class="label">建议改为</div>
      ${editable ? `<textarea class="fix-proposed">${esc(f.proposed_excerpt)}</textarea>` : `<pre class="lines diff-new">${esc(f.proposed_excerpt)}</pre>`}
      ${actions ? `<div class="row-actions">${actions}</div>` : ""}
    </article>`;
  }).join("");
}

async function qaAction(button) {
  const act = button.dataset.qa;
  const card = button.closest("article");
  const mid = card.dataset.mid, aid = card.dataset.aid, fid = card.dataset.fid;
  const busy = async (label, work) => {
    const original = button.textContent;
    button.disabled = true; button.textContent = label;
    try { return await work(); } finally { button.disabled = false; button.textContent = original; }
  };
  try {
    if (act === "open-correct") { card.querySelector(".correct-box").hidden = false; card.querySelector(".correction").focus(); return; }
    if (act === "close-correct") { card.querySelector(".correct-box").hidden = true; return; }
    if (act === "close-archive") { card.querySelector(".archive-box").hidden = true; return; }
    if (act === "correct") {
      const text = card.querySelector(".correction").value.trim();
      if (!text) { toast("先写下哪里错了", true); return; }
      await busy("AI 正在重新回答…", () => api("POST", `/api/qa/${encodeURIComponent(mid)}/correct`, { correction: text }));
      toast("已生成新答案");
      state.filter = "correcting"; state.page = 1; invalidateQa(); return load();
    }
    if (act === "draft") {
      const draft = await busy("AI 正在整理…", () => api("POST", `/api/qa/${encodeURIComponent(mid)}/draft`));
      renderArchiveForm(card.querySelector(".archive-box"), draft);
      return;
    }
    if (act === "archive") {
      const box = card.querySelector(".archive-box");
      const fixes = [...box.querySelectorAll(".fix-item")].filter((el) => el.querySelector(".fix-pick").checked).map((el) => ({
        doc_path: el.querySelector(".fix-path").value,
        original_excerpt: el.querySelector(".fix-old").value,
        proposed_excerpt: el.querySelector(".fix-new").value,
        reason: el.querySelector(".sources").textContent,
      }));
      const r = await busy("正在归档…", () => api("POST", `/api/qa/${encodeURIComponent(mid)}/archive`, {
        canonical_question: box.querySelector(".a-question").value,
        category: box.querySelector(".a-category").value,
        keywords: box.querySelector(".a-keywords").value,
        scripts: box.querySelector(".a-scripts").value,
        answer: box.querySelector(".a-answer").value,
        doc_fixes: fixes,
      }));
      toast(`已归档为问答 #${r.archive_id}` + (r.doc_fixes ? `，生成 ${r.doc_fixes} 条资料修改建议` : ""));
      invalidateQa(); return load();
    }
    if (act === "dismiss") { await api("POST", `/api/qa/${encodeURIComponent(mid)}/dismiss`); toast("已标记为不归档"); invalidateQa(); return load(); }
    if (act === "restore") { await api("POST", `/api/qa/${encodeURIComponent(mid)}/restore`); toast("已恢复为待评审"); invalidateQa(); return load(); }
    if (act === "save-archive") {
      await api("PUT", `/api/qa-archive/${aid}`, {
        canonical_question: card.querySelector(".a-question").value,
        category: card.querySelector(".a-category").value,
        keywords: card.querySelector(".a-keywords").value,
        scripts: card.querySelector(".a-scripts").value,
        answer: card.querySelector(".a-answer").value,
      });
      toast("已保存"); invalidateQa(); return;
    }
    if (act === "retire" || act === "reactivate") {
      await api("PUT", `/api/qa-archive/${aid}`, { status: act === "retire" ? "retired" : "active" });
      toast(act === "retire" ? "已下线，不再参与回答" : "已恢复上线"); invalidateQa(); return load();
    }
    if (act === "delete-archive") {
      if (!confirm(`删除问答 #${aid}？删除后不再参与回答。`)) return;
      await api("DELETE", `/api/qa-archive/${aid}`); toast("已删除"); invalidateQa(); return load();
    }
    if (act === "fix-accept" || act === "fix-ignore" || act === "fix-reopen") {
      const body = { status: act === "fix-accept" ? "accepted" : act === "fix-ignore" ? "ignored" : "pending" };
      const proposed = card.querySelector(".fix-proposed");
      if (proposed && act === "fix-accept") body.proposed_excerpt = proposed.value;
      await api("PUT", `/api/doc-fixes/${fid}`, body);
      toast({ "fix-accept": "已采纳，等待回写仓库", "fix-ignore": "已忽略", "fix-reopen": "已退回待处理" }[act]);
      invalidateQa(); return load();
    }
  } catch (err) { toast(`操作失败：${err.message}`, true); }
}

$("#content").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-qa]");
  if (button) qaAction(button);
});
$("#filters").addEventListener("click", (e) => {
  const chip = e.target.closest("button[data-filter]");
  if (!chip || chip.dataset.filter === state.filter) return;
  state.filter = chip.dataset.filter; state.page = 1; load();
});

function setMode(mode) {
  document.querySelectorAll('#mode input').forEach((i) => { i.checked = i.value === mode; });
  $("#mode-hint").textContent = MODE_HINT[mode] || "";
}

function emptyBox(kind) {
  const [title, hint] = EMPTY[kind];
  return `<div class="state-box"><p>${title}</p><p class="hint">${hint}</p></div>`;
}

function renderPager() {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  $("#pager").hidden = total <= PAGE_SIZE;
  $("#page-info").textContent = `第 ${state.page} / ${pages} 页，共 ${total} 条`;
  $("#prev").disabled = state.page <= 1;
  $("#next").disabled = state.page >= pages;
}

/* ---------------- templates ---------------- */
function renderTemplates(patterns) {
  if (!patterns.length) { $("#content").innerHTML = emptyBox(state.tab); return; }
  $("#content").innerHTML = patterns.map((p) => {
    const script = p.script_path.split("/").pop() || "（无脚本）";
    const review = state.tab === "candidate"
      ? '<button class="btn primary" data-act="approve">通过</button>'
      : '<button class="btn" data-act="revert">退回待审核</button>';
    return `
    <article class="tpl" data-id="${p.pattern_id}">
      <div class="tpl-head">
        <label class="check" title="选择"><input type="checkbox" class="pick" value="${p.pattern_id}" aria-label="选择 ${esc(script)}"></label>
        <span class="script">${esc(script)}</span>
        <span class="tag">${esc(p.failure_category)}</span>
        <dl class="facts">
          <div><dt>命中</dt><dd>${p.hit_count} 次</dd></div>
          <div><dt>首次出现</dt><dd>${fmt(p.first_seen_at)}</dd></div>
          <div><dt>最近出现</dt><dd>${fmt(p.last_seen_at)}</dd></div>
          ${p.approved_at ? `<div><dt>审批于</dt><dd>${fmt(p.approved_at)}</dd></div>` : ""}
        </dl>
      </div>
      <p class="reason">失败原因：${esc(p.normalized_reason) || "（无）"}</p>
      <pre class="lines">${esc(p.key_error_lines.join("\n")) || "（没有提取到关键错误行）"}</pre>
      <div class="field-label"><span>诊断（卡片上展示给组员）</span><span class="dirty" hidden>未保存</span></div>
      <textarea class="diagnosis" aria-label="诊断">${esc(p.diagnosis)}</textarea>
      <div class="tpl-foot">
        <div class="verdict">
          <span>结果判定</span>
          <div class="segmented" role="radiogroup" aria-label="结果判定">
            <label><input type="radio" name="v-${p.pattern_id}" value="failure" ${p.outcome !== "warning" ? "checked" : ""}><span>执行失败</span></label>
            <label><input type="radio" name="v-${p.pattern_id}" value="warning" ${p.outcome === "warning" ? "checked" : ""}><span>执行成功，状态存疑</span></label>
          </div>
          <span class="verdict-note">仅查询类脚本、模板通过后生效</span>
        </div>
        <div class="actions">
          <button class="btn" data-act="save" disabled>保存诊断</button>
          ${review}
          <button class="btn danger" data-act="delete">删除</button>
        </div>
      </div>
      <details class="more">
        <summary>出现范围（${p.seen_scopes.length}）与最近执行</summary>
        <h4>出现过的范围</h4>
        <pre class="lines">${esc(p.seen_scopes.join("\n")) || "–"}</pre>
        <h4>最近执行</h4>
        <div class="samples">点开后加载…</div>
      </details>
    </article>`;
  }).join("");
}

function selectedIds() { return [...document.querySelectorAll(".pick:checked")].map((c) => Number(c.value)); }
function updateBatchButtons() {
  const n = selectedIds().length;
  [["#batch-approve", "通过所选"], ["#batch-revert", "退回所选"], ["#batch-delete", "删除所选"]].forEach(([sel, label]) => {
    $(sel).disabled = n === 0;
    $(sel).textContent = n ? `${label}（${n}）` : label;
  });
  document.querySelectorAll(".tpl").forEach((el) => {
    el.dataset.selected = el.querySelector(".pick")?.checked ? "true" : "false";
  });
}

async function act(ids, action) {
  if (!ids.length) return;
  if (action === "delete" && !confirm(`删除 ${ids.length} 条模板？同类错误下次出现时会重新交给 AI 分析。`)) return;
  try {
    const r = await api("POST", "/api/patterns/batch", { ids, action });
    toast({ approve: "已通过", revert: "已退回待审核", delete: "已删除" }[action] + ` ${r.changed} 条`);
    ids.forEach((id) => dirty.delete(id));
    invalidateTemplates();
    await load();
  } catch (err) { toast(`操作失败：${err.message}`, true); }
}

$("#content").addEventListener("input", (e) => {
  if (!e.target.classList.contains("diagnosis")) return;
  const item = e.target.closest(".tpl");
  const id = Number(item.dataset.id);
  const changed = e.target.value !== e.target.defaultValue;
  if (changed) dirty.add(id); else dirty.delete(id);
  e.target.dataset.dirty = changed ? "true" : "false";
  item.querySelector(".dirty").hidden = !changed;
  item.querySelector('[data-act="save"]').disabled = !changed;
});

$("#content").addEventListener("change", async (e) => {
  if (e.target.classList.contains("pick")) { updateBatchButtons(); return; }
  if (e.target.type === "radio" && e.target.closest(".verdict")) {
    const id = Number(e.target.closest(".tpl").dataset.id);
    try {
      await api("PUT", `/api/patterns/${id}/outcome`, { outcome: e.target.value });
      invalidateTemplates();
      toast(e.target.value === "warning" ? "已改为：执行成功，状态存疑" : "已改为：执行失败");
    } catch (err) { toast(`保存失败：${err.message}`, true); }
  }
});

$("#content").addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-act]");
  if (!button) return;
  const item = button.closest(".tpl");
  const id = Number(item.dataset.id);
  const action = button.dataset.act;
  if (action === "save") {
    const area = item.querySelector(".diagnosis");
    try {
      await api("PUT", `/api/patterns/${id}`, { diagnosis: area.value });
      area.defaultValue = area.value;
      area.dataset.dirty = "false";
      item.querySelector(".dirty").hidden = true;
      button.disabled = true;
      dirty.delete(id);
      invalidateTemplates();
      toast("诊断已保存");
    } catch (err) { toast(`保存失败：${err.message}`, true); }
    return;
  }
  if (dirty.has(id) && action !== "delete" && !confirm("这条诊断还没保存，确定继续？")) return;
  await act([id], action);
});

$("#content").addEventListener("toggle", async (e) => {
  const d = e.target;
  if (!d.classList || !d.classList.contains("more") || !d.open) return;
  const box = d.querySelector(".samples");
  if (box.dataset.loaded) return;
  try {
    const id = Number(d.closest(".tpl").dataset.id);
    const s = (await api("GET", `/api/patterns/${id}/samples`)).samples;
    box.innerHTML = s.length
      ? s.map((a) => `<div class="sample"><div class="meta">${fmt(a.created_at)}，${esc(SOURCE[a.report_source] || a.report_source)}</div><pre class="lines">${esc(a.summary_text)}</pre></div>`).join("")
      : '<p class="hint">执行记录已过保留期。</p>';
    box.dataset.loaded = "1";
  } catch (err) { box.textContent = `加载失败：${err.message}`; }
}, true);

/* ---------------- executions ---------------- */
function renderExecutions(rows) {
  if (!rows.length) { $("#content").innerHTML = emptyBox("executions"); return; }
  const body = rows.map((r, i) => {
    const planning = r.planning_model
      ? `${esc(r.planning_model)}<span class="sub">${(r.planning_ms / 1000).toFixed(1)} 秒，${r.planning_calls} 次调用</span>` : "–";
    const reading = r.model_name
      ? `${esc(r.model_name)}<span class="sub">${(r.elapsed_ms / 1000).toFixed(1)} 秒</span>` : "–";
    const readingNote = r.interpretation_status && r.interpretation_status !== "done"
      ? `<span class="sub">研读：${esc(r.interpretation_status)}${r.error_kind ? "，" + esc(r.error_kind) : ""}</span>` : "";
    return `
      <tr class="row" data-i="${i}" data-state="${esc(r.terminal_status)}" data-source="${esc(r.report_source)}" tabindex="0" aria-expanded="false">
        <td class="when">${fmt(r.created_at)}</td>
        <td class="request">${esc(r.request_text) || "–"}<span class="sub">${esc(r.user_name || r.user_id) || ""}</span></td>
        <td>${planning}</td>
        <td><span class="state ${esc(r.terminal_status)}">${STATUS[r.terminal_status] || esc(r.terminal_status)}</span></td>
        <td>${esc(SOURCE[r.report_source] || r.report_source)}${readingNote}</td>
        <td>${reading}</td>
        <td class="chev">展开</td>
      </tr>
      <tr class="detail" data-for="${i}" hidden><td colspan="7">
        ${r.ai_summary ? `<h4>AI 说明</h4><pre class="lines">${esc(r.ai_summary)}</pre>` : ""}
        <h4>执行明细</h4><pre class="lines">${esc(r.summary_text)}</pre>
      </td></tr>`;
  }).join("");
  $("#content").innerHTML = `<div class="table-wrap"><table class="exec">
    <thead><tr><th>时间</th><th>请求与发起人</th><th>需求分析</th><th>终态</th><th>说明来源</th><th>结果研读</th><th></th></tr></thead>
    <tbody>${body}</tbody></table></div>`;
}

function toggleRow(row) {
  const detail = document.querySelector(`tr.detail[data-for="${row.dataset.i}"]`);
  const open = detail.hidden;
  detail.hidden = !open;
  row.setAttribute("aria-expanded", String(open));
  row.querySelector(".chev").textContent = open ? "收起" : "展开";
}
$("#content").addEventListener("click", (e) => { const row = e.target.closest("tr.row"); if (row) toggleRow(row); });
$("#content").addEventListener("keydown", (e) => {
  const row = e.target.closest && e.target.closest("tr.row");
  if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggleRow(row); }
});

/* ---------------- chrome ---------------- */
document.querySelectorAll("nav.tabs a").forEach((a) => a.addEventListener("click", (e) => {
  e.preventDefault();
  if (a.dataset.tab === state.tab || !confirmDiscard()) return;
  state = { tab: a.dataset.tab, filter: DEFAULT_FILTER[a.dataset.tab] || "", page: 1 };
  load();
}));
$("#refresh").onclick = () => { if (confirmDiscard()) load({ force: true }); };
$("#prev").onclick = () => { if (confirmDiscard()) { state.page -= 1; load(); } };
$("#next").onclick = () => { if (confirmDiscard()) { state.page += 1; load(); } };
$("#select-all").onchange = (e) => { document.querySelectorAll(".pick").forEach((c) => { c.checked = e.target.checked; }); updateBatchButtons(); };
$("#batch-approve").onclick = () => act(selectedIds(), "approve");
$("#batch-revert").onclick = () => act(selectedIds(), "revert");
$("#batch-delete").onclick = () => act(selectedIds(), "delete");
document.querySelectorAll("#mode input").forEach((input) => input.addEventListener("change", async () => {
  try {
    await api("PUT", "/api/settings/read-success-mode", { mode: input.value });
    if (summaryCache) summaryCache.data.read_success_mode = input.value;
    setMode(input.value);
    toast(input.value === "ai" ? "查询成功后：AI 解读日志" : "查询成功后：只发程序结果");
  } catch (err) { toast(`切换失败：${err.message}`, true); load({ force: true }); }
}));
window.addEventListener("hashchange", () => { readHash(); load(); });
window.addEventListener("beforeunload", (e) => { if (dirty.size) { e.preventDefault(); e.returnValue = ""; } });

readHash();
load();
</script>
</body>
</html>
"""
