"""Hotfix threshold registry page (served at /thresholds; data in Gateway SQLite)."""

THRESHOLDS_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>热更阈值注册表</title>
<style>
  :root {
    --paper: #f3f5f7; --panel: #fff; --ink: #1f2a36; --ink-2: #4b5866; --ink-3: #7a8693; --line: #dfe4e9;
    --accent: #2f5d8a; --accent-soft: #e8eff6; --ok: #2e7d4f; --ok-soft: #e8f5ee; --todo: #8a94a0;
    --bad: #b8322a; --warn: #a86a12; --warn-soft: #fff4e5; --radius: 6px;
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  body { margin: 0; background: var(--paper); color: var(--ink);
    font: 14px/1.6 "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", "Noto Sans CJK SC", system-ui, sans-serif;
    font-variant-numeric: tabular-nums; }
  button, input, textarea, select { font: inherit; color: inherit; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  code { font: 12px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace; }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 0 28px; }
  header { background: var(--panel); border-bottom: 1px solid var(--line); }
  header .wrap { padding-top: 18px; padding-bottom: 0; }
  .top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  h1 { font-size: 20px; margin: 0; font-weight: 650; }
  .back { font-size: 13px; color: var(--accent); text-decoration: none; border: 1px solid var(--line); border-radius: var(--radius); padding: 3px 10px; }
  .principle { margin: 10px 0 0; font-size: 13px; color: var(--ink-2); max-width: 96ch; }
  .tabs { display: flex; gap: 4px; margin-top: 12px; }
  .tabs button { border: 0; background: none; padding: 8px 14px 9px; cursor: pointer; color: var(--ink-2); border-bottom: 3px solid transparent; font-size: 14px; }
  .tabs button[aria-selected=true] { color: var(--ink); border-bottom-color: var(--accent); font-weight: 600; }
  .tabs .n { margin-left: 6px; font-size: 12px; color: var(--ink-3); }
  .controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 16px 0 12px; }
  .controls input[type=search] { flex: 1; min-width: 240px; padding: 7px 10px; border: 1px solid var(--line); border-radius: var(--radius); background: #fff; }
  .btn { padding: 6px 12px; border-radius: var(--radius); border: 1px solid var(--line); background: var(--panel); cursor: pointer; font-size: 13px; }
  .btn:hover:not(:disabled) { border-color: var(--ink-3); }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn.danger { color: var(--bad); }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .stamp { font-size: 12px; color: var(--ink-3); }
  main .wrap { padding-bottom: 48px; }
  .list { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
  .head, .row { display: grid; grid-template-columns: 18px minmax(200px, 1.6fr) minmax(170px, 1.1fr) minmax(240px, 1.6fr) minmax(160px, 1.4fr) 70px 92px;
    gap: 12px; align-items: center; padding: 9px 14px; }
  .head { font-size: 12px; color: var(--ink-3); background: #fafbfc; border-bottom: 1px solid var(--line); }
  .row { border-top: 1px solid var(--line); cursor: pointer; }
  .row:first-of-type { border-top: 0; }
  .row:hover { background: #fafbfc; }
  .row[aria-expanded=true] { background: var(--accent-soft); }
  .row.disabled .name, .row.disabled .path { color: var(--ink-3); }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--ok); }
  .dot.disabled { background: transparent; border: 2px solid var(--todo); }
  .name { font-weight: 550; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .aliases { font-size: 12px; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .key, .path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .key code { color: var(--ink-2); }
  .desc { font-size: 13px; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { font-size: 12px; padding: 1px 8px; border-radius: 4px; white-space: nowrap; justify-self: start; }
  .badge.used { background: var(--warn-soft); color: var(--warn); }
  .when { font-size: 12px; color: var(--ink-3); text-align: right; white-space: nowrap; }
  .detail { border-top: 1px solid var(--line); background: #fcfcfd; padding: 14px 18px 18px 44px; }
  .form { display: grid; grid-template-columns: 90px 1fr 90px 1fr; gap: 8px 12px; align-items: start; }
  .form label { font-size: 12px; color: var(--ink-3); padding-top: 6px; }
  .form input, .form textarea { width: 100%; padding: 6px 8px; border: 1px solid var(--line); border-radius: 4px; background: #fff; line-height: 1.6; }
  .form input:disabled { background: var(--paper); color: var(--ink-3); }
  .form textarea { min-height: 60px; resize: vertical; }
  .form .wide { grid-column: 2 / 5; }
  .hint { font-size: 12px; color: var(--ink-3); margin: 2px 0 0; }
  .actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; align-items: center; }
  .meta-line { font-size: 12px; color: var(--ink-3); margin-left: auto; }
  .review { margin-top: 12px; padding: 10px 12px; border-radius: var(--radius); border: 1px solid var(--line); background: #fff; font-size: 13px; }
  .review.warn { border-color: #f3d6a8; background: var(--warn-soft); }
  .review dl { display: grid; grid-template-columns: 90px 1fr; gap: 2px 10px; margin: 6px 0 0; }
  .review dt { color: var(--ink-3); } .review dd { margin: 0; }
  .history { margin-top: 10px; font-size: 12px; color: var(--ink-2); }
  .history li { margin-bottom: 3px; }
  .new-panel { background: var(--panel); border: 1px solid var(--accent); border-radius: var(--radius); margin-bottom: 14px; }
  .new-panel .detail { border-top: 0; padding-left: 18px; }
  .new-panel h3 { margin: 0 0 10px; font-size: 14px; }
  .toast { position: fixed; right: 24px; bottom: 24px; background: var(--ink); color: #fff; padding: 10px 14px; border-radius: var(--radius); font-size: 13px; opacity: 0; transition: opacity .18s; pointer-events: none; max-width: 520px; }
  .toast.show { opacity: 1; } .toast.error { background: var(--bad); }
  .state-box { background: var(--panel); border: 1px dashed var(--line); border-radius: var(--radius); padding: 36px; text-align: center; color: var(--ink-2); }
  @media (max-width: 900px) {
    .head { display: none; }
    .row { grid-template-columns: 18px 1fr 70px; } .row .key, .row .desc, .row .when { display: none; }
    .row .path { grid-column: 2 / 4; }
    .form { grid-template-columns: 80px 1fr; } .form .wide { grid-column: 2; } .detail { padding-left: 18px; }
  }
  @media (prefers-reduced-motion: reduce) { .toast { transition: none; } }
</style>
</head>
<body>
<header><div class="wrap">
  <div class="top">
    <h1>热更阈值注册表</h1>
    <a class="back" href="/">审批台</a>
    <a class="back" href="/plan">项目计划</a>
  </div>
  <p class="principle">飞书「热更 X 服 某阈值为 N」只能使用这里启用的条目。保存后下一条飞书请求立即生效，不需要改代码、不需要部署 GS-1。
  AI 只看到 key 与中文名；生效路径（表名.字段名）由 Gateway 确定性展开。已执行过的条目不能删除、不能改生效路径，只能停用。</p>
  <div class="tabs" id="tabs" role="tablist"></div>
</div></header>
<main><div class="wrap">
  <div class="controls">
    <input type="search" id="search" placeholder="搜索中文名、别名、key、表名、字段名、说明…">
    <button class="btn primary" id="new">新增阈值</button>
    <button class="btn" id="refresh">刷新</button>
    <span class="stamp" id="stamp"></span>
  </div>
  <section class="new-panel" id="new-panel" hidden></section>
  <div id="content"><div class="state-box">加载中…</div></div>
</div></main>
<div class="toast" id="toast" role="status"></div>

<script>
const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const TABS = [["all", "全部"], ["active", "启用"], ["disabled", "停用"]];
let tab = new URLSearchParams(location.hash.slice(1)).get("state") || "all";
let items = [];
const open = new Set();
const reviews = {};

async function api(method, url, body) {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-Admin-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) { let d = res.statusText; try { d = (await res.json()).detail || d; } catch (_) {} throw new Error(`${res.status} ${d}`); }
  return res.json();
}
let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.classList.toggle("error", err); t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), err ? 5000 : 1800);
}
const clock = (ts) => { const d = ts ? new Date(ts * 1000) : new Date(); const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };
const splitAliases = (text) => text.split(/[,，、\n]/).map((s) => s.trim()).filter(Boolean);

async function load() {
  $("#refresh").disabled = true;
  try {
    items = (await api("GET", "/api/thresholds")).thresholds;
    $("#stamp").textContent = `数据更新于 ${clock()}`;
    render();
  } catch (err) {
    $("#content").innerHTML = `<div class="state-box">加载失败：${esc(err.message)}</div>`;
  } finally { $("#refresh").disabled = false; }
}

function counts() {
  return { all: items.length, active: items.filter((i) => i.state === "active").length, disabled: items.filter((i) => i.state === "disabled").length };
}

function matches(item) {
  const q = $("#search").value.trim().toLowerCase();
  if (tab !== "all" && item.state !== tab) return false;
  if (!q) return true;
  return [item.name, item.key, item.table_name, item.field_name, item.description, item.source, item.notes, ...item.aliases]
    .some((v) => String(v || "").toLowerCase().includes(q));
}

function render() {
  const c = counts();
  $("#tabs").innerHTML = TABS.map(([k, label]) => `<button role="tab" data-tab="${k}" aria-selected="${k === tab}">${label}<span class="n">${c[k]}</span></button>`).join("");
  const shown = items.filter(matches);
  if (!shown.length) { $("#content").innerHTML = `<div class="state-box">${items.length ? "没有匹配的条目" : "还没有登记任何阈值"}</div>`; return; }
  $("#content").innerHTML = `<div class="list"><div class="head"><span></span><span>中文名 / 别名</span><span>key</span><span>生效路径</span><span>说明</span><span>执行</span><span style="text-align:right">最后修改</span></div>
    ${shown.map(rowHtml).join("")}</div>`;
}

function rowHtml(item) {
  const expanded = open.has(item.key);
  return `<div class="row ${item.state}" data-key="${esc(item.key)}" aria-expanded="${expanded}" tabindex="0">
      <span class="dot ${item.state}" title="${item.state === "active" ? "启用" : "停用"}"></span>
      <span><div class="name">${esc(item.name)}</div>${item.aliases.length ? `<div class="aliases">${esc(item.aliases.join("、"))}</div>` : ""}</span>
      <span class="key"><code>${esc(item.key)}</code></span>
      <span class="path"><code>${esc(item.table_name)}.${esc(item.field_name)}</code></span>
      <span class="desc" title="${esc(item.description)}">${esc(item.description)}</span>
      <span>${item.executed ? '<span class="badge used" title="已有任务执行过：不能删除、不能改生效路径">已执行</span>' : ""}</span>
      <span class="when">${clock(item.updated_at)}</span>
    </div>${expanded ? `<div class="detail" data-detail="${esc(item.key)}">${formHtml(item)}</div>` : ""}`;
}

function formHtml(item, isNew = false) {
  const lock = item.executed ? "disabled" : "";
  const review = reviews[isNew ? "__new__" : item.key];
  return `<div class="form">
      ${isNew ? `<label>key</label><div class="wide"><input name="key" placeholder="如 union_impeach_min" autocomplete="off"><p class="hint">小写字母开头，小写字母 / 数字 / 下划线；创建后不能改，飞书计划里使用它。</p></div>` : ""}
      <label>中文名</label><input name="name" value="${esc(item.name)}">
      <label>别名</label><input name="aliases" value="${esc((item.aliases || []).join("、"))}" placeholder="用 、 或逗号分隔">
      <label>表名</label><input name="table_name" value="${esc(item.table_name)}" ${lock}>
      <label>字段名</label><input name="field_name" value="${esc(item.field_name)}" ${lock}>
      <label>说明</label><textarea class="wide" name="description">${esc(item.description)}</textarea>
      <label>来源</label><input name="source" value="${esc(item.source)}">
      <label>备注</label><input name="notes" value="${esc(item.notes)}">
    </div>
    ${item.executed ? '<p class="hint">已执行过：生效路径锁定（服务器上可能已有对应补丁，改路径会导致还原找不到）。需要换字段请停用后新增。</p>' : ""}
    <div class="actions">
      <button class="btn primary" data-act="${isNew ? "create" : "save"}">${isNew ? "创建" : "保存"}</button>
      <button class="btn" data-act="review">AI 审核</button>
      ${isNew ? '<button class="btn" data-act="cancel">取消</button>' : `
      <button class="btn" data-act="toggle">${item.state === "active" ? "停用" : "启用"}</button>
      <button class="btn danger" data-act="delete" ${item.executed ? 'disabled title="已执行过的条目只能停用"' : ""}>删除</button>
      <button class="btn" data-act="history">修改历史</button>
      <span class="meta-line">最后修改 ${clock(item.updated_at)} · ${esc(item.updated_by)}</span>`}
    </div>
    ${review ? reviewHtml(review) : ""}
    <div class="history" data-history></div>`;
}

function reviewHtml(r) {
  if (r.loading) return '<div class="review">AI 审核中…（只读元数据，不影响保存）</div>';
  if (r.error) return `<div class="review warn">AI 审核失败：${esc(r.error)}</div>`;
  const warn = r.consistent === false || r.duplicate_of;
  return `<div class="review ${warn ? "warn" : ""}"><strong>AI 审核建议</strong>（仅供参考，${esc(r.model || "")}）<dl>
    <dt>自洽</dt><dd>${r.consistent === null ? "无法判断" : r.consistent ? "一致" : "疑似不一致"}${r.reason ? "：" + esc(r.reason) : ""}</dd>
    <dt>疑似重复</dt><dd>${esc(r.duplicate_of || "无")}</dd>
    <dt>侧别建议</dt><dd>${esc(r.side)}（执行时不说侧别仍默认双端）</dd>
    ${r.comments ? `<dt>其他</dt><dd>${esc(r.comments)}</dd>` : ""}</dl></div>`;
}

function readForm(container) {
  const v = (name) => { const el = container.querySelector(`[name=${name}]`); return el ? el.value.trim() : undefined; };
  const data = { name: v("name"), aliases: splitAliases(v("aliases") || ""), description: v("description"), source: v("source"), notes: v("notes") };
  const table = container.querySelector("[name=table_name]"), field = container.querySelector("[name=field_name]");
  if (!table.disabled) data.table_name = table.value.trim();
  if (!field.disabled) data.field_name = field.value.trim();
  if (container.querySelector("[name=key]")) data.key = v("key");
  return data;
}

async function act(action, key, container, button) {
  const item = items.find((i) => i.key === key);
  button.disabled = true;
  try {
    if (action === "save") {
      await api("PUT", `/api/thresholds/${encodeURIComponent(key)}`, readForm(container));
      toast("已保存，下一条飞书请求生效"); await load();
    } else if (action === "create") {
      const created = await api("POST", "/api/thresholds", readForm(container));
      delete reviews.__new__; $("#new-panel").hidden = true; open.add(created.key);
      toast("已创建，下一条飞书请求即可使用"); await load();
    } else if (action === "cancel") {
      delete reviews.__new__; $("#new-panel").hidden = true;
    } else if (action === "toggle") {
      const state = item.state === "active" ? "disabled" : "active";
      if (state === "disabled" && !confirm(`停用「${item.name}」？停用后飞书不能再热更它（数据保留，可随时启用）。`)) return;
      await api("PUT", `/api/thresholds/${encodeURIComponent(key)}`, { state });
      toast(state === "active" ? "已启用" : "已停用"); await load();
    } else if (action === "delete") {
      if (!confirm(`删除「${item.name}」？删除前的内容保留在修改历史里。`)) return;
      await api("DELETE", `/api/thresholds/${encodeURIComponent(key)}`);
      open.delete(key); toast("已删除"); await load();
    } else if (action === "review") {
      const slot = key || "__new__";
      const record = { ...(item || {}), ...readForm(container) };
      reviews[slot] = { loading: true }; rerenderForm(slot, container);
      try { reviews[slot] = await api("POST", "/api/thresholds/review", record); }
      catch (err) { reviews[slot] = { error: err.message }; }
      rerenderForm(slot, container);
    } else if (action === "history") {
      const events = (await api("GET", `/api/thresholds/events?key=${encodeURIComponent(key)}`)).events;
      container.querySelector("[data-history]").innerHTML = events.length
        ? `<ul>${events.map((e) => `<li>${clock(e.at)} · ${esc(e.operator)} · ${esc(e.field)}：${esc(e.old_value).slice(0, 80)} → ${esc(e.new_value).slice(0, 80)}</li>`).join("")}</ul>`
        : "暂无修改记录";
    }
  } catch (err) { toast(err.message, true); }
  finally { button.disabled = false; }
}

function rerenderForm(slot, container) {
  // keep typed values: patch only the review box
  const box = container.querySelector(".review");
  const html = reviewHtml(reviews[slot]);
  if (box) box.outerHTML = html; else container.querySelector(".actions").insertAdjacentHTML("afterend", html);
}

$("#content").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-act]");
  if (button) { const detail = button.closest("[data-detail]"); act(button.dataset.act, detail.dataset.detail, detail, button); return; }
  const row = e.target.closest(".row");
  if (!row) return;
  const key = row.dataset.key;
  open.has(key) ? open.delete(key) : open.add(key);
  render();
});
$("#content").addEventListener("keydown", (e) => {
  const row = e.target.closest(".row");
  if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); row.click(); }
});
$("#new-panel").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-act]");
  if (button) act(button.dataset.act, "", $("#new-panel .detail"), button);
});
$("#new").onclick = () => {
  const panel = $("#new-panel");
  panel.innerHTML = `<div class="detail"><h3>新增阈值</h3>${formHtml({ name: "", aliases: [], table_name: "", field_name: "", description: "", source: "", notes: "" }, true)}</div>`;
  panel.hidden = false; panel.querySelector("[name=key]").focus();
};
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  tab = b.dataset.tab;
  history.replaceState(null, "", tab === "all" ? "#" : `#state=${tab}`);
  render();
});
$("#search").addEventListener("input", () => render());
$("#refresh").onclick = load;
load();
</script>
</body>
</html>
"""
