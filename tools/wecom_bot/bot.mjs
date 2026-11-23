import { isoDate, lookupDuty } from './sheet.mjs';

export function questionDate(text, now = new Date()) {
  if (!/值班|排班/.test(text) || /改|调整|删除|新增|写入|更新/.test(text)) return null;
  const dates = [...text.matchAll(/(?<!\d)(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日|号)?(?!\d)/g)];
  const relative = [...text.matchAll(/今天|明天|昨天/g)].map(match => match[0]);
  if (dates.length + relative.length !== 1) return null;
  if (dates.length) return isoDate(...dates[0].slice(1));
  const days = { '今天': 0, '明天': 1, '昨天': -1 };
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date(+now + days[relative[0]] * 86400000));
}

export function makeDutyHandler({ getSettings, cliFor, reply, warn, now = Date.now }) {
  const seen = new Map();
  let busy = false;
  return async (frame) => {
    const message = frame.body;
    const { allowedUser, source, configDir } = getSettings() ?? {};
    if (!allowedUser || message?.chattype !== 'single' || message.from?.userid !== allowedUser) return;
    if (!message.msgid || typeof message.text?.content !== 'string') return;
    const timestamp = now();
    for (const [id, expiry] of seen) {
      if (expiry <= timestamp) seen.delete(id);
    }
    if (seen.has(message.msgid) || seen.size >= 128) return;
    seen.set(message.msgid, timestamp + 300000);

    const date = questionDate(message.text.content.slice(0, 300), new Date(timestamp));
    if (!date) return reply(frame, '请查询一个日期的值班安排，例如「今天谁值班？」。');
    if (busy) return reply(frame, '上一条值班查询还在处理中，请稍后再试。');
    busy = true;
    try {
      let answer;
      try {
        const people = await lookupDuty(cliFor(configDir), source, date);
        answer = people.length ? `${date} 值班人员：${people.join('、')}\n来源：${source.name}` :
          `${source.name}中没有查到 ${date} 的值班记录。`;
      } catch {
        warn('值班表查询失败');
        answer = '值班表暂时无法读取，请查看配置页中的企微授权和表格链接。';
      }
      await reply(frame, answer);
    } finally {
      busy = false;
    }
  };
}
