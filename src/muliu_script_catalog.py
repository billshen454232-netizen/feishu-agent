"""读取并校验提供给 AI 的 Muliu 脚本调用合同。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .muliu_plan import MuliuPlan, MuliuPlanError, MuliuStep


_CONTRACT_BLOCK_PATTERN = re.compile(
    r"<!--\s*MULIU_CALL_CONTRACTS\s*(.*?)\s*MULIU_CALL_CONTRACTS\s*-->",
    re.DOTALL,
)
_VARIABLE_TEMPLATE_PATTERN = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_RUNNER_VALUES = frozenset({"bash", "python3.7"})
_RISK_VALUES = frozenset({"read", "write"})


class MuliuScriptCatalogError(RuntimeError):
    """Raised when the human-readable execution catalog is unavailable."""


class MuliuCallContractError(MuliuPlanError):
    """Raised when a plan is outside the catalog's explicitly registered calls."""


@dataclass(frozen=True)
class MuliuCallContract:
    """One exact path-and-arguments template registered for robot execution."""

    name: str
    path: str
    args: tuple[str, ...]
    variable_patterns: dict[str, re.Pattern[str]]
    runner: str
    risk: str
    # Gateway/Worker-only routing metadata. It is intentionally not emitted to
    # the GS-1 argv manifest because it must never become a script argument.
    route_scopes: frozenset[str] = frozenset()

    def matches(self, step: MuliuStep) -> bool:
        if step.path != self.path or len(step.args) != len(self.args):
            return False

        for actual_argument, template_argument in zip(step.args, self.args):
            variable_match = _VARIABLE_TEMPLATE_PATTERN.fullmatch(template_argument)
            if variable_match is None:
                if actual_argument != template_argument:
                    return False
                continue

            variable_name = variable_match.group(1)
            if self.variable_patterns[variable_name].fullmatch(actual_argument) is None:
                return False
        return True


@dataclass(frozen=True)
class MuliuRunnerManifest:
    """A catalog-derived candidate deployment artifact.

    It records the catalog's exact path, argv template, runner, and risk metadata
    without credentials. The repository runtime does not consume this artifact,
    and it is not evidence that a GS-1 dispatcher has deployed or reads it.
    """

    contracts: tuple[MuliuCallContract, ...]

    def runner_for(self, path: str) -> tuple[str, ...]:
        runners = {contract.runner for contract in self.contracts if contract.path == path}
        if not runners:
            raise MuliuCallContractError("脚本路径未在 runner manifest 中登记：{}".format(path))
        if len(runners) != 1:
            raise MuliuCallContractError("同一路径不能登记不同 runner：{}".format(path))
        return (next(iter(runners)),)

    def as_jsonable(self) -> dict[str, object]:
        return {
            "version": 1,
            "contracts": [
                {
                    "name": contract.name,
                    "path": contract.path,
                    "args": list(contract.args),
                    "variables": {
                        name: pattern.pattern
                        for name, pattern in sorted(contract.variable_patterns.items())
                    },
                    "runner": [contract.runner],
                    "risk": contract.risk,
                }
                for contract in self.contracts
            ],
        }


