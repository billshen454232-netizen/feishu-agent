import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { openTableSettings } from './table-settings.mjs';

const duty = { name: '值班表', url: 'https://doc.weixin.qq.com/sheet/FIXTURE_DUTY', description: '国服值班表', tab: null };
const smoke = { name: '服务器冒烟表', url: 'https://doc.weixin.qq.com/sheet/FIXTURE_SMOKE?tab=s1', description: '查询冒烟结果' };
const scratch = fileURLToPath(new URL('../../tmp_test/', import.meta.url));

async function fixture(t, legacy = true) {
  await mkdir(scratch, { recursive: true });
  const stateDir = await mkdtemp(join(scratch, 'wecom-tables-'));
  t.after(() => rm(stateDir, { recursive: true, force: true }));
  const stateFile = join(stateDir, 'table-settings.json');
  const legacyAuth = join(stateDir, 'legacy-cli');
  if (legacy) await writeFile(stateFile, JSON.stringify({ source: duty, configDir: legacyAuth }));
  const calls = [];
  const denied = new Set();
  const reader = (_, configDir) => async (args) => {
    const url = args[args.indexOf('--docid') + 1];
    calls.push({ configDir, url });
    if (denied.has(url)) throw new Error('读表失败');
    if (args[0] === 'get') return { sheets: [{ sheet_id: 's1' }] };
    return { grid_data: { rows: [] } };
  };
  const process = new EventEmitter();
  const options = { stateDir, cliBinary: 'fixture-cli', reader, spawnCli: () => process };
  const settings = await openTableSettings(options);
  const settingsOnDisk = async () => JSON.parse(await readFile(stateFile, 'utf8'));
  return { settings, calls, denied, process, options, settingsOnDisk, legacyAuth };
}

test('legacy table stays first, adding or editing only verifies the submitted entry', async (t) => {
  const { settings, calls, denied, options, settingsOnDisk, legacyAuth } = await fixture(t);
  assert.deepEqual(settings.view(), {
    tables: [{ ...duty, id: 'default' }], authorized: true, enrollment: 'idle',
  });
  assert.equal(settings.current.source.url, duty.url);

  const added = await settings.save(smoke);
  assert.equal(added.feishuEnabled, false);
  assert.deepEqual(calls, Array(2).fill({ configDir: legacyAuth, url: smoke.url }));
  assert.deepEqual((await settingsOnDisk()).tables.map(table => table.name), [duty.name, smoke.name]);

  denied.add(smoke.url);
  await assert.rejects(settings.save({ ...added, name: '待验证名称' }), /读表失败/);
  assert.equal(settings.view().tables[1].name, smoke.name);
  denied.clear();

  await assert.rejects(settings.save({ ...added, feishuEnabled: 'true' }), /必须是布尔值/);
  const renamed = await settings.save({ ...added, name: '冒烟记录', description: '', feishuEnabled: true });
  const reopened = await openTableSettings(options);
  assert.equal(renamed.id, added.id);
  assert.equal(renamed.description, '');
  assert.equal(reopened.readerSource(added.id).source.feishuEnabled, true);
  assert.deepEqual(reopened.view().tables.map(table => table.name), [duty.name, '冒烟记录']);
  assert.equal(reopened.current.source.url, duty.url);
  assert.equal(reopened.current.configDir, legacyAuth);
});

test('scan renewal keeps shared authorization until all saved tables can be read', async (t) => {
  const { settings, calls, denied, process, settingsOnDisk, legacyAuth } = await fixture(t);
  await settings.save(smoke);
  await settings.authorize();
  process.emit('close', 0);
  assert.equal(settings.view().enrollment, 'ready');
  assert.equal(settings.current.configDir, legacyAuth);

  denied.add(smoke.url);
  await assert.rejects(settings.activateAuthorization(), /新授权无法读取/);
  assert.equal((await settingsOnDisk()).configDir, legacyAuth);

  denied.clear();
  calls.length = 0;
  await settings.activateAuthorization();
  const saved = await settingsOnDisk();
  assert.notEqual(saved.configDir, legacyAuth);
  assert.deepEqual(calls.map(call => call.url), [duty.url, duty.url, smoke.url, smoke.url]);
  assert.ok(calls.every(call => call.configDir === saved.configDir));
  assert.equal(settings.view().enrollment, 'idle');
  assert.equal(settings.current.source.url, duty.url);
});

test('first authorization can be enabled before any table is added', async (t) => {
  const { settings, process, calls } = await fixture(t, false);
  assert.equal(settings.view().authorized, false);
  await assert.rejects(settings.save(duty), /先完成并启用/);
  await settings.authorize();
  process.emit('close', 0);
  await settings.activateAuthorization();
  assert.equal(settings.view().authorized, true);
  assert.deepEqual(settings.view().tables, []);
  assert.deepEqual(calls, []);
  await settings.save(duty);
  assert.equal(settings.view().tables.length, 1);
});
