from datetime import datetime

import pytest

from src.muliu_plan import MuliuPlan, MuliuPlanError, MuliuStep
from src.muliu_planner import convert_time_arguments
from src.muliu_script_catalog import MuliuScriptCatalog
from src.utils.time_text import BEIJING, TimeTextError, parse_time_text

NOV_7_10AM = 1794016800  # 2026-11-07 10:00 +08:00
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=BEIJING)


@pytest.mark.parametrize("text", [
    "2026-11-07T10:00",
    "2026-11-07 10:00",
    "2026-11-07 10:00:00",
    "2026/11/7 10:00",
    "2026.11.7 10点",
    "2026.11.7 10点整",
    "2026年11月7日10点",
    "2026年11月7号 10点",
    "2026年11月7号，上午10点",
    "11月7号10点",
    "2026年11月7日10时",
    "1794016800",
])
def test_equivalent_texts_convert_to_same_beijing_timestamp(text):
    converted = parse_time_text(text, now=NOW)

    assert converted.timestamp == NOV_7_10AM
    assert converted.display == "2026-11-07 10:00 北京时间"


@pytest.mark.parametrize("text, expected", [
    ("2026年11月7号 下午3点", "2026-11-07 15:00"),
    ("2026-11-07 晚上8点", "2026-11-07 20:00"),
    ("2026.11.7 10点半", "2026-11-07 10:30"),
    ("2026年11月7日10点15分", "2026-11-07 10:15"),
    ("2026-10-28T10:00", "2026-10-28 10:00"),
])
def test_periods_halves_and_minutes(text, expected):
    assert parse_time_text(text, now=NOW).display == expected + " 北京时间"


@pytest.mark.parametrize("text", [
    "2026.11.4号",          # 没有具体时刻：不猜 00:00
    "2026-10-27",
    "明天10点",             # 相对日期：交给 AI 澄清
    "2026-02-30 10:00",     # 不存在的日期
    "2026-11-07 25:00",
    "2026-11-07 10:00 左右",
    "",
])
def test_ambiguous_or_invalid_texts_are_rejected(text):
    with pytest.raises(TimeTextError):
        parse_time_text(text, now=NOW)


def _contracts():
    return MuliuScriptCatalog("config/muliu_script_catalog.md").read_call_contracts()


def test_planner_converts_only_timestamp_slots_and_annotates_description():
    plan = MuliuPlan(
        summary="2018 改开服时间",
        steps=[
            MuliuStep("/home/serverGeneralScript/starttime", ["2018", "2026-11-07T10:00", "--backup", "yes"], "设置开服时间"),
            MuliuStep("/home/serverGeneralScript/starttime", ["2018", "ctime", "--backup", "yes"], "使用当前时间"),
        ],
    )

    converted = convert_time_arguments(plan, _contracts())

    assert converted.steps[0].args == ["2018", str(NOV_7_10AM), "--backup", "yes"]
    assert converted.steps[0].description == "设置开服时间（时间：2026-11-07 10:00 北京时间）"
    assert converted.steps[1] == plan.steps[1]
    _contracts().validate(converted)


def test_planner_keeps_numeric_timestamp_and_is_idempotent():
    plan = MuliuPlan(
        summary="s",
        steps=[MuliuStep("/home/serverGeneralScript/starttime", ["2018", str(NOV_7_10AM), "--backup", "yes"], "d")],
    )

    once = convert_time_arguments(plan, _contracts())
    twice = convert_time_arguments(once, _contracts())

    assert once.steps[0].args[1] == str(NOV_7_10AM)
    assert twice == once


def test_planner_rejects_time_without_hour():
    plan = MuliuPlan(
        summary="s",
        steps=[MuliuStep("/home/serverGeneralScript/starttime", ["2018", "2026-11-04", "--backup", "yes"], "d")],
    )

    with pytest.raises(MuliuPlanError, match="时间无法确定性换算"):
        convert_time_arguments(plan, _contracts())
