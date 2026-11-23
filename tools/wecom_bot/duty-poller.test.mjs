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
