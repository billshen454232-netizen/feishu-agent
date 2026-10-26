import json
from pathlib import Path

import pytest

from src.server_environments import ServerEnvironments, ServerEnvironmentsError


REPOSITORY_ROOT = Path(__file__).parents[1]
ENVIRONMENT_PATH = REPOSITORY_ROOT / "config" / "server_environments.json"


def test_checked_in_environment_registry_derives_global_and_overseas_dev_once():
    environments = ServerEnvironments.load(ENVIRONMENT_PATH)

    global_dev = next(
        mapping for mapping in environments.region_mappings() if mapping.key == "global_dev"
    )
    overseas_dev = next(
        mapping for mapping in environments.group_mappings() if mapping.key == "overseas_dev"
    )

    assert global_dev.name == "全球服DEV环境"
    assert global_dev.server_ids == ("5500",)
    assert overseas_dev.server_ids == ("5000", "5001", "5500", "6000", "6001", "7001", "7002")


def test_environment_registry_derives_closed_worker_route_from_registered_region():
    environments = ServerEnvironments.load(ENVIRONMENT_PATH)

    assert environments.route_region_for_server_ids(("2003",)) == "cn"
    assert environments.route_region_for_server_ids(("5500", "6001")) == "overseas"
    assert environments.route_region_for_server_ids(("5000", "5001")) == "overseas"
    with pytest.raises(ServerEnvironmentsError, match="不能同时包含国服和海外服"):
        environments.route_region_for_server_ids(("2003", "5500"))


def test_environment_registry_rejects_duplicate_server_membership():
    raw = json.loads(ENVIRONMENT_PATH.read_text(encoding="utf-8"))
    raw["regions"]["global"]["environments"]["dev"].append("5000")

    with pytest.raises(ServerEnvironmentsError, match="5000.*重复登记"):
        ServerEnvironments.from_data(raw)


def test_environment_registry_rejects_unknown_group_region():
    raw = json.loads(ENVIRONMENT_PATH.read_text(encoding="utf-8"))
    raw["groups"]["overseas"]["regions"].append("unknown")

    with pytest.raises(ServerEnvironmentsError, match="不存在的区域 unknown"):
        ServerEnvironments.from_data(raw)
