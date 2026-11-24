import { randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';

const page = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>企微表格配置</title>
<style>
:root { color-scheme:light; --paper:#f8f7f4; --panel:#fffefa; --ink:#24231f; --muted:#625e56; --line:#e5e0d7; --accent:#81503c; --accent-dark:#6a3e2e; }
* { box-sizing:border-box; }
[hidden] { display:none !important; }
body { margin:0; background:var(--paper); color:var(--ink); font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
main { width:min(680px,calc(100% - 40px)); margin:clamp(36px,9vw,88px) auto 48px; }
h1,h2,p { margin:0; }
h1 { margin-bottom:32px; font-size:clamp(30px,6vw,42px); line-height:1.15; font-weight:650; letter-spacing:-.04em; }
h2 { font-size:18px; font-weight:650; }
.hint { color:var(--muted); font-size:14px; }
.authorization,.tables { border:1px solid var(--line); border-radius:18px; background:var(--panel); }
.authorization { padding:22px 24px; }
.bar,.table-heading,.actions { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.authorization h2 { margin-bottom:5px; }
.auth-status { display:flex; align-items:center; gap:8px; margin-top:14px; font-size:14px; color:var(--muted); }
.auth-status::before { content:''; flex:none; width:8px; height:8px; border-radius:50%; background:#a39a8b; }
.auth-status[data-authorized=true]::before { background:#41785a; }
.auth-detail { margin-top:10px; }
#qr { display:block; width:184px; height:184px; margin-top:12px; padding:8px; border:1px solid var(--line); border-radius:12px; background:white; }
#activate { margin-top:12px; }
.table-heading { margin:32px 0 12px; }
.table-heading h2 { font-size:21px; }
button { padding:8px 14px; min-height:40px; border:1px solid var(--line); border-radius:10px; background:var(--panel); color:var(--ink); font:inherit; font-weight:550; white-space:nowrap; cursor:pointer; }
button:hover:not(:disabled) { background:#f2eee7; }
button.primary { background:var(--accent); border-color:var(--accent); color:white; }
button.primary:hover:not(:disabled) { background:var(--accent-dark); }
button:disabled { opacity:.56; cursor:default; }
:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
.table + .table { border-top:1px solid var(--line); }
summary { display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:62px; padding:16px 24px; border-radius:15px; list-style:none; cursor:pointer; font-weight:600; overflow-wrap:anywhere; }
summary::-webkit-details-marker { display:none; }
summary:hover { background:#f5f2ec; }
summary::after { content:''; flex:none; width:7px; height:7px; margin-right:4px; border-right:1.5px solid var(--muted); border-bottom:1.5px solid var(--muted); transform:rotate(-45deg); }
details[open] summary::after { transform:rotate(45deg); }
form { display:grid; gap:14px; padding:2px 24px 24px; }
label { display:grid; gap:5px; font-size:14px; font-weight:550; }
input,textarea { width:100%; padding:9px 12px; border:1px solid #d8d1c5; border-radius:9px; background:white; color:var(--ink); font:inherit; font-weight:400; }
textarea { min-height:84px; resize:vertical; }
input::placeholder,textarea::placeholder { color:#746c61; }
.actions { justify-content:flex-start; flex-wrap:wrap; }
.notice { font-size:14px; color:var(--muted); }
.notice:empty { display:none; }
.notice[data-error=true] { color:#a12f25; }
#table-message { margin:0 0 10px; }
#auth-message { margin-top:10px; }
#empty { padding:22px 24px; }
@media (max-width:480px) { .authorization,summary,form,#empty { padding-left:16px; padding-right:16px; } .bar { align-items:flex-start; flex-direction:column; } }
</style>
</head>
<body><main>
<h1>企微表格配置</h1>
<section class="authorization" aria-labelledby="auth-heading">
  <div class="bar">
    <div><h2 id="auth-heading">共享企微授权</h2><p class="hint">所有表格共用这份授权。</p></div>
    <button id="authorize" type="button" disabled>企微扫码授权</button>
  </div>
  <p id="auth-status" class="auth-status" aria-live="polite">正在加载…</p>
  <p id="scan-status" class="hint auth-detail" aria-live="polite" hidden></p>
  <img id="qr" alt="企微授权二维码" width="184" height="184" hidden>
  <button id="activate" class="primary" type="button" hidden>验证并启用新授权</button>
  <p id="auth-message" class="notice" role="status"></p>
</section>
<section aria-labelledby="table-heading">
  <div class="table-heading">
    <h2 id="table-heading">表格列表</h2>
    <button id="add-table" class="primary" type="button" disabled>＋ 添加表格</button>
  </div>
  <p id="table-message" class="notice" role="status"></p>
  <div id="tables" class="tables">
    <p id="empty" class="hint" hidden>还没有表格。添加一条并验证读表权限。</p>
  </div>
</section>
<template id="table-template">
  <details class="table">
    <summary></summary>
    <form method="post">
      <label>表格名称<input name="name" maxlength="100" required></label>
      <label>企微表格链接<input name="url" type="url" placeholder="https://doc.weixin.qq.com/sheet/…" required></label>
      <label>用途说明<textarea name="description" rows="2" maxlength="500" placeholder="例如按日期查询值班人员"></textarea></label>
      <div class="actions">
        <button type="submit" class="primary">保存并验证</button>
        <button type="button" data-cancel>取消编辑</button>
      </div>
      <p class="notice" role="status"></p>
    </form>
  </details>
</template>
</main>
<script nonce="__NONCE__">
const authStatus = document.querySelector('#auth-status');
const scanStatus = document.querySelector('#scan-status');
const authMessage = document.querySelector('#auth-message');
const tableMessage = document.querySelector('#table-message');
const authorize = document.querySelector('#authorize');
const activate = document.querySelector('#activate');
const qr = document.querySelector('#qr');
const list = document.querySelector('#tables');
const empty = document.querySelector('#empty');
const add = document.querySelector('#add-table');
const template = document.querySelector('#table-template');
let timer;
let draft;
let authBusy = false;

async function api(path, body) {
  const options = body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败，请稍后再试。');
  return data;
}

function show(target, text, error = false) {
  target.textContent = text;
  target.dataset.error = error;
}

async function refreshAuth(data) {
  clearTimeout(timer);
  data ??= await api('api/settings');
  authStatus.textContent = data.authorized ? '已授权' : '未授权';
  authStatus.dataset.authorized = data.authorized;
  authorize.textContent = data.authorized ? '重新扫码授权' : '企微扫码授权';
  authorize.disabled = authBusy || data.enrollment === 'waiting';
  activate.hidden = data.enrollment !== 'ready';
  activate.disabled = authBusy;
  const tips = { waiting: '请用企业微信扫码。', ready: '扫码完成，验证后启用新授权。', failed: '扫码失败或超时，可以重新扫码。' };
  scanStatus.textContent = tips[data.enrollment] ?? '';
  scanStatus.hidden = !scanStatus.textContent;
  if (data.enrollment === 'waiting') {
    if (qr.hidden) qr.src = 'api/qr?' + Date.now();
    timer = setTimeout(() => refreshAuth().catch(error => show(authMessage, error.message, true)), 1500);
  } else {
    qr.hidden = true;
    qr.removeAttribute('src');
  }
}
qr.onload = () => { qr.hidden = false; };

async function authAction(path, pending, success) {
  authBusy = true;
  authorize.disabled = activate.disabled = true;
  show(authMessage, pending);
  try {
    await api(path, {});
    show(authMessage, success);
  } catch (error) {
    show(authMessage, error.message, true);
  } finally {
    authBusy = false;
    await refreshAuth().catch(error => show(authMessage, error.message, true));
  }
}

authorize.onclick = () => authAction('api/authorize', '正在获取二维码…', '');
activate.onclick = () => authAction('api/authorize/activate', '正在验证各表格的读权限…', '新授权已启用。');

function updateEmpty() {
  empty.hidden = Boolean(list.querySelector('.table'));
}

function addRow(saved) {
  const row = template.content.firstElementChild.cloneNode(true);
  const summary = row.querySelector('summary');
  const form = row.querySelector('form');
  const message = form.querySelector('.notice');
  const submit = form.querySelector('[type=submit]');
  const cancel = form.querySelector('[data-cancel]');
  const fields = form.querySelectorAll('input, textarea');
  const fill = () => {
    summary.textContent = saved?.name ?? '新表格';
    for (const field of fields) field.value = saved?.[field.name] ?? '';
  };
  fill();

  cancel.onclick = () => {
    if (saved) {
      fill();
      show(message, '');
      row.open = false;
      summary.focus();
    } else {
      row.remove();
      draft = undefined;
      updateEmpty();
      add.focus();
    }
  };

  form.onsubmit = async event => {
    event.preventDefault();
    const input = Object.fromEntries(new FormData(form));
    if (saved) input.id = saved.id;
    for (const element of [submit, cancel, ...fields]) element.disabled = true;
    show(message, '正在验证读表权限…');
    show(tableMessage, '');
    try {
      const { table } = await api('api/settings', input);
      if (!saved) draft = undefined;
      saved = table;
      fill();
      row.open = false;
      show(message, '');
      show(tableMessage, '已保存「' + saved.name + '」。');
      summary.focus();
    } catch (error) {
      row.open = true;
      show(message, error.message, true);
    } finally {
      for (const element of [submit, cancel, ...fields]) element.disabled = false;
    }
  };

  list.append(row);
  updateEmpty();
  return row;
}

add.onclick = () => {
  draft ??= addRow();
  draft.open = true;
  draft.querySelector('input').focus();
};

async function init() {
  const data = await api('api/settings');
  data.tables.forEach(addRow);
  updateEmpty();
  add.disabled = false;
  await refreshAuth(data);
}
init().catch(error => {
  authStatus.textContent = '配置加载失败';
  show(authMessage, error.message, true);
});
</script></body></html>`;

export function createAdminServer({ settings, password, publicOrigin }) {
  if (publicOrigin && (!/^https?:\/\//.test(publicOrigin) || new URL(publicOrigin).origin !== publicOrigin)) {
    throw new Error('WECOM_ADMIN_PUBLIC_ORIGIN 必须是网站来源地址，例如 http://jks.example');
  }
  const expected = Buffer.from('Basic ' + Buffer.from(`admin:${password}`).toString('base64'));
  const server = createServer(async (req, res) => {
    const host = `127.0.0.1:${server.address().port}`;
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    const json = (status, data) => {
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(data));
    };
    if (req.headers.host !== host) return json(400, { error: '请通过本地管理地址访问。' });
    const actual = Buffer.from(req.headers.authorization || '');
    if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
      res.setHeader('WWW-Authenticate', 'Basic realm="WeCom setup", charset="UTF-8"');
      return json(401, { error: '请登录配置页。' });
    }
    try {
      if (req.method === 'GET' && req.url === '/') {
        const nonce = randomBytes(18).toString('base64');
        res.writeHead(200, {
          'Content-Type': 'text/html; charset=utf-8',
          'Content-Security-Policy': `default-src 'self'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'`,
        });
        return res.end(page.replace('__NONCE__', nonce));
      }
      if (req.method === 'GET' && req.url === '/api/settings') return json(200, settings.view());
      if (req.method === 'GET' && /^\/api\/qr(?:\?\d+)?$/.test(req.url)) {
        const image = await settings.qr();
        if (!image) return json(404, { error: '二维码尚未生成。' });
        res.writeHead(200, { 'Content-Type': 'image/png' });
        return res.end(image);
      }
      if (req.method !== 'POST') return json(404, { error: '页面不存在。' });
      const allowedOrigins = [`http://${host}`, ...(publicOrigin ? [publicOrigin] : [])];
      if (!allowedOrigins.includes(req.headers.origin) || req.headers['content-type'] !== 'application/json') {
        return json(403, { error: '请通过配置页提交。' });
      }
      if (!['/api/authorize', '/api/authorize/activate', '/api/settings'].includes(req.url)) {
        return json(404, { error: '接口不存在。' });
      }
      let size = 0;
      const chunks = [];
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 8192) {
          json(413, { error: '配置内容过长。' });
          return;
        }
        chunks.push(chunk);
      }
      const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (req.url === '/api/settings') return json(200, { table: await settings.save(input) });
      if (req.url === '/api/authorize') await settings.authorize();
      else await settings.activateAuthorization();
      return json(200, { ok: true });
    } catch (error) {
      const message = error.constructor === Error && !error.code ? error.message : '配置操作失败，请检查填写内容。';
      return json(400, { error: message });
    }
  });
  return server;
}
