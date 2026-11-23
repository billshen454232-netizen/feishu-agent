import { fileURLToPath } from 'node:url';
import { createAdminServer } from './admin.mjs';
import { runDutyPoller } from './duty-poller.mjs';
import { makeSheetCli } from './sheet.mjs';
import { openTableSettings } from './table-settings.mjs';

async function start() {
  const stateDir = process.env.STATE_DIRECTORY;
  const password = process.env.WECOM_ADMIN_PASSWORD;
  if (!stateDir || !password || password.length < 20) throw new Error('配置页管理凭据或状态目录缺失。');
  const port = 18409;
  const cliBinary = fileURLToPath(new URL('./node_modules/.bin/wecom-cli', import.meta.url));
  const settings = await openTableSettings({ stateDir, cliBinary });
  const server = createAdminServer({ settings, password, publicOrigin: process.env.WECOM_ADMIN_PUBLIC_ORIGIN });
  server.on('error', () => {
    console.error('企微配置页监听失败。');
    process.exitCode = 1;
  });
  const stop = new AbortController();
  server.listen(port, '127.0.0.1', () => {
    console.info('企微配置页已监听 127.0.0.1:18409');
    const gateway = process.env.FEISHU_DUTY_GATEWAY_ORIGIN;
    const token = process.env.FEISHU_DUTY_TOKEN;
    if (gateway && token) {
      runDutyPoller({
        gateway, token, signal: stop.signal,
        getSettings: () => settings.current,
        cliFor: (configDir, signal) => makeSheetCli(cliBinary, configDir, signal),
        warn: message => console.warn(message),
      }).catch(() => console.warn('值班轮询已退出，请检查 Gateway 配置。'));
    }
  });
  process.once('SIGTERM', () => {
    stop.abort();
    server.close();
  });
}

start().catch(() => {
  console.error('企微后端启动失败，请核对服务配置和状态目录。');
  process.exitCode = 1;
});
