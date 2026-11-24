"""Handle read-only WeCom duty queries from Feishu group mentions."""
from __future__ import annotations

import hmac
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from .team_kb_questions import ANSWER_TIMEOUT_SECONDS, CLAIM_TIMEOUT_SECONDS, TeamKbQuestionStore
from .utils.time_text import BEIJING

logger = logging.getLogger(__name__)
_DUTY_SOURCE = 'wecom_duty'
_DAY = r'今天|明天|昨天|[0-9]{4}[-/.年][0-9]{1,2}[-/.月][0-9]{1,2}(?:日|号)?'
_QUERY = re.compile(rf'(?:({_DAY})\s*(?:谁值班|值班是谁)|(?:查\s*)?值班\s*({_DAY}))[？?。]?$')
_UNAVAILABLE = '值班表暂时无法读取，请稍后再查。'


def resolve_day(day: str, now: datetime) -> date:
    relative = {'昨天': -1, '今天': 0, '明天': 1}
    if day in relative:
        return now.astimezone(BEIJING).date() + timedelta(days=relative[day])
    year, month, day_of_month = map(int, re.findall(r'\d+', day))
    return date(year, month, day_of_month)


class DutyQueries:
    def __init__(self, store: TeamKbQuestionStore, feishu_client: Any, *, token: str,
                 event_token: str, bot_open_id: str) -> None:
        self.store = store
        self.feishu_client = feishu_client
        self.token = token
        self.event_token = event_token
        self.bot_open_id = bot_open_id

    @classmethod
    def from_env(cls, store: TeamKbQuestionStore, feishu_client: Any, *, event_token: str) -> DutyQueries:
        token = os.getenv('FEISHU_DUTY_TOKEN', '').strip()
        bot_open_id = os.getenv('FEISHU_DUTY_BOT_OPEN_ID', '').strip()
        return cls(store, feishu_client, token=token, event_token=event_token, bot_open_id=bot_open_id)

    def worker_authorized(self, authorization: Optional[str]) -> bool:
        return bool(self.token and hmac.compare_digest((authorization or '').encode(), f'Bearer {self.token}'.encode()))

    async def accept(self, *, text: str, chat_type: str, chat_id: str, message_id: str,
                     mentions: list[dict[str, Any]], verification_token: Any) -> bool:
        match = _QUERY.fullmatch(text.strip())
        if not match or not self.token:
            return False
        tagged = self.bot_open_id and any(
            isinstance(mention, dict) and isinstance(mention.get('id'), dict)
            and mention['id'].get('open_id') == self.bot_open_id for mention in mentions
        )
        if not (self.event_token and verification_token == self.event_token and tagged and chat_type == 'group'
                and chat_id and re.fullmatch(r'om_[A-Za-z0-9_-]+', message_id)):
            return True
        now = datetime.now(BEIJING)
        try:
            date_text = resolve_day(match[1] or match[2], now).isoformat()
        except ValueError:
            await self._reply(message_id, '请填写有效的值班查询日期。')
            return True
        self.store.ask(message_id, chat_id, date_text, now=int(now.timestamp()), source=_DUTY_SOURCE)
        return True

    def claim(self) -> list[dict[str, str]]:
        if not self.event_token or not self.bot_open_id:
            return []
        items = self.store.claim(limit=1, source=_DUTY_SOURCE, max_age=CLAIM_TIMEOUT_SECONDS)
        return [{'id': item['id'], 'date': item['text']} for item in items]

    async def finish(self, question_id: str, payload: dict[str, Any]) -> bool:
        status = payload.get('status')
        if status not in ('found', 'empty', 'grouped', 'unavailable'):
            raise ValueError('invalid duty result status')
        people: list[str] = []
        lines: list[str] = []
        title = ''
        has_readable_sheet = False
        if status == 'found':
            people = self._people(payload.get('people'), minimum=1)
        elif status == 'grouped':
            sheets = payload.get('sheets')
            if not isinstance(sheets, list) or not 2 <= len(sheets) <= 32:
                raise ValueError('invalid duty sheets')
            for sheet in sheets:
                if not isinstance(sheet, dict) or 'people' not in sheet:
                    raise ValueError('invalid duty sheet')
                sheet_title = self._field(sheet.get('title'))
                if sheet['people'] is None:
                    text = '暂时无法查询'
                else:
                    names = self._people(sheet['people'])
                    text = '、'.join(names) if names else '未查到值班记录'
                    has_readable_sheet = True
                lines.append(f'{sheet_title}：{text}')
        if status != 'unavailable':
            title = self._field(payload.get('source'))
        ok = has_readable_sheet if status == 'grouped' else status != 'unavailable'
        item = self.store.finish(question_id, {'ok': ok, 'text': '', 'sources': [],
                                               'model': '', 'error': ''}, source=_DUTY_SOURCE,
                                 max_age=ANSWER_TIMEOUT_SECONDS)
        if item is None:
            return False
        if not self.event_token:
            return True
        if status == 'found':
            reply = f"{item['text']} 值班人员：{'、'.join(people)}\n来源：{title}"
        elif status == 'empty':
            reply = f"{title}中没有查到 {item['text']} 的值班记录。"
        elif status == 'grouped':
            reply = '\n'.join([f"{item['text']} 值班人员", *lines, f'来源：{title}'])
        else:
            reply = _UNAVAILABLE
        await self._reply(item['question_id'], reply)
        return True

    async def expire(self) -> None:
        for item in self.store.expire(source=_DUTY_SOURCE):
            if self.event_token and self.token:
                await self._reply(item['question_id'], _UNAVAILABLE)

    async def _reply(self, message_id: str, text: str) -> None:
        try:
            await self.feishu_client.reply_text(message_id, text)
        except Exception:  # The upstream response may include the reply body.
            logger.warning('duty reply failed')

    @classmethod
    def _people(cls, values: Any, *, minimum: int = 0) -> list[str]:
        if not isinstance(values, list) or not minimum <= len(values) <= 32:
            raise ValueError('invalid duty result list')
        return [cls._field(value) for value in values]

    @staticmethod
    def _field(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            raise ValueError('invalid duty result field')
        return ' '.join(value.split())
