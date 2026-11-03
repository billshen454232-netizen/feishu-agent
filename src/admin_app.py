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
    async def list_patterns(status: str = PATTERN_CANDIDATE) -> dict[str, Any]:
        if status not in PATTERN_STATUSES:
            raise HTTPException(status_code=400, detail="unknown status")
        return {"patterns": [pattern.as_jsonable() for pattern in store.list_patterns(status)]}

    @app.get("/api/executions")
    async def executions(limit: int = 50) -> dict[str, Any]:
        return {"executions": store.list_recent_executions(max(1, min(limit, 200)))}

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


_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>执行结果模板审批</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --line:#e3e6ea; --ink:#1f2328; --muted:#656d76; --accent:#2563eb; --ok:#15803d; --warn:#b45309; --bad:#b91c1c; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--ink); }
  header { padding:16px 24px; background:var(--card); border-bottom:1px solid var(--line); display:flex; align-items:center; gap:24px; flex-wrap:wrap; }
  h1 { font-size:18px; margin:0; }
  .tabs button { border:1px solid var(--line); background:var(--card); padding:6px 14px; cursor:pointer; border-radius:6px; margin-right:6px; }
  .tabs button.active { background:var(--accent); color:#fff; border-color:var(--accent); }
  .mode { margin-left:auto; display:flex; align-items:center; gap:8px; color:var(--muted); }
  .mode select { padding:4px 8px; }
  main { padding:16px 24px; max-width:1200px; }
  .toolbar { display:flex; gap:8px; align-items:center; margin-bottom:12px; }
  .toolbar button, .row-actions button { border:1px solid var(--line); background:var(--card); padding:4px 12px; border-radius:6px; cursor:pointer; }
  button.primary { background:var(--ok); color:#fff; border-color:var(--ok); }
  button.danger { color:var(--bad); border-color:#f3c4c4; }
  .item { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin-bottom:10px; }
  .item-head { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  .script { font-weight:600; }
  .tag { font-size:12px; padding:1px 8px; border-radius:10px; background:#eef2ff; color:var(--accent); }
  .meta { color:var(--muted); font-size:12px; }
  pre { background:#f6f8fa; padding:8px; border-radius:6px; white-space:pre-wrap; word-break:break-all; margin:8px 0; font-size:12px; }
  textarea { width:100%; min-height:90px; font:inherit; padding:8px; border:1px solid var(--line); border-radius:6px; }
  .row-actions { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; }
  .outcome-row { display:flex; align-items:center; gap:8px; margin:6px 0; flex-wrap:wrap; }
  .empty { color:var(--muted); padding:40px; text-align:center; }
  details summary { cursor:pointer; color:var(--accent); font-size:12px; }
  table.exec { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:8px; }
  table.exec th, table.exec td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:13px; }
  table.exec th { color:var(--muted); font-weight:500; background:#fafbfc; }
  .st-done { color:var(--ok); } .st-failed { color:var(--bad); } .st-submitted_result_unknown { color:var(--warn); }
  .toast { position:fixed; bottom:20px; right:20px; background:#111; color:#fff; padding:8px 14px; border-radius:6px; opacity:0; transition:opacity .2s; }
  .toast.show { opacity:.9; }
</style>
</head>
<body>
<header>
  <h1>执行结果模板审批</h1>
  <div class="tabs">
    <button data-tab="candidate" class="active">待审核 <span id="n-candidate">0</span></button>
    <button data-tab="verified">可用模板 <span id="n-verified">0</span></button>
    <button data-tab="executions">最近执行</button>
  </div>
  <div class="mode">
    查询成功时：
    <select id="mode">
      <option value="ai">AI 后台解读日志</option>
      <option value="template">只发程序模板（不调 AI）</option>
    </select>
  </div>
</header>
<main>
  <div class="toolbar" id="toolbar">
    <label><input type="checkbox" id="select-all"> 全选</label>
    <button id="batch-approve" class="primary">批量通过</button>
    <button id="batch-revert">批量退回未审核</button>
    <button id="batch-delete" class="danger">批量删除</button>
  </div>
  <div id="list"></div>
</main>
<div class="toast" id="toast"></div>
<script>
let tab = "candidate";
let patterns = [];
const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (ts) => ts ? new Date(ts * 1000).toLocaleString() : "-";

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "X-Admin-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 1800); }

const STATUS = { done: "执行完成", failed: "执行失败", submitted_result_unknown: "结果未核验" };
const SOURCE = {
  template: "程序模板", ai_pending: "AI 分析中", ai_read: "AI 解读", ai_new: "AI 新分析",
  candidate_template: "复用待审核模板", verified_template: "复用可用模板",
  verified_warning: "复用可用模板 · 判定警告",
};

async function refresh() {
  const summary = await api("GET", "/api/summary");
  $("#n-candidate").textContent = summary.counts.candidate;
  $("#n-verified").textContent = summary.counts.verified;
  $("#mode").value = summary.read_success_mode;
  $("#toolbar").style.display = tab === "executions" ? "none" : "";
  if (tab === "executions") { renderExecutions((await api("GET", "/api/executions?limit=50")).executions); return; }
  patterns = (await api("GET", "/api/patterns?status=" + tab)).patterns;
  render();
}

function renderExecutions(rows) {
  if (!rows.length) { $("#list").innerHTML = '<div class="empty">暂无执行记录</div>'; return; }
  $("#list").innerHTML = `<table class="exec"><thead><tr>
      <th>时间</th><th>请求</th><th>终态</th><th>说明来源</th><th>模型 / 耗时</th><th>详情</th></tr></thead><tbody>` +
    rows.map((r) => `<tr>
      <td>${fmt(r.created_at)}</td>
      <td>${esc(r.request_text) || "-"}</td>
      <td class="st-${esc(r.terminal_status)}">${STATUS[r.terminal_status] || esc(r.terminal_status)}</td>
      <td>${SOURCE[r.report_source] || esc(r.report_source)}${r.interpretation_status && r.interpretation_status !== "done" ? "<div class=meta>研读：" + esc(r.interpretation_status) + (r.error_kind ? " / " + esc(r.error_kind) : "") + "</div>" : ""}</td>
      <td>${esc(r.model_name) || "-"}${r.elapsed_ms ? "<div class=meta>" + (r.elapsed_ms / 1000).toFixed(1) + " 秒</div>" : ""}</td>
      <td><details><summary>展开</summary>
        ${r.ai_summary ? "<div class=meta>AI 说明</div><pre>" + esc(r.ai_summary) + "</pre>" : ""}
        <div class="meta">执行明细</div><pre>${esc(r.summary_text)}</pre></details></td>
    </tr>`).join("") + "</tbody></table>";
}

function render() {
  $("#batch-approve").style.display = tab === "candidate" ? "" : "none";
  $("#batch-revert").style.display = tab === "verified" ? "" : "none";
  $("#select-all").checked = false;
  if (!patterns.length) { $("#list").innerHTML = '<div class="empty">暂无模板</div>'; return; }
  $("#list").innerHTML = patterns.map((p) => `
    <div class="item" data-id="${p.pattern_id}">
      <div class="item-head">
        <input type="checkbox" class="pick" value="${p.pattern_id}">
        <span class="script">${esc(p.script_path.split("/").pop() || "（无脚本）")}</span>
        <span class="tag">${esc(p.failure_category)}</span>
        <span class="meta">命中 ${p.hit_count} 次 · 首次 ${fmt(p.first_seen_at)} · 最近 ${fmt(p.last_seen_at)}${p.approved_at ? " · 审批 " + fmt(p.approved_at) : ""}</span>
      </div>
      <div class="meta">失败原因：${esc(p.normalized_reason) || "（无）"}</div>
      <pre>${esc(p.key_error_lines.join("\\n")) || "（无关键错误行）"}</pre>
      <div class="outcome-row">
        结果判定：
        <select class="outcome">
          <option value="failure" ${p.outcome === "failure" ? "selected" : ""}>执行失败（默认）</option>
          <option value="warning" ${p.outcome === "warning" ? "selected" : ""}>执行成功 · 服务器状态存疑</option>
        </select>
        <span class="meta">仅对查询类脚本、且模板已通过后生效；机器终态仍记为失败</span>
      </div>
      <textarea class="diagnosis">${esc(p.diagnosis)}</textarea>
      <details class="scopes"><summary>出现范围（${p.seen_scopes.length}）与最近执行</summary>
        <pre>${esc(p.seen_scopes.join("\\n")) || "-"}</pre>
        <div class="samples"></div>
      </details>
      <div class="row-actions">
        <button class="save">保存诊断</button>
        ${tab === "candidate" ? '<button class="approve primary">通过</button>' : '<button class="revert">退回未审核</button>'}
        <button class="delete danger">删除</button>
      </div>
    </div>`).join("");
}

async function act(ids, action) {
  if (!ids.length) { toast("未选择模板"); return; }
  if (action === "delete" && !confirm("删除 " + ids.length + " 条模板？同类错误下次会重新走 AI 分析。")) return;
  const r = await api("POST", "/api/patterns/batch", { ids, action });
  toast("已处理 " + r.changed + " 条");
  await refresh();
}

document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach((x) => x.classList.remove("active"));
  b.classList.add("active"); tab = b.dataset.tab; refresh();
});
$("#select-all").onchange = (e) => document.querySelectorAll(".pick").forEach((c) => c.checked = e.target.checked);
const picked = () => [...document.querySelectorAll(".pick:checked")].map((c) => Number(c.value));
$("#batch-approve").onclick = () => act(picked(), "approve");
$("#batch-revert").onclick = () => act(picked(), "revert");
$("#batch-delete").onclick = () => act(picked(), "delete");
$("#mode").onchange = async (e) => { await api("PUT", "/api/settings/read-success-mode", { mode: e.target.value }); toast("已切换"); };

$("#list").addEventListener("click", async (e) => {
  const item = e.target.closest(".item"); if (!item) return;
  const id = Number(item.dataset.id);
  if (e.target.classList.contains("save")) {
    await api("PUT", "/api/patterns/" + id, { diagnosis: item.querySelector(".diagnosis").value });
    toast("已保存");
  } else if (e.target.classList.contains("approve")) { await act([id], "approve"); }
  else if (e.target.classList.contains("revert")) { await act([id], "revert"); }
  else if (e.target.classList.contains("delete")) { await act([id], "delete"); }
});
$("#list").addEventListener("change", async (e) => {
  if (!e.target.classList.contains("outcome")) return;
  const id = Number(e.target.closest(".item").dataset.id);
  await api("PUT", "/api/patterns/" + id + "/outcome", { outcome: e.target.value });
  toast("结果判定已保存");
});
$("#list").addEventListener("toggle", async (e) => {
  const d = e.target; if (!d.classList || !d.classList.contains("scopes") || !d.open) return;
  const id = Number(d.closest(".item").dataset.id);
  const box = d.querySelector(".samples"); if (box.dataset.loaded) return;
  const s = (await api("GET", "/api/patterns/" + id + "/samples")).samples;
  box.innerHTML = s.map((a) => `<div class="meta">${fmt(a.created_at)} · ${esc(a.report_source)}</div><pre>${esc(a.summary_text)}</pre>`).join("") || '<div class="meta">（无保留的执行记录）</div>';
  box.dataset.loaded = "1";
}, true);

refresh().catch((err) => { $("#list").innerHTML = '<div class="empty">加载失败：' + esc(err.message) + "</div>"; });
</script>
</body>
</html>
"""
