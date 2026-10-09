#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch server query tool for GS-1.

Queries multiple servers in parallel for version.txt, resolving server IPs
from config.ini. Designed to be invoked by Muliu Task 89.
Compatible with Python 3.6+.
"""

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys

VERSION_FILE_PATH = "/data/app/game-server/release_etc/version.txt"
DEFAULT_TIMEOUT_SECONDS = 6


def load_environment_map(custom_config_path=None):
    """从 server_environments.json 配置文件加载环境映射，不硬编码任何服号。"""
    candidates = []
    if custom_config_path:
        candidates.append(custom_config_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.extend([
        os.path.join(script_dir, "server_environments.json"),
        os.path.join(script_dir, "config", "server_environments.json"),
        os.path.join(os.path.dirname(script_dir), "config", "server_environments.json"),
    ])

    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "alias_groups" in data:
                        return data["alias_groups"]
            except Exception as exc:
                print("AI_OP_WARN: 加载环境配置文件 {} 失败: {}".format(path, exc), file=sys.stderr)

    return {}


def parse_config_ini(ini_path):
    """从 config.ini 解析 server_id -> host 映射。"""
    mapping = {}
    if not os.path.isfile(ini_path):
        return mapping

    pattern = re.compile(r"^([0-9]{3,8})_game\s*=\s*(.+)$")
    try:
        with open(ini_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                match = pattern.match(line)
                if match:
                    server_id = match.group(1).strip()
                    host = match.group(2).strip()
                    mapping[server_id] = host
    except Exception:
        pass
    return mapping


def query_single_server_version(server_id, host, timeout=DEFAULT_TIMEOUT_SECONDS):
    """通过 SSH 读取单台目标服的 version.txt 文件。"""
    if not host:
        return {
            "server_id": server_id,
            "host": "未知",
            "success": False,
            "version": "",
            "error": "未在 config.ini 中找到对应路由 (_game)",
        }

    cmd = [
        "ssh",
        "-o", "ConnectTimeout={}".format(timeout),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "root@{}".format(host),
        "cat {}".format(VERSION_FILE_PATH),
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 2,
            universal_newlines=True,
            errors="replace",
        )
        if proc.returncode == 0:
            version = proc.stdout.strip()
            return {
                "server_id": server_id,
                "host": host,
                "success": True,
                "version": version or "（空版本文件）",
                "error": "",
            }
        stderr_msg = proc.stderr.strip() or "SSH 退出码 {}".format(proc.returncode)
        return {
            "server_id": server_id,
            "host": host,
            "success": False,
            "version": "",
            "error": stderr_msg,
        }
    except subprocess.TimeoutExpired:
        return {
            "server_id": server_id,
            "host": host,
            "success": False,
            "version": "",
            "error": "SSH 连接或读取超时 ({}s)".format(timeout),
        }
    except Exception as exc:
        return {
            "server_id": server_id,
            "host": host,
            "success": False,
            "version": "",
            "error": str(exc),
        }


def run_batch_query(servers, ini_path, timeout=DEFAULT_TIMEOUT_SECONDS, max_workers=8):
    """线程池并发查询各目标服务器。"""
    server_host_map = parse_config_ini(ini_path)
    results = []

    worker_count = min(max_workers, max(1, len(servers)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(
                query_single_server_version,
                sid,
                server_host_map.get(sid),
                timeout,
            ): sid
            for sid in servers
        }
        for future in concurrent.futures.as_completed(future_map):
            results.append(future.result())

    # 按入参服号原顺序排序
    server_order = {sid: idx for idx, sid in enumerate(servers)}
    results.sort(key=lambda r: server_order.get(r["server_id"], 9999))
    return results


def format_text_report(label, servers, results):
    """输出适合在飞书卡片中展示的清晰对齐格式。"""
    lines = []
    lines.append("============================================================")
    lines.append("【批量查询】{}".format(label))
    lines.append("目标服务器：{}（共 {} 台）".format("、".join(servers), len(servers)))
    lines.append("------------------------------------------------------------")

    success_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - success_count

    distinct_versions = set(r["version"] for r in results if r["success"])
    if success_count == len(results) and len(distinct_versions) == 1:
        summary_status = "全部成功（{}/{}）｜ 代码版本完全一致：{}".format(
            success_count, len(results), distinct_versions.pop()
        )
    elif success_count == len(results):
        summary_status = "全部成功（{}/{}）⚠️ 存在版本不一致（{} 种不同版本）".format(
            success_count, len(results), len(distinct_versions)
        )
    else:
        summary_status = "部分完成：成功 {} 台，失败/超时 {} 台".format(
            success_count, fail_count
        )

    lines.append("汇总状态：{}".format(summary_status))
    lines.append("------------------------------------------------------------")
    lines.append("{:<8} | {:<4} | {:<16} | 版本信息 / 错误详情".format("服号", "状态", "IP主机"))
    lines.append("-" * 60)

    for r in results:
        status_icon = "✅" if r["success"] else "❌"
        detail = r["version"] if r["success"] else "失败: {}".format(r["error"])
        lines.append("{:<8} | {:<4} | {:<16} | {}".format(r["server_id"], status_icon, r["host"], detail))

    lines.append("============================================================")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Batch server version query tool for GS-1")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--env",
        type=str,
        help="环境标识，例如: overseas_dev, hmt_dev, kr_dev, cn_weekly, overseas_weekly 等",
    )
    group.add_argument(
        "--servers",
        nargs="+",
        help="直接指定服号列表，例如: --servers 5000 5001 6000 6001",
    )
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="自定义查询显示标签",
    )
    parser.add_argument(
        "--config-ini",
        type=str,
        default="config.ini",
        help="config.ini 路径 (默认当前目录下 config.ini)",
    )
    parser.add_argument(
        "--env-config",
        type=str,
        default=None,
        help="server_environments.json 自定义路径",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="单服 SSH 查询超时秒数 (默认 6 秒)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=8,
        help="并发线程数 (默认 8)",
    )

    args = parser.parse_args()

    if args.env:
        env_map = load_environment_map(args.env_config)
        if not env_map:
            print("AI_OP_ERROR: 未找到 server_environments.json 配置文件，无法解析环境标识")
            print("AI_OP_END status=failed error=缺少server_environments.json配置文件")
            print("END")
            return 1

        env_key = args.env.strip().lower()
        if env_key not in env_map:
            available = ", ".join(sorted(env_map.keys()))
            print("AI_OP_ERROR: 未知环境 '{}'。可选环境: {}".format(args.env, available))
            print("AI_OP_END status=failed error=未知环境_{}".format(args.env))
            print("END")
            return 1

        env_info = env_map[env_key]
        servers = [str(s).strip() for s in env_info.get("servers", [])]
        label = args.label or "{} ({})".format(env_info.get("name", env_key), env_key)
    else:
        servers = [str(s).strip() for s in args.servers if str(s).strip()]
        label = args.label or "自定义服务器列表 ({} 台)".format(len(servers))

    if not servers:
        print("AI_OP_ERROR: 目标服务器列表为空")
        print("AI_OP_END status=failed error=目标服务器列表为空")
        print("END")
        return 1

    print("AI_OP_START")
    results = run_batch_query(
        servers=servers,
        ini_path=args.config_ini,
        timeout=args.timeout,
        max_workers=args.max_workers,
    )

    report = format_text_report(label, servers, results)
    print(report)

    success_count = sum(1 for r in results if r["success"])
    if success_count > 0:
        print("AI_OP_END status=success")
    else:
        print("AI_OP_END status=failed error=所有服务器查询均失败")
    print("END")
    return 0


if __name__ == "__main__":
    sys.exit(main())
