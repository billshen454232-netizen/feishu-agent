"""Provide non-authoritative request hints for observability and UI wording."""

from __future__ import annotations

from enum import Enum


class MuliuRequestIntent(str, Enum):
    """Legacy hint values retained for callers that display request categories."""

    KNOWLEDGE = "knowledge"
    OPERATION = "operation"
    UNCERTAIN = "uncertain"


class MuliuIntentRouter:
    """Return only a weak hint; never decide whether a registered call is possible.

    The registry-aware AI planner receives every request and produces a bounded
    ``knowledge``, ``operation``, ``clarify``, or ``reject`` decision. In particular,
    question words such as “能否” and compact verbs such as “关/开/清/重置” cannot
    prevent the planner from seeing a registered capability.
    """

    _KNOWLEDGE_MARKERS = (
        "用法",
        "参数",
        "用途",
        "做什么",
        "说明",
        "帮助",
        "注意事项",
        "风险",
        "影响",
        "前置",
        "流程",
        "方法",
        "方式",
        "步骤",
        "什么脚本",
        "哪个脚本",
        "脚本用法",
        "参数是什么",
    )
    _OPERATION_MARKERS = (
        "执行",
        "运行",
        "查询",
        "查看",
        "检查",
        "获取",
        "查",
        "启动",
        "起服",
        "开服",
        "开",
        "关闭",
        "关服",
        "关",
        "停止",
        "重启",
        "清档",
        "清服",
        "清",
        "重置",
        "修改",
        "设置",
        "调整",
        "更新",
        "热更",
        "备份",
        "恢复",
        "删除",
        "上传",
    )

    def route(self, user_text: str) -> MuliuRequestIntent:
        text = user_text.strip().lower()
        if not text:
            return MuliuRequestIntent.UNCERTAIN
        if any(marker in text for marker in self._OPERATION_MARKERS):
            return MuliuRequestIntent.OPERATION
        if any(marker in text for marker in self._KNOWLEDGE_MARKERS):
            return MuliuRequestIntent.KNOWLEDGE
        return MuliuRequestIntent.UNCERTAIN
