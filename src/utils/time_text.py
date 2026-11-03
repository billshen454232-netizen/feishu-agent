"""Deterministic time-text → Unix timestamp conversion for planner output.

The planner must not do timezone arithmetic itself: it writes the time it read
from the user (preferably ``YYYY-MM-DDTHH:MM``), and this module converts it in
Beijing time (UTC+8, all test servers use it). Ambiguous inputs — no hour,
unknown format, impossible dates — raise :class:`TimeTextError` so the plan is
rejected/clarified instead of guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")

_TIMESTAMP_PATTERN = re.compile(r"[0-9]{9,12}")
_DATE_PATTERN = re.compile(
    r"^(?:(?P<year>\d{4})\s*[-/.年]\s*)?(?P<month>\d{1,2})\s*[-/.月]\s*(?P<day>\d{1,2})\s*[日号]?"
)
_PERIOD_PATTERN = re.compile(r"^(?P<period>凌晨|早上|上午|中午|下午|晚上)")
_CLOCK_PATTERN = re.compile(
    r"^(?P<hour>\d{1,2})\s*(?:[:：]\s*(?P<minute>\d{1,2})(?:\s*[:：]\s*(?P<second>\d{1,2}))?|点\s*(?:(?P<half>半)|(?P<cn_minute>\d{1,2})\s*分?)?|时\s*(?:(?P<h_minute>\d{1,2})\s*分?)?)"
)


class TimeTextError(ValueError):
    """The text cannot be converted to a timestamp without guessing."""


@dataclass(frozen=True)
class ConvertedTime:
    timestamp: int
    display: str  # "2026-11-07 10:00 北京时间"


def parse_time_text(text: str, *, now: datetime | None = None) -> ConvertedTime:
    """Convert a Beijing-time text (or an existing Unix timestamp) to a timestamp.

    Accepted: ``2026-11-07T10:00``, ``2026-11-07 10:00:00``, ``2026/11/7 10:00``,
    ``2026.11.7 10点``, ``2026年11月7日10点30分``, ``2026年11月7号 下午3点``,
    ``11月7号10点半`` (year defaults to the current Beijing year) and plain
    9–12 digit timestamps (returned unchanged).
    """
    raw = (text or "").strip()
    if _TIMESTAMP_PATTERN.fullmatch(raw):
        timestamp = int(raw)
        return ConvertedTime(timestamp, _display(timestamp))

    rest = raw.replace("T", " ", 1) if re.match(r"^\d{4}-\d{1,2}-\d{1,2}T", raw) else raw
    date_match = _DATE_PATTERN.match(rest)
    if date_match is None:
        raise TimeTextError("无法识别的日期：{!r}".format(raw))
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    year = int(date_match.group("year") or current.year)
    month = int(date_match.group("month"))
    day = int(date_match.group("day"))

    rest = rest[date_match.end():].strip().lstrip(",，").strip()
    period = ""
    period_match = _PERIOD_PATTERN.match(rest)
    if period_match:
        period = period_match.group("period")
        rest = rest[period_match.end():].strip()
    clock_match = _CLOCK_PATTERN.match(rest)
    if clock_match is None:
        raise TimeTextError("缺少具体时刻（如 10点 / 10:00）：{!r}".format(raw))
    trailing = rest[clock_match.end():].strip()
    if trailing and trailing not in {"整", "钟", "北京时间", "(北京时间)", "（北京时间）"}:
        raise TimeTextError("时间文本含无法识别的内容 {!r}：{!r}".format(trailing, raw))

    hour = int(clock_match.group("hour"))
    minute_text = clock_match.group("minute") or clock_match.group("cn_minute") or clock_match.group("h_minute")
    minute = 30 if clock_match.group("half") else int(minute_text or 0)
    second = int(clock_match.group("second") or 0)
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif period == "凌晨" and hour == 12:
        hour = 0

    try:
        moment = datetime(year, month, day, hour, minute, second, tzinfo=BEIJING)
    except ValueError as exc:
        raise TimeTextError("日期或时刻不存在：{!r}".format(raw)) from exc
    timestamp = int(moment.timestamp())
    return ConvertedTime(timestamp, _display(timestamp))


def _display(timestamp: int) -> str:
    return "{} 北京时间".format(datetime.fromtimestamp(timestamp, BEIJING).strftime("%Y-%m-%d %H:%M"))
