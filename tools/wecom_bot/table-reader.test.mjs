import assert from 'node:assert/strict';
import test from 'node:test';
import { tableReader } from './table-reader.mjs';

const sheets = [
  { sheet_id: 'plan', title: '计划', data_range: 'B4:F8' },
  { sheet_id: 'wide', title: '宽表', data_range: 'A1:BA100' },
  { sheet_id: 'empty', title: '空表', data_range: '' },
  { sheet_id: 'denied', title: '不可读取', data_range: 'A1:B10' },
  { sheet_id: 'overflow', title: '额外子表', data_range: 'A1:B10' },
];

const text = value => ({ cell_value: { text: value } });

function fixture() {
  const source = { id: 'registered', name: '测试计划', description: '按版本筛选', url: 'https://doc.weixin.qq.com/sheet/FIXTURE?tab=plan', feishuEnabled: true };
  let tables = [source, { ...source, id: 'private', feishuEnabled: false }];
  const settings = {
    view: () => ({ tables }),
    readerSource: id => ({ source: tables.find(table => table.id === id), configDir: 'fixture-auth' }),
  };
  const calls = [];
  const cliFor = configDir => async args => {
    assert.equal(configDir, 'fixture-auth');
    assert.equal(args[args.indexOf('--docid') + 1], source.url);
    if (args[0] === 'get') return { sheets };
    const sheetId = args[args.indexOf('--sheet-id') + 1];
    const range = args[args.indexOf('--range') + 1];
    calls.push([sheetId, range]);
    if (sheetId === 'denied') throw new Error('fixture denied');
    if (sheetId === 'wide') return { grid_data: { rows: [] } };
    assert.equal(sheetId, 'plan');
    return { grid_data: { start_column: 2, start_row: 4, rows: [
      { values: [text('执行进度'), {}, text('版本')] },
      {},
      { values: [text('完成'), { cell_value: { time: { year: 2026, month: 11, day: 24 } } }, { cell_value: { number: 42 } }] },
    ] } };
  };
  return {
    reader: tableReader({ settings, cliFor }), calls,
    disable: () => { tables = tables.map(table => ({ ...table, feishuEnabled: false })); },
  };
}

test('registered workbooks expose display metadata and only approved local IDs are resolved', async () => {
  const { reader, calls, disable } = fixture();
  const catalog = reader.catalog();
  assert.equal(catalog.length, 1);
  assert.ok(catalog[0].revision);
  assert.deepEqual(catalog.map(({ id, name, description }) => ({ id, name, description })), [
    { id: 'registered', name: '测试计划', description: '按版本筛选' },
  ]);
  await assert.rejects(reader.preview('unknown'), /Table not available/);
  await assert.rejects(reader.preview('private'), /Table not available/);
  disable();
  assert.deepEqual(reader.catalog(), []);
  await assert.rejects(reader.preview('registered'), /Table not available/);
  assert.deepEqual(calls, []);
});

test('a preview stops returning data if sharing is turned off during the read', async () => {
  const { reader, disable } = fixture();
  const pending = reader.preview('registered');
  disable();
  await assert.rejects(pending, /Table settings changed/);
});

test('previews keep real offsets and blank positions, and report unread or partial tabs', async () => {
  const { reader, calls } = fixture();
  const result = await reader.preview('registered');
  assert.equal(result.skippedTabs, 1);
  assert.deepEqual(result.tabs[0], { title: '计划', coverage: ['B4:F8'], rows: [
    { row: 5, column: 3, values: ['执行进度', null, '版本'] },
    { row: 7, column: 3, values: ['完成', '2026-11-24', 42] },
  ], partial: false });
  assert.deepEqual(result.tabs[1].coverage, ['A1:AV12', 'A13:AV24', 'A25:AV36', 'A37:AV48', 'A49:AV50']);
  assert.equal(result.tabs[1].partial, true);
  assert.deepEqual(result.tabs[2].rows, []);
  assert.equal(result.tabs[3].unavailable, true);
  assert.ok(!calls.some(([id]) => id === 'empty' || id === 'overflow'));
  assert.ok(!JSON.stringify(result).includes('doc.weixin'));
});
