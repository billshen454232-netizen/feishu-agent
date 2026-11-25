"""Safety checks at the vendor topology execution boundary."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.gvg_topology import parse_topology_preview

SOURCE = Path(__file__).parents[1] / "knowledge/scripts/gvg_topology.py"
SPEC = importlib.util.spec_from_file_location("gs1_gvg_topology", SOURCE)
assert SPEC and SPEC.loader
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)


@pytest.mark.parametrize(("scope", "cluster"), [("weeklygvg", "4"), ("devgvg", "260005"), ("qagvg", "260006")])
def test_query_distinguishes_zone_report_from_current_game_process(scope, cluster):
    def server(host_num, group, running, connections):
        return {
            "host_num": host_num, "server_group": group, "is_running": running, "status": "succeeded",
            "source_type": "runtime" if running else "static_config", "connected_host_nums": connections,
            "configured_connected_host_nums": connections,
        }

    lines = wrapper.dependency_lines({"servers": [
        server(cluster, "gvg_zone", True, ["2005", "2015"]),
        server("2005", "game", True, [cluster]),
        server("2015", "game", False, [cluster]),
        server("2016", "game", False, [cluster]),
        server("2022", "gvg_zone", True, [cluster]),
        server("2213", "game", True, ["999"]),
    ]}, scope)
    assert "GVG Zone 运行态报告的服号：2005、2015" in lines
    assert "拓扑关联且 game 仍在运行：2005" in lines
    assert "拓扑关联且 game 已停：2015、2016" in lines


@pytest.mark.parametrize(("operation", "runner_name", "execute_name"), [
    ("stop", "run_shutdown", "execute_shutdown_plan"),
    ("start", "run_start", "execute_start_plan"),
])
def test_preview_uses_vendor_planner_and_rejects_drift_before_execute(operation, runner_name, execute_name):
    action = {"host_num": "4", "group": "gvg_zone", "machine": "test-machine", "mode": "qa_gvg_zone", "status": "pending"}
    plan = {"batches": [{"batch_index": 2, "actions": [action]}]}
    executed = []
    module = SimpleNamespace()

    def run_shutdown(args):
        return module.execute_shutdown_plan(plan, args)

    def run_start(args):
        return module.execute_start_plan(plan, args)

    def apply(chosen, args):
        executed.append(chosen)
        return 0

    setattr(module, runner_name, run_shutdown if operation == "stop" else run_start)
    setattr(module, execute_name, apply)
    module.main = lambda args: getattr(module, runner_name)(args)
    _, preview = wrapper.run_plan(module, "weeklygvg", operation, [], None)
    assert preview["actions"] == [[2, "4", "gvg_zone", 0]] and executed == []

    action["machine"] = "different-test-machine"
    with pytest.raises(wrapper.TopologyError, match="拓扑计划已变化"):
        wrapper.run_plan(module, "weeklygvg", operation, [], preview["signature"])
    assert executed == []

    action["machine"] = "test-machine"
    wrapper.run_plan(module, "weeklygvg", operation, [], preview["signature"])
    assert executed == [plan]


def test_preview_response_rejects_cross_scope_cross_operation_and_multiple_markers():
    signature = "a" * 64
    marker = "GVG_TOPOLOGY_PREVIEW " + json.dumps({
        "scope": "qagvg", "operation": "stop", "signature": signature,
        "actions": [[0, "2006", "game", 0], [2, "260006", "gvg_zone", 0]],
    })
    assert parse_topology_preview(marker, "qagvg", "stop") == (
        signature, ["批次 0 · game 2006", "批次 2 · gvg_zone 260006"],
    )
    with pytest.raises(ValueError):
        parse_topology_preview(marker, "devgvg", "stop")
    with pytest.raises(ValueError):
        parse_topology_preview(marker, "qagvg", "start")
    with pytest.raises(ValueError):
        parse_topology_preview(marker + "\n" + marker, "qagvg", "stop")


def test_gs1_scope_matches_registered_assets():
    assets = json.loads((SOURCE.parents[2] / "config/server_environments.json").read_text(encoding="utf-8"))
    assert {scope: int(cluster) for scope, (_, cluster) in wrapper.SCOPES.items()} == {
        scope: spec["number"] for scope, spec in assets["gvg_clusters"].items()
    }
