#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only inventory of the GS-1 script directory.

Lists every top-level file under /home/serverGeneralScript with size, modification
time and SHA-256, the names of sub-directories, and the section / key names of
config.ini. It never prints file contents or config.ini values (server IPs).
Usage: ``python3.7 gs1_inventory.py summary`` (the only accepted argv). Run by hand by
the GS-1 owner; deliberately NOT registered in the bot catalog (GS-1 details are not
exposed to Feishu users). Compatible with Python 3.6+.
"""

import datetime
import hashlib
import os
import platform
import re
import socket
import sys

ROOT = "/home/serverGeneralScript"
INVENTORY_VERSION = "1"
MAX_FILES = 500
MAX_HASH_BYTES = 64 * 1024 * 1024
_SERVER_KEY = re.compile(r"^([0-9]{3,8})_([A-Za-z_]+)$")


def sha256_of(path, size):
    if size > MAX_HASH_BYTES:
        return "too-large"
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (IOError, OSError):
        return "unreadable"
    return digest.hexdigest()[:16]


def config_ini_summary(path):
    """Section names and key names only; values (hosts) are never read into the output."""
    sections, by_suffix, other_keys = [], {}, []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line[0] in "#;":
                    continue
                if line.startswith("[") and line.endswith("]"):
                    sections.append(line[1:-1].strip())
                    continue
                key = line.split("=", 1)[0].strip()
                match = _SERVER_KEY.match(key)
                if match:
                    by_suffix.setdefault(match.group(2), []).append(match.group(1))
                elif key:
                    other_keys.append(key)
    except (IOError, OSError) as exc:
        return ["config.ini 无法读取：{}".format(type(exc).__name__)]
    lines = ["config.ini：section {}".format("、".join(sections) or "（无）")]
    for suffix in sorted(by_suffix):
        ids = sorted(set(by_suffix[suffix]), key=lambda item: (len(item), item))
        lines.append("  <服号>_{}：{} 个：{}".format(suffix, len(ids), ",".join(ids)))
    if other_keys:
        lines.append("  其他键名：{}".format("、".join(sorted(set(other_keys)))))
    return lines


def inventory(root=ROOT):
    lines = [
        "GS-1 盘点 v{} host={} python={} time={}".format(
            INVENTORY_VERSION, socket.gethostname(), platform.python_version(),
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    ]
    try:
        entries = sorted(os.listdir(root))
    except (IOError, OSError) as exc:
        return lines + ["目录 {} 无法读取：{}".format(root, type(exc).__name__)], 1
    files, dirs = [], []
    for name in entries:
        path = os.path.join(root, name)
        if os.path.isdir(path) and not os.path.islink(path):
            dirs.append(name)
        elif os.path.isfile(path):
            files.append(name)
    lines.append("目录 {}：{} 个文件，{} 个子目录".format(root, len(files), len(dirs)))
    lines.append("文件（名称 | 大小 | 修改时间 | sha256 前 16 位）：")
    for name in files[:MAX_FILES]:
        path = os.path.join(root, name)
        stat = os.stat(path)
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append("  {} | {} | {} | {}".format(name, stat.st_size, mtime, sha256_of(path, stat.st_size)))
    if len(files) > MAX_FILES:
        lines.append("  ……另有 {} 个文件未列出".format(len(files) - MAX_FILES))
    lines.append("子目录：{}".format("、".join(dirs) or "（无）"))
    config_path = os.path.join(root, "config.ini")
    if os.path.isfile(config_path):
        lines.extend(config_ini_summary(config_path))
    else:
        lines.append("config.ini：不存在")
    return lines, 0


def main():
    # One fixed argument so a stray invocation with other arguments does nothing.
    if sys.argv[1:] != ["summary"]:
        print("用法：python3.7 gs1_inventory.py summary（不接受其他参数）", file=sys.stderr)
        return 2
    lines, code = inventory()
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
