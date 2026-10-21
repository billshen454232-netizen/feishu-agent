#!/usr/bin/env python3
"""同步热更唯一注册表到 Planner 静态资料、运行镜像和 GS-1 manifest。"""

from __future__ import print_function

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.muliu_script_catalog import MuliuScriptCatalog
from src.server_environments import ServerEnvironments, ServerEnvironmentsError
from tools.auto_hotfix_registry import hotfix_key_pattern, render_planner_catalog_section


DEFAULT_CATALOG_PATH = REPOSITORY_ROOT / "config" / "muliu_script_catalog.md"
DEFAULT_ENVIRONMENT_PATH = REPOSITORY_ROOT / "config" / "server_environments.json"
DEFAULT_REGISTRY_PATH = REPOSITORY_ROOT / "tools" / "auto_hotfix_registry.py"
DEFAULT_REGISTRY_MIRROR_PATH = REPOSITORY_ROOT / "knowledge" / "scripts" / "auto_hotfix_registry.py"
DEFAULT_MANIFEST_PATH = REPOSITORY_ROOT / "deployment" / "gs1" / "muliu_runner_manifest.json"

ROUTING_BEGIN = "<!-- HOTFIX_THRESHOLD_REGISTRY:BEGIN -->"
ROUTING_END = "<!-- HOTFIX_THRESHOLD_REGISTRY:END -->"
ENVIRONMENT_BEGIN = "<!-- ENVIRONMENT_MAPPING:BEGIN -->"
ENVIRONMENT_END = "<!-- ENVIRONMENT_MAPPING:END -->"
BROAD_KEY_PATTERN = '"key": "[A-Za-z0-9_]{1,64}"'


class HotfixCatalogSyncError(RuntimeError):
    """Raised when checked-in generated hotfix artifacts drift from the registry."""


def _replace_marked_block(catalog_text, begin_marker, end_marker, generated_content, label):
    begin_count = catalog_text.count(begin_marker)
    end_count = catalog_text.count(end_marker)
    if begin_count != 1 or end_count != 1:
        raise HotfixCatalogSyncError(
            "调用资料必须且只能包含一个{}标记对；当前 BEGIN={}，END={}".format(
                label, begin_count, end_count
            )
        )

    begin_index = catalog_text.index(begin_marker)
    end_index = catalog_text.index(end_marker)
    if end_index <= begin_index:
        raise HotfixCatalogSyncError("{}结束标记必须位于开始标记之后".format(label))
    generated_block = "{}\n{}\n{}".format(begin_marker, generated_content, end_marker)
    return "{}{}{}".format(
        catalog_text[:begin_index], generated_block, catalog_text[end_index + len(end_marker):]
    )


def render_environment_mapping_section(environments):
    """Render Planner-visible environment names and server IDs from one registry."""
    mappings = environments.all_mappings()
    lines = [
        "#### 环境服号映射（自动生成）",
        "",
        "> 唯一来源：`config/server_environments.json`。环境成员只在区域的 `environments` 中登记一次；跨区域环境由 `groups` 的选择器派生，禁止在本文件手写或复制服号列表。",
        "",
    ]
    lines.extend("- `{}`：`{}`".format(mapping.name, ",".join(mapping.server_ids)) for mapping in mappings)
    overseas_dev = next((mapping for mapping in mappings if mapping.key == "overseas_dev"), None)
    if overseas_dev is None:
        raise HotfixCatalogSyncError("环境注册表缺少海外所有DEV环境")
    servers = ",".join(overseas_dev.server_ids)
    lines.extend(
        [
            "",
            "**标准计划示例**：",
            "",
            "```json",
            "{",
            '  "kind": "operation",',
            '  "summary": "批量查询海外所有DEV环境服务器代码版本",',
            '  "steps": [',
            "    {",
            '      "path": "/home/serverGeneralScript/batch_server_query.py",',
            '      "args": ["--servers", "{}", "--label", "海外所有DEV环境"],'.format(servers),
            '      "description": "并发查询 {} 服务器代码版本"'.format(servers),
            "    }",
            "  ]",
            "}",
            "```",
        ]
    )
    return "\n".join(lines)


