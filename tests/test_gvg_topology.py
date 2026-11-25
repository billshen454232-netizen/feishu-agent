"""Safety checks at the vendor topology execution boundary."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import MuliuConfig
from src.gvg_topology import GVG_TOPOLOGY_PATH, parse_topology_preview
from src.muliu_executor import MuliuExecutionError, MuliuExecutor, MuliuSubmittedError
from src.muliu_plan import MuliuPlan, MuliuStep
from src.muliu_script_catalog import MuliuCallContractError, MuliuScriptCatalog

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


def test_restart_preview_pins_the_running_gvg_as_a_future_start_target():
    router = {"host_num": "1", "group": "router", "status": "already_running"}
    gvg = {
        "host_num": "4", "group": "gvg_zone", "machine": "gvg-primary",
        "mode": "qa_gvg_zone", "status": "already_running",
        "member_start_plans": [{"machine": "gvg-primary", "role": "master", "scripts": ["start_gvg_zone.sh"]}],
    }
    plan = {"batches": [{"batch_index": 4, "actions": [router]}, {"batch_index": 2, "actions": [gvg]}]}
    assert wrapper.plan_preview(plan, "weeklygvg", "start")["actions"] == []
    preview = wrapper.plan_preview(plan, "weeklygvg", "start", include_running_gvg=True)
    gvg["status"] = "pending"
    assert preview == wrapper.plan_preview(plan, "weeklygvg", "start")
    assert preview["actions"] == [[2, "4", "gvg_zone", 1]]


def test_contract_allows_only_ordered_gvg_steps_for_the_same_scope():
    catalog = SOURCE.parents[2] / "config/muliu_script_catalog.md"
    contracts = MuliuScriptCatalog(catalog).read_call_contracts()
    stop = MuliuStep(GVG_TOPOLOGY_PATH, ["preview-stop", "weeklygvg"], "预检停服")
    start = MuliuStep(GVG_TOPOLOGY_PATH, ["preview-start", "weeklygvg"], "预检起服")
    contracts.validate(MuliuPlan("预检", [stop, start], route_scope="cn"))
    cross_scope = MuliuStep(GVG_TOPOLOGY_PATH, ["preview-start", "devgvg"], "预检其他 GVG 起服")
    for rejected in ([start, stop], [stop, cross_scope]):
        with pytest.raises(MuliuCallContractError, match="先停服，再启动同一组 GVG"):
            contracts.validate(MuliuPlan("预检", rejected, route_scope="cn"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_outcome", ["success", "failed", "unknown"])
async def test_task_89_runs_gvg_start_only_after_verified_stop(monkeypatch, stop_outcome):
    submitted = []

    class Client:
        def __init__(self, config):
            assert config.task_id == 89

        def login(self):
            pass

        def execute_step(self, step):
            submitted.append(step.args[0])
            if step.args[0] == "stop":
                if stop_outcome == "unknown":
                    raise MuliuSubmittedError(MuliuExecutionError("停服结果未知"), submitted=True)
                return f"AI_OP_END status={stop_outcome}"
            return "AI_OP_END status=success"

    monkeypatch.setattr("src.muliu_executor.MuliuHttpClient", Client)
    plan = MuliuPlan("重启 Weekly GVG", [
        MuliuStep(GVG_TOPOLOGY_PATH, ["stop", "weeklygvg", "a" * 64], "拓扑停服"),
        MuliuStep(GVG_TOPOLOGY_PATH, ["start", "weeklygvg", "b" * 64], "拓扑起服"),
    ])
    result = await MuliuExecutor(MuliuConfig()).execute_plan(plan)
    assert submitted == (["stop", "start"] if stop_outcome == "success" else ["stop"])
    assert result.succeeded == (stop_outcome == "success")


def test_gs1_scope_matches_registered_assets():
    assets = json.loads((SOURCE.parents[2] / "config/server_environments.json").read_text(encoding="utf-8"))
    assert {scope: int(cluster) for scope, (_, cluster) in wrapper.SCOPES.items()} == {
        scope: spec["number"] for scope, spec in assets["gvg_clusters"].items()
    }
