import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.admin_app import create_admin_app
from src.execution_report import ExecutionReportStore
from src.hotfix_thresholds import (
    HOTFIX_PATH,
    HotfixPlannerCatalog,
    ThresholdError,
    ThresholdStore,
)
from src.muliu_plan import MuliuPlan, MuliuPlanError, MuliuStep
from src.muliu_planner import MuliuPlanner
from src.muliu_script_catalog import MuliuScriptCatalog, parse_call_contracts

CATALOG_PATH = Path(__file__).parents[1] / "config" / "muliu_script_catalog.md"
HEADERS = {"X-Admin-Request": "1"}


def _store(tmp_path, seed=True):
    store = ThresholdStore(tmp_path / "db.sqlite3")
    store.initialize() if seed else store.initialize(seed_path=None)
    return store


def _hotfix(args, description="热更 5010 服弹劾人数为 1"):
    return MuliuPlan(summary=description, steps=[MuliuStep(path=HOTFIX_PATH, args=args, description=description)])


def _add_job(store, status, args, message_id="om_1"):
    with sqlite3.connect(store.database_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS message_jobs (message_id TEXT PRIMARY KEY, status TEXT, plan_json TEXT)")
        plan = {"summary": "x", "steps": [{"path": HOTFIX_PATH, "args": args, "description": "x"}]}
        conn.execute("INSERT INTO message_jobs VALUES (?, ?, ?)", (message_id, status, json.dumps(plan)))


def test_seed_migrates_the_24_registered_thresholds(tmp_path):
    items = _store(tmp_path).list_thresholds()

    assert len(items) == 24 and all(item["state"] == "active" for item in items)
    swap = next(item for item in items if item["key"] == "faction_swap_team_member_min")
    assert swap["name"] == "战队交换人数" and swap["aliases"] == ["战队交换人数阈值"]
    assert swap["executed"] is False


def test_planner_catalog_exposes_only_registered_keys(tmp_path):
    store = _store(tmp_path)
    store.update("rank_quota", {"state": "disabled"}, operator="t")
    text = HotfixPlannerCatalog(CATALOG_PATH, store).read()

    assert "-k faction_swap_team_member_min" in text and "战队交换人数阈值" in text
    assert "-k rank_quota" not in text
    contracts = parse_call_contracts(text)
    names = {c.name for c in contracts.contracts if c.path == HOTFIX_PATH}
    assert names == {"hotfix-server-threshold", "hotfix-server-threshold-with-patch-type",
                     "cleanup-legacy-auto-hotfix", "restore-auto-hotfix"}
    contracts.validate(_hotfix(["-s", "5010", "-k", "union_impeach_min", "-v", "1"]))
    for args in (["-s", "5010", "-k", "rank_quota", "-v", "1"],
                 ["-s", "5010", "-k", "union_impeach_union_member_min", "--table", "union_config_setting",
                  "-v", "1", "--patch-type", "common"]):
        with pytest.raises(MuliuPlanError):
            contracts.validate(_hotfix(args))


def test_planner_catalog_without_active_entries_has_no_hotfix_contract(tmp_path):
    store = _store(tmp_path, seed=False)
    text = HotfixPlannerCatalog(CATALOG_PATH, store).read()

    assert "当前没有已登记的热更阈值" in text
    assert not [c for c in parse_call_contracts(text).contracts if c.path == HOTFIX_PATH]


def test_expand_plan_defaults_to_both_sides_and_passes_the_static_contracts(tmp_path):
    store = _store(tmp_path)
    expanded = store.expand_plan(_hotfix(["-s", "5010", "-k", "union_impeach_min", "-v", "1"]))

    assert [step.args for step in expanded.steps] == [
        ["-s", "5010", "-k", "union_impeach_union_member_min", "--table", "union_config_setting", "-v", "1", "--patch-type", "common"],
        ["-s", "5010", "-k", "union_impeach_union_member_min", "--table", "union_config_setting", "-v", "1", "--patch-type", "client_avatar"],
    ]
    assert expanded.steps[0].description.endswith("（服务端）") and expanded.steps[1].description.endswith("（客户端）")
    static = MuliuScriptCatalog(CATALOG_PATH).read_call_contracts()
    static.validate(expanded)

    single = store.expand_plan(_hotfix(["-s", "5010", "-k", "union_impeach_min", "-v", "1", "--patch-type", "common"]))
    assert len(single.steps) == 1 and single.steps[0].args[-1] == "common"
    restore = store.expand_plan(_hotfix(["-s", "5010", "--restore-auto", "capital_city_extra_quota"]))
    assert restore.steps[0].args == ["-s", "5010", "--restore-auto", "capital_city_extra_quota"]
    static.validate(restore)


def test_expand_plan_rejects_unknown_or_disabled_keys(tmp_path):
    store = _store(tmp_path)
    store.update("rank_quota", {"state": "disabled"}, operator="t")

    with pytest.raises(MuliuPlanError, match="未登记或已停用"):
        store.expand_plan(_hotfix(["-s", "5010", "-k", "rank_quota", "-v", "1"]))
    with pytest.raises(MuliuPlanError, match="未登记或已停用"):
        store.expand_plan(_hotfix(["-s", "5010", "--cleanup-legacy", "nope"]))
    untouched = MuliuPlan(summary="s", steps=[MuliuStep(path="/home/serverGeneralScript/basic_info.sh", args=["6000"], description="d")])
    assert store.expand_plan(untouched) is untouched


def test_new_entry_is_usable_immediately(tmp_path):
    store = _store(tmp_path)
    store.create({"key": "test_member_limit", "name": "测试人数", "aliases": ["测试阈值"], "table_name": "game_config_common",
                  "field_name": "test_member_limit"}, operator="owner@example.com")

    assert "测试阈值" in HotfixPlannerCatalog(CATALOG_PATH, store).read()
    expanded = store.expand_plan(_hotfix(["-s", "5010", "-k", "test_member_limit", "-v", "3"]))
    assert expanded.steps[0].args[3:6] == ["test_member_limit", "--table", "game_config_common"]
    assert store.list_events("test_member_limit")[0]["field"] == "created"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"key": "Bad-Key", "name": "x", "table_name": "t", "field_name": "f"}, "key"),
        ({"key": "union_impeach_min", "name": "x", "table_name": "t", "field_name": "f"}, "已存在"),
        ({"key": "new_key", "name": "弹劾人数", "table_name": "t", "field_name": "f"}, "已被 union_impeach_min 使用"),
        ({"key": "new_key", "name": "x", "aliases": ["战队交换人数阈值"], "table_name": "t", "field_name": "f"}, "已被"),
        ({"key": "new_key", "name": "x", "table_name": "union_config_setting", "field_name": "union_impeach_union_member_min"}, "生效路径"),
        ({"key": "new_key", "name": "x", "table_name": "t.x", "field_name": "f"}, "表名"),
        ({"key": "new_key", "name": "x", "table_name": "t", "field_name": "f;os"}, "字段名"),
        ({"key": "new_key", "name": "x", "aliases": "单个字符串", "table_name": "t", "field_name": "f"}, "别名"),
        ({"key": "new_key", "name": "", "table_name": "t", "field_name": "f"}, "中文名"),
    ],
)
def test_deterministic_validation_rejects_bad_entries(tmp_path, fields, message):
    with pytest.raises(ThresholdError, match=message):
        _store(tmp_path).create(fields, operator="t")


