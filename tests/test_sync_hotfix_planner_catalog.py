from pathlib import Path

import pytest

from src.muliu_script_catalog import parse_call_contracts
from tools.sync_hotfix_planner_catalog import (
    HotfixCatalogSyncError,
    check_artifacts,
    expected_artifacts,
    render_catalog,
)


REPOSITORY_ROOT = Path(__file__).parents[1]
CATALOG_PATH = REPOSITORY_ROOT / "config" / "muliu_script_catalog.md"


def test_generated_catalog_contains_environment_mapping_and_only_executable_hotfix_contracts():
    rendered = expected_artifacts()[CATALOG_PATH]

    assert "全球服DEV环境" in rendered
    assert "海外所有DEV环境`：`5000,5001,5500,5501,6000,6001,7001,7002" in rendered
    assert '"--servers", "5000,5001,5500,5501,6000,6001,7001,7002"' in rendered
    # Hotfix routes are rendered live by the Gateway registry, never checked in.
    assert "-k faction_swap_team_member_min" not in rendered
    hotfix = [c for c in parse_call_contracts(rendered).contracts if c.path.endswith("auto_hotfix_tool.py")]
    assert sorted(c.name for c in hotfix) == ["cleanup-legacy-auto-hotfix", "hotfix-threshold-apply", "restore-auto-hotfix"]
    apply = next(c for c in hotfix if c.name == "hotfix-threshold-apply")
    assert apply.args == ("-s", "{server_id}", "-k", "{field}", "--table", "{table}", "-v", "{value}", "--patch-type", "{patch_type}")


def test_checked_in_artifacts_are_in_sync():
    assert check_artifacts() == ()


def test_catalog_sync_rejects_missing_generated_environment_marker():
    source = CATALOG_PATH.read_text(encoding="utf-8")

    with pytest.raises(HotfixCatalogSyncError, match="标记对"):
        render_catalog(source.replace("<!-- ENVIRONMENT_MAPPING:END -->", "", 1))
