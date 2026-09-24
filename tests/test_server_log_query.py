"""Unit tests for the GS-1-only fixed game-server log query script.

The deployment script deliberately lives outside ``src/`` because Muliu executes
it on GS-1. These tests load its local source without attempting SSH.
"""

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "deployment"
    / "gs1"
    / "server_log_query.py"
)
SPEC = importlib.util.spec_from_file_location("server_log_query", SCRIPT_PATH)
server_log_query = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server_log_query)


def test_accepts_only_fixed_request_values():
    for profile in server_log_query.PROFILE_CHOICES:
        server_log_query.validate_request("6000", profile, 50)


@pytest.mark.parametrize("server_id", ["", "12", "6000a", "6000;id", "600000000"])
def test_rejects_invalid_server_id(server_id):
    with pytest.raises(server_log_query.LogQueryError, match="server_id"):
        server_log_query.validate_request(server_id, "game_recent", 50)


@pytest.mark.parametrize("max_lines", [9, 101, True, "50"])
def test_rejects_out_of_range_or_non_integer_line_limit(max_lines):
    with pytest.raises(server_log_query.LogQueryError, match="max_lines"):
        server_log_query.validate_request("6000", "game_recent", max_lines)


def test_rejects_unregistered_profile():
    with pytest.raises(server_log_query.LogQueryError, match="固定日志档案"):
        server_log_query.validate_request("6000", "arbitrary_grep", 50)


def test_resolves_only_matching_game_route(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "6000_zone = 10.0.0.1\n6000_game = gs-game-6000.internal\n",
        encoding="utf-8",
    )

    assert (
        server_log_query.resolve_target_host("6000", config_path)
        == "gs-game-6000.internal"
    )


