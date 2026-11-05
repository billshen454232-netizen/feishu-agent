"""Per-capability execution route switches (Muliu or Server Manager API), stored on the Gateway.

Design: docs/superpowers/specs/2026-11-04-server-manager-executor.md §4 / §6.4. Everything
defaults to Muliu; only contracts in the translation table can be switched, per region.
The Gateway stamps each claimed step with a route; the Worker re-checks before using it.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .muliu_plan import MuliuPlan
from .server_manager import REGIONS, ROUTE_API, ROUTE_MULIU, ROUTE_SPECS, translate


class RouteError(ValueError):
    """Invalid route change."""


class ExecutionRouteStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS execution_routes (
                    contract TEXT NOT NULL,
                    region TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    updated_by TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (contract, region)
                );
                CREATE TABLE IF NOT EXISTS execution_route_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contract TEXT NOT NULL,
                    region TEXT NOT NULL,
                    old_backend TEXT NOT NULL,
                    new_backend TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    at INTEGER NOT NULL
                );
                """
            )
            conn.commit()

    def settings(self) -> dict[tuple[str, str], dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM execution_routes").fetchall()
        return {(row["contract"], row["region"]): dict(row) for row in rows}

    def backend(self, contract: str, region: str) -> str:
        row = self.settings().get((contract, region))
        return row["backend"] if row else ROUTE_MULIU

    def set_backend(self, contract: str, region: str, backend: str, *, operator: str, now: Optional[int] = None) -> dict[str, Any]:
        if contract not in ROUTE_SPECS:
            raise RouteError("该能力暂无 API 对应，不能切换")
        if region not in REGIONS:
            raise RouteError("区域只能是 cn / overseas")
        if backend not in (ROUTE_MULIU, ROUTE_API):
            raise RouteError("路线只能是 muliu / api")
        old = self.backend(contract, region)
        timestamp = int(time.time()) if now is None else now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_routes (contract, region, backend, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (contract, region) DO UPDATE SET backend = excluded.backend,
                    updated_at = excluded.updated_at, updated_by = excluded.updated_by
                """,
                (contract, region, backend, timestamp, operator),
            )
            if old != backend:
                conn.execute(
                    "INSERT INTO execution_route_events (contract, region, old_backend, new_backend, operator, at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (contract, region, old, backend, operator, timestamp),
                )
            conn.commit()
        return {"contract": contract, "region": region, "backend": backend, "updated_at": timestamp, "updated_by": operator}

    def list_events(self, contract: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM execution_route_events {} ORDER BY event_id DESC LIMIT ?".format(
            "WHERE contract = ?" if contract else ""
        )
        params: tuple[Any, ...] = (contract, limit) if contract else (limit,)
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def step_routes(self, plan: MuliuPlan, region: str, contracts: Any) -> list[str]:
        """Route per step at claim time: api only when switched on and the step translates."""
        settings = self.settings()
        routes = []
        for step in plan.steps:
            matching = [contract for contract in contracts.contracts if contract.matches(step)]
            name = matching[0].name if len(matching) == 1 else ""
            switched = settings.get((name, region), {}).get("backend") == ROUTE_API
            routes.append(ROUTE_API if switched and translate(name, step) else ROUTE_MULIU)
        return routes

    def overview(self, contracts: Any) -> list[dict[str, Any]]:
        """Every catalog contract with its API mapping (if any) and per-region switch, for the admin page."""
        settings = self.settings()
        items = []
        for contract in contracts.contracts:
            spec = ROUTE_SPECS.get(contract.name)
            regions = {}
            for region in REGIONS:
                row = settings.get((contract.name, region))
                regions[region] = {
                    "backend": row["backend"] if row else ROUTE_MULIU,
                    "updated_at": row["updated_at"] if row else 0,
                    "updated_by": row["updated_by"] if row else "",
                }
            items.append({
                "contract": contract.name,
                "path": contract.path,
                "args": " ".join(contract.args),
                "risk": contract.risk,
                "switchable": spec is not None,
                "label": spec.label if spec else "",
                "sequence": spec.sequence if spec else "",
                "exceptions": spec.exceptions if spec else "",
                "regions": regions,
            })
        order = list(ROUTE_SPECS)  # rollout order: smallest impact first
        items.sort(key=lambda item: (not item["switchable"], order.index(item["contract"]) if item["switchable"] else 0, item["contract"]))
        return items

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn
