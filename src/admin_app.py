"""Template review page for the execution report library.

Served by a second uvicorn server bound to 127.0.0.1 only, separate from the
public Feishu/Worker listener. Reach it through ``ssh -L``. Mutating requests
must carry ``X-Admin-Request: 1`` (blocks cross-site form posts against
localhost) and the Host header must be a loopback name (blocks DNS rebinding).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

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


def create_admin_app(store: ExecutionReportStore) -> FastAPI:
    app = FastAPI(title="执行结果模板审批", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Any:
        if _request_host(request) not in _LOOPBACK_HOSTS:
            return PlainTextResponse("forbidden host", status_code=403)
        if request.method not in {"GET", "HEAD"} and request.headers.get("x-admin-request") != "1":
            return PlainTextResponse("missing admin header", status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _INDEX_HTML

    @app.get("/api/summary")
    async def summary() -> dict[str, Any]:
        return {"counts": store.count_patterns(), "read_success_mode": store.read_success_mode()}

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
    async def edit(pattern_id: int, payload: dict[str, Any]) -> dict[str, str]:
        diagnosis = payload.get("diagnosis")
        if not isinstance(diagnosis, str) or not diagnosis.strip() or len(diagnosis) > _MAX_DIAGNOSIS_CHARS:
            raise HTTPException(status_code=400, detail="diagnosis must be 1-{} chars".format(_MAX_DIAGNOSIS_CHARS))
        if not store.update_diagnosis(pattern_id, diagnosis.strip()):
            raise HTTPException(status_code=404, detail="not found")
        return {"status": "ok"}

    @app.put("/api/patterns/{pattern_id}/outcome")
    async def set_outcome(pattern_id: int, payload: dict[str, Any]) -> dict[str, str]:
        outcome = payload.get("outcome")
        if outcome not in PATTERN_OUTCOMES:
            raise HTTPException(status_code=400, detail="unknown outcome")
        if not store.set_pattern_outcome(pattern_id, outcome):
            raise HTTPException(status_code=404, detail="not found")
        logger.info("template review pattern=%s outcome=%s", pattern_id, outcome)
        return {"outcome": outcome}

    @app.post("/api/patterns/batch")
    async def batch(payload: dict[str, Any]) -> dict[str, Any]:
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
        logger.info("template review action=%s ids=%s changed=%s", action, ids, changed)
        return {"changed": changed}

    @app.put("/api/settings/read-success-mode")
    async def set_mode(payload: dict[str, Any]) -> dict[str, str]:
        mode = payload.get("mode")
        if mode not in READ_SUCCESS_MODES:
            raise HTTPException(status_code=400, detail="unknown mode")
        store.set_read_success_mode(mode)
        logger.info("template review read_success_mode=%s", mode)
        return {"read_success_mode": mode}

    return app


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

        self._server = uvicorn.Server(
            uvicorn.Config(app, host=ADMIN_HOST, port=port, log_level="warning", lifespan="off")
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
      <h1>执行结果审批台</h1>
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
};

const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const fmt = (ts) => {
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const clock = () => { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };

let state = { tab: "candidate", page: 1 };
let total = 0;
const dirty = new Set();

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  const tab = params.get("tab");
  state.tab = ["candidate", "verified", "executions"].includes(tab) ? tab : "candidate";
  state.page = Math.max(1, parseInt(params.get("page") || "1", 10) || 1);
}
function writeHash() {
  const hash = `#tab=${state.tab}` + (state.page > 1 ? `&page=${state.page}` : "");
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

async function load() {
  writeHash();
  document.querySelectorAll("nav.tabs a").forEach((a) => {
    if (a.dataset.tab === state.tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  const isTemplates = state.tab !== "executions";
  ["#select-all-wrap", "#batch-approve", "#batch-delete"].forEach((s) => { $(s).hidden = !isTemplates; });
  $("#batch-approve").hidden = state.tab !== "candidate";
  $("#batch-revert").hidden = state.tab !== "verified";
  $("#select-all").checked = false;
  $("#content").innerHTML = '<div class="state-box"><p>正在加载…</p></div>';
  $("#refresh").disabled = true;
  try {
    const summary = await api("GET", "/api/summary");
    $("#n-candidate").textContent = summary.counts.candidate;
    $("#n-verified").textContent = summary.counts.verified;
    setMode(summary.read_success_mode);
    if (isTemplates) {
      const data = await api("GET", `/api/patterns?status=${state.tab}&page=${state.page}&page_size=${PAGE_SIZE}`);
      if (!data.patterns.length && state.page > 1) { state.page -= 1; return load(); }
      total = data.total;
      dirty.clear();
      renderTemplates(data.patterns);
      $("#summary").textContent = `${state.tab === "candidate" ? "待审核" : "可用模板"} ${total} 条`;
    } else {
      const data = await api("GET", `/api/executions?page=${state.page}&page_size=${PAGE_SIZE}`);
      if (!data.executions.length && state.page > 1) { state.page -= 1; return load(); }
      total = data.total;
      renderExecutions(data.executions);
      $("#summary").textContent = `最近执行 ${total} 条`;
    }
    renderPager();
    $("#stamp").textContent = `更新于 ${clock()}`;
  } catch (err) {
    $("#content").innerHTML = `<div class="state-box error"><p>加载失败：${esc(err.message)}</p>
      <p class="hint">检查 SSH 隧道是否仍然连接，然后重试。</p><p><button class="btn" id="retry">重试</button></p></div>`;
    $("#retry").onclick = load;
    $("#pager").hidden = true;
  } finally {
    $("#refresh").disabled = false;
    if (isTemplates && !document.querySelector(".tpl")) {
      ["#select-all-wrap", "#batch-approve", "#batch-revert", "#batch-delete"].forEach((sel) => { $(sel).hidden = true; });
    }
    updateBatchButtons();
  }
}

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
  state = { tab: a.dataset.tab, page: 1 };
  load();
}));
$("#refresh").onclick = () => { if (confirmDiscard()) load(); };
$("#prev").onclick = () => { if (confirmDiscard()) { state.page -= 1; load(); } };
$("#next").onclick = () => { if (confirmDiscard()) { state.page += 1; load(); } };
$("#select-all").onchange = (e) => { document.querySelectorAll(".pick").forEach((c) => { c.checked = e.target.checked; }); updateBatchButtons(); };
$("#batch-approve").onclick = () => act(selectedIds(), "approve");
$("#batch-revert").onclick = () => act(selectedIds(), "revert");
$("#batch-delete").onclick = () => act(selectedIds(), "delete");
document.querySelectorAll("#mode input").forEach((input) => input.addEventListener("change", async () => {
  try {
    await api("PUT", "/api/settings/read-success-mode", { mode: input.value });
    setMode(input.value);
    toast(input.value === "ai" ? "查询成功后：AI 解读日志" : "查询成功后：只发程序结果");
  } catch (err) { toast(`切换失败：${err.message}`, true); load(); }
}));
window.addEventListener("hashchange", () => { readHash(); load(); });
window.addEventListener("beforeunload", (e) => { if (dirty.size) { e.preventDefault(); e.returnValue = ""; } });

readHash();
load();
</script>
</body>
</html>
"""