def test_does_not_fall_back_to_another_server_route(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("6001_game = 10.0.0.2\n", encoding="utf-8")

    with pytest.raises(server_log_query.LogQueryError, match="没有该服务器编号"):
        server_log_query.resolve_target_host("6000", config_path)


def test_resolves_first_route_when_config_has_utf8_bom(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "6000_game = gs-game-6000.internal\n",
        encoding="utf-8-sig",
    )

    assert (
        server_log_query.resolve_target_host("6000", config_path)
        == "gs-game-6000.internal"
    )


def test_rejects_undecodable_route_configuration(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_bytes(b"6000_game = gs-game-6000.internal\xff\n")

    with pytest.raises(server_log_query.LogQueryError, match="无法读取 GS-1 路由配置"):
        server_log_query.resolve_target_host("6000", config_path)


@pytest.mark.parametrize(
    "configured_host",
    ["root@10.0.0.2", "10.0.0.2;id", "host name", "$(id)"],
)
def test_rejects_unsafe_target_host_from_config(tmp_path, configured_host):
    config_path = tmp_path / "config.ini"
    config_path.write_text("6000_game = {}\n".format(configured_host), encoding="utf-8")

    with pytest.raises(server_log_query.LogQueryError, match="目标主机格式不安全"):
        server_log_query.resolve_target_host("6000", config_path)


def test_query_remote_passes_only_fixed_ssh_argv_and_static_bash_source(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        captured["shell"] = kwargs["shell"]
        captured["timeout"] = kwargs["timeout"]
        return SimpleNamespace(
            returncode=0,
            stdout=b"LOG_QUERY_PROFILE=game_recent\nLOG_QUERY_STATUS=OK\n",
        )

    monkeypatch.setattr(server_log_query.subprocess, "run", fake_run)

    result = server_log_query.query_remote("gs-game-6000.internal", "game_recent", 50)

    assert result == "LOG_QUERY_PROFILE=game_recent\nLOG_QUERY_STATUS=OK"
    assert captured["command"] == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "LogLevel=ERROR",
        "root@gs-game-6000.internal",
        "bash",
        "-s",
        "--",
        "game_recent",
        "50",
    ]
    assert captured["input"] == server_log_query.REMOTE_QUERY_SCRIPT.encode("utf-8")
    assert captured["shell"] is False
    assert captured["timeout"] == 30


def test_query_remote_never_forwards_raw_ssh_failure_output(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=255,
            stdout=b"ssh: Could not resolve hostname gs-game-6000.internal\n",
        )

    monkeypatch.setattr(server_log_query.subprocess, "run", fake_run)

    with pytest.raises(server_log_query.LogQueryError) as error:
        server_log_query.query_remote("gs-game-6000.internal", "game_recent", 50)

    assert "gs-game-6000.internal" not in str(error.value)
    assert "Could not resolve hostname" not in str(error.value)


def test_remote_failure_exposes_only_fixed_status_marker(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=(
                b"LOG_QUERY_PROFILE=game_recent\n"
                b"secret=do-not-return\n"
                b"LOG_QUERY_STATUS=NOT_FOUND\n"
            ),
        )

    monkeypatch.setattr(server_log_query.subprocess, "run", fake_run)

    with pytest.raises(server_log_query.LogQueryError) as error:
        server_log_query.query_remote("gs-game-6000.internal", "game_recent", 50)

    assert str(error.value).endswith("LOG_QUERY_STATUS=NOT_FOUND")
    assert "secret" not in str(error.value)


def test_rejects_success_transport_without_terminal_status(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=b"LOG_QUERY_PROFILE=game_recent\npartial log\n",
        )

    monkeypatch.setattr(server_log_query.subprocess, "run", fake_run)

    with pytest.raises(server_log_query.LogQueryError, match="状态协议不完整"):
        server_log_query.query_remote("gs-game-6000.internal", "game_recent", 50)


def test_preserves_status_when_success_payload_requires_truncation(monkeypatch):
    raw_output = "\n".join([
        "LOG_QUERY_PROFILE=startup_recent",
        *(["x" * server_log_query.MAX_LINE_CHARS] * 100),
        "LOG_QUERY_STATUS=OK",
        "",
    ]).encode("utf-8")

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=raw_output)

    monkeypatch.setattr(server_log_query.subprocess, "run", fake_run)

    output = server_log_query.query_remote(
        "gs-game-6000.internal",
        "startup_recent",
        100,
    )

    assert output.startswith("LOG_QUERY_PROFILE=startup_recent\n")
    assert server_log_query.TRUNCATION_MARKER in output
    assert output.endswith("LOG_QUERY_STATUS=OK")
    assert len(output) <= server_log_query.MAX_OUTPUT_CHARS


def test_rejects_fatal_remote_status_even_when_ssh_succeeds(monkeypatch):
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=b"LOG_QUERY_PROFILE=game_recent\nLOG_QUERY_STATUS=READ_ERROR\n",
        )

    monkeypatch.setattr(server_log_query.subprocess, "run", fake_run)

    with pytest.raises(server_log_query.LogQueryError, match="READ_ERROR"):
        server_log_query.query_remote("gs-game-6000.internal", "game_recent", 50)


def test_sanitizes_common_sensitive_values_and_bounds_output():
    raw_output = "\n".join([
        "token=top-secret",
        "authorization: Bearer eyJhbGciOi.secret",
        "Proxy-Authorization: Basic dXNlcjpwYXNz",
        '{"token":"json-token"}',
        '{"access_token":"access-secret"}',
        '{"refresh_token":"refresh-secret"}',
        '{"client_secret":"client-secret"}',
        '{"db_password":"database-secret"}',
        "connect 10.42.0.8",
        "https://user:password@example.internal/path",
        "\n".join(["x" * server_log_query.MAX_LINE_CHARS] * 20),
    ])

    output = server_log_query.sanitize_output(raw_output)

    for secret in (
        "top-secret",
        "eyJhbGciOi.secret",
        "dXNlcjpwYXNz",
        "json-token",
        "access-secret",
        "refresh-secret",
        "client-secret",
        "database-secret",
        "10.42.0.8",
        "user:password",
    ):
        assert secret not in output
    assert "[REDACTED]" in output
    assert "[IP_REDACTED]" in output
    assert server_log_query.TRUNCATION_MARKER in output
    assert len(output) <= server_log_query.MAX_OUTPUT_CHARS


