"""List-view page for the server-backed project plan board (served at /plan)."""

PLAN_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>飞书机器人 · 项目计划</title>
<style>
  :root {
    --paper: #f3f5f7; --panel: #fff; --ink: #1f2a36; --ink-2: #4b5866; --ink-3: #7a8693; --line: #dfe4e9;
    --accent: #2f5d8a; --accent-soft: #e8eff6; --ok: #2e7d4f; --ok-soft: #e8f5ee; --active: #2f6fd1; --active-soft: #e6effc;
    --todo: #8a94a0; --bad: #b8322a; --warn: #a86a12; --warn-soft: #fff4e5; --radius: 6px;
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  body { margin: 0; background: var(--paper); color: var(--ink);
    font: 14px/1.6 "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", "Noto Sans CJK SC", system-ui, sans-serif;
    font-variant-numeric: tabular-nums; }
  button, input, textarea, select { font: inherit; color: inherit; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 0 28px; }
  header { background: var(--panel); border-bottom: 1px solid var(--line); }
  header .wrap { padding-top: 18px; padding-bottom: 14px; }
  .top { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  h1 { font-size: 20px; margin: 0; font-weight: 650; }
  .back { font-size: 13px; color: var(--accent); text-decoration: none; border: 1px solid var(--line); border-radius: var(--radius); padding: 3px 10px; }
  .progress { margin-left: auto; display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--ink-2); }
  .bar { width: 180px; height: 6px; border-radius: 3px; background: var(--line); overflow: hidden; }
  .bar i { display: block; height: 100%; background: var(--ok); }
  .principle { margin: 10px 0 0; font-size: 13px; color: var(--ink-2); max-width: 90ch; }
  .controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 14px; }
  .controls input[type=search] { flex: 1; min-width: 220px; padding: 7px 10px; border: 1px solid var(--line); border-radius: var(--radius); background: #fff; }
  .controls select { padding: 6px 8px; border: 1px solid var(--line); border-radius: var(--radius); background: #fff; }
  .btn { padding: 6px 12px; border-radius: var(--radius); border: 1px solid var(--line); background: var(--panel); cursor: pointer; font-size: 13px; }
  .btn:hover:not(:disabled) { border-color: var(--ink-3); }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn.danger { color: var(--bad); }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .stamp { font-size: 12px; color: var(--ink-3); }
  main .wrap { padding-top: 18px; padding-bottom: 48px; }
  .banner { background: var(--warn-soft); border: 1px solid #f3d6a8; border-radius: var(--radius); padding: 10px 14px; margin-bottom: 14px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; font-size: 13px; }
  .group { margin-bottom: 18px; }
  .group-head { display: flex; align-items: center; gap: 8px; padding: 6px 2px; cursor: pointer; user-select: none; font-weight: 600; font-size: 13px; color: var(--ink-2); }
  .group-head .chev { width: 14px; color: var(--ink-3); }
  .group-head .n { font-weight: 500; color: var(--ink-3); }
  .list { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
  .row { display: grid; grid-template-columns: 26px 44px minmax(220px, 2.2fr) 84px 70px minmax(160px, 2fr) 92px; gap: 10px; align-items: center;
    padding: 9px 14px; border-top: 1px solid var(--line); cursor: pointer; }
  .row:first-child { border-top: 0; }
  .row:hover { background: #fafbfc; }
  .row[aria-expanded=true] { background: var(--accent-soft); }
  .dot { width: 16px; height: 16px; border-radius: 50%; border: 2px solid var(--todo); background: transparent; padding: 0; cursor: pointer; }
  .dot.active { border-color: var(--active); background: radial-gradient(circle, var(--active) 0 3px, transparent 4px); }
  .dot.done { border-color: var(--ok); background: var(--ok); box-shadow: inset 0 0 0 3px #fff; }
  .sid { font-size: 12px; color: var(--ink-3); }
  .title { font-weight: 550; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .area { font-size: 12px; padding: 1px 8px; border-radius: 4px; background: var(--paper); color: var(--ink-2); justify-self: start; white-space: nowrap; }
  .prog { font-size: 12px; color: var(--ink-3); white-space: nowrap; }
  .now { font-size: 12px; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .when { font-size: 12px; color: var(--ink-3); text-align: right; white-space: nowrap; }
  .detail { border-top: 1px solid var(--line); background: #fcfcfd; padding: 14px 18px 18px 58px; }
  .detail .desc { margin: 0 0 10px; color: var(--ink-2); }
  .cols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  .col h4 { margin: 0 0 6px; font-size: 12px; font-weight: 600; }
  .col.done h4 { color: var(--ok); } .col.current h4 { color: var(--active); } .col.remaining h4 { color: var(--ink-2); }
  .col ul { margin: 0; padding-left: 18px; font-size: 13px; color: var(--ink); }
  .col li { margin-bottom: 4px; }
  .col .empty { font-size: 12px; color: var(--ink-3); }
  .edit-grid { display: grid; grid-template-columns: 90px 1fr; gap: 8px 12px; align-items: start; }
  .edit-grid label { font-size: 12px; color: var(--ink-3); padding-top: 6px; }
  .edit-grid input, .edit-grid textarea, .edit-grid select, .notes textarea {
    width: 100%; padding: 6px 8px; border: 1px solid var(--line); border-radius: 4px; background: #fff; line-height: 1.6; }
  .edit-grid textarea { min-height: 84px; resize: vertical; }
  .notes { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 14px; }
  .notes textarea { min-height: 72px; resize: vertical; }
  .notes h4 { margin: 0 0 4px; font-size: 12px; color: var(--ink-3); font-weight: 500; }
  .actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; align-items: center; }
  .meta-line { font-size: 12px; color: var(--ink-3); margin-left: auto; }
  .history { margin-top: 10px; font-size: 12px; color: var(--ink-2); }
  .history li { margin-bottom: 3px; }
  .menu { position: absolute; z-index: 10; background: #fff; border: 1px solid var(--line); border-radius: var(--radius); box-shadow: 0 6px 20px rgba(31,42,54,.12); padding: 4px; }
  .menu button { display: block; width: 100%; text-align: left; border: 0; background: none; padding: 6px 14px; cursor: pointer; border-radius: 4px; font-size: 13px; }
  .menu button:hover { background: var(--paper); }
  .flow { margin-top: 14px; border: 1px solid var(--line); border-radius: var(--radius); background: #fff; padding: 10px 12px; }
  .flow h4 { margin: 0 0 6px; font-size: 12px; color: var(--ink-2); }
  .flow-scroll { overflow-x: auto; }
  svg.seq text { font-size: 11px; fill: var(--ink-2); }
  svg.seq .a-name { font-size: 12px; font-weight: 600; fill: var(--ink); }
  svg.seq .a-box { fill: var(--paper); stroke: var(--line); }
  svg.seq .life { stroke: var(--line); stroke-dasharray: 3 3; }
  svg.seq .ln { stroke-width: 1.6; fill: none; }
  svg.seq .st-done .ln { stroke: var(--ok); } svg.seq .st-active .ln { stroke: var(--active); }
  svg.seq .st-todo .ln { stroke: var(--todo); stroke-dasharray: 5 4; }
  svg.seq .hop { cursor: pointer; } svg.seq .hit { fill: transparent; } svg.seq .hop.sel .hit { fill: var(--accent-soft); }
  svg.seq .num { fill: #fff; stroke: var(--line); } svg.seq .num-t { font-size: 10px; text-anchor: middle; }
  .hop-panel { font-size: 13px; margin-top: 6px; padding: 8px 10px; background: var(--paper); border-radius: 4px; }
  .hop-panel code { background: #fff; padding: 0 4px; border-radius: 3px; }
  .toast { position: fixed; right: 24px; bottom: 24px; background: var(--ink); color: #fff; padding: 10px 14px; border-radius: var(--radius); font-size: 13px; opacity: 0; transition: opacity .18s; pointer-events: none; }
  .toast.show { opacity: 1; } .toast.error { background: var(--bad); }
  .state-box { background: var(--panel); border: 1px dashed var(--line); border-radius: var(--radius); padding: 36px; text-align: center; color: var(--ink-2); }
  @media (max-width: 900px) {
    .row { grid-template-columns: 26px 40px 1fr 70px; } .row .prog, .row .now, .row .when { display: none; }
    .cols, .notes { grid-template-columns: 1fr; } .detail { padding-left: 18px; }
  }
  @media (prefers-reduced-motion: reduce) { .toast { transition: none; } }
</style>
</head>
<body>
<header><div class="wrap">
  <div class="top">
    <h1 id="title">项目计划</h1>
    <a class="back" href="/">审批台</a>
    <div class="progress"><span id="progress-text"></span><div class="bar"><i id="bar"></i></div></div>
  </div>
  <p class="principle" id="principle"></p>
  <div class="controls">
    <input type="search" id="search" placeholder="搜索标题、说明、待办…">
    <select id="area-filter"><option value="">全部领域</option></select>
    <button class="btn" id="new-step">新增步骤</button>
    <span class="stamp" id="stamp"></span>
    <button class="btn" id="refresh">刷新</button>
  </div>
</div></header>
<main><div class="wrap">
  <div class="banner" id="migrate-banner" hidden>
    <span id="migrate-text"></span>
    <button class="btn primary" id="migrate">同步到服务器</button>
    <button class="btn" id="migrate-skip">忽略并清除</button>
  </div>
  <div id="content" aria-live="polite"><div class="state-box">正在加载…</div></div>
</div></main>
<div class="toast" id="toast" role="status"></div>

<script>
const STATUS = { active: "进行中", todo: "未开始", done: "已完成" };
const ORDER = ["active", "todo", "done"];
const OLD_NOTES = "hotfix-plan-notes-v1", OLD_STATUS = "hotfix-plan-status-overrides-v1";
const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const richCode = (t) => esc(t).replace(/&lt;(\/?)code&gt;/g, "<$1code>");
const pad = (n) => String(n).padStart(2, "0");
const fmt = (ts) => { if (!ts) return "–"; const d = new Date(ts * 1000); return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const clock = () => { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };

let plan = null;
const open = new Set();
const editing = new Set();
const collapsed = new Set(["done"]);
const flowSel = {};

async function api(method, url, body) {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-Admin-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) { let d = res.statusText; try { d = (await res.json()).detail || d; } catch (_) {} throw new Error(`${res.status} ${d}`); }
  return res.json();
}
let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.classList.toggle("error", err); t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), err ? 4000 : 1800);
}

async function load() {
  $("#refresh").disabled = true;
  try {
    plan = await api("GET", "/api/plan");
    $("#stamp").textContent = `数据更新于 ${clock()}`;
    renderHeader(); render(); checkMigration();
  } catch (err) {
    $("#content").innerHTML = `<div class="state-box">加载失败：${esc(err.message)}</div>`;
  } finally { $("#refresh").disabled = false; }
}

function renderHeader() {
  $("#title").textContent = plan.meta.title || "项目计划";
  document.title = plan.meta.title || "项目计划";
  $("#principle").textContent = plan.meta.principle || "";
  const done = plan.counts.done, total = plan.total || 1;
  $("#progress-text").textContent = `已完成 ${done} / ${plan.total}`;
  $("#bar").style.width = `${Math.round(done / total * 100)}%`;
  const select = $("#area-filter"), current = select.value;
  select.innerHTML = '<option value="">全部领域</option>' + plan.areas.map((a) => `<option ${a === current ? "selected" : ""}>${esc(a)}</option>`).join("");
}

function matches(step) {
  const area = $("#area-filter").value, q = $("#search").value.trim().toLowerCase();
  if (area && step.area !== area) return false;
  if (!q) return true;
  return [step.id, step.title, step.desc, ...step.done, ...step.current, ...step.remaining, step.note, step.question]
    .join("\n").toLowerCase().includes(q);
}

function render() {
  const filtering = $("#area-filter").value || $("#search").value.trim();
  const html = ORDER.map((status) => {
    const steps = plan.steps.filter((s) => s.status === status && matches(s));
    const isCollapsed = collapsed.has(status) && !filtering;
    return `<section class="group">
      <div class="group-head" data-group="${status}"><span class="chev">${isCollapsed ? "▸" : "▾"}</span>${STATUS[status]} <span class="n">${steps.length}</span></div>
      ${isCollapsed || !steps.length ? "" : `<div class="list">${steps.map(rowHtml).join("")}</div>`}
    </section>`;
  }).join("");
  $("#content").innerHTML = html;
}

function rowHtml(s) {
  const total = s.done.length + s.current.length + s.remaining.length;
  const now = s.current[0] || s.remaining[0] || "";
  const isOpen = open.has(s.id);
  return `<div class="row" data-id="${s.id}" tabindex="0" aria-expanded="${isOpen}">
      <button class="dot ${s.status}" data-status-menu="${s.id}" title="${STATUS[s.status]}（点击切换）" aria-label="状态：${STATUS[s.status]}"></button>
      <span class="sid">${esc(s.id)}</span>
      <span class="title">${esc(s.title)}</span>
      <span class="area">${esc(s.area)}</span>
      <span class="prog">${total ? `${s.done.length}/${total}` : "–"}</span>
      <span class="now">${esc(now)}</span>
      <span class="when" title="${esc(s.updated_by)}">${s.updated_by === "seed" ? "–" : fmt(s.updated_at)}</span>
    </div>${isOpen ? detailHtml(s) : ""}`;
}

function listCol(kind, label, items) {
  return `<div class="col ${kind}"><h4>${label}（${items.length}）</h4>${items.length
    ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : '<div class="empty">无</div>'}</div>`;
}

function detailHtml(s) {
  const body = editing.has(s.id) ? `
    <div class="edit-grid">
      <label>标题</label><input class="e-title" value="${esc(s.title)}">
      <label>说明</label><input class="e-desc" value="${esc(s.desc)}">
      <label>领域</label><select class="e-area">${plan.areas.map((a) => `<option ${a === s.area ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>
      <label>已完成</label><textarea class="e-done" placeholder="每行一项">${esc(s.done.join("\n"))}</textarea>
      <label>正在做</label><textarea class="e-current" placeholder="每行一项">${esc(s.current.join("\n"))}</textarea>
      <label>后续待办</label><textarea class="e-remaining" placeholder="每行一项">${esc(s.remaining.join("\n"))}</textarea>
    </div>
    <div class="actions"><button class="btn primary" data-act="save-edit">保存</button><button class="btn" data-act="cancel-edit">取消</button></div>` : `
    ${s.desc ? `<p class="desc">${esc(s.desc)}</p>` : ""}
    <div class="cols">${listCol("done", "已完成", s.done)}${listCol("current", "正在做", s.current)}${listCol("remaining", "后续待办", s.remaining)}</div>`;
  return `<div class="detail" data-detail="${s.id}">
    ${body}
    ${s.flow ? `<div class="flow" data-flow="${s.id}">${flowHtml(s)}</div>` : ""}
    <div class="notes">
      <div><h4>批注</h4><textarea class="n-note" placeholder="想法、结论、待确认点">${esc(s.note)}</textarea></div>
      <div><h4>疑问</h4><textarea class="n-question" placeholder="需要问清楚的问题">${esc(s.question)}</textarea></div>
    </div>
    <div class="actions">
      ${editing.has(s.id) ? "" : '<button class="btn" data-act="edit">编辑内容</button>'}
      <button class="btn" data-act="save-notes">保存批注</button>
      <button class="btn" data-act="history">修改历史</button>
      <button class="btn danger" data-act="delete">删除（想法已过时）</button>
      <span class="meta-line">${s.updated_by === "seed" ? "初始导入后未修改" : `最后修改 ${fmt(s.updated_at)}${s.updated_by ? `，${esc(s.updated_by)}` : ""}`}</span>
    </div>
    <ul class="history" hidden></ul>
  </div>`;
}

function flowHtml(s) {
  const f = s.flow, sel = flowSel[s.id];
  const colW = 160, top = 66, rowH = 58, W = f.actors.length * colW, H = top + f.msgs.length * rowH + 12;
  const ix = {}; f.actors.forEach((a, i) => { ix[a.id] = i; });
  const cx = (i) => colW * i + colW / 2;
  const color = { done: "var(--ok)", active: "var(--active)", todo: "var(--todo)" };
  let svg = `<svg class="seq" viewBox="0 0 ${W} ${H}" style="min-width:${Math.min(W, 760)}px"><defs>` +
    Object.entries(color).map(([k, v]) => `<marker id="ah-${s.id}-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" style="fill:${v}"/></marker>`).join("") + "</defs>";
  f.actors.forEach((a, i) => {
    svg += `<line class="life" x1="${cx(i)}" y1="50" x2="${cx(i)}" y2="${H}"/><rect class="a-box" x="${cx(i) - 66}" y="4" width="132" height="44" rx="6"/>
      <text class="a-name" x="${cx(i)}" y="23" text-anchor="middle">${esc(a.name)}</text><text x="${cx(i)}" y="39" text-anchor="middle" style="font-size:10px">${esc(a.sub || "")}</text>`;
  });
  f.msgs.forEach((m, i) => {
    const y = top + i * rowH + 26, a = cx(ix[m.from]), b = cx(ix[m.to]);
    const marker = `url(#ah-${s.id}-${m.s})`;
    const line = a === b ? `<path class="ln" d="M${a} ${y - 9} h42 v22 h-40" marker-end="${marker}"/>` : `<line class="ln" x1="${a}" y1="${y}" x2="${b}" y2="${y}" marker-end="${marker}"/>`;
    const lx = a === b ? a + 52 : (a + b) / 2, ly = a === b ? y + 6 : y - 8, nx = a === b ? a - 14 : a + (b > a ? 14 : -14);
    svg += `<g class="hop st-${m.s}${sel === i ? " sel" : ""}" data-hop="${i}"><rect class="hit" x="${Math.min(a, b) - 22}" y="${y - 24}" width="${Math.abs(b - a) + (a === b ? 180 : 44)}" height="${rowH - 6}" rx="6"/>
      ${line}<circle class="num" cx="${nx}" cy="${y}" r="9"/><text class="num-t" x="${nx}" y="${y + 4}">${i + 1}</text>
      <text x="${lx}" y="${ly}" ${a === b ? "" : 'text-anchor="middle"'}>${esc(m.t)}</text></g>`;
  });
  svg += "</svg>";
  const panel = sel != null ? `<div class="hop-panel"><b>第 ${sel + 1} 步（${STATUS[f.msgs[sel].s] || "未开始"}）</b> ${richCode(f.msgs[sel].d)}</div>`
    : '<div class="hop-panel">点击箭头查看这一步的说明</div>';
  return `<h4>${esc(f.title)}</h4><div class="flow-scroll">${svg}</div>${panel}`;
}

async function update(id, changes, message) {
  try {
    const step = await api("PUT", `/api/plan/steps/${encodeURIComponent(id)}`, changes);
    plan.steps = plan.steps.map((s) => (s.id === id ? step : s));
    plan = await api("GET", "/api/plan");
    renderHeader(); render(); toast(message);
  } catch (err) { toast(`保存失败：${err.message}`, true); }
}

function closeMenu() { document.querySelector(".menu")?.remove(); }
function openMenu(button, id) {
  closeMenu();
  const step = plan.steps.find((s) => s.id === id);
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.innerHTML = ORDER.map((k) => `<button data-set="${k}">${k === step.status ? "● " : ""}${STATUS[k]}</button>`).join("");
  document.body.appendChild(menu);
  const r = button.getBoundingClientRect();
  menu.style.left = `${r.left + window.scrollX}px`; menu.style.top = `${r.bottom + window.scrollY + 4}px`;
  menu.onclick = (e) => { const b = e.target.closest("button[data-set]"); if (!b) return; closeMenu();
    if (b.dataset.set !== step.status) update(id, { status: b.dataset.set }, `第 ${id} 步改为${STATUS[b.dataset.set]}`); };
}

$("#content").addEventListener("click", async (e) => {
  const menuBtn = e.target.closest("[data-status-menu]");
  if (menuBtn) { e.stopPropagation(); openMenu(menuBtn, menuBtn.dataset.statusMenu); return; }
  const head = e.target.closest("[data-group]");
  if (head) { const g = head.dataset.group; collapsed.has(g) ? collapsed.delete(g) : collapsed.add(g); render(); return; }
  const hop = e.target.closest("[data-hop]");
  if (hop) { const id = hop.closest("[data-flow]").dataset.flow; flowSel[id] = Number(hop.dataset.hop);
    hop.closest("[data-flow]").innerHTML = flowHtml(plan.steps.find((s) => s.id === id)); return; }
  const actBtn = e.target.closest("[data-act]");
  if (actBtn) {
    const detail = actBtn.closest("[data-detail]"), id = detail.dataset.detail, act = actBtn.dataset.act;
    if (act === "edit") { editing.add(id); render(); return; }
    if (act === "cancel-edit") { editing.delete(id); render(); return; }
    if (act === "save-edit") {
      const lines = (sel) => detail.querySelector(sel).value.split("\n").map((l) => l.trim()).filter(Boolean);
      editing.delete(id);
      return update(id, { title: detail.querySelector(".e-title").value, desc: detail.querySelector(".e-desc").value,
        area: detail.querySelector(".e-area").value, done: lines(".e-done"), current: lines(".e-current"), remaining: lines(".e-remaining") }, "已保存");
    }
    if (act === "save-notes") return update(id, { note: detail.querySelector(".n-note").value, question: detail.querySelector(".n-question").value }, "批注已保存");
    if (act === "history") {
      const box = detail.querySelector(".history");
      if (!box.hidden) { box.hidden = true; return; }
      const events = (await api("GET", `/api/plan/events?step_id=${encodeURIComponent(id)}`)).events;
      box.innerHTML = events.length ? events.map((ev) => `<li>${fmt(ev.at)} ${esc(ev.operator)}：${esc(ev.field)} → ${esc(String(ev.new_value).slice(0, 120))}</li>`).join("") : "<li>暂无修改记录</li>";
      box.hidden = false; return;
    }
    if (act === "delete") {
      const step = plan.steps.find((s) => s.id === id);
      if (!confirm(`删除第 ${id} 步「${step.title}」？删除前的内容保留在修改历史里。`)) return;
      try { await api("DELETE", `/api/plan/steps/${encodeURIComponent(id)}`); open.delete(id); toast("已删除"); load(); }
      catch (err) { toast(`删除失败：${err.message}`, true); }
      return;
    }
  }
  const row = e.target.closest(".row");
  if (row) { const id = row.dataset.id; open.has(id) ? open.delete(id) : open.add(id); render(); }
});
$("#content").addEventListener("keydown", (e) => {
  const row = e.target.closest && e.target.closest(".row");
  if (row && e.target === row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); row.click(); }
});
document.addEventListener("click", (e) => { if (!e.target.closest(".menu")) closeMenu(); });
$("#search").addEventListener("input", () => render());
$("#area-filter").addEventListener("change", () => render());
$("#refresh").onclick = load;
$("#new-step").onclick = async () => {
  const title = prompt("新步骤标题");
  if (!title || !title.trim()) return;
  try { const step = await api("POST", "/api/plan/steps", { title: title.trim(), status: "todo", area: $("#area-filter").value || "其他" });
    open.add(step.id); editing.add(step.id); toast(`已新增第 ${step.id} 步`); load(); }
  catch (err) { toast(`新增失败：${err.message}`, true); }
};

function readOld() {
  let overrides = {}, notes = {};
  try { overrides = JSON.parse(localStorage.getItem(OLD_STATUS)) || {}; } catch (_) {}
  try { notes = JSON.parse(localStorage.getItem(OLD_NOTES)) || {}; } catch (_) {}
  return { overrides, notes };
}
function clearOld() { localStorage.removeItem(OLD_STATUS); localStorage.removeItem(OLD_NOTES); }
function checkMigration() {
  const { overrides, notes } = readOld();
  const changed = Object.entries(overrides).filter(([id, st]) => { const s = plan.steps.find((x) => x.id === id); return s && s.status !== st; });
  const noteCount = Object.values(notes).filter((n) => n && (n.note || n.question)).length;
  if (!changed.length && !noteCount) { $("#migrate-banner").hidden = true; return; }
  $("#migrate-text").textContent = `这个浏览器里有旧看板留下的本机改动：${changed.length} 条状态（${changed.map(([id, st]) => `${id}→${STATUS[st]}`).join("，") || "无"}），${noteCount} 条批注。同步后所有设备都能看到。`;
  $("#migrate-banner").hidden = false;
}
$("#migrate").onclick = async () => {
  try { const r = await api("POST", "/api/plan/migrate-local", readOld()); clearOld(); $("#migrate-banner").hidden = true;
    toast(`已同步 ${r.statuses} 条状态、${r.notes} 条批注`); load(); }
  catch (err) { toast(`同步失败：${err.message}`, true); }
};
$("#migrate-skip").onclick = () => { if (confirm("清除这个浏览器里旧的本机改动？")) { clearOld(); $("#migrate-banner").hidden = true; } };

load();
</script>
</body>
</html>
"""
