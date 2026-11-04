import json
from pathlib import Path

import pytest

from src.plan_board import PlanError, PlanStore


def _store(tmp_path, seed=None):
    store = PlanStore(tmp_path / "db.sqlite3")
    store.initialize(seed)
    return store


def test_repository_seed_imports_every_step_with_flows():
    snapshot = json.loads(Path("docs/plan-board.json").read_text(encoding="utf-8"))

    assert len(snapshot["steps"]) >= 36
    assert all(step["status"] in {"done", "active", "todo"} for step in snapshot["steps"])
    assert {step["id"] for step in snapshot["steps"] if step.get("flow")} >= {"05", "21"}


def test_seed_only_when_empty_and_snapshot_round_trip(tmp_path):
    store = _store(tmp_path, Path("docs/plan-board.json"))
    total = store.get_plan()["total"]
    store.update_step("01", {"note": "keep"}, operator="me")

    store.initialize(Path("docs/plan-board.json"))  # second start must not reseed
    assert store.get_step("01")["note"] == "keep"

    snapshot = store.export_snapshot()
    other = _store(tmp_path / "other")
    other.import_snapshot(snapshot, operator="restore")
    assert other.get_plan()["total"] == total
    assert other.get_step("01")["note"] == "keep"
    assert other.get_step("21")["flow"]["actors"]


def test_validation_and_events(tmp_path):
    store = _store(tmp_path)
    created = store.create_step({"title": "  想法  ", "done": [" a ", ""]}, operator="me")

    assert created["id"] == "01" and created["title"] == "想法" and created["done"] == ["a"]
    for bad in ({"status": "obsolete"}, {"title": ""}, {"done": "x"}, {"unknown": 1}):
        with pytest.raises(PlanError):
            store.update_step("01", bad, operator="me")
    store.update_step("01", {"status": "active", "title": "想法"}, operator="me")
    assert [e["field"] for e in store.list_events("01")] == ["status", "created"]
    assert store.delete_step("01", operator="me") is True
    assert json.loads(store.list_events("01")[0]["old_value"])["title"] == "想法"
