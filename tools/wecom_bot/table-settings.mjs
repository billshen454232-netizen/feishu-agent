import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { cliEnvironment, makeSheetCli, validateSources } from './sheet.mjs';

export async function openTableSettings({ stateDir, cliBinary, reader = makeSheetCli }) {
  const file = join(stateDir, 'table-settings.json');
  let current;
  try {
    current = JSON.parse(await readFile(file, 'utf8'));
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  let enrollment;
  let saving = false;

  async function authorize() {
    if (saving || enrollment?.status === 'waiting') throw new Error('请等当前操作结束。');
    const attempt = { status: 'waiting' };
    enrollment = attempt;
    try {
      const attemptDir = await mkdtemp(join(stateDir, 'cli-'));
      attempt.configDir = join(attemptDir, 'config');
      // The CLI forbids ordinary file writes inside its credential directory.
      attempt.qrPath = join(attemptDir, 'enroll.png');
      const child = spawn(cliBinary, ['auth', 'init', '--noninteractive', '--no-browser', '--output-qrcode', attempt.qrPath], {
        cwd: attemptDir, env: cliEnvironment(attempt.configDir), stdio: 'ignore',
      });
      const timer = setTimeout(() => child.kill(), 180000);
      child.once('error', () => {
        clearTimeout(timer);
        attempt.status = 'failed';
      });
      child.once('close', code => {
        clearTimeout(timer);
        attempt.status = code === 0 ? 'ready' : 'failed';
      });
    } catch {
      attempt.status = 'failed';
      throw new Error('无法启动企微扫码。');
    }
  }

  async function qr() {
    if (enrollment?.status !== 'waiting' || !enrollment.qrPath) return null;
    try {
      return await readFile(enrollment.qrPath);
    } catch (error) {
      if (error.code === 'ENOENT') return null;
      throw error;
    }
  }

  async function save({ url, name, description }) {
    if (saving || enrollment?.status === 'waiting') throw new Error('请等当前操作结束。');
    saving = true;
    try {
      let source;
      try {
        [source] = validateSources({ tables: [{ name, url, description: description || '按日期查询值班人员' }] });
      } catch {
        throw new Error('请填写表名和有效的企微普通表格链接。');
      }
      const configDir = enrollment?.status === 'ready' ? enrollment.configDir : current?.configDir;
      if (!configDir) throw new Error('请先完成企微扫码授权。');
      const cli = reader(cliBinary, configDir);
      const { sheets } = await cli(['get', '--docid', source.url]);
      const sheet = Array.isArray(sheets) && sheets.find(item => item.sheet_id && (!source.tab || source.tab === item.sheet_id));
      if (!sheet) throw new Error('未找到链接对应的工作表。');
      const sample = await cli(['ranges', 'get', '--docid', source.url, '--sheet-id', sheet.sheet_id,
        '--range', 'A1:A1', '--mode', 'default']);
      if (!sample.grid_data) throw new Error('未能验证单元格读权限。');
      const next = { source, configDir };
      const pending = join(stateDir, `table-settings-${randomUUID()}.json`);
      await writeFile(pending, JSON.stringify(next), { mode: 0o600, flag: 'wx' });
      await rename(pending, file);
      current = next;
      if (enrollment?.status === 'ready') enrollment = undefined;
    } finally {
      saving = false;
    }
  }

  return {
    get current() { return current; },
    view: () => ({
      ready: Boolean(current), enrollment: enrollment?.status ?? 'idle',
      url: current?.source.url ?? '', name: current?.source.name ?? '值班表',
      description: current?.source.description ?? '',
    }),
    authorize, qr, save,
  };
}
