"""Execution route admin page (served at /routes): per capability and region, Muliu or Server Manager API."""

ROUTES_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>执行路线</title>
<style>
  :root {
    --paper: #f3f5f7; --panel: #fff; --ink: #1f2a36; --ink-2: #4b5866; --ink-3: #7a8693; --line: #dfe4e9;
    --accent: #2f5d8a; --accent-soft: #e8eff6; --ok: #2e7d4f; --ok-soft: #e8f5ee; --api: #6a3fb0; --api-soft: #f1ebfa;
    --bad: #b8322a; --warn: #a86a12; --warn-soft: #fff4e5; --radius: 6px;
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  body { margin: 0; background: var(--paper); color: var(--ink);
    font: 14px/1.6 "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", "Noto Sans CJK SC", system-ui, sans-serif; }
  button { font: inherit; color: inherit; }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  code { font: 12px/1.5 ui-monospace, "Cascadia Mono", Consolas, monospace; }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 0 28px; }
  header { background: var(--panel); border-bottom: 1px solid var(--line); }
  header .wrap { padding-top: 18px; padding-bottom: 16px; }
  .top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  h1 { font-size: 20px; margin: 0; font-weight: 650; }
  .back { font-size: 13px; color: var(--accent); text-decoration: none; border: 1px solid var(--line); border-radius: var(--radius); padding: 3px 10px; }
  .principle { margin: 10px 0 0; font-size: 13px; color: var(--ink-2); max-width: 100ch; }
  .banner { margin-top: 12px; background: var(--warn-soft); border: 1px solid #f3d6a8; border-radius: var(--radius); padding: 8px 12px; font-size: 13px; }
  main .wrap { padding-top: 18px; padding-bottom: 48px; }
  h2 { font-size: 14px; margin: 22px 0 8px; color: var(--ink-2); font-weight: 600; }
  .list { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
  .row { display: grid; grid-template-columns: minmax(200px, 1.3fr) minmax(260px, 2fr) 150px 150px; gap: 14px; align-items: center;
    padding: 12px 16px; border-top: 1px solid var(--line); }
  .row:first-child { border-top: 0; }
  .head { font-size: 12px; color: var(--ink-3); background: #fafbfc; padding: 8px 16px; }
  .name { font-weight: 600; }
  .contract { font-size: 12px; color: var(--ink-3); }
  .seq { font-size: 13px; }
  .exc { font-size: 12px; color: var(--ink-3); margin-top: 2px; }
  .seg { display: inline-flex; border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
  .seg button { border: 0; background: var(--panel); padding: 4px 12px; cursor: pointer; font-size: 13px; color: var(--ink-2); }
  .seg button + button { border-left: 1px solid var(--line); }
  .seg button[aria-pressed=true][data-backend=muliu] { background: var(--ok-soft); color: var(--ok); font-weight: 600; }
  .seg button[aria-pressed=true][data-backend=api] { background: var(--api-soft); color: var(--api); font-weight: 600; }
  .seg button:disabled { cursor: wait; opacity: .6; }
  .who { font-size: 11px; color: var(--ink-3); margin-top: 3px; white-space: nowrap; }
  .static { display: grid; grid-template-columns: minmax(200px, 1.3fr) 2fr 1fr; gap: 14px; padding: 8px 16px; border-top: 1px solid var(--line); font-size: 13px; color: var(--ink-2); }
  .static:first-child { border-top: 0; }
  .pill { font-size: 12px; color: var(--ok); }
  .history { margin-top: 22px; font-size: 12px; color: var(--ink-2); }
  .history li { margin-bottom: 3px; }
  .toast { position: fixed; right: 24px; bottom: 24px; background: var(--ink); color: #fff; padding: 10px 14px; border-radius: var(--radius); font-size: 13px; opacity: 0; transition: opacity .18s; pointer-events: none; }
  .toast.show { opacity: 1; } .toast.error { background: var(--bad); }
  .state-box { background: var(--panel); border: 1px dashed var(--line); border-radius: var(--radius); padding: 36px; text-align: center; color: var(--ink-2); }
  @media (max-width: 900px) { .row { grid-template-columns: 1fr 1fr; } .row .seqcol { grid-column: 1 / 3; } .static { grid-template-columns: 1fr; } }
  @media (prefers-reduced-motion: reduce) { .toast { transition: none; } }
</style>
</head>
<body>
<header><div class="wrap">
  <div class="top">
    <h1>执行路线</h1>
    <a class="back" href="/">审批台</a>
    <a class="back" href="/plan">项目计划</a>
  </div>
  <p class="principle">每项能力、每个区域单独选择走<b>木流</b>还是 <b>Server Manager API</b>，默认全部木流。切换在下一次 Worker 领取任务时生效；
  Worker 还会再核对一次（翻译不了或服号不在 API 清单里就自动留在木流）。建议顺序：只起 game → 起服 → 关服 → 清档，每项先开一个区域测试。</p>
  <div class="banner">真正走 API 还需要 Worker 的 <code>config.json</code> 开启 <code>server_manager.enabled</code> 并填写两个区域地址；未开启时这里的开关不生效、一律木流（Worker 日志会提示）。</div>
</div></header>
<main><div class="wrap">
  <div id="content"><div class="state-box">加载中…</div></div>
  <h2>修改历史</h2>
  <ul class="history" id="history"></ul>
</div></main>
<div class="toast" id="toast" role="status"></div>

<script>
const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const REGION = { cn: "国服", overseas: "海外" };
const BACKEND = { muliu: "木流", api: "API" };
let items = [];

async function api(method, url, body) {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-Admin-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) { let d = res.statusText; try { d = (await res.json()).detail || d; } catch (_) {} throw new Error(`${res.status} ${d}`); }
  return res.json();
}
let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.classList.toggle("error", err); t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), err ? 5000 : 2000);
}
const clock = (ts) => { if (!ts) return ""; const d = new Date(ts * 1000); const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };

async function load() {
  try {
    items = (await api("GET", "/api/routes")).items;
    render();
    const events = (await api("GET", "/api/routes/events")).events;
    $("#history").innerHTML = events.length ? events.map((e) =>
      `<li>${clock(e.at)} · ${esc(e.operator)} · <code>${esc(e.contract)}</code> ${REGION[e.region] || esc(e.region)}：${BACKEND[e.old_backend]} → ${BACKEND[e.new_backend]}</li>`).join("")
      : "<li>暂无修改</li>";
  } catch (err) {
    $("#content").innerHTML = `<div class="state-box">加载失败：${esc(err.message)}</div>`;
  }
}

function seg(item, region) {
  const state = item.regions[region];
  return `<div><div class="seg" role="group" aria-label="${REGION[region]}">${["muliu", "api"].map((b) =>
    `<button data-contract="${esc(item.contract)}" data-region="${region}" data-backend="${b}" aria-pressed="${state.backend === b}">${BACKEND[b]}</button>`).join("")}</div>
    <div class="who">${state.updated_at ? `${clock(state.updated_at)} · ${esc(state.updated_by)}` : "默认"}</div></div>`;
}

function render() {
  const live = items.filter((i) => i.switchable), fixed = items.filter((i) => !i.switchable);
  $("#content").innerHTML = `
    <h2>可切换（一期）</h2>
    <div class="list"><div class="row head"><span>能力</span><span class="seqcol">走 API 时的提交序列 / 不走 API 的情况</span><span>国服</span><span>海外</span></div>
    ${live.map((i) => `<div class="row">
        <div><div class="name">${esc(i.label)}</div><div class="contract"><code>${esc(i.contract)}</code></div></div>
        <div class="seqcol"><div class="seq"><code>${esc(i.sequence)}</code></div><div class="exc">留在木流：${esc(i.exceptions)}</div></div>
        ${seg(i, "cn")}${seg(i, "overseas")}
      </div>`).join("")}</div>
    <h2>暂无 API 对应（始终木流，${fixed.length} 项）</h2>
    <div class="list">${fixed.map((i) => `<div class="static"><code>${esc(i.contract)}</code><code>${esc(i.path.split("/").pop())} ${esc(i.args)}</code><span class="pill">木流 · ${esc(i.risk)}</span></div>`).join("")}</div>`;
}

$("#content").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-backend]");
  if (!b || b.getAttribute("aria-pressed") === "true") return;
  const { contract, region, backend } = b.dataset;
  const item = items.find((i) => i.contract === contract);
  if (backend === "api" && !confirm(`把「${item.label}」${REGION[region]}切到 API？\n下一条该能力的飞书请求将走 Server Manager（Worker 核对不通过时自动留在木流）。`)) return;
  b.parentElement.querySelectorAll("button").forEach((x) => x.disabled = true);
  try {
    await api("PUT", `/api/routes/${encodeURIComponent(contract)}`, { region, backend });
    toast(`${item.label} ${REGION[region]}：已切到${BACKEND[backend]}`);
    await load();
  } catch (err) { toast(err.message, true); await load(); }
});
load();
</script>
</body>
</html>
"""
