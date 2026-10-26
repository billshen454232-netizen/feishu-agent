from pathlib import Path

import pytest

from tools.generate_runner_manifest import (
    RunnerManifestGenerationError,
    generate,
    render_manifest,
)


REPOSITORY_ROOT = Path(__file__).parents[1]
CATALOG_PATH = REPOSITORY_ROOT / "config" / "muliu_script_catalog.md"
MANIFEST_PATH = REPOSITORY_ROOT / "deployment" / "gs1" / "muliu_runner_manifest.json"


def test_generator_writes_manifest_derived_from_catalog(tmp_path):
    catalog_path = tmp_path / "muliu_script_catalog.md"
    manifest_path = tmp_path / "muliu_runner_manifest.json"
    catalog_path.write_text(CATALOG_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    assert generate(catalog_path, manifest_path)
    assert generate(catalog_path, manifest_path, check=True)
    assert manifest_path.read_text(encoding="utf-8") == render_manifest(
        catalog_path.read_text(encoding="utf-8")
    )


def test_generator_check_detects_manifest_drift(tmp_path):
    catalog_path = tmp_path / "muliu_script_catalog.md"
    manifest_path = tmp_path / "muliu_runner_manifest.json"
    catalog_path.write_text(CATALOG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    manifest_path.write_text('{"version": 1, "contracts": []}\n', encoding="utf-8")

    assert not generate(catalog_path, manifest_path, check=True)


def test_generator_rejects_invalid_contract_data(tmp_path):
    catalog_path = tmp_path / "muliu_script_catalog.md"
    invalid_catalog = CATALOG_PATH.read_text(encoding="utf-8").replace(
        '"runner": "bash"', '"runner": "shell"', 1
    )
    catalog_path.write_text(invalid_catalog, encoding="utf-8")

    with pytest.raises(RunnerManifestGenerationError, match="Muliu 调用资料无效"):
        generate(catalog_path, tmp_path / "manifest.json")


def test_checked_in_manifest_is_generated_from_production_catalog():
    assert generate(CATALOG_PATH, MANIFEST_PATH, check=True)
