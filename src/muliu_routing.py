"""Derive one fail-closed Worker route from a contract-validated Muliu plan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .muliu_plan import MuliuPlan
from .muliu_script_catalog import BACKEND_GVG_CHANGE, BACKEND_GVG_QUERY, MuliuCallContracts
from .gvg_connection import WeeklyGvgScope
from .gvg_topology import GVG_TOPOLOGY_PATH
from .server_environments import ServerEnvironments, ServerEnvironmentsError


RouteRegion = Literal["cn", "overseas"]

_ROUTE_DETAILS: dict[str, tuple[str, str]] = {
    "cn": ("国服", "route-cn"),
    "overseas": ("海外服", "route-overseas"),
}


class MuliuRoutingError(ValueError):
    """Raised when a confirmed plan cannot safely select one Worker route."""


@dataclass(frozen=True)
class MuliuRouteSnapshot:
    """Safe route details deterministically derived from a persisted region."""

    region: RouteRegion
    label: str
    identifier: str

    @classmethod
    def from_region(cls, region: str) -> "MuliuRouteSnapshot":
        details = _ROUTE_DETAILS.get(region)
        if details is None:
            raise MuliuRoutingError("未知受控路由分类：{}".format(region or "（空）"))
        label, identifier = details
        return cls(region=region, label=label, identifier=identifier)  # type: ignore[arg-type]


class MuliuPlanRouteResolver:
    """Resolve target servers only through registered call-contract variables."""

    def __init__(self, server_environments_path: str, call_contracts: MuliuCallContracts) -> None:
        self._server_environments_path = server_environments_path
        self._call_contracts = call_contracts

    def resolve(self, plan: MuliuPlan) -> MuliuRouteSnapshot:
        try:
            self._call_contracts.validate(plan)
            if any(step.path == GVG_TOPOLOGY_PATH for step in plan.steps):
                if len(plan.steps) != 1:
                    raise ValueError("GVG 拓扑操作必须单独发起")
                return MuliuRouteSnapshot.from_region("cn")
            backends = {self._call_contracts.backend_for(step) for step in plan.steps}
            if backends == {BACKEND_GVG_QUERY}:
                if len(plan.steps) != 1 or plan.route_scope != "cn" or not self._call_contracts.is_read_only(plan):
                    raise ValueError("GVG 配置查询必须是国服的单步只读计划")
                return MuliuRouteSnapshot.from_region("cn")
            if backends == {BACKEND_GVG_CHANGE}:
                scope = WeeklyGvgScope.load(self._server_environments_path)
                targets = self._call_contracts.extract_server_ids(plan)
                if (len(plan.steps) != 1 or len(set(targets)) != len(targets)
                        or not set(targets).issubset(scope.game_servers)):
                    raise ValueError("GVG 配置变更仅允许一次操作国服 Weekly 普通游戏服")
                return MuliuRouteSnapshot.from_region("cn")
            if backends != {""}:
                if len(plan.steps) != 1:
                    raise ValueError("开服配置检查需要单独发起，不能和其他操作放在同一个计划里")
                # Executed by the intranet Worker itself (UI-automation API), not by a regional Muliu task.
                return MuliuRouteSnapshot.from_region("cn")
            if plan.route_scope is not None:
                return MuliuRouteSnapshot.from_region(plan.route_scope)

            environments = ServerEnvironments.load(self._server_environments_path)
            server_ids = self._call_contracts.extract_server_ids(plan)
            region = environments.route_region_for_server_ids(server_ids)
        except (ServerEnvironmentsError, ValueError) as exc:
            raise MuliuRoutingError("无法为已确认计划选择唯一 Worker 路由：{}".format(exc)) from exc
        return MuliuRouteSnapshot.from_region(region)
