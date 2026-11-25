#!/usr/bin/env python3
"""Task 89 entry point for the GS-1 topology executors (Python 3.7)."""
import argparse
import hashlib
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CODE_ROOT = Path("/data/app/muliu_ws/workspace/code")
INVENTORY = CODE_ROOT / "config/qa_env_all_machine.txt"
SCOPES = {
    "weeklygvg": ("Weekly", "4"),
    "devgvg": ("DEV", "260005"),
    "qagvg": ("QA", "260006"),
}
ID = re.compile(r"[0-9]{1,8}")
LABEL = re.compile(r"[a-z][a-z0-9_]{0,31}")


class TopologyError(RuntimeError):
    pass


def read_scan(output):
    command = [sys.executable, str(CODE_ROOT / "run_console/discover_game_server_topology.py"),
               "--machine-file", str(INVENTORY), "--output-dir", str(output)]
    env = dict(os.environ, PYTHONPATH=str(CODE_ROOT), PYTHONUNBUFFERED="1")
    if subprocess.call(command, cwd=str(output), env=env, stdout=sys.stdout, stderr=sys.stderr):
        raise TopologyError("拓扑扫描失败，请核对 GS-1 日志。")
    data = json.loads((output / "game_server_topology.json").read_text(encoding="utf-8"))
    if data["summary"]["failed_machine_count"] != 0:
        raise TopologyError("拓扑扫描不完整。")
    return data


def numbers(values):
    return sorted({str(value) for value in values if ID.fullmatch(str(value))}, key=int)


def dependency_lines(scan, scope):
    label, cluster = SCOPES[scope]
    roots = [row for row in scan["servers"] if row["host_num"] == cluster and row["server_group"] == "gvg_zone"]
    if len(roots) != 1:
        raise TopologyError("无法在拓扑中唯一定位 {} GVG {}。".format(label, cluster))
    root = roots[0]
    root_runtime = root["is_running"] is True and root["source_type"] == "runtime" and root["status"] == "succeeded"
    reported = root["connected_host_nums"] if root_runtime else []
    active = set()
    stopped = set()
    known_games = {row["host_num"] for row in scan["servers"] if row["server_group"] == "game"}
    unknown = set(reported) - known_games
    for row in scan["servers"]:
        if row["server_group"] != "game":
            continue
        server = row["host_num"]
        if cluster not in row["configured_connected_host_nums"] and cluster not in row["connected_host_nums"] and server not in reported:
            continue
        if row["status"] != "succeeded":
            unknown.add(server)
        elif row["is_running"] is True:
            active.add(server)
        elif row["is_running"] is False:
            stopped.add(server)
        else:
            unknown.add(server)
    def display(values):
        return "、".join(numbers(values)) or "无"

    return ["国服 {} GVG {} 本次拓扑扫描".format(label, cluster),
            "GVG Zone 运行态报告的服号：" + (display(reported) if root_runtime else "未取得"),
            "拓扑关联且 game 仍在运行：" + display(active),
            "拓扑关联且 game 已停：" + display(stopped),
            "game 状态待核对：" + display(unknown)]


def plan_preview(plan, scope, operation, *, include_running_gvg=False):
    """Bind scope, operation, target identities and order while keeping machine names on GS-1."""
    cluster = SCOPES[scope][1]
    if plan.get("ignored_missing_connections"):
        raise TopologyError("关服计划包含无法定位的关联节点。")
    identities, display = [], []
    for batch in plan["batches"]:
        index = batch["batch_index"]
        for action in batch["actions"]:
            server, group = action["host_num"], action["group"]
            status = action["status"]
            if include_running_gvg and status == "already_running" and (server, group) == (cluster, "gvg_zone"):
                status = "pending"
            if status == "already_running" or status.startswith("skipped"):
                continue
            if (status != "pending" or not ID.fullmatch(server) or not LABEL.fullmatch(group)
                    or (server == cluster and group != "gvg_zone")):
                raise TopologyError("拓扑执行计划包含无法核实的节点。")
            identities.append([index, server, group, action["machine"], action["mode"], action.get("start_shell_script"),
                               [(m["machine"], m["role"], m["scripts"]) for m in action.get("member_start_plans", [])]])
            display.append([index, server, group, len(action.get("member_start_plans") or [])])
    if len(display) > 20:
        raise TopologyError("拓扑计划超过单张确认卡的 20 项上限。")
    signature = hashlib.sha256(json.dumps([scope, cluster, operation, identities], ensure_ascii=True).encode()).hexdigest()
    return {"scope": scope, "operation": operation, "signature": signature, "actions": display}


