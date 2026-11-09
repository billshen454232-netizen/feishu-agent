import importlib.util
from pathlib import Path

import pytest

from src.baye_check import load_accounts

_SPEC = importlib.util.spec_from_file_location(
    "install_baye_accounts", Path(__file__).resolve().parents[1] / "deployment/worker/install_baye_accounts.py"
)
installer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(installer)

TABLE = "服号,完整服务器名,账号,密码,服号,设备\nS11800,1800服 鼎业千秋,qa01,secret,S11800,\n"


def test_installs_gbk_table_readable_by_worker_and_hides_secrets(tmp_path, capsys):
    source, target = tmp_path / "upload.csv", tmp_path / "baye_accounts.csv"
    source.write_bytes(TABLE.encode("gbk"))

    assert installer.main(["x", str(source), str(target)]) == 0

    out = capsys.readouterr().out
    assert "1800" in out and "secret" not in out and "qa01" not in out
    assert not source.exists()
    assert load_accounts(target)["1800"].password == "secret"


@pytest.mark.parametrize("content, reason", [
    (b"PK\x03\x04rest", "xlsx"),
    ("服号,账号\n1800,qa01\n".encode("utf-8"), "缺少列"),
    ("服号,完整服务器名,账号,密码\nS11800,1800服 鼎业千秋,qa01,\n".encode("utf-8"), "第 2 行缺少：密码"),
    ("服号,完整服务器名,账号,密码\n".encode("utf-8"), "没有任何账号行"),
])
def test_rejected_upload_keeps_the_previous_table(tmp_path, capsys, content, reason):
    source, target = tmp_path / "upload.csv", tmp_path / "baye_accounts.csv"
    target.write_text("old", encoding="utf-8")
    source.write_bytes(content)

    assert installer.main(["x", str(source), str(target)]) == 1

    assert reason in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "old"
    assert not source.exists()
