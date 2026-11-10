import importlib.util
import shutil
import re
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "gen_combine_config", Path(__file__).resolve().parents[1] / "knowledge/scripts/gen_combine_config.py"
)
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)


@pytest.fixture(autouse=True)
def stub_hosts(monkeypatch):
    monkeypatch.setattr(gen, "get_db_host", lambda server, db="": {"host1": f"h{server}{db}1", "host2": f"h{server}{db}2"})
    monkeypatch.setattr(gen, "read_config_value", lambda path, key: "10.0.0.1")


def _game_block(lua, order):
    return re.search(r"config\['game_%d'\] = \{(.*?)\n\}" % order, lua, re.S).group(1)


def test_single_source_keeps_season_in_common():
    lua = gen.generate_lua_config("5010", ["5012"], "4", "9")
    assert "src_server_id = {5010}," in lua
    assert "    season_scenario_info = {  scenario = 9, season = 4,}, -- 合服来源的赛季、剧本信息" in lua
    assert all("season_scenario_info" not in _game_block(lua, order) for order in (1, 2, 3))


def test_two_sources_with_different_scenarios_go_per_process():
    lua = gen.generate_lua_config("5010", ["5012"], "4", "9", extra_sources=[("5011", "5", "10")])
    assert "src_server_id = {5010, 5011}," in lua and "src_zone_id = 501001," in lua
    assert "scenario = 9, season = 4" in _game_block(lua, 1)
    assert "scenario = 10, season = 5" in _game_block(lua, 2)
    assert "season_scenario_info" not in _game_block(lua, 3)
    assert "season_scenario_info = {  scenario = 9, season = 4,}, -- 合服来源" not in lua
    assert "[5011] = {" in lua and "authdb = 'nslg_game_5011'" in lua
    assert 'connectDbList = {"nslg_common", "nslg_game_common", "nslg_combine", 5010, 501001, 5011, 5012}' in lua


def test_sources_sharing_a_scenario_keep_it_in_common():
    lua = gen.generate_lua_config("5010", ["5012"], "4", "9", extra_sources=[("5011", "4", "9")])
    assert "src_server_id = {5010, 5011}," in lua
    assert "season_scenario_info = {  scenario = 9, season = 4,}, -- 合服来源" in lua


@pytest.mark.parametrize("extra, end, reason", [
    ([("5011", "5", "10"), ("5013", "6", "11"), ("5020", "7", "12")], ["5012"], "超过合服工具进程数"),
    ([("5010", "5", "10")], ["5012"], "不能重复"),
    ([("5012", "5", "10")], ["5012"], "不能重复"),
])
def test_rejects_unsupported_source_sets(extra, end, reason):
    with pytest.raises(ValueError, match=reason):
        gen.generate_lua_config("5010", end, "4", "9", extra_sources=extra)


def test_command_line_accepts_extra_sources(monkeypatch, tmp_path):
    (tmp_path / "combine_config").mkdir()
    monkeypatch.setattr(gen, "__file__", str(tmp_path / "gen_combine_config.py"))
    monkeypatch.setattr(sys, "argv", ["gen", "5010", "5012", "4", "9", "--also", "5011", "5", "10"])
    gen.main()
    lua = (tmp_path / "combine_config" / "combine_demo_new.lua").read_text(encoding="utf-8")
    assert "src_server_id = {5010, 5011}," in lua


def test_repository_catalog_accepts_a_two_source_merge_and_routes_by_both_sources():
    from src.config import MuliuConfig
    from src.muliu_firewall import MuliuFirewall
    from src.muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
    from src.muliu_script_catalog import MuliuScriptCatalog

    contracts = MuliuScriptCatalog("config/muliu_script_catalog.md").read_call_contracts()
    plan = MuliuPlan(summary="5010、5011 合到 5012", kind=MuliuPlanKind.OPERATION, steps=[MuliuStep(
        path="/home/serverGeneralScript/exc_server_merge.sh",
        args=["5010", "5012", "4", "9", "--also", "5011", "5", "10"],
        description="5010（赛季 4 剧本 9）、5011（赛季 5 剧本 10）合到 5012",
    )])
    MuliuFirewall(MuliuConfig(script_root="/home/serverGeneralScript"), call_contracts=contracts).validate(plan)
    assert set(contracts.extract_server_ids(plan)) == {"5010", "5012", "5011"}


@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32", reason="needs bash with POSIX paths")
def test_get_db_host_runs_the_helper_without_37_only_arguments(monkeypatch, tmp_path):
    # GS-1 (HK) runs this with pyenv Python 3.6 under Muliu: no capture_output / text
    monkeypatch.undo()  # use the real get_db_host, not the autouse stub
    helper = tmp_path / "script_helper"
    helper.mkdir()
    (helper / "get_db_host.py").write_text("echo host-a-$1$2\necho host-b-$1$2\n", encoding="utf-8")
    monkeypatch.setattr(gen, "__file__", str(tmp_path / "pys_col" / "gen_combine_config.py"))
    assert gen.get_db_host("5011", "zone") == {"host1": "host-a-5011zone", "host2": "host-b-5011zone"}
