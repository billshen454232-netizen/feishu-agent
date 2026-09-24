"""Muliu 执行计划的本地防火墙。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .config import MuliuConfig
from .muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
from .muliu_script_catalog import MuliuCallContractError, MuliuCallContracts


class MuliuFirewallError(ValueError):
    """Raised when a generated plan contains a blocked execution request."""


@dataclass(frozen=True)
class FirewallCheckResult:
    plan: MuliuPlan


class MuliuFirewall:
    """Validate safety properties after the registry contract check.

    The catalog remains the only authority for executable ``path + args`` forms.
    This layer checks root containment, size limits, and injection primitives; it
    never infers allowed scripts from file extensions or a second script-name list.
    """

    def __init__(
        self,
        config: MuliuConfig,
        call_contracts: MuliuCallContracts | None = None,
    ) -> None:
        self._config = config
        self._call_contracts = call_contracts
        self._blocked_keywords = tuple(
            keyword.lower() for keyword in config.blocked_keywords if keyword.strip()
        )
        self._blocked_patterns = tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in config.blocked_patterns
            if pattern.strip()
        )

    def validate(self, plan: MuliuPlan) -> FirewallCheckResult:
        if plan.kind is not MuliuPlanKind.OPERATION:
            raise MuliuFirewallError("只有 kind=operation 的请求可以进入 Muliu 防火墙")
        if not plan.steps:
            raise MuliuFirewallError("执行计划至少需要一个步骤")
        if len(plan.steps) > self._config.max_steps:
            raise MuliuFirewallError("执行步骤超过配置上限")

        if self._call_contracts is not None:
            try:
                self._call_contracts.validate(plan)
            except MuliuCallContractError as exc:
                raise MuliuFirewallError(str(exc)) from exc

        for step_number, step in enumerate(plan.steps, start=1):
            self._validate_step(step, step_number)

        return FirewallCheckResult(plan=plan)

    def _validate_step(self, step: MuliuStep, step_number: int) -> None:
        self._validate_path(step.path, step_number)
        self._validate_text(step.description, "步骤 {} 描述".format(step_number))

        if len(step.args) > self._config.max_arguments_per_step:
            raise MuliuFirewallError("步骤 {} 参数数量超过配置上限".format(step_number))

        for argument_index, argument in enumerate(step.args, start=1):
            self._validate_argument(argument, step_number, argument_index)

    def _validate_path(self, path: str, step_number: int) -> None:
        if not path.startswith("/"):
            raise MuliuFirewallError("步骤 {} 脚本路径必须是绝对路径".format(step_number))

        root = PurePosixPath(self._config.script_root)
        candidate = PurePosixPath(path)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise MuliuFirewallError(
                "步骤 {} 脚本路径不在允许目录 {} 中".format(step_number, self._config.script_root)
            ) from exc

        self._validate_text(path, "步骤 {} 脚本路径".format(step_number))

    def _validate_argument(self, argument: str, step_number: int, argument_index: int) -> None:
        label = "步骤 {} 参数 {}".format(step_number, argument_index)
        if len(argument) > self._config.max_argument_length:
            raise MuliuFirewallError("{} 超过长度上限".format(label))
        if "\x00" in argument or "\n" in argument or "\r" in argument:
            raise MuliuFirewallError("{} 包含空字节或换行符".format(label))
        if any(character in argument for character in ("|", "&", ";", "`", "$", "<", ">")):
            raise MuliuFirewallError("{} 包含不允许的 Shell 控制符".format(label))
        if argument in {"-c", "--command", "-e", "--eval"}:
            raise MuliuFirewallError("{} 是不允许的动态代码执行参数".format(label))
        self._validate_text(argument, label)

    def _validate_text(self, value: str, label: str) -> None:
        lowered_value = value.lower()
        for keyword in self._blocked_keywords:
            if keyword in lowered_value:
                raise MuliuFirewallError("{} 命中拦截关键词：{}".format(label, keyword))

        for pattern in self._blocked_patterns:
            if pattern.search(value):
                raise MuliuFirewallError("{} 命中拦截规则：{}".format(label, pattern.pattern))
