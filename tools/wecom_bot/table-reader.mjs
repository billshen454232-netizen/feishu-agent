import { createServer } from 'node:http';
import { isoDate } from './sheet.mjs';

const MAX_TABS = 4;
const MAX_COLUMNS = 48;
const MAX_ROWS = 120;
const MAX_CELLS_PER_TAB = 2400;
const MAX_CELLS_PER_REQUEST = 600;
const MAX_CELL_CHARS = 160;

function columnNumber(label) {
  return [...label.toUpperCase()].reduce((number, letter) => number * 26 + letter.charCodeAt(0) - 64, 0);
}

function columnLabel(number) {
  let label = '';
  for (; number; number = Math.floor((number - 1) / 26)) {
    label = String.fromCharCode(65 + (number - 1) % 26) + label;
  }
  return label;
}

function usedRange(sheet) {
  if (sheet.data_range === '') return null;
  const match = /^\$?([A-Z]{1,4})\$?([1-9]\d{0,7})(?::\$?([A-Z]{1,4})\$?([1-9]\d{0,7}))?$/i.exec(sheet.data_range);
  if (match) {
    const bounds = [columnNumber(match[1]), Number(match[2]), columnNumber(match[3] ?? match[1]), Number(match[4] ?? match[2])];
    if (bounds[0] <= bounds[2] && bounds[1] <= bounds[3]) return { bounds, known: true };
  }
  const { row_count: rows, column_count: columns } = sheet;
  if (!Number.isSafeInteger(rows) || !Number.isSafeInteger(columns) || rows < 1 || columns < 1) return null;
  return { bounds: [1, 1, Math.min(columns, 24), Math.min(rows, 12)], known: false };
}

function cellValue(cell) {
  const value = cell?.cell_value ?? {};
  if (typeof value.text === 'string') return value.text;
  if (typeof value.number === 'number' && Number.isFinite(value.number)) return value.number;
  if (value.time) return isoDate(value.time.year, value.time.month, value.time.day);
  if (typeof value.link?.text === 'string') return value.link.text;
  if (Array.isArray(value.select?.options) && Array.isArray(value.select.value)) {
    return value.select.options.filter(option => value.select.value.includes(option.id)).map(option => option.text).join('、');
  }
  return null;
}

async function previewTab(cli, url, tab) {
  const used = usedRange(tab);
  if (!used) return { coverage: [], rows: [], partial: tab.data_range !== '' };
  const [firstColumn, firstRow, lastColumn, lastRow] = used.bounds;
  const width = Math.min(MAX_COLUMNS, lastColumn - firstColumn + 1);
  const previewLastColumn = firstColumn + width - 1;
  const previewLastRow = Math.min(lastRow, firstRow + Math.min(MAX_ROWS, Math.floor(MAX_CELLS_PER_TAB / width)) - 1);
  const batchSize = Math.floor(MAX_CELLS_PER_REQUEST / width);
  const coverage = [];
  const rows = [];
  let partial = !used.known || previewLastColumn < lastColumn || previewLastRow < lastRow;
  for (let top = firstRow; top <= previewLastRow; top += batchSize) {
    const bottom = Math.min(top + batchSize - 1, previewLastRow);
    const range = `${columnLabel(firstColumn)}${top}:${columnLabel(previewLastColumn)}${bottom}`;
    const result = await cli(['ranges', 'get', '--docid', url, '--sheet-id', tab.sheet_id,
      '--range', range, '--mode', 'default']);
    const grid = result.grid_data;
    if (!Array.isArray(grid?.rows)) throw new Error('Invalid grid data');
    const column = (grid.start_column ?? firstColumn - 1) + 1;
    const startRow = (grid.start_row ?? top - 1) + 1;
    if (!Number.isSafeInteger(column) || column < firstColumn || column > previewLastColumn ||
        !Number.isSafeInteger(startRow) || startRow < top || startRow > bottom) throw new Error('Invalid grid offsets');
    for (const [offset, row] of grid.rows.slice(0, bottom - startRow + 1).entries()) {
      const values = (row?.values ?? []).slice(0, previewLastColumn - column + 1).map(cell => {
        const value = cellValue(cell);
        if (typeof value === 'string' && value.length > MAX_CELL_CHARS) {
          partial = true;
          return value.slice(0, MAX_CELL_CHARS) + '…';
        }
        return value;
      });
      if (values.some(value => value !== null && value !== '')) rows.push({ row: startRow + offset, column, values });
    }
    coverage.push(range);
  }
  return { rows, coverage, partial };
}

export function tableReader({ settings, cliFor }) {
  return {
    catalog() {
      return settings.view().tables.map(({ id, name, description }) => ({ id, name, description }));
    },
    async preview(id) {
      const { source, configDir } = settings.readerSource(id) ?? {};
      if (!source || !configDir) throw new Error('Table not available');
      const cli = cliFor(configDir, AbortSignal.timeout(36000));
      const { sheets } = await cli(['get', '--docid', source.url]);
      if (!Array.isArray(sheets)) throw new Error('Invalid sheet metadata');
      const tabs = [];
      for (const [index, sheet] of sheets.slice(0, MAX_TABS).entries()) {
        if (typeof sheet?.sheet_id !== 'string' || !sheet.sheet_id) continue;
        const title = String(sheet.title || `工作表 ${index + 1}`).slice(0, 120);
        try {
          tabs.push({ title, ...await previewTab(cli, source.url, sheet) });
        } catch {
          tabs.push({ title, coverage: [], rows: [], partial: true, unavailable: true });
        }
      }
      return { name: source.name, tabs, skippedTabs: sheets.length - tabs.length };
    },
  };
}

// This server is bound only to the systemd-managed private Unix socket.
export function createTableReaderServer(reader) {
  let busy = false;
  return createServer({ requestTimeout: 45000, headersTimeout: 5000 }, async (req, res) => {
    const send = (code, body) => {
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify(body));
    };
    if (req.method !== 'GET') return send(405, { error: 'method not allowed' });
    if (req.url === '/tables') return send(200, { tables: reader.catalog() });
    const match = /^\/tables\/([A-Za-z0-9_-]{1,80})$/.exec(req.url);
    if (!match) return send(404, { error: 'unknown route' });
    if (busy) return send(503, { error: 'reader busy' });
    busy = true;
    try {
      send(200, await reader.preview(match[1]));
    } catch {
      send(502, { error: 'table unavailable' });
    } finally {
      busy = false;
    }
  });
}
