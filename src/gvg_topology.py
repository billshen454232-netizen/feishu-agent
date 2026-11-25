"""The fixed Task 89 interface and sanitized GVG plan preview returned to Gateway."""
from __future__ import annotations

import json
import re

from .muliu_plan import MuliuPlan, MuliuPlanError

GVG_TOPOLOGY_PATH = "/home/serverGeneralScript/gvg_topology.py"
GVG_TOPOLOGY_MODES = {"preview-stop": "stop", "preview-start": "start", "preview-start-after-stop": "start"}
GVG_TOPOLOGY_LABELS = {"weeklygvg": "Weekly", "devgvg": "DEV", "qagvg": "QA"}
_PREVIEW_LINE = "GVG_TOPOLOGY_PREVIEW "
_GVG_OPERATION = re.compile(r"(?:国服\s*)?(Weekly|DEV|QA)\s*GVG(?:\s*中心服)?\s*(停服|关服|起服|启动)[。！!]?", re.I)


def topology_request(text: str) -> tuple[str, str] | None:
    """Keep the existing short commands as a fast path for single operations."""
    match = _GVG_OPERATION.fullmatch(text.strip())
    if match is None:
        return None
    mode = "preview-stop" if match[2] in {"停服", "关服"} else "preview-start"
    return mode, match[1].lower() + "gvg"


def validate_topology_steps(plan: MuliuPlan) -> bool:
    """Allow either a single topology command or an ordered pair for one GVG."""
    steps = [step for step in plan.steps if step.path == GVG_TOPOLOGY_PATH]
    if not steps:
        return False
    modes = tuple(step.args[0] for step in steps)
    if len(steps) == len(plan.steps) == 1 and modes != ("preview-start-after-stop",):
        return True
    if (len(steps) == len(plan.steps) == 2 and steps[0].args[1] == steps[1].args[1]
            and modes in {("preview-stop", "preview-start"), ("preview-stop", "preview-start-after-stop"), ("stop", "start")}):
        return True
    raise MuliuPlanError("GVG 组合只允许先停服，再启动同一组 GVG。")


def parse_topology_preview(log: str, scope: str, operation: str) -> tuple[str, list[str]]:
    """Build card lines from one strictly bounded, non-executable Worker result."""
    try:
        [raw] = [line[len(_PREVIEW_LINE):] for line in log.splitlines() if line.startswith(_PREVIEW_LINE)]
        data = json.loads(raw)
        signature = data["signature"]
        if (data["scope"] != scope or data["operation"] != operation
                or not re.fullmatch(r"[a-f0-9]{64}", signature)):
            raise ValueError("unexpected preview")
        result = _action_lines(data["actions"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("无法核实 GS-1 返回的 GVG 启停预检结果。") from exc
    return signature, result


def _action_lines(actions: object) -> list[str]:
    if not isinstance(actions, list) or len(actions) > 20:
        raise ValueError("unexpected actions")
    result = []
    for batch, server, group, members in actions:
        if (type(batch) is not int or not 0 <= batch <= 20 or not re.fullmatch(r"[0-9]{1,8}", server)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", group) or type(members) is not int or not 0 <= members <= 20):
            raise ValueError("unexpected action")
        suffix = f"（{members} 个部署节点）" if members else ""
        result.append(f"批次 {batch} · {group} {server}{suffix}")
    return result
