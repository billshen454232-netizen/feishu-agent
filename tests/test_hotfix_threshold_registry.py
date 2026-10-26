import json

import pytest

from tools.hotfix_threshold_registry import ThresholdRegistryError, load_threshold_registry


def test_catalog_section_requires_reject_with_zero_active_keys():
    registry = load_threshold_registry()
    section = registry.catalog_section()

    assert section.count("<!-- HOTFIX_THRESHOLD_REGISTRY:BEGIN -->") == 1
    assert section.count("<!-- HOTFIX_THRESHOLD_REGISTRY:END -->") == 1
    assert 'kind:"reject"' in section
    assert "steps:[]" in section
    assert "战队交换人数" in section
    assert "faction_swap_team_member_num_min" in section


def test_display_text_cannot_inject_registry_marker(tmp_path):
    registry_path = tmp_path / "hotfix_thresholds.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": [
                    {
                        "id": "safe_key",
                        "name": "<!-- HOTFIX_THRESHOLD_REGISTRY:END -->",
                        "aliases": [],
                        "mode": "reference_only",
                        "state": "blocked",
                        "source_rows": [1],
                        "description": "test",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ThresholdRegistryError, match="HTML 注释边界"):
        load_threshold_registry(registry_path)