def test_disabled_entries_still_reserve_their_terms(tmp_path):
    store = _store(tmp_path)
    store.update("union_impeach_min", {"state": "disabled"}, operator="t")

    with pytest.raises(ThresholdError, match="已被"):
        store.create({"key": "other", "name": "弹劾人数", "table_name": "t", "field_name": "f"}, operator="t")


def test_executed_entries_cannot_move_or_be_deleted(tmp_path):
    store = _store(tmp_path)
    _add_job(store, "done", ["-s", "5010", "-k", "union_impeach_min", "-v", "1"])
    _add_job(store, "done", ["-s", "5010", "-k", "capital_city_extra_quota_field", "--table", "t", "-v", "1",
                             "--patch-type", "common"], message_id="om_2")
    _add_job(store, "awaiting_confirmation", ["-s", "5010", "-k", "rank_quota", "-v", "1"], message_id="om_3")

    assert store.get("union_impeach_min")["executed"] is True
    assert store.get("rank_quota")["executed"] is False
    with pytest.raises(ThresholdError, match="只能停用"):
        store.delete("union_impeach_min", operator="t")
    with pytest.raises(ThresholdError, match="不能修改生效路径"):
        store.update("union_impeach_min", {"field_name": "other_field"}, operator="t")
    store.update("union_impeach_min", {"description": "新说明", "state": "disabled"}, operator="t")
    assert store.delete("rank_quota", operator="t") is True
    assert store.get("rank_quota") is None


