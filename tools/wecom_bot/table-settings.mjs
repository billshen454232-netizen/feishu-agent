import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { cliEnvironment, makeSheetCli, validateSources } from './sheet.mjs';

export async function openTableSettings({ stateDir, cliBinary, reader = makeSheetCli, spawnCli = spawn }) {
  const file = join(stateDir, 'table-settings.json');
  let current = { tables: [] };
  try {
    const saved = JSON.parse(await readFile(file, 'utf8'));
    // Keep the existing table first so the Feishu duty query keeps its source.
    current = {
      tables: saved.tables ?? (saved.source ? [{ ...saved.source, id: 'default' }] : []),
      configDir: saved.configDir,
    };
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
      const child = spawnCli(cliBinary, ['auth', 'init', '--noninteractive', '--no-browser', '--output-qrcode', attempt.qrPath], {
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

  async function verifyTable(source, configDir) {
    const cli = reader(cliBinary, configDir);
    const { sheets } = await cli(['get', '--docid', source.url]);
    const sheet = Array.isArray(sheets) && sheets.find(item => item.sheet_id && (!source.tab || source.tab === item.sheet_id));
    if (!sheet) throw new Error('未找到链接对应的工作表。');
    const sample = await cli(['ranges', 'get', '--docid', source.url, '--sheet-id', sheet.sheet_id,
      '--range', 'A1:A1', '--mode', 'default']);
    if (!sample.grid_data) throw new Error('未能验证单元格读权限。');
  }

  async function persist(next) {
    const pending = join(stateDir, `table-settings-${randomUUID()}.json`);
    await writeFile(pending, JSON.stringify(next), { mode: 0o600, flag: 'wx' });
    await rename(pending, file);
    current = next;
  }

  async function save({ id, url, name, description, feishuEnabled = false }) {
    if (typeof feishuEnabled !== 'boolean') throw new Error('飞书查表开关必须是布尔值。');
    if (saving || enrollment?.status === 'waiting') throw new Error('请等当前操作结束。');
    saving = true;
    try {
      const index = id === undefined ? current.tables.length : current.tables.findIndex(table => table.id === id);
      if (index < 0) throw new Error('找不到要更新的表格，请刷新页面。');
      let source;
      try {
        // Notes are optional in the admin form. Generic index consumers still require a description.
        [source] = validateSources({ tables: [{ name: name?.trim(), url, description: description?.trim() || '企微表格' }] });
        source.description = description?.trim() || '';
      } catch {
        throw new Error('请填写表格名称和有效的企微普通表格链接。');
      }
      if (current.tables.some((table, position) => position !== index && table.name === source.name)) {
        throw new Error('已有同名表格，请换一个名称。');
      }
      const { configDir } = current;
      if (!configDir) throw new Error('请先完成并启用顶部的企微授权。');
      await verifyTable(source, configDir);
      const table = { ...source, id: id ?? randomUUID(), feishuEnabled };
      const tables = [...current.tables];
      tables[index] = table;
      await persist({ configDir, tables });
      return table;
    } finally {
      saving = false;
    }
  }

  async function activateAuthorization() {
    if (saving || enrollment?.status !== 'ready') throw new Error('请先完成企微扫码。');
    saving = true;
    try {
      const { configDir } = enrollment;
      for (const table of current.tables) {
        try {
          await verifyTable(table, configDir);
        } catch {
          throw new Error(`新授权无法读取「${table.name}」，已保留当前授权。`);
        }
      }
      await persist({ ...current, configDir });
      enrollment = undefined;
    } finally {
      saving = false;
    }
  }

  return {
    get current() { return { source: current.tables[0], configDir: current.configDir }; },
    readerSource(id) {
      return { source: current.tables.find(table => table.id === id), configDir: current.configDir };
    },
    view: () => ({
      authorized: Boolean(current.configDir), enrollment: enrollment?.status ?? 'idle',
      tables: current.tables,
    }),
    authorize, qr, save, activateAuthorization,
  };
}