@dataclass(frozen=True)
class MuliuCallContracts:
    """The machine-checkable part of the human-readable script catalog."""

    max_plan_steps: int
    contracts: tuple[MuliuCallContract, ...]

    def validate(self, plan: MuliuPlan) -> None:
        if not plan.steps:
            return
        if len(plan.steps) > self.max_plan_steps:
            raise MuliuCallContractError(
                "当前执行资料最多允许 {} 个已登记步骤".format(self.max_plan_steps)
            )

        matched_contracts: list[MuliuCallContract] = []
        for step_number, step in enumerate(plan.steps, start=1):
            matching_contracts = [contract for contract in self.contracts if contract.matches(step)]
            if len(matching_contracts) == 1:
                matched_contracts.append(matching_contracts[0])
                continue
            if len(matching_contracts) > 1:
                raise MuliuCallContractError(
                    "步骤 {} 无法唯一匹配调用合同".format(step_number)
                )
            raise MuliuCallContractError(
                "步骤 {} 不符合任何已登记调用合同：脚本路径和参数必须完全匹配执行资料中的模板"
                .format(step_number)
            )

        if plan.route_scope is None:
            if any(contract.route_scopes for contract in matched_contracts):
                raise MuliuCallContractError("该已登记范围查询必须声明 cn 或 overseas 路由范围")
            return
        if len(matched_contracts) != 1 or not self.permits_route_scope(plan, plan.route_scope):
            raise MuliuCallContractError("计划声明的范围路由未由已登记只读调用合同允许")

    def runner_manifest(self) -> MuliuRunnerManifest:
        runners_by_path: dict[str, str] = {}
        for contract in self.contracts:
            existing = runners_by_path.setdefault(contract.path, contract.runner)
            if existing != contract.runner:
                raise MuliuCallContractError(
                    "同一路径不能登记不同 runner：{}".format(contract.path)
                )
        return MuliuRunnerManifest(contracts=self.contracts)

    def extract_server_ids(self, plan: MuliuPlan) -> tuple[str, ...]:
        """Extract only contract-declared target-server values from an exact plan.

        This deliberately ignores free-form descriptions and ordinary numeric
        arguments such as scenario, season, account, user id, or threshold value.
        Any call shape that does not declare a target server is not routable unless
        a matching read-only contract explicitly permits its closed range route.
        """
        server_ids: list[str] = []
        for step_number, step in enumerate(plan.steps, start=1):
            contract = self._matching_contract(step, step_number)
            step_server_ids = self._contract_server_ids(contract, step)
            if not step_server_ids or any(not server_id for server_id in step_server_ids):
                raise MuliuCallContractError(
                    "步骤 {} 的调用合同未声明有效目标服号，不能选择 Worker 路由".format(step_number)
                )
            server_ids.extend(step_server_ids)
        return tuple(server_ids)

    def permits_route_scope(self, plan: MuliuPlan, route_scope: str) -> bool:
        """Return whether every exact plan step allows this closed range route."""
        if route_scope not in {"cn", "overseas"}:
            return False
        if not plan.steps:
            return False
        return all(
            contract.risk == "read" and contract.route_scopes and route_scope in contract.route_scopes
            for step_number, step in enumerate(plan.steps, start=1)
            for contract in (self._matching_contract(step, step_number),)
        )

    def _matching_contract(self, step: MuliuStep, step_number: int) -> MuliuCallContract:
        matching_contracts = [contract for contract in self.contracts if contract.matches(step)]
        if len(matching_contracts) != 1:
            raise MuliuCallContractError(
                "步骤 {} 无法唯一匹配调用合同，不能选择 Worker 路由".format(step_number)
            )
        return matching_contracts[0]

    @staticmethod
    def _contract_server_ids(contract: MuliuCallContract, step: MuliuStep) -> list[str]:
        step_server_ids: list[str] = []
        for actual_argument, template_argument in zip(step.args, contract.args):
            variable_match = _VARIABLE_TEMPLATE_PATTERN.fullmatch(template_argument)
            if variable_match is None:
                continue
            variable_name = variable_match.group(1)
            if (
                variable_name == "server_id"
                or variable_name.startswith("server_id_")
                or variable_name.endswith("_server_id")
            ):
                step_server_ids.append(actual_argument)
            elif variable_name == "server_ids":
                step_server_ids.extend(server_id.strip() for server_id in actual_argument.split(","))
        return step_server_ids


