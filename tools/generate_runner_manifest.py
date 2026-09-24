#!/usr/bin/env python3
"""Generate the Task 89 runner manifest from the single Muliu call catalog."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.muliu_script_catalog import MuliuScriptCatalog


DEFAULT_CATALOG_PATH = REPOSITORY_ROOT / "config" / "muliu_script_catalog.md"
DEFAULT_OUTPUT_PATH = REPOSITORY_ROOT / "deployment" / "gs1" / "muliu_runner_manifest.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a fixed path-to-runner manifest from the Muliu call catalog."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    options = parser.parse_args()

    output_path = MuliuScriptCatalog(options.catalog).write_runner_manifest(options.output)
    print("已生成 runner manifest：{}".format(output_path))


if __name__ == "__main__":
    main()
