import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { promisify } from 'node:util';

const run = promisify(execFile);
const DATE_HEADERS = new Set(['日期', '值班日期', '排班日期', '时间', 'date']);
const DUTY_HEADERS = new Set(['值班', '值班人', '值班人员', '值班同学', '值班负责人', '主值班', '备值班']);
const HEADER_ROWS = 12;
const MAX_SCAN_ROWS = 2400;

export async function loadSources(path) {
  return validateSources(JSON.parse(await readFile(path, 'utf8')));
}

export function validateSources({ tables }) {
  if (!Array.isArray(tables) || !tables.length) throw new Error('需要配置至少一张表');
  const seen = new Set();
  return tables.map(({ name, description, url }) => {
    if (typeof name !== 'string' || !name.trim() || name.length > 100 || seen.has(name) ||
        typeof description !== 'string' || !description.trim() || description.length > 500 || typeof url !== 'string') {
      throw new Error('表格配置无效');
    }
    const link = new URL(url);
    if (link.origin !== 'https://doc.weixin.qq.com' || link.username || link.password ||
        !/^\/sheet\/[a-zA-Z0-9_-]+\/?$/.test(link.pathname)) throw new Error('需要企微普通表格链接');
    seen.add(name);
    return { name, description, url: link.href, tab: link.searchParams.get('tab') };
  });
}

export function cliEnvironment(configDir) {
  return { PATH: process.env.PATH, LANG: 'C.UTF-8', WECOM_CLI_CONFIG_DIR: configDir };
}

export function makeSheetCli(binary, configDir, signal) {
  const env = cliEnvironment(configDir);
  return async (args) => {
    try {
      const { stdout } = await run(binary, ['sheet', ...args], { env, signal, timeout: 4000, maxBuffer: 1024 * 1024 });
      const payload = JSON.parse(stdout);
      if (payload.errcode != null && payload.errcode !== 0) throw new Error('api error');
      return payload;
    } catch {
      // execFile errors may include the private document link and CLI output.
      throw new Error('企微读表失败，请检查 Worker 的 CLI 授权和表格权限。');
    }
  };
}

function cellText(cell) {
  return (cell?.cell_value?.text ?? cell?.cell_value?.link?.text ?? '').trim();
}

export function isoDate(year, month, day) {
  const key = [year, month, day].map((value, index) => String(value).padStart(index ? 2 : 4, '0')).join('-');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return null;
  const parsed = new Date(key + 'T00:00:00Z');
  return Number.isFinite(+parsed) && parsed.toISOString().slice(0, 10) === key ? key : null;
}

function cellDate(cell) {
  const time = cell?.cell_value?.time;
  if (time) return isoDate(time.year, time.month, time.day);
  const match = cellText(cell).match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$/);
  return match ? isoDate(...match.slice(1)) : null;
}

function col(index) {
  let letters = '';
  for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26)) {
    letters = String.fromCharCode(65 + (value - 1) % 26) + letters;
  }
  return letters;
}

function rowCount(sheet) {
  const lastRow = sheet.data_range?.match(/\$?[A-Z]+\$?(\d+)$/i);
  return lastRow ? Number(lastRow[1]) : sheet.row_count;
}

async function listSheets(cli, source) {
  const { sheets } = await cli(['get', '--docid', source.url]);
  if (!Array.isArray(sheets)) throw new Error('企微返回的工作表列表不可用。');
  return sheets;
}

async function readDutySheet(cli, source, sheet, date) {
  const end = rowCount(sheet);
  if (sheet.data_range === '' || !Number.isSafeInteger(end) || end < 1 || !sheet.sheet_id) return null;
  const range = async a1 => {
    const result = await cli(['ranges', 'get', '--docid', source.url, '--sheet-id', sheet.sheet_id,
      '--range', a1, '--mode', 'default']);
    const grid = result.grid_data;
    if (!Array.isArray(grid?.rows)) throw new Error('企微返回的单元格数据不可用。');
    return grid;
  };

  const width = Math.min(36, Math.max(1, sheet.column_count || 36));
  const header = await range(`A1:${col(width - 1)}${Math.min(end, HEADER_ROWS)}`);
  for (const [index, row] of header.rows.entries()) {
    const labels = (row.values ?? []).map(cell => cellText(cell).replace(/\s+/g, '').toLowerCase());
    const dateCol = labels.findIndex(text => DATE_HEADERS.has(text));
    const dutyCols = labels.flatMap((text, i) =>
      DUTY_HEADERS.has(text) || /^值班人[一二三四五六七八九十0-9]+$/.test(text) ? [i] : []);
    if (dateCol < 0 || !dutyCols.length) continue;

    const first = (header.start_row ?? 0) + index + 2;
    if (end - first + 1 > MAX_SCAN_ROWS) throw new Error('值班表行数超出单次查询范围。');
    const firstColumn = header.start_column ?? 0;
    const letter = col(firstColumn + dateCol);
    const people = new Set();
    let sawDate = false;
    for (let start = first; start <= end; start += 200) {
      const dates = await range(`${letter}${start}:${letter}${Math.min(end, start + 199)}`);
      for (const [offset, day] of dates.rows.entries()) {
        const key = cellDate(day.values?.[0]);
        if (key) sawDate = true;
        if (key !== date) continue;
        const actualRow = (dates.start_row ?? start - 1) + offset + 1;
        for (const position of dutyCols) {
          const address = `${col(firstColumn + position)}${actualRow}`;
          const { rows: [result] } = await range(`${address}:${address}`);
          const person = cellText(result?.values?.[0]);
          if (person) people.add(person);
        }
      }
    }
    return { people: [...people], sawDate };
  }
  return null;
}

export async function lookupDuty(cli, source, date) {
  const sheets = await listSheets(cli, source);
  const candidates = source.tab ? sheets.filter(sheet => sheet.sheet_id === source.tab) : sheets;
  if (!candidates.length) throw new Error('没有找到目标工作表，请核对表格链接。');
  const results = [];
  for (const sheet of candidates) {
    const record = await readDutySheet(cli, source, sheet, date);
    if (record) results.push(record);
  }
  if (!results.length) throw new Error('找不到日期和值班人列，请核对表头。');
  if (!results.some(record => record.sawDate)) throw new Error('没有读到完整日期，请核对值班表日期格式。');
  return [...new Set(results.flatMap(record => record.people))];
}

export async function lookupDutyBySheet(cli, source, date) {
  // A copied link usually pins the tab open in the browser. Feishu queries span the workbook.
  const sheets = await listSheets(cli, source);
  const results = [];
  for (const [index, sheet] of sheets.entries()) {
    const title = sheet.title?.trim() || `工作表 ${index + 1}`;
    let record;
    try {
      record = await readDutySheet(cli, source, sheet, date);
    } catch {
      // Preserve results from other tabs when a tab cannot be read.
    }
    const people = record?.sawDate ? record.people : null;
    results.push({ title, people });
  }
  return results;
}