def run_plan(module, scope, operation, argv, expected, *, include_running_gvg=False):
    """Use the same vendor planner for preview and commit, with the guard at its write boundary."""
    boundary = "execute_{}_plan".format("shutdown" if operation == "stop" else "start")
    runner = getattr(module, "run_shutdown" if operation == "stop" else "run_start")
    execute = getattr(module, boundary)
    if boundary not in runner.__code__.co_names:
        raise TopologyError("GS-1 拓扑执行器接口已变化。")
    previews = []

    def guarded(plan, args):
        if previews:
            raise TopologyError("GS-1 返回多份执行计划，请重新核对。")
        preview = plan_preview(plan, scope, operation, include_running_gvg=include_running_gvg)
        actions = preview["actions"]
        if include_running_gvg or (operation == "stop" and actions):
            cluster = SCOPES[scope][1]
            if sum((server, group) == (cluster, "gvg_zone") for _, server, group, _ in actions) != 1:
                raise TopologyError("没有唯一的 GVG 中心服操作目标。")
        if include_running_gvg:
            if any(group == "game" for _, _, group, _ in actions):
                raise TopologyError("GVG 起服计划包含普通游戏服，请核对拓扑。")
        previews.append(preview)
        if expected is None:
            return 0
        if preview["signature"] != expected:
            raise TopologyError("确认后的拓扑计划已变化，请重新发起预检。")
        return execute(plan, args)

    setattr(module, boundary, guarded)
    try:
        status = module.main(argv)
    finally:
        setattr(module, boundary, execute)
    if not previews:
        raise TopologyError("GS-1 未生成可核对的启停计划。")
    return status, previews[0]


def run_phase(scope, operation, output, expected=None, *, include_running_gvg=False):
    number_file = output / "target.txt"
    number_file.write_text(SCOPES[scope][1] + "\n", encoding="ascii")
    argv = ["--all-machine-file", str(INVENTORY), "--output-dir", str(output)]
    sys.path[:0] = [str(CODE_ROOT / "run_console"), str(CODE_ROOT)]
    if operation == "stop":
        read_scan(output)
        module = importlib.import_module("shutdown_game_server_by_topology")
        argv = ["shutdown", "--op-hostnum-file", str(number_file), "--topology-file",
                str(output / "game_server_topology.json")] + argv
    else:
        module = importlib.import_module("start_game_server_by_topology")
        argv = ["--start-hostnum-file", str(number_file)] + argv
    return run_plan(module, scope, operation, argv, expected, include_running_gvg=include_running_gvg)


def phase_result(scope, operation, output, status, *, require_root=False):
    label, cluster = SCOPES[scope]
    result_file = output / ("game_server_shutdown_topology.json" if operation == "stop" else "game_server_start_topology.json")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    terminal = "shutdown_completed" if operation == "stop" else "start_completed"
    action_label = "关服" if operation == "stop" else "起服"
    lines = ["国服 {} GVG {} {}".format(label, cluster, action_label)]
    root_completed = False
    for batch in result["batches"]:
        for action in batch["actions"]:
            server, group, state = action["host_num"], action["group"], action["status"]
            if ID.fullmatch(server) and LABEL.fullmatch(group) and LABEL.fullmatch(state):
                lines.append("批次 {} · {} {} · {}".format(batch["batch_index"], group, server, state))
            if (server, group, state) == (cluster, "gvg_zone", terminal):
                root_completed = True
    ok = status == 0 and result["status"] == terminal and (root_completed or not require_root)
    return 0 if ok else 1, lines


def execute(args, output):
    if args.mode == "list":
        return 0, dependency_lines(read_scan(output), args.scope)
    after_stop = args.mode == "preview-start-after-stop"
    operation = "start" if after_stop else args.mode.replace("preview-", "")
    status, preview = run_phase(args.scope, operation, output, args.signature, include_running_gvg=after_stop)
    if args.signature is None:
        return status, ["GVG_TOPOLOGY_PREVIEW " + json.dumps(preview, separators=(",", ":"))]
    cluster = SCOPES[args.scope][1]
    require_root = any((server, group) == (cluster, "gvg_zone") for _, server, group, _ in preview["actions"])
    return phase_result(args.scope, operation, output, status, require_root=require_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("list", "preview-stop", "preview-start", "preview-start-after-stop", "stop", "start"))
    parser.add_argument("scope", choices=tuple(SCOPES))
    parser.add_argument("signature", nargs="?")
    args = parser.parse_args(argv)
    if args.mode in {"stop", "start"}:
        if not re.fullmatch(r"[a-f0-9]{64}", args.signature or ""):
            parser.error("启停必须提供确认计划指纹")
    elif args.signature is not None:
        parser.error("只读操作不接受计划指纹")
    if not INVENTORY.is_file() or INVENTORY.stat().st_size == 0:
        print("GS-1 拓扑资产文件不可用。")
        return 1
    os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="gvg-topology-") as directory:
            output = Path(directory)
            # The vendor executors log machine identities. Only the bounded result goes to Task 89.
            sys.stdout.flush()
            sys.stderr.flush()
            with (output / "runner.log").open("w", encoding="utf-8") as log:
                original_stdout, original_stderr = os.dup(1), os.dup(2)
                try:
                    os.dup2(log.fileno(), 1)
                    os.dup2(log.fileno(), 2)
                    status, lines = execute(args, output)
                finally:
                    sys.stdout.flush()
                    sys.stderr.flush()
                    os.dup2(original_stdout, 1)
                    os.dup2(original_stderr, 2)
                    os.close(original_stdout)
                    os.close(original_stderr)
            print("\n".join(lines))
            return status
    except TopologyError as exc:
        print(str(exc))
    except Exception as exc:
        print("GVG 拓扑执行未取得可信结果（{}）。".format(type(exc).__name__))
    return 1


if __name__ == "__main__":
    sys.exit(main())
