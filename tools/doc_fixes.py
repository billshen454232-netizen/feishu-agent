#!/usr/bin/env python3
"""Write accepted knowledge-doc fixes back to the repository.

The Gateway never edits its own git working tree. Accepted suggestions travel:

    Gateway:  python tools/doc_fixes.py export > fixes.json
    Dev box:  python tools/doc_fixes.py apply fixes.json      # edits knowledge/*.md, then review + commit + push
    Gateway:  git pull && python tools/doc_fixes.py mark-applied 3 5

``apply`` replaces each ``original_excerpt`` only when it occurs exactly once in
the target file; anything else is reported and left untouched.
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
from src.qa_library import FIX_ACCEPTED, QaStore  # noqa: E402


def _store(config_path: str) -> QaStore:
    store = QaStore(load_config(config_path).job_queue.database_path)
    store.initialize()
    return store


def export(config_path: str) -> list[dict]:
    items, _total = _store(config_path).list_doc_fixes(FIX_ACCEPTED, limit=1000)
    return [
        {key: item[key] for key in ("suggestion_id", "archive_id", "doc_path", "original_excerpt", "proposed_excerpt", "reason")}
        for item in items
    ]


def apply(fixes: list[dict], repo_root: Path) -> tuple[list[int], list[str]]:
    """Apply fixes in place; returns (applied suggestion ids, problems)."""
    applied, problems = [], []
    for fix in fixes:
        suggestion_id = int(fix["suggestion_id"])
        relative = str(fix["doc_path"])
        if not (relative == "knowledge/index.md" or (relative.startswith("knowledge/docs/") and relative.endswith(".md")
                                                     and "/" not in relative[len("knowledge/docs/"):])):
            problems.append("#{} 路径不在 knowledge/index.md 或 knowledge/docs/*.md：{}".format(suggestion_id, relative))
            continue
        path = repo_root / relative
        if not path.is_file():
            problems.append("#{} 文件不存在：{}".format(suggestion_id, relative))
            continue
        raw = path.read_bytes().decode("utf-8")
        newline = "\r\n" if "\r\n" in raw else "\n"
        text = raw.replace("\r\n", "\n")
        original = str(fix["original_excerpt"]).replace("\r\n", "\n")
        count = text.count(original)
        if count != 1:
            problems.append("#{} 原文在 {} 中出现 {} 次（需恰好 1 次），未修改".format(suggestion_id, relative, count))
            continue
        text = text.replace(original, str(fix["proposed_excerpt"]).replace("\r\n", "\n"), 1)
        path.write_bytes(text.replace("\n", newline).encode("utf-8"))
        applied.append(suggestion_id)
    return applied, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识资料修改建议：导出、回写仓库、标记已回写")
    parser.add_argument("--config", default="config/config.json", help="Gateway 配置文件（export / mark-applied 使用）")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("export", help="在 Gateway 上导出已采纳的建议（JSON 输出到标准输出）")
    apply_parser = commands.add_parser("apply", help="在开发机仓库中应用导出的建议")
    apply_parser.add_argument("input", help="export 生成的 JSON 文件")
    apply_parser.add_argument("--repo", default=str(REPOSITORY_ROOT), help="仓库根目录")
    mark_parser = commands.add_parser("mark-applied", help="在 Gateway 上把已回写的建议标为已回写")
    mark_parser.add_argument("ids", nargs="+", type=int)
    args = parser.parse_args(argv)

    if args.command == "export":
        print(json.dumps(export(args.config), ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply":
        fixes = json.loads(Path(args.input).read_text(encoding="utf-8"))
        applied, problems = apply(fixes, Path(args.repo))
        for problem in problems:
            print("跳过：" + problem, file=sys.stderr)
        print("已修改 {} 条：{}".format(len(applied), " ".join(str(item) for item in applied) or "无"))
        if applied:
            print("请检查 git diff 后提交推送；Gateway git pull 后执行：mark-applied {}".format(" ".join(map(str, applied))))
        return 1 if problems else 0
    changed = _store(args.config).mark_fixes_applied(args.ids)
    print("已标记为已回写：{} 条".format(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
