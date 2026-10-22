"""Load and validate the single source of truth for server environments."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SERVER_ID_PATTERN = re.compile(r"[0-9]{3,8}")


class ServerEnvironmentsError(ValueError):
    """Raised when the environment registry is unavailable or internally inconsistent."""


@dataclass(frozen=True)
class EnvironmentMapping:
    key: str
    name: str
    server_ids: tuple[str, ...]


@dataclass(frozen=True)
class ServerEnvironments:
    regions: dict[str, dict[str, Any]]
    groups: dict[str, dict[str, Any]]
    environment_labels: dict[str, str]

    @classmethod
    def load(cls, path: str | Path) -> "ServerEnvironments":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ServerEnvironmentsError("环境资产配置不可用或格式无效") from exc
        return cls.from_data(raw)

    @classmethod
    def from_data(cls, raw: Any) -> "ServerEnvironments":
        if not isinstance(raw, dict):
            raise ServerEnvironmentsError("环境资产配置根节点必须是对象")
        regions = raw.get("regions")
        groups = raw.get("groups", {})
        labels = raw.get("environment_labels", {})
        if not isinstance(regions, dict) or not isinstance(groups, dict) or not isinstance(labels, dict):
            raise ServerEnvironmentsError("regions、groups 和 environment_labels 必须是对象")

        normalized_regions: dict[str, dict[str, Any]] = {}
        seen_server_ids: dict[str, str] = {}
        for region_key, region in regions.items():
            if not isinstance(region_key, str) or not region_key:
                raise ServerEnvironmentsError("区域 key 必须是非空字符串")
            if not isinstance(region, dict):
                raise ServerEnvironmentsError("区域 {} 必须是对象".format(region_key))
            name = region.get("name")
            environments = region.get("environments")
            if not isinstance(name, str) or not name.strip() or not isinstance(environments, dict):
                raise ServerEnvironmentsError("区域 {} 缺少 name 或 environments".format(region_key))

            normalized_environments: dict[str, tuple[str, ...]] = {}
            for environment_key, server_ids in environments.items():
                if not isinstance(environment_key, str) or not environment_key:
                    raise ServerEnvironmentsError("区域 {} 存在无效环境 key".format(region_key))
                if not isinstance(server_ids, list) or not server_ids:
                    raise ServerEnvironmentsError(
                        "区域 {} 的环境 {} 必须包含至少一个服号".format(region_key, environment_key)
                    )
                normalized_ids: list[str] = []
                for server_id in server_ids:
                    if not isinstance(server_id, str) or _SERVER_ID_PATTERN.fullmatch(server_id) is None:
                        raise ServerEnvironmentsError(
                            "区域 {} 的环境 {} 含无效服号：{}".format(region_key, environment_key, server_id)
                        )
                    location = "{}/{}".format(region_key, environment_key)
                    if server_id in seen_server_ids:
                        raise ServerEnvironmentsError(
                            "服号 {} 重复登记在 {} 和 {}".format(
                                server_id, seen_server_ids[server_id], location
                            )
                        )
                    seen_server_ids[server_id] = location
                    normalized_ids.append(server_id)
                normalized_environments[environment_key] = tuple(normalized_ids)

            normalized_regions[region_key] = {
                "name": name.strip(),
                "catalog_name": _required_text(region.get("catalog_name"), name),
                "environments": normalized_environments,
            }

        normalized_labels: dict[str, str] = {}
        for key, label in labels.items():
            if isinstance(key, str) and key and isinstance(label, str) and label.strip():
                normalized_labels[key] = label.strip()

        normalized_groups: dict[str, dict[str, Any]] = {}
        for group_key, group in groups.items():
            if not isinstance(group_key, str) or not group_key or not isinstance(group, dict):
                raise ServerEnvironmentsError("聚合环境定义无效")
            name = group.get("name")
            region_keys = group.get("regions")
            environment_keys = group.get("environments")
            if not isinstance(name, str) or not name.strip():
                raise ServerEnvironmentsError("聚合环境 {} 缺少 name".format(group_key))
            if not isinstance(region_keys, list) or not region_keys:
                raise ServerEnvironmentsError("聚合环境 {} 缺少 regions".format(group_key))
            if not isinstance(environment_keys, list) or not environment_keys:
                raise ServerEnvironmentsError("聚合环境 {} 缺少 environments".format(group_key))
            if len(region_keys) != len(set(region_keys)) or len(environment_keys) != len(set(environment_keys)):
                raise ServerEnvironmentsError("聚合环境 {} 不允许重复选择器".format(group_key))
            for region_key in region_keys:
                if region_key not in normalized_regions:
                    raise ServerEnvironmentsError(
                        "聚合环境 {} 引用了不存在的区域 {}".format(group_key, region_key)
                    )
            normalized_groups[group_key] = {
                "name": name.strip(),
                "regions": tuple(region_keys),
                "environments": tuple(environment_keys),
            }

        return cls(
            regions=normalized_regions,
            groups=normalized_groups,
            environment_labels=normalized_labels,
        )

    def region_mappings(self) -> tuple[EnvironmentMapping, ...]:
        mappings: list[EnvironmentMapping] = []
        for region_key, region in self.regions.items():
            for environment_key, server_ids in region["environments"].items():
                mappings.append(
                    EnvironmentMapping(
                        key="{}_{}".format(region_key, environment_key),
                        name="{}{}环境".format(
                            region["catalog_name"], self._environment_label(environment_key)
                        ),
                        server_ids=server_ids,
                    )
                )
        return tuple(mappings)

    def region_context_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        for region_key, region in self.regions.items():
            entries = [
                "{}：{}".format(environment_key, "、".join(server_ids))
                for environment_key, server_ids in region["environments"].items()
            ]
            if entries:
                lines.append("{}（{}）：{}".format(region["name"], region_key, "；".join(entries)))
        return tuple(lines)

    def group_mappings(self) -> tuple[EnvironmentMapping, ...]:
        mappings: list[EnvironmentMapping] = []
        for group_key, group in self.groups.items():
            for environment_key in group["environments"]:
                server_ids: list[str] = []
                for region_key in group["regions"]:
                    region = self.regions[region_key]
                    server_ids.extend(region["environments"].get(environment_key, ()))
                if server_ids:
                    mappings.append(
                        EnvironmentMapping(
                            key="{}_{}".format(group_key, environment_key),
                            name="{}{}环境".format(group["name"], self._environment_label(environment_key)),
                            server_ids=tuple(server_ids),
                        )
                    )
        return tuple(mappings)

    def all_mappings(self) -> tuple[EnvironmentMapping, ...]:
        return self.region_mappings() + self.group_mappings()

    def route_region_for_server_ids(self, server_ids: tuple[str, ...]) -> str:
        """Map registered servers to one closed Worker execution classification."""
        if not server_ids:
            raise ServerEnvironmentsError("计划未包含可路由的目标服号")

        route_regions: set[str] = set()
        for server_id in server_ids:
            owning_region = self._owning_region_for_server_id(server_id)
            route_regions.add("cn" if owning_region == "cn" else "overseas")

        if len(route_regions) != 1:
            raise ServerEnvironmentsError("同一计划不能同时包含国服和海外服目标")
        return next(iter(route_regions))

    def _owning_region_for_server_id(self, server_id: str) -> str:
        if not isinstance(server_id, str) or _SERVER_ID_PATTERN.fullmatch(server_id) is None:
            raise ServerEnvironmentsError("目标服号格式无效：{}".format(server_id))
        for region_key, region in self.regions.items():
            if any(server_id in server_ids for server_ids in region["environments"].values()):
                return region_key
        raise ServerEnvironmentsError("目标服号未在受控环境资产中登记：{}".format(server_id))

    def _environment_label(self, environment_key: str) -> str:
        return self.environment_labels.get(environment_key, environment_key)


def _required_text(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ServerEnvironmentsError("catalog_name 必须是非空字符串")