class MuliuScriptCatalog:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._content: str | None = None

    @classmethod
    def from_text(cls, content: str) -> "MuliuScriptCatalog":
        """Construct an in-memory catalog for deterministic artifact generation."""
        catalog = cls("<generated-catalog>")
        catalog._content = _require_catalog_content(content, "内存 Muliu 脚本资料")
        return catalog

    def read(self) -> str:
        if self._content is not None:
            return self._content
        try:
            content = self._path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise MuliuScriptCatalogError("找不到 Muliu 脚本资料文件：{}".format(self._path)) from exc
        return _require_catalog_content(content, "Muliu 脚本资料文件：{}".format(self._path))

    def read_call_contracts(self) -> MuliuCallContracts:
        return parse_call_contracts(self.read())

    def read_runner_manifest(self) -> MuliuRunnerManifest:
        return self.read_call_contracts().runner_manifest()

    def write_runner_manifest(self, output_path: str | Path) -> Path:
        """Write a catalog-derived candidate manifest for deployment review."""
        manifest_path = Path(output_path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                self.read_runner_manifest().as_jsonable(),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return manifest_path

    def read_capability_context(self) -> str:
        """Return a small, current registry snapshot safe to show the knowledge AI.

        Script documents explain behavior and risk, but their historical wording is
        not allowed to override this generated snapshot when deciding whether the
        bot currently exposes a call.
        """
        contracts = self.read_call_contracts()
        lines = ["当前机器人可执行能力注册表（唯一权威）："]
        for contract in contracts.contracts:
            args = ", ".join(contract.args) if contract.args else "（无参数）"
            lines.append(
                "- {}：{} args=[{}]；风险={}".format(
                    contract.name,
                    contract.path,
                    args,
                    contract.risk,
                )
            )
        return "\n".join(lines)


def _require_catalog_content(content: str, label: str) -> str:
    normalized = content.strip()
    if not normalized:
        raise MuliuScriptCatalogError("{}为空".format(label))
    return normalized


def parse_call_contracts(catalog_text: str) -> MuliuCallContracts:
    """Extract the compact contract block embedded in the readable Markdown catalog.

    Keeping templates, runner, and risk beside their human explanations prevents
    drift: a newly documented execution mode is not usable until its exact
    machine-checkable shape passes this parser.
    """
    matches = list(_CONTRACT_BLOCK_PATTERN.finditer(catalog_text))
    if len(matches) != 1:
        raise MuliuCallContractError(
            "Muliu 执行资料必须且只能包含一个 MULIU_CALL_CONTRACTS 调用合同块"
        )

    try:
        raw_contract_set = json.loads(matches[0].group(1))
    except json.JSONDecodeError as exc:
        raise MuliuCallContractError("Muliu 调用合同不是合法 JSON") from exc

    if not isinstance(raw_contract_set, dict):
        raise MuliuCallContractError("Muliu 调用合同根对象必须是 JSON 对象")
    _reject_unknown_fields(raw_contract_set, {"max_plan_steps", "contracts"}, "Muliu 调用合同")

    max_plan_steps = raw_contract_set.get("max_plan_steps")
    if isinstance(max_plan_steps, bool) or not isinstance(max_plan_steps, int) or max_plan_steps < 1:
        raise MuliuCallContractError("Muliu 调用合同的 max_plan_steps 必须是正整数")

    raw_contracts = raw_contract_set.get("contracts")
    if not isinstance(raw_contracts, list) or not raw_contracts:
        raise MuliuCallContractError("Muliu 调用合同的 contracts 必须是非空数组")

    contracts = tuple(_parse_contract(raw_contract, index) for index, raw_contract in enumerate(raw_contracts, start=1))
    names = [contract.name for contract in contracts]
    if len(names) != len(set(names)):
        raise MuliuCallContractError("Muliu 调用合同不能有重名模式")
    parsed_contracts = MuliuCallContracts(max_plan_steps=max_plan_steps, contracts=contracts)
    # Force the same-path runner consistency check while reporting catalog errors at load time.
    parsed_contracts.runner_manifest()
    return parsed_contracts


def _parse_contract(raw_contract: object, index: int) -> MuliuCallContract:
    label = "Muliu 调用合同 contracts[{}]".format(index)
    if not isinstance(raw_contract, dict):
        raise MuliuCallContractError("{} 必须是 JSON 对象".format(label))
    _reject_unknown_fields(
        raw_contract,
        {"name", "path", "args", "variables", "runner", "risk", "route_scopes"},
        label,
    )

    name = _require_non_empty_string(raw_contract.get("name"), "{}.name".format(label))
    path = _require_non_empty_string(raw_contract.get("path"), "{}.path".format(label))
    if not path.startswith("/"):
        raise MuliuCallContractError("{}.path 必须是绝对路径".format(label))
    runner = _require_non_empty_string(raw_contract.get("runner"), "{}.runner".format(label))
    if runner not in _RUNNER_VALUES:
        raise MuliuCallContractError(
            "{}.runner 必须是以下值之一：{}".format(label, ", ".join(sorted(_RUNNER_VALUES)))
        )
    risk = _require_non_empty_string(raw_contract.get("risk"), "{}.risk".format(label))
    if risk not in _RISK_VALUES:
        raise MuliuCallContractError(
            "{}.risk 必须是以下值之一：{}".format(label, ", ".join(sorted(_RISK_VALUES)))
        )
    route_scopes = _parse_route_scopes(raw_contract.get("route_scopes"), label)
    if route_scopes and risk != "read":
        raise MuliuCallContractError("{}.route_scopes 仅允许用于 read 合同".format(label))

    raw_args = raw_contract.get("args")
    if not isinstance(raw_args, list):
        raise MuliuCallContractError("{}.args 必须是数组".format(label))
    args = tuple(_require_non_empty_string(value, "{}.args[{}]".format(label, position)) for position, value in enumerate(raw_args, start=1))

    raw_variables = raw_contract.get("variables")
    if not isinstance(raw_variables, dict):
        raise MuliuCallContractError("{}.variables 必须是 JSON 对象".format(label))
    variable_patterns: dict[str, re.Pattern[str]] = {}
    for variable_name, raw_pattern in raw_variables.items():
        if not isinstance(variable_name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable_name) is None:
            raise MuliuCallContractError("{}.variables 的变量名不合法".format(label))
        pattern = _require_non_empty_string(raw_pattern, "{}.variables.{}".format(label, variable_name))
        try:
            variable_patterns[variable_name] = re.compile(pattern)
        except re.error as exc:
            raise MuliuCallContractError(
                "{}.variables.{} 不是合法正则".format(label, variable_name)
            ) from exc

    for argument in args:
        variable_match = _VARIABLE_TEMPLATE_PATTERN.fullmatch(argument)
        if variable_match is not None and variable_match.group(1) not in variable_patterns:
            raise MuliuCallContractError(
                "{}.args 中的变量 {} 没有对应正则".format(label, argument)
            )
    return MuliuCallContract(
        name=name,
        path=path,
        args=args,
        variable_patterns=variable_patterns,
        runner=runner,
        risk=risk,
        route_scopes=route_scopes,
    )


def _parse_route_scopes(raw_route_scopes: object, label: str) -> frozenset[str]:
    if raw_route_scopes is None:
        return frozenset()
    if not isinstance(raw_route_scopes, list) or not raw_route_scopes:
        raise MuliuCallContractError("{}.route_scopes 必须是非空数组".format(label))
    if any(not isinstance(region, str) or region not in {"cn", "overseas"} for region in raw_route_scopes):
        raise MuliuCallContractError("{}.route_scopes 只能包含 cn 或 overseas".format(label))
    if len(raw_route_scopes) != len(set(raw_route_scopes)):
        raise MuliuCallContractError("{}.route_scopes 不能重复".format(label))
    return frozenset(raw_route_scopes)


def _require_non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MuliuCallContractError("{} 必须是非空字符串".format(label))
    return value


def _reject_unknown_fields(value: dict[str, object], allowed_fields: set[str], label: str) -> None:
    unexpected_fields = set(value) - allowed_fields
    if unexpected_fields:
        raise MuliuCallContractError(
            "{} 包含不允许的字段：{}".format(label, ", ".join(sorted(unexpected_fields)))
        )
