#!/usr/bin/env python3
"""Project plan board snapshots (the board itself lives in the Gateway SQLite).

    Gateway:  .venv/bin/python tools/plan_board.py export > plan-board.json   # snapshot for git history
    Gateway:  .venv/bin/python tools/plan_board.py import plan-board.json     # restore (replaces every step)
    Anywhere: .venv/bin/python tools/plan_board.py summary                    # quick text overview
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.config import load_config  # noqa: E402
from src.plan_board import PlanStore  # noqa: E402

LABEL = {"active": "进行中", "todo": "未开始", "done": "已完成"}


def _store(config_path: str) -> PlanStore:
    store = PlanStore(load_config(config_path).job_queue.database_path)
    store.initialize()
    return store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="项目计划看板：导出 / 恢复快照、文字概览")
    parser.add_argument("--config", default="config/config.json")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("export", help="导出 JSON 快照到标准输出")
    restore = commands.add_parser("import", help="用快照覆盖全部步骤（修改历史保留）")
    restore.add_argument("input")
    commands.add_parser("summary", help="按状态列出步骤")
    args = parser.parse_args(argv)
    store = _store(args.config)

    if args.command == "export":
        print(json.dumps(store.export_snapshot(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "import":
        count = store.import_snapshot(json.loads(Path(args.input).read_text(encoding="utf-8")), operator="import")
        print("已导入 {} 个步骤".format(count))
        return 0
    plan = store.get_plan()
    print("{}：已完成 {} / {}".format(plan["meta"].get("title", "项目计划"), plan["counts"]["done"], plan["total"]))
    for status in ("active", "todo", "done"):
        steps = [step for step in plan["steps"] if step["status"] == status]
        print("\n{}（{}）".format(LABEL[status], len(steps)))
        for step in steps:
            now = (step["current"] or step["remaining"] or [""])[0]
            print("  {} [{}] {}{}".format(step["id"], step["area"], step["title"], "  — " + now if now and status != "done" else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
