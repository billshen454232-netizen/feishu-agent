"""The fixed Task 89 interface and sanitized GVG plan preview returned to Gateway."""
from __future__ import annotations

import json
import re

GVG_TOPOLOGY_PATH = "/home/serverGeneralScript/gvg_topology.py"
GVG_TOPOLOGY_MODES = frozenset({"preview-stop", "preview-start"})
_PREVIEW_LINE = "GVG_TOPOLOGY_PREVIEW "
_GVG_OPERATION = re.compile(r"(?:国服\s*)?(Weekly|DEV|QA)\s*GVG\s*(停服|关服|起服|启动)[。！!]?", re.I)
GVG_TOPOLOGY_LABELS = {"weeklygvg": "Weekly", "devgvg": "DEV", "qagvg": "QA"}


def topology_request(text: str) -> tuple[str, str] | None:
    match = _GVG_OPERATION.fullmatch(text.strip())
    if match is None:
        return None
    mode = "preview-stop" if match[2] in {"停服", "关服"} else "preview-start"
    return mode, match[1].lower() + "gvg"


def parse_topology_preview(log: str, scope: str, operation: str) -> tuple[str, list[str]]:
    """Build card lines from one strictly bounded, non-executable Worker result."""
    try:
        [raw] = [line[len(_PREVIEW_LINE):] for line in log.splitlines() if line.startswith(_PREVIEW_LINE)]
        data = json.loads(raw)
        signature, actions = data["signature"], data["actions"]
        if (data["scope"] != scope or data["operation"] != operation
                or not re.fullmatch(r"[a-f0-9]{64}", signature) or not isinstance(actions, list) or len(actions) > 20):
            raise ValueError("unexpected preview")
        result = []
        for batch, server, group, members in actions:
            if (type(batch) is not int or not 0 <= batch <= 20 or not re.fullmatch(r"[0-9]{1,8}", server)
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", group) or type(members) is not int or not 0 <= members <= 20):
                raise ValueError("unexpected action")
            suffix = f"（{members} 个部署节点）" if members else ""
            result.append(f"批次 {batch} · {group} {server}{suffix}")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("无法核实 GS-1 返回的 GVG 启停预检结果。") from exc
    return signature, result
