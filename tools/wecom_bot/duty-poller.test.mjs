import assert from 'node:assert/strict';
import test from 'node:test';
import { pollDutyOnce } from './duty-poller.mjs';

const source = { name: '测试值班表', url: 'https://doc.weixin.qq.com/sheet/FIXTURE' };
const cell = text => ({ cell_value: { text } });
const grid = values => ({ grid_data: { rows: values.map(values => ({ values })) } });

for (const [date, failure, expected] of [
  ['2026-11-23', false, { status: 'found', people: ['示例值班人'], source: source.name }],
  ['2026-11-24', false, { status: 'empty', people: [], source: source.name }],
  ['2026-11-23', true, { status: 'unavailable' }],
]) {
  test(`duty lookup returns ${expected.status} using only configured sheets`, async () => {
    const calls = [];
    const request = async (path, body) => {
      calls.push([path, body]);
      return path === '/claim' ? { items: [{ id: 'om_example', date }] } : {};
    };
    const cliFor = () => async args => {
      assert.ok(args.includes(source.url));
      if (failure) throw new Error('fixture failure');
      if (args[0] === 'get') return { sheets: [{ sheet_id: 's1', data_range: 'A1:C2', column_count: 3 }] };
      const range = args[args.indexOf('--range') + 1];
      if (range === 'A1:C2') return grid([[cell('日期'), cell('值班人一'), cell('其他列')]]);
      if (range === 'A2:A2') return grid([[cell('2026-11-23')]]);
      assert.equal(range, 'B2:B2');
      return grid([[cell('示例值班人')]]);
    };
    assert.equal(await pollDutyOnce({ request, cliFor, getSettings: () => ({ source, configDir: 'test-dir' }) }), true);
    assert.deepEqual(calls, [['/claim', undefined], ['/om_example/answer', expected]]);
  });
}

for (const overseas of ['found', 'empty', 'unavailable']) {
  test(`workbook query includes both tabs with overseas ${overseas} even when the link pins domestic`, async () => {
    const config = { source: { ...source, url: `${source.url}?tab=cn`, tab: 'cn' }, configDir: 'test-dir' };
    const calls = [];
    const request = async (path, body) => {
      calls.push([path, body]);
      return path === '/claim' ? { items: [{ id: 'om_example', date: '2026-11-23' }] } : {};
    };
    const cliFor = () => async args => {
      assert.equal(args[args.indexOf('--docid') + 1], config.source.url);
      if (args[0] === 'get') {
        return { sheets: [
          { sheet_id: 'cn', title: '国内', data_range: 'A1:B2', column_count: 2 },
          { sheet_id: 'overseas', title: '海外', data_range: 'A1:B3', column_count: 2 },
        ] };
      }
      const tab = args[args.indexOf('--sheet-id') + 1];
      const range = args[args.indexOf('--range') + 1];
      if (tab === 'cn') {
        if (range === 'A1:B2') return grid([[cell('日期'), cell('值班人一')]]);
        if (range === 'A2:A2') return grid([[cell('2026-11-23')]]);
        assert.equal(range, 'B2:B2');
        return grid([[cell('国内值班人')]]);
      }
      assert.equal(tab, 'overseas');
      if (overseas === 'unavailable') throw new Error('fixture read failure');
      if (range === 'A1:B3') return grid([[cell('日期'), cell('值班人一')]]);
      if (range === 'A2:A3') return { grid_data: { ...grid([[
        cell(overseas === 'found' ? '2026-11-23' : '2026-11-24'),
      ]]).grid_data, start_row: 2 } };
      assert.equal(range, 'B3:B3');
      return grid([[cell('海外值班人')]]);
    };
    assert.equal(await pollDutyOnce({ request, cliFor, getSettings: () => config }), true);
    const overseasPeople = { found: ['海外值班人'], empty: [], unavailable: null };
    assert.deepEqual(calls, [['/claim', undefined], ['/om_example/answer', {
      status: 'grouped', source: source.name, sheets: [
        { title: '国内', people: ['国内值班人'] }, { title: '海外', people: overseasPeople[overseas] },
      ],
    }]]);
  });
}
