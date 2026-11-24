import { setTimeout as delay } from 'node:timers/promises';
import { isoDate, lookupDutyBySheet } from './sheet.mjs';

export async function pollDutyOnce({ request, getSettings, cliFor, signal }) {
  const { items } = await request('/claim');
  if (!Array.isArray(items) || items.length > 1) throw new Error('Unexpected duty claim');
  if (!items.length) return false;
  const { id, date } = items[0];
  if (typeof id !== 'string' || !/^om_[A-Za-z0-9_-]+$/.test(id) || typeof date !== 'string' ||
      !/^\d{4}-\d{2}-\d{2}$/.test(date) || isoDate(...date.split('-')) !== date) throw new Error('Invalid duty task');

  let result = { status: 'unavailable' };
  const config = getSettings();
  if (config?.source && config.configDir) {
    try {
      const deadline = AbortSignal.timeout(85000);
      const lookupSignal = signal ? AbortSignal.any([signal, deadline]) : deadline;
      const sheets = await lookupDutyBySheet(cliFor(config.configDir, lookupSignal), config.source, date);
      const bounded = sheets.map(({ title, people }) => ({
        title: title.slice(0, 120),
        people: people && people.length <= 32 && people.every(person => person.length <= 120) ? people : null,
      }));
      const source = config.source.name;
      if (bounded.length === 1 && bounded[0].people !== null) {
        const { people } = bounded[0];
        result = { status: people.length ? 'found' : 'empty', people, source };
      } else if (bounded.length > 1 && bounded.length <= 32) {
        result = { status: 'grouped', sheets: bounded, source };
      }
    } catch {
      // CLI errors may contain private document URLs or cell data.
    }
  }
  if (!signal?.aborted) await request(`/${id}/answer`, result);
  return true;
}

export async function runDutyPoller({ gateway, token, getSettings, cliFor, signal, warn }) {
  const url = new URL(gateway);
  if (url.protocol !== 'https:' || url.username || url.password || url.origin !== gateway.replace(/\/$/, '')) {
    throw new Error('Duty Gateway must be an HTTPS origin');
  }
  const endpoint = `${url.origin}/internal/worker/duty`;
  const request = async (path, body) => {
    const response = await fetch(endpoint + path, {
      method: 'POST', redirect: 'error',
      signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    if (!response.ok) {
      const error = new Error('Duty Gateway request failed');
      error.status = response.status;
      throw error;
    }
    return response.json();
  };
  let interval = 1000;
  while (!signal.aborted) {
    try {
      const claimed = await pollDutyOnce({ request, getSettings, cliFor, signal });
      interval = 1000;
      if (claimed) continue;
    } catch (error) {
      if (signal.aborted) return;
      if (error.status === 401) {
        warn('值班领取鉴权失败，请核对 Gateway 与 Worker 的专用凭据。');
        return;
      }
      warn('值班队列请求失败。');
      interval = Math.min(interval * 2, 30000);
    }
    await delay(interval, undefined, { signal }).catch(error => {
      if (!signal.aborted) throw error;
    });
  }
}
