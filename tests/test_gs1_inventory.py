import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).parents[1]
SCRIPT = REPOSITORY_ROOT / "deployment" / "gs1" / "gs1_inventory.py"


def _module():
    spec = importlib.util.spec_from_file_location("gs1_inventory", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_lists_files_and_server_keys_without_values(tmp_path):
    (tmp_path / "start").write_text("#!/bin/bash\necho start\n", encoding="utf-8")
    (tmp_path / "config.ini").write_text(
        "[serverName]\n2006_game=10.1.2.3\n2006_zone=10.1.2.4\n5010_game=10.9.9.9\nmongo_password=hunter2\n",
        encoding="utf-8",
    )
    (tmp_path / "muliuscripts").mkdir()

    lines, code = _module().inventory(str(tmp_path))
    output = "\n".join(lines)

    assert code == 0
    assert "2 个文件，1 个子目录" in output
    digest = hashlib.sha256((tmp_path / "start").read_bytes()).hexdigest()[:16]
    assert "start | " in output and digest in output
    assert "<服号>_game：2 个：2006,5010" in output and "<服号>_zone：1 个：2006" in output
    assert "子目录：muliuscripts" in output and "mongo_password" in output
    for secret in ("10.1.2.3", "10.1.2.4", "10.9.9.9", "hunter2", "echo start"):
        assert secret not in output


def test_inventory_reports_missing_directory(tmp_path):
    lines, code = _module().inventory(str(tmp_path / "missing"))

    assert code == 1 and "无法读取" in lines[-1]


@pytest.mark.parametrize("argv", [[], ["summary", "x"], ["/etc/passwd"]])
def test_script_refuses_any_argv_but_summary(argv):
    result = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True)

    assert result.returncode == 2 and result.stdout == ""


def test_inventory_is_not_a_bot_capability():
    """GS-1 details are for the owner only: no catalog contract, no copy in the Q&A knowledge base."""
    catalog = (REPOSITORY_ROOT / "config" / "muliu_script_catalog.md").read_text(encoding="utf-8")

    assert "gs1_inventory" not in catalog
    assert not (REPOSITORY_ROOT / "knowledge" / "scripts" / "gs1_inventory.py").exists()