def render_catalog(catalog_text, environment_path=DEFAULT_ENVIRONMENT_PATH):
    """Render generated environment and hotfix registry sections into the catalog."""
    try:
        environments = ServerEnvironments.load(environment_path)
    except ServerEnvironmentsError as exc:
        raise HotfixCatalogSyncError("环境注册表无效：{}".format(exc)) from exc
    catalog_text = _replace_marked_block(
        catalog_text,
        ENVIRONMENT_BEGIN,
        ENVIRONMENT_END,
        render_environment_mapping_section(environments),
        "环境映射",
    )

    rendered = _replace_marked_block(
        catalog_text,
        ROUTING_BEGIN,
        ROUTING_END,
        render_planner_catalog_section(),
        "热更注册表",
    )

    strict_key_pattern = '"key": "{}"'.format(hotfix_key_pattern())
    generated_pattern_count = rendered.count(BROAD_KEY_PATTERN) + rendered.count(strict_key_pattern)
    if generated_pattern_count != 4:
        raise HotfixCatalogSyncError(
            "热更调用合同中必须恰有 4 个受控 key 正则；当前为 {}".format(
                generated_pattern_count
            )
        )
    return rendered.replace(BROAD_KEY_PATTERN, strict_key_pattern)


def expected_artifacts(catalog_path=DEFAULT_CATALOG_PATH, environment_path=DEFAULT_ENVIRONMENT_PATH):
    """Return every static artifact derived from registered environment and hotfix data."""
    source_catalog = Path(catalog_path).read_text(encoding="utf-8")
    rendered_catalog = render_catalog(source_catalog, environment_path)
    rendered_manifest = json.dumps(
        MuliuScriptCatalog.from_text(rendered_catalog).read_runner_manifest().as_jsonable(),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    registry_source = DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8")
    return {
        Path(catalog_path): rendered_catalog,
        DEFAULT_REGISTRY_MIRROR_PATH: registry_source,
        DEFAULT_MANIFEST_PATH: rendered_manifest,
    }


def check_artifacts(catalog_path=DEFAULT_CATALOG_PATH, environment_path=DEFAULT_ENVIRONMENT_PATH):
    """Return paths whose checked-in generated content differs from the registry."""
    drifted_paths = []
    for path, expected in expected_artifacts(catalog_path, environment_path).items():
        actual = path.read_text(encoding="utf-8") if path.exists() else None
        if actual != expected:
            drifted_paths.append(path)
    return tuple(drifted_paths)


def write_artifacts(catalog_path=DEFAULT_CATALOG_PATH, environment_path=DEFAULT_ENVIRONMENT_PATH):
    """Write all static outputs atomically enough for a local source checkout."""
    artifacts = expected_artifacts(catalog_path, environment_path)
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tuple(artifacts)


def main():
    parser = argparse.ArgumentParser(
        description="同步热更注册表到 Planner catalog、运行镜像与 runner manifest。"
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--environments", type=Path, default=DEFAULT_ENVIRONMENT_PATH)
    parser.add_argument("--check", action="store_true", help="只检查静态产物是否与注册表一致")
    parser.add_argument("--write", action="store_true", help="写入静态产物")
    options = parser.parse_args()
    if options.check == options.write:
        parser.error("必须且只能指定 --check 或 --write")

    if options.check:
        drifted_paths = check_artifacts(options.catalog, options.environments)
        if drifted_paths:
            print("热更注册表静态产物不一致：")
            for path in drifted_paths:
                print("- {}".format(path))
            raise SystemExit(1)
        print("热更注册表静态产物一致")
        return

    for path in write_artifacts(options.catalog, options.environments):
        print("已同步：{}".format(path))


if __name__ == "__main__":
    main()
