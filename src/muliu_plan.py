"""AI 生成的 Muliu 请求决策与严格 JSON 解析。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MuliuPlanError(ValueError):
    """Raised when the AI response is not a valid Muliu request decision."""


class MuliuPlanKind(str, Enum):
    """The bounded outcomes available to the registry-aware planner."""

    OPERATION = "operation"
    KNOWLEDGE = "knowledge"
    CLARIFY = "clarify"
    REJECT = "reject"


@dataclass(frozen=True)
class MuliuStep:
    path: str
    args: list[str]
    description: str


@dataclass(frozen=True)
class MuliuPlan:
    summary: str
    steps: list[MuliuStep]
    # Older persisted confirmation plans did not carry ``kind``. Keep an operation
    # default so they remain parseable while new AI responses must declare it.
    kind: MuliuPlanKind = MuliuPlanKind.OPERATION


def parse_plan(
    raw_text: str,
    max_steps: int = 10,
    *,
    allow_legacy_kind: bool = True,
) -> MuliuPlan:
    """Parse exactly one bounded planner decision returned by the AI.

    ``operation`` is the only kind allowed to contain steps. ``knowledge`` answers
    a capability or script question, while ``clarify`` and ``reject`` explain why a
    request cannot yet become a registered call. None of the non-operation forms can
    reach Muliu.
    """
    try:
        raw_plan = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise MuliuPlanError("AI 未返回合法 JSON 请求决策") from exc

    if not isinstance(raw_plan, dict):
        raise MuliuPlanError("请求决策必须是 JSON 对象")

    _reject_unknown_fields(raw_plan, {"kind", "summary", "steps"}, "请求决策")
    summary = _require_short_text(raw_plan.get("summary"), "summary")
    raw_steps = raw_plan.get("steps")
    if not isinstance(raw_steps, list):
        raise MuliuPlanError("steps 必须是数组")
    if len(raw_steps) > max_steps:
        raise MuliuPlanError("steps 最多允许 {} 步".format(max_steps))

    kind = _parse_kind(
        raw_plan.get("kind"),
        raw_steps,
        allow_legacy_kind=allow_legacy_kind,
    )
    steps = [_parse_step(raw_step, index) for index, raw_step in enumerate(raw_steps, start=1)]
    if kind is MuliuPlanKind.OPERATION and not steps:
        raise MuliuPlanError("kind=operation 时 steps 必须至少包含一个步骤")
    if kind is not MuliuPlanKind.OPERATION and steps:
        raise MuliuPlanError("kind={} 时 steps 必须为空数组".format(kind.value))
    return MuliuPlan(summary=summary, steps=steps, kind=kind)


def _parse_kind(
    raw_kind: Any,
    raw_steps: list[Any],
    *,
    allow_legacy_kind: bool,
) -> MuliuPlanKind:
    """Require decision kind for model output while retaining stored-plan migration."""
    if raw_kind is None:
        if not allow_legacy_kind:
            raise MuliuPlanError("kind 必须是字符串")
        return MuliuPlanKind.OPERATION if raw_steps else MuliuPlanKind.REJECT
    if not isinstance(raw_kind, str):
        raise MuliuPlanError("kind 必须是字符串")
    try:
        return MuliuPlanKind(raw_kind)
    except ValueError as exc:
        allowed = ", ".join(kind.value for kind in MuliuPlanKind)
        raise MuliuPlanError("kind 必须是以下值之一：{}".format(allowed)) from exc


def _parse_step(raw_step: Any, index: int) -> MuliuStep:
    if not isinstance(raw_step, dict):
        raise MuliuPlanError("steps[{}] 必须是 JSON 对象".format(index))

    _reject_unknown_fields(raw_step, {"path", "args", "description"}, "steps[{}]".format(index))
    path = _require_short_text(raw_step.get("path"), "steps[{}].path".format(index), max_length=512)
    description = _require_short_text(raw_step.get("description"), "steps[{}].description".format(index))
    args = _parse_args(raw_step.get("args"), index)
    return MuliuStep(path=path, args=args, description=description)


def _parse_args(raw_args: Any, index: int) -> list[str]:
    if not isinstance(raw_args, list):
        raise MuliuPlanError("steps[{}].args 必须是数组".format(index))
    if len(raw_args) > 32:
        raise MuliuPlanError("steps[{}].args 最多允许 32 项".format(index))

    args: list[str] = []
    for argument_index, value in enumerate(raw_args):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise MuliuPlanError(
                "steps[{}].args[{}] 必须是字符串或数字".format(index, argument_index)
            )
        argument = str(value)
        if not argument:
            raise MuliuPlanError("steps[{}].args[{}] 不能为空".format(index, argument_index))
        if len(argument) > 1024:
            raise MuliuPlanError(
                "steps[{}].args[{}] 不能超过 1024 个字符".format(index, argument_index)
            )
        args.append(argument)
    return args


def _require_short_text(value: Any, field_name: str, max_length: int = 120) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MuliuPlanError("{} 必须是非空字符串".format(field_name))
    text = value.strip()
    if len(text) > max_length:
        raise MuliuPlanError("{} 不能超过 {} 个字符".format(field_name, max_length))
    return text


def _reject_unknown_fields(value: dict[str, Any], allowed_fields: set[str], field_name: str) -> None:
    unexpected_fields = set(value) - allowed_fields
    if unexpected_fields:
        raise MuliuPlanError(
            "{} 包含不允许的字段：{}".format(field_name, ", ".join(sorted(unexpected_fields)))
        )
