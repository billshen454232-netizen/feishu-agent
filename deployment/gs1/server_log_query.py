#!/usr/bin/env python3.7
"""Read fixed diagnostic profiles from a routed test game server.

Deploy this file to GS-1 as:

    /home/serverGeneralScript/server_log_query.py

It is intentionally not a general remote-command runner. The caller may select
only a known diagnostic profile. The script resolves a numeric server ID through
its colocated config.ini, then sends one fixed Bash program to the target server
through the existing GS-1 SSH route.
"""

from __future__ import print_function

import argparse
import os
import re
import subprocess
import sys


SERVER_ID_RE = re.compile(r"^[0-9]{3,8}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
PROFILE_CHOICES = (
    "layout",
    "startup_recent",
    "startup_errors",
    "game_recent",
    "game_trace",
    "patch_errors",
)
MIN_LINES = 10
MAX_LINES = 100
MAX_OUTPUT_CHARS = 12000
MAX_LINE_CHARS = 800
TRUNCATION_MARKER = "[LOG_QUERY_TRUNCATED]"

REMOTE_STATUSES = frozenset((
    "OK",
    "NOT_FOUND",
    "NO_GAME_LOG_FILES",
    "NO_MATCH",
    "READ_ERROR",
    "INVALID_ARGUMENT",
    "INVALID_PROFILE",
))
FATAL_REMOTE_STATUSES = frozenset(("READ_ERROR", "INVALID_ARGUMENT", "INVALID_PROFILE"))
_REMOTE_STATUS_RE = re.compile(
    r"^LOG_QUERY_STATUS=({})$".format("|".join(REMOTE_STATUSES))
)

# The body is static. Its only positional arguments are a locally validated
# profile name and a bounded integer. It never receives a user-supplied path,
# host, shell fragment, regular expression, or search text.
REMOTE_QUERY_SCRIPT = r'''#!/usr/bin/env bash
set -uo pipefail

profile="${1:-}"
max_lines="${2:-}"
readonly log_root="/data/log"
readonly supervisor_log="/data/log/supervisor/supervisor_game_6.log"
readonly game_log_dir="/data/log/bglog/game"
readonly max_files=20
readonly match_source_lines=2000
query_status="OK"

emit() {
  # Bound every returned line before it crosses the remote boundary.
  printf '%s\n' "$1" | cut -c1-800
}

emit_section() {
  emit ""
  emit "===== $1 ====="
}

set_read_error() {
  query_status="READ_ERROR"
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    query_status="NOT_FOUND"
    emit "固定日志文件不存在：$path"
    return 1
  fi
  return 0
}

recent_game_files() {
  if [[ ! -d "$game_log_dir" ]]; then
    return 0
  fi
  # Output is timestamp, size and absolute file path. Paths only flow back into
  # fixed commands as quoted data; they are never interpolated into a program.
  # No `head` is used here: head can close early and turn a healthy upstream
  # process into SIGPIPE under pipefail.
  local output
  if ! output="$(
    find "$game_log_dir" -maxdepth 1 -type f -name '*.log' -printf '%T@|%s|%p\n' 2>/dev/null \
      | sort -t '|' -k1,1nr \
      | sed -n "1,${max_files}p"
  )"; then
    return 1
  fi
  if [[ -n "$output" ]]; then
    printf '%s\n' "$output"
  fi
  return 0
}

emit_game_file_overview() {
  local entries mtime size path
  if ! entries="$(recent_game_files)"; then
    set_read_error
    emit "固定 game 日志目录读取失败"
    return 1
  fi
  if [[ -z "$entries" ]]; then
    query_status="NO_GAME_LOG_FILES"
    emit "固定目录中没有匹配的 .log 文件：$game_log_dir"
    return 1
  fi

  emit "最近修改的 game 日志（最多 $max_files 个）："
  while IFS='|' read -r mtime size path; do
    [[ -z "${path:-}" ]] && continue
    # A basename is only displayed as data through printf; it is not used in a
    # sed/awk program or any shell-evaluated position.
    emit "mtime_epoch=$mtime size_bytes=$size file=$(basename -- "$path")"
  done <<< "$entries"
  return 0
}

emit_bounded_tail() {
  local path="$1"
  local output
  if ! output="$(tail -n "$max_lines" -- "$path" 2>/dev/null)"; then
    set_read_error
    emit "固定日志文件读取失败"
    return 1
  fi
  if [[ -n "$output" ]]; then
    # awk reads all input before printing its bounded lines, so it cannot cause
    # an upstream tail to fail from a closed pipe under pipefail.
    printf '%s\n' "$output" | awk '
      {
        gsub(/\000/, "")
        print substr($0, 1, 800)
      }
    '
  fi
  return 0
}

emit_startup_errors() {
  local output
  if ! output="$(tail -n "$match_source_lines" -- "$supervisor_log" 2>/dev/null)"; then
    set_read_error
    emit "固定启动日志读取失败"
    return 1
  fi
  output="$(printf '%s\n' "$output" | awk -v max="$max_lines" '
    BEGIN { IGNORECASE=1; emitted=0 }
    {
      gsub(/\000/, "")
      if ($0 ~ /error|fail|exception|fatal|panic|listen|traceback/ && emitted < max) {
        print "startup_log_match:" substr($0, 1, 780)
        emitted++
      }
    }
  ')"
  if [[ -z "$output" ]]; then
    query_status="NO_MATCH"
    emit "固定档案未发现匹配日志"
    return 0
  fi
  printf '%s\n' "$output"
  return 0
}

emit_game_recent() {
  local entries latest
  emit_section "近期 game 日志"
  if ! entries="$(recent_game_files)"; then
    set_read_error
    emit "固定 game 日志目录读取失败"
    return 1
  fi
  if [[ -z "$entries" ]]; then
    query_status="NO_GAME_LOG_FILES"
    emit "固定目录中没有匹配的 .log 文件：$game_log_dir"
    return 0
  fi

  emit "最近修改的 game 日志（最多 $max_files 个）："
  while IFS='|' read -r mtime size path; do
    [[ -z "${path:-}" ]] && continue
    emit "mtime_epoch=$mtime size_bytes=$size file=$(basename -- "$path")"
  done <<< "$entries"

  latest="$(printf '%s\n' "$entries" | sed -n '1p' | cut -d '|' -f3-)"
  if [[ -z "$latest" || ! -f "$latest" ]]; then
    set_read_error
    emit "最新固定 game 日志文件不可读取"
    return 1
  fi
  emit "最新文件：$(basename -- "$latest")"
  emit_bounded_tail "$latest"
}

emit_recent_matches() {
  local pattern="$1"
  local entries path file_matches emitted remaining
  local found_match=0

  if ! entries="$(recent_game_files)"; then
    set_read_error
    emit "固定 game 日志目录读取失败"
    return 1
  fi
  if [[ -z "$entries" ]]; then
    query_status="NO_GAME_LOG_FILES"
    emit "固定目录中没有匹配的 .log 文件：$game_log_dir"
    return 0
  fi

  remaining="$max_lines"
  while IFS='|' read -r _mtime _size path; do
    [[ -z "${path:-}" ]] && continue
    [[ "$remaining" -le 0 ]] && break

    # The match pattern is selected only by this static script. awk receives
    # both the pattern and log content as data, not as executable source; the
    # displayed prefix is fixed so an untrusted filename cannot alter awk/sed.
    if ! file_matches="$(tail -n "$match_source_lines" -- "$path" 2>/dev/null)"; then
      set_read_error
      emit "固定 game 日志文件读取失败"
      return 1
    fi
    file_matches="$(printf '%s\n' "$file_matches" | awk -v max="$remaining" -v pattern="$pattern" '
      BEGIN { IGNORECASE=1; emitted=0 }
      {
        gsub(/\000/, "")
        if ($0 ~ pattern && emitted < max) {
          print "game_log_match:" substr($0, 1, 783)
          emitted++
        }
      }
    ')"

    if [[ -n "$file_matches" ]]; then
      printf '%s\n' "$file_matches"
      found_match=1
      emitted="$(printf '%s\n' "$file_matches" | awk 'END { print NR }')"
      remaining=$((remaining - emitted))
    fi
  done <<< "$entries"

  if [[ "$found_match" -eq 0 ]]; then
    query_status="NO_MATCH"
    emit "固定档案未发现匹配日志"
  fi
  return 0
}

emit_layout() {
  local layout_output
  emit_section "固定日志目录布局"
  if [[ ! -d "$log_root" ]]; then
    query_status="NOT_FOUND"
    emit "固定日志根目录不存在：$log_root"
    return 0
  fi
  if ! layout_output="$(
    find "$log_root" -mindepth 1 -maxdepth 2 -type d -printf 'DIR %p\n' 2>/dev/null \
      | sort \
      | sed -n '1,50p'
  )"; then
    set_read_error
    emit "固定日志目录读取失败"
    return 1
  fi
  if [[ -n "$layout_output" ]]; then
    printf '%s\n' "$layout_output" | awk '
      {
        gsub(/\000/, "")
        print substr($0, 1, 800)
      }
    '
  fi

  emit_section "game 日志文件概览"
  emit_game_file_overview || true
  return 0
}

if ! [[ "$max_lines" =~ ^[0-9]+$ ]] \
  || (( max_lines < 10 || max_lines > 100 )); then
  query_status="INVALID_ARGUMENT"
  emit "LOG_QUERY_PROFILE=$profile"
  emit "LOG_QUERY_STATUS=$query_status"
  exit 0
fi

emit "LOG_QUERY_PROFILE=$profile"

case "$profile" in
  layout)
    emit_layout
    ;;

  startup_recent)
    emit_section "近期启动日志"
    if require_file "$supervisor_log"; then
      emit_bounded_tail "$supervisor_log"
    fi
    ;;

  startup_errors)
    emit_section "近期启动异常信号"
    if require_file "$supervisor_log"; then
      emit_startup_errors
    fi
    ;;

  game_recent)
    emit_game_recent
    ;;

  game_trace)
    emit_section "近期 game Trace / 异常信号"
    emit_recent_matches 'trace|exception|error|fatal|panic|assert'
    ;;

  patch_errors)
    emit_section "近期 game Patch 异常信号"
    emit_recent_matches 'run_patch[[:space:]_]*error|patch.*(error|fail)|error.*patch'
    ;;

  *)
    query_status="INVALID_PROFILE"
    emit "固定日志档案不允许"
    ;;
esac

emit "LOG_QUERY_STATUS=$query_status"
'''

_SENSITIVE_FIELD_NAME = (
    r"(?:authorization|proxy[-_]?authorization|cookie|set[-_]?cookie|"
    r"(?:access|refresh|id)[_-]?token|token|"
    r"(?:api|client|db)[_-]?(?:key|secret|password)|"
    r"password|passwd|pwd|secret|session(?:[_-]?id)?)"
)
_HEADER_SECRET_RE = re.compile(
    r"(?im)(^\s*(?:authorization|proxy[-_]?authorization|cookie|set[-_]?cookie)\s*:\s*)([^\r\n]*)"
)
_QUOTED_SECRET_VALUE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])([\"']?{}[\"']?\s*[:=]\s*[\"'])[^\"'\r\n]*".format(
        _SENSITIVE_FIELD_NAME
    )
)
_UNQUOTED_SECRET_VALUE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])([\"']?{}[\"']?\s*[:=]\s*)([^\s,;}}\]\r\n]+)".format(
        _SENSITIVE_FIELD_NAME
    )
)
_URL_CREDENTIALS_RE = re.compile(
    r"(?i)([A-Za-z][A-Za-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"
)
IPV4_RE = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")


class LogQueryError(RuntimeError):
    """Raised when the fixed diagnostic query cannot be completed."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="查询目标游戏服的固定只读日志诊断档案"
    )
    parser.add_argument("--server-id", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--max-lines", default="50")
    return parser.parse_args(argv)


def validate_request(server_id, profile, max_lines):
    if not isinstance(server_id, str) or SERVER_ID_RE.fullmatch(server_id) is None:
        raise LogQueryError("server_id 必须是 3 到 8 位数字")
    if profile not in PROFILE_CHOICES:
        raise LogQueryError("profile 不在允许的固定日志档案中")
    if isinstance(max_lines, bool) or not isinstance(max_lines, int):
        raise LogQueryError("max_lines 必须是整数")
    if max_lines < MIN_LINES or max_lines > MAX_LINES:
        raise LogQueryError(
            "max_lines 必须在 {} 到 {} 之间".format(MIN_LINES, MAX_LINES)
        )


def parse_max_lines(raw_max_lines):
    try:
        max_lines = int(raw_max_lines)
    except (TypeError, ValueError):
        raise LogQueryError("max_lines 必须是整数")
    if str(max_lines) != str(raw_max_lines).strip():
        raise LogQueryError("max_lines 必须是十进制整数")
    return max_lines


def resolve_target_host(server_id, config_path):
    expected_key = "{}_game".format(server_id)
    try:
        with open(config_path, "r", encoding="utf-8-sig") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeError) as error:
        raise LogQueryError("无法读取 GS-1 路由配置") from error

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != expected_key:
            continue
        host = value.strip()
        if HOST_RE.fullmatch(host) is None:
            raise LogQueryError("GS-1 路由配置中的目标主机格式不安全")
        return host
    raise LogQueryError("GS-1 路由配置中没有该服务器编号")


def query_remote(host, profile, max_lines):
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "LogLevel=ERROR",
        "root@{}".format(host),
        "bash",
        "-s",
        "--",
        profile,
        str(max_lines),
    ]
    try:
        completed = subprocess.run(
            command,
            input=REMOTE_QUERY_SCRIPT.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            shell=False,
        )
    except OSError as error:
        raise LogQueryError("GS-1 无法启动既有 SSH 客户端") from error
    except subprocess.TimeoutExpired as error:
        raise LogQueryError("目标游戏服日志查询超时") from error

    output = decode_output(completed.stdout)
    terminal_status = extract_terminal_status(output)
    if completed.returncode != 0:
        # SSH diagnostics can disclose internal target details. Return only an
        # exact terminal status generated by this script, never raw stderr.
        if terminal_status:
            raise LogQueryError(
                "目标游戏服日志查询失败：LOG_QUERY_STATUS={}".format(
                    terminal_status
                )
            )
        raise LogQueryError("目标游戏服日志查询失败；请检查 GS-1 到目标服的连接、权限或固定日志路径")

    if terminal_status is None:
        raise LogQueryError("目标游戏服日志查询返回的状态协议不完整")
    if terminal_status in FATAL_REMOTE_STATUSES:
        raise LogQueryError(
            "目标游戏服日志查询失败：LOG_QUERY_STATUS={}".format(terminal_status)
        )
    return format_success_output(output, profile, terminal_status)


def extract_terminal_status(output):
    lines = output.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None
    marker = _REMOTE_STATUS_RE.fullmatch(lines[-1])
    if marker is None:
        return None
    return marker.group(1)


def format_success_output(raw_output, profile, status):
    payload_lines = []
    for line in raw_output.splitlines():
        if line == "LOG_QUERY_PROFILE={}".format(profile):
            continue
        if _REMOTE_STATUS_RE.fullmatch(line) is not None:
            continue
        payload_lines.append(line)
    payload = "\n".join(payload_lines)

    header = "LOG_QUERY_PROFILE={}".format(profile)
    footer = "LOG_QUERY_STATUS={}".format(status)
    payload_budget = MAX_OUTPUT_CHARS - len(header) - len(footer) - 2
    sanitized_payload = sanitize_output(payload, max_output_chars=payload_budget)
    parts = [header]
    if sanitized_payload:
        parts.append(sanitized_payload)
    parts.append(footer)
    return "\n".join(parts)


def decode_output(raw_output):
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw_output.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_output.decode("utf-8", errors="replace")


def sanitize_output(output, max_output_chars=MAX_OUTPUT_CHARS):
    redacted = _URL_CREDENTIALS_RE.sub(r"\1[REDACTED]@", output)
    redacted = _HEADER_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _QUOTED_SECRET_VALUE_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _UNQUOTED_SECRET_VALUE_RE.sub(r"\1[REDACTED]", redacted)
    redacted = IPV4_RE.sub("[IP_REDACTED]", redacted)

    if max_output_chars <= 0:
        return ""

    safe_lines = []
    truncated = False
    for raw_line in redacted.splitlines():
        line = "".join(
            character if character == "\t" or ord(character) >= 32 else " "
            for character in raw_line
        )
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS]
            truncated = True

        # Reserve room for a terminal truncation marker before accepting each
        # payload line. The marker remains observable even at the size limit.
        candidate = safe_lines + [line, TRUNCATION_MARKER]
        if len("\n".join(candidate)) <= max_output_chars:
            safe_lines.append(line)
            continue

        current = "\n".join(safe_lines)
        separator_before_line = 1 if safe_lines else 0
        remaining = (
            max_output_chars
            - len(current)
            - separator_before_line
            - 1
            - len(TRUNCATION_MARKER)
        )
        if remaining > 0:
            safe_lines.append(line[:remaining])
        truncated = True
        break

    if truncated:
        if safe_lines:
            result = "\n".join(safe_lines + [TRUNCATION_MARKER])
        else:
            result = TRUNCATION_MARKER[:max_output_chars]
        return result[:max_output_chars]
    return "\n".join(safe_lines)


def main(argv=None):
    args = parse_args(argv)
    max_lines = parse_max_lines(args.max_lines)
    validate_request(args.server_id, args.profile, max_lines)
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    host = resolve_target_host(args.server_id, config_path)
    print(query_remote(host, args.profile, max_lines))


if __name__ == "__main__":
    try:
        main()
    except LogQueryError as error:
        print("LOG_QUERY_STATUS=FAILED", file=sys.stderr)
        print(str(error), file=sys.stderr)
        sys.exit(1)
