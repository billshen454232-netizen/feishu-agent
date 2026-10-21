from pathlib import Path

import pytest

from tools.sync_hotfix_planner_catalog import (
    HotfixCatalogSyncError,
    check_artifacts,
    expected_artifacts,
    render_catalog,
)


REPOSITORY_ROOT = Path(__file__).parents[1]
CATALOG_PATH = REPOSITORY_ROOT / "config" / "muliu_script_catalog.md"


def test_checked_in_hotfix_static_artifacts_match_execution_registry():
    assert check_artifacts() == ()


def test_generated_catalog_contains_requested_chinese_routes_and_strict_keys():
    rendered = expected_artifacts()[CATALOG_PATH]

    assert "全球服DEV环境" in rendered
    assert "海外所有DEV环境`：`5000,5001,5500,6000,6001,7001,7002" in rendered
    assert '"--servers", "5000,5001,5500,6000,6001,7001,7002"' in rendered
    assert "剧本预选州战队人数显示阈值" in rendered
    assert "-k faction_team_show_member_limit" in rendered
    assert "战队交换人数" in rendered
    assert "-k faction_swap_team_member_min" in rendered
    assert '"key": "[A-Za-z0-9_]{1,64}"' not in rendered
    assert '"key": "(?:' in rendered


def test_catalog_sync_rejects_missing_generated_route_marker():
    source = CATALOG_PATH.read_text(encoding="utf-8")

    with pytest.raises(HotfixCatalogSyncError, match="标记对"):
        render_catalog(source.replace("<!-- HOTFIX_THRESHOLD_REGISTRY:END -->", "", 1))
    with pytest.raises(HotfixCatalogSyncError, match="标记对"):
        render_catalog(source.replace("<!-- ENVIRONMENT_MAPPING:END -->", "", 1))


def test_catalog_sync_is_idempotent_after_generation():
    source = CATALOG_PATH.read_text(encoding="utf-8")

    assert render_catalog(source) == source
