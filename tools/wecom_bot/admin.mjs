import { randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';

const page = `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>企微表格配置</title>
<style>
* { box-sizing:border-box; } body { margin:0; color:#17283a; background:#f3f6f9; font:16px/1.5 system-ui,sans-serif; }
main { width:min(100% - 32px,560px); margin:48px auto; padding:26px; border:1px solid #d5dee7; border-radius:12px; background:white; }
h1 { margin:0 0 18px; font-size:24px; } label { display:block; margin:14px 0 4px; }
input { width:100%; padding:10px; border:1px solid #8295a8; border-radius:6px; font:inherit; }
button { margin-top:16px; padding:9px 14px; border:1px solid #1b579a; border-radius:6px; background:white; color:#164b83; font:inherit; cursor:pointer; }
button[type=submit] { color:white; background:#1b579a; } button:disabled { opacity:.6; cursor:wait; }
#qr { display:block; width:200px; height:200px; margin-top:12px; } #qr[hidden] { display:none; }
#status { color:#284e73; } #message { min-height:24px; } section { margin-top:20px; border-top:1px solid #d5dee7; }
</style>
</head>
<body><main>
<h1>企微表格配置</h1>
<p id="status" aria-live="polite">正在加载配置…</p>
<form id="settings">
<label for="name">表格名称</label><input id="name" name="name" value="值班表" maxlength="100" required>
<label for="url">企微表格链接</label><input id="url" name="url" type="url" placeholder="https://doc.weixin.qq.com/sheet/…" required>
<label for="description">用途说明</label><input id="description" name="description" placeholder="按日期查询值班人员" maxlength="500">
<p>在机器人所在的飞书群 @机器人查询值班。</p>
<section>
<button id="authorize" type="button">企微扫码授权</button>
<img id="qr" alt="企微授权二维码" hidden>
</section>
<button type="submit">保存并验证</button>
<p id="message" role="status"></p>
</form>
</main>
<script nonce="__NONCE__">
const form = document.querySelector('#settings');
const status = document.querySelector('#status');
const message = document.querySelector('#message');
const qr = document.querySelector('#qr');
let timer;
async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)};
  const res = await fetch(path.slice(1), options);
  const data = await res.json();
  if (!res.ok) throw Error(data.error || '请求失败');
  return data;
}
async function refresh(fill = false) {
  clearTimeout(timer);
  const data = await api('/api/settings');
  if (fill) {
    for (const key of ['url', 'name', 'description']) form.elements[key].value = data[key];
  }
  const tips = {idle:'', waiting:'等待企微扫码', ready:'扫码完成，请保存并验证', failed:'扫码失败或超时'};
  status.textContent = [data.ready ? '表格已配置' : '请完成表格配置', tips[data.enrollment]].filter(Boolean).join(' · ');
  if (data.enrollment === 'waiting') {
    if (qr.hidden) qr.src = 'api/qr?' + Date.now();
    timer = setTimeout(() => refresh().catch(showError), 1500);
  } else {
    qr.hidden = true;
    qr.removeAttribute('src');
  }
}
qr.onload = () => { qr.hidden = false; };
function showError(error) { message.textContent = error.message; }
document.querySelector('#authorize').onclick = async event => {
  const button = event.currentTarget;
  button.disabled = true;
  message.textContent = '';
  try { await api('/api/authorize', {}); await refresh(); }
  catch (error) { showError(error); }
  finally { button.disabled = false; }
};
form.onsubmit = async event => {
  event.preventDefault();
  const button = form.querySelector('[type=submit]');
  button.disabled = true;
  message.textContent = '正在验证读表权限…';
  try {
    await api('/api/settings', Object.fromEntries(new FormData(form)));
    message.textContent = '已保存，表格读权限验证通过。';
    await refresh();
  } catch (error) { showError(error); }
  finally { button.disabled = false; }
};
refresh(true).catch(showError);
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
      if (!['/api/authorize', '/api/settings'].includes(req.url)) return json(404, { error: '接口不存在。' });
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
      if (req.url === '/api/authorize') await settings.authorize();
      else await settings.save(input);
      return json(200, { ok: true });
    } catch (error) {
      const message = error.constructor === Error && !error.code ? error.message : '配置操作失败，请检查填写内容。';
      return json(400, { error: message });
    }
  });
  return server;
}