@pytest.mark.asyncio
async def test_planner_returns_the_expanded_plan(tmp_path):
    store = _store(tmp_path)

    class FakeAI:
        def __init__(self):
            self.prompts = []

        async def generate(self, history, prompt):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                return json.dumps({"kind": "operation", "summary": "热更 5010 服战队交换人数为 1", "steps": [
                    {"path": HOTFIX_PATH, "args": ["-s", "5010", "-k", "faction_swap_team_member_min", "-v", "1"],
                     "description": "热更 5010 服战队交换人数为 1"}]}, ensure_ascii=False)
            marker = "候选决策：\n---\n"
            start = prompt.index(marker) + len(marker)
            return prompt[start:prompt.index("\n---", start)]

    ai = FakeAI()
    planner = MuliuPlanner(ai, HotfixPlannerCatalog(CATALOG_PATH, store), max_steps=10, plan_expander=store.expand_plan)
    plan = await planner.create_plan("给 5010 服热更战队交换人数为 1")

    assert "战队交换人数" in ai.prompts[0] and "-k faction_swap_team_member_min" in ai.prompts[0]
    assert [step.args[-1] for step in plan.steps] == ["common", "client_avatar"]
    assert plan.steps[0].args[3:6] == ["faction_swap_team_member_num_min", "--table", "season_process_season_config"]


def test_threshold_routes(tmp_path):
    store = _store(tmp_path)
    reports = ExecutionReportStore(tmp_path / "db.sqlite3")
    reports.initialize()

    class FakeReviewer:
        async def generate(self, history, prompt):
            assert "union_impeach_min" in prompt
            return '{"consistent": false, "reason": "说明与字段不符", "duplicate_of": "union_impeach_min", "side": "both", "comments": ""}'

    client = TestClient(create_admin_app(reports, threshold_store=store, review_client=FakeReviewer()),
                        base_url="http://127.0.0.1:8901")
    page = client.get("/thresholds")
    assert page.status_code == 200 and "/api/thresholds" in page.text

    assert len(client.get("/api/thresholds").json()["thresholds"]) == 24
    assert client.post("/api/thresholds", json={"key": "x"}).status_code == 403  # missing admin header
    created = client.post("/api/thresholds", headers=HEADERS, json={
        "key": "test_limit", "name": "测试人数", "table_name": "t", "field_name": "f"}).json()
    assert created["key"] == "test_limit" and created["updated_by"] == "ssh-tunnel"
    assert client.post("/api/thresholds", headers=HEADERS, json={
        "key": "test_limit2", "name": "测试人数", "table_name": "t", "field_name": "g"}).status_code == 400
    updated = client.put("/api/thresholds/test_limit", headers=HEADERS, json={"state": "disabled", "key": "ignored"}).json()
    assert updated["state"] == "disabled" and updated["key"] == "test_limit"
    assert len(client.get("/api/thresholds?state=disabled").json()["thresholds"]) == 1
    assert client.put("/api/thresholds/nope", headers=HEADERS, json={"state": "active"}).status_code == 404
    review = client.post("/api/thresholds/review", headers=HEADERS, json={"key": "test_limit", "name": "弹劾"}).json()
    assert review["consistent"] is False and review["duplicate_of"] == "union_impeach_min" and review["side"] == "双端"
    assert [e["field"] for e in client.get("/api/thresholds/events?key=test_limit").json()["events"]] == ["state", "created"]
    assert client.delete("/api/thresholds/test_limit", headers=HEADERS).json() == {"status": "deleted"}
    assert client.delete("/api/thresholds/test_limit", headers=HEADERS).status_code == 404


def test_review_route_without_ai_client(tmp_path):
    store = _store(tmp_path)
    reports = ExecutionReportStore(tmp_path / "db.sqlite3")
    reports.initialize()
    client = TestClient(create_admin_app(reports, threshold_store=store), base_url="http://127.0.0.1:8901")

    assert client.post("/api/thresholds/review", headers=HEADERS, json={}).status_code == 503


def test_snapshot_round_trip(tmp_path):
    store = _store(tmp_path)
    snapshot = store.export_snapshot()
    other = _store(tmp_path / "b", seed=False)

    assert other.import_snapshot(snapshot, operator="t") == 24
    assert other.export_snapshot() == snapshot