def test_remote_program_has_valid_bash_syntax():
    project_relative_path = SCRIPT_PATH.parent / "_test_remote_log_query.sh"
    try:
        project_relative_path.write_bytes(
            server_log_query.REMOTE_QUERY_SCRIPT.encode("utf-8")
        )
        completed = subprocess.run(
            ["bash", "-n", "_test_remote_log_query.sh"],
            cwd=SCRIPT_PATH.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    finally:
        project_relative_path.unlink()

    assert completed.returncode == 0, completed.stdout.decode("utf-8", errors="replace")


def _write_remote_test_script(script_name, script):
    script_path = SCRIPT_PATH.parent / script_name
    script_path.write_bytes(script.encode("utf-8"))
    return script_path


def _run_remote_test_script(script_path, profile, max_lines):
    return subprocess.run(
        ["bash", script_path.name, profile, str(max_lines)],
        cwd=SCRIPT_PATH.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def run_remote_bash(tmp_path, profile, max_lines, files):
    log_root = tmp_path / "data" / "log"
    supervisor_log = log_root / "supervisor" / "supervisor_game_6.log"
    game_log_dir = log_root / "bglog" / "game"
    supervisor_log.parent.mkdir(parents=True)
    game_log_dir.mkdir(parents=True)
    supervisor_log.write_text("startup ok\n", encoding="utf-8")
    for name, content in files.items():
        (game_log_dir / name).write_text(content, encoding="utf-8")

    script = server_log_query.REMOTE_QUERY_SCRIPT.replace(
        'readonly log_root="/data/log"',
        'readonly log_root="{}"'.format(log_root.as_posix()),
    ).replace(
        'readonly supervisor_log="/data/log/supervisor/supervisor_game_6.log"',
        'readonly supervisor_log="{}"'.format(supervisor_log.as_posix()),
    ).replace(
        'readonly game_log_dir="/data/log/bglog/game"',
        'readonly game_log_dir="{}"'.format(game_log_dir.as_posix()),
    )
    script_path = _write_remote_test_script("_test_remote_log_query.sh", script)
    try:
        return _run_remote_test_script(script_path, profile, max_lines)
    finally:
        script_path.unlink()


def test_remote_bash_handles_missing_supervisor_file_with_status(tmp_path):
    log_root = tmp_path / "data" / "log"
    game_log_dir = log_root / "bglog" / "game"
    supervisor_log = log_root / "supervisor" / "supervisor_game_6.log"
    game_log_dir.mkdir(parents=True)
    supervisor_log.parent.mkdir(parents=True)

    script = server_log_query.REMOTE_QUERY_SCRIPT.replace(
        'readonly log_root="/data/log"',
        'readonly log_root="{}"'.format(log_root.as_posix()),
    ).replace(
        'readonly supervisor_log="/data/log/supervisor/supervisor_game_6.log"',
        'readonly supervisor_log="{}"'.format(supervisor_log.as_posix()),
    ).replace(
        'readonly game_log_dir="/data/log/bglog/game"',
        'readonly game_log_dir="{}"'.format(game_log_dir.as_posix()),
    )
    script_path = _write_remote_test_script("_test_missing_supervisor_query.sh", script)
    try:
        result = _run_remote_test_script(script_path, "startup_recent", 10)
    finally:
        script_path.unlink()

    output = result.stdout.decode("utf-8")
    assert result.returncode == 0
    assert output.rstrip().endswith("LOG_QUERY_STATUS=NOT_FOUND")


def test_remote_program_keeps_filename_out_of_interpreted_programs():
    source = server_log_query.REMOTE_QUERY_SCRIPT

    assert 'sed "s#^#$(basename -- "$path"):#"' not in source
    assert 'awk -v max="$remaining" -v pattern="$pattern"' in source
    assert 'print "game_log_match:" substr($0, 1, 783)' in source


def test_remote_program_has_no_dynamic_request_interpreter():
    source = server_log_query.REMOTE_QUERY_SCRIPT

    assert "eval " not in source
    assert "bash -c" not in source
    assert "python -c" not in source
    assert "REMOTE_HOST" not in source
    assert 'readonly log_root="/data/log"' in source
    assert 'readonly game_log_dir="/data/log/bglog/game"' in source
    assert 'readonly match_source_lines=2000' in source
    assert 'tail -n "$match_source_lines" -- "$path"' in source
    assert 'emit "LOG_QUERY_STATUS=$query_status"' in source
