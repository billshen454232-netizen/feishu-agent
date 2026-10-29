#!/usr/bin/env python3
"""Generate a catalog-derived candidate GS-1 manifest for deployment review.

The repository runtime does not read the output. Its presence or a successful
consistency check is not evidence that GS-1 has deployed or consumes it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.muliu_script_catalog import (
    MuliuCallContractError,
    MuliuScriptCatalog,
    MuliuScriptCatalogError,
)


DEFAULT_CATALOG_PATH = REPOSITORY_ROOT / "config" / "muliu_script_catalog.md"
DEFAULT_OUTPUT_PATH = REPOSITORY_ROOT / "deployment" / "gs1" / "muliu_runner_manifest.json"


class RunnerManifestGenerationError(RuntimeError):
    """Raised when the runner manifest cannot be generated from its catalog."""


def render_manifest(catalog_text: str) -> str:
    """Render the candidate manifest derived from one catalog snapshot."""
    try:
        manifest = MuliuScriptCatalog.from_text(catalog_text).read_runner_manifest()
    except (MuliuCallContractError, MuliuScriptCatalogError) as exc:
        raise RunnerManifestGenerationError("Muliu 调用资料无效：{}".format(exc)) from exc
    return json.dumps(manifest.as_jsonable(), ensure_ascii=False, indent=2) + "\n"


def generate(
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    *,
    check: bool = False,
) -> bool:
    """Generate or check the manifest derived from one catalog.

    ``check=True`` never writes and returns ``False`` when the checked-in
    artifact differs from the catalog.  The catalog parser remains the sole
    source of path, argv, runner, and risk validation.
    """
    catalog_path = Path(catalog_path)
    output_path = Path(output_path)
    try:
        rendered_manifest = render_manifest(catalog_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RunnerManifestGenerationError("无法读取 Muliu 调用资料：{}".format(catalog_path)) from exc

    if check:
        try:
            return output_path.read_text(encoding="utf-8") == rendered_manifest
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise RunnerManifestGenerationError("无法读取 runner manifest：{}".format(output_path)) from exc

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered_manifest, encoding="utf-8")
    except OSError as exc:
        raise RunnerManifestGenerationError("无法写入 runner manifest：{}".format(output_path)) from exc
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a catalog-derived candidate runner manifest for deployment review."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--check", action="store_true", help="仅检查 manifest 是否与调用资料一致")
    options = parser.parse_args()

    if generate(options.catalog, options.output, check=options.check):
        print("runner manifest 与调用资料一致：{}".format(options.output))
        return
    print("runner manifest 未由当前调用资料生成：{}".format(options.output), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
