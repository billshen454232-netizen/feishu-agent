"""Append human-readable Feishu bot conversations to date-based Markdown files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ConversationLogEntry:
    occurred_at: datetime
    chat_id: str
    message_id: str
    kind: str
    user_text: str
    reply_text: str
    source_paths: tuple[str, ...] = ()
    plan_raw_responses: tuple[str, ...] = ()
    # Each item corresponds to the same-position AI response; None means it passed.
    plan_validation_errors: tuple[str | None, ...] = ()
    # Stage names make independent semantic review distinguishable from format repair.
    plan_response_stages: tuple[str, ...] = ()
    plan_repair_attempts: int = 0
    normalized_single_step: bool = False


class ConversationLog:
    """Store date-based, human-readable bot records on the local machine."""

    def __init__(self, directory: str | Path, max_text_chars: int = 12000) -> None:
        self._directory = Path(directory)
        self._max_text_chars = max_text_chars

    def append(self, entry: ConversationLogEntry) -> Path:
        local_time = entry.occurred_at.astimezone()
        timestamp = local_time.strftime("%Y-%m-%d %H:%M:%S")
        day = local_time.strftime("%Y-%m-%d")
        log_path = self._directory / "{}.md".format(day)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        blocks = [
            "## {}｜{}".format(timestamp, self._display_kind(entry.kind)),
            "- 会话：`{}`".format(entry.chat_id),
            "- 消息：`{}`".format(entry.message_id),
            "",
            "### 用户消息",
            self._as_text_block(entry.user_text),
            "",
            "### 机器人回复",
            self._as_text_block(entry.reply_text),
        ]
        if entry.source_paths:
            blocks.extend([
                "",
                "### 本地资料来源",
                *["- `{}`".format(path) for path in entry.source_paths],
            ])
        if entry.plan_raw_responses:
            blocks.extend(self._plan_trace_blocks(entry))
        processing_notes = self._processing_notes(entry)
        if processing_notes:
            blocks.extend([
                "",
                "### 本地处理",
                *["- {}".format(note) for note in processing_notes],
            ])
        blocks.extend(["", "---", ""])

        if not log_path.exists():
            log_path.write_text("# 飞书机器人日志｜{}\n\n".format(day), encoding="utf-8")
        with log_path.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write("\n".join(blocks))
        return log_path

    def _plan_trace_blocks(self, entry: ConversationLogEntry) -> list[str]:
        blocks = ["", "### AI 计划生成过程"]
        for index, raw_response in enumerate(entry.plan_raw_responses, start=1):
            stage = entry.plan_response_stages[index - 1] if index <= len(entry.plan_response_stages) else ""
            label = "AI 第 {} 次输出{}".format(index, self._display_plan_stage(stage, index))
            blocks.extend([
                "",
                "#### {}".format(label),
                self._as_text_block(raw_response),
            ])
            if index > len(entry.plan_validation_errors):
                blocks.append("- 本地校验：结果未记录。")
                continue
            validation_error = entry.plan_validation_errors[index - 1]
            if validation_error is None:
                if entry.normalized_single_step and index == len(entry.plan_raw_responses):
                    blocks.append(
                        "- 本地校验：原始 `steps` 是单个对象，已安全转换为单元素数组后通过严格校验。"
                    )
                else:
                    blocks.append("- 本地校验：通过严格 JSON 计划校验。")
            else:
                blocks.append("- 本地校验：未通过：{}".format(validation_error))
                if self._is_repair_stage(stage, index):
                    blocks.append("- 自动纠错：此输出是针对前一次不合规结果的受限修复。")
        return blocks

    @staticmethod
    def _display_plan_stage(stage: str, index: int) -> str:
        labels = {
            "initial": "（初始决策）",
            "initial_repair": "（初始决策格式修复）",
            "semantic_review": "（独立语义复核）",
            "semantic_review_repair": "（语义复核格式修复）",
        }
        if stage:
            return labels.get(stage, "（{}）".format(stage))
        return "（自动纠错）" if index > 1 else ""

    @staticmethod
    def _is_repair_stage(stage: str, index: int) -> bool:
        if stage:
            return stage in {"initial_repair", "semantic_review_repair"}
        return index > 1

    @staticmethod
    def _display_kind(kind: str) -> str:
        return {
            "script_knowledge": "脚本资料问答",
            "knowledge_base": "本地知识库问答",
            "general_question": "机器人问答",
            "operation_plan": "执行计划生成",
            "operation_rejected": "执行计划拒绝",
            "operation_cancelled": "执行计划已取消",
            "execution_result": "执行结果",
        }.get(kind, "机器人问答")

    def _as_text_block(self, text: str) -> str:
        cleaned = self._clip(text).replace("```", "``​`")
        return "```text\n{}\n```".format(cleaned)

    def _clip(self, text: str) -> str:
        cleaned = text.strip()
        if len(cleaned) <= self._max_text_chars:
            return cleaned
        return "{}\n\n[已截断：原始内容超过 {} 个字符。]".format(
            cleaned[: self._max_text_chars],
            self._max_text_chars,
        )

    @staticmethod
    def _processing_notes(entry: ConversationLogEntry) -> list[str]:
        if entry.kind == "operation_plan":
            return ["最终状态：计划已通过本地校验与防火墙检查，正在等待原请求人在交互卡片中确认。"]
        if entry.kind == "operation_rejected":
            return ["最终状态：计划已拒绝，未进入 Muliu 执行。"]
        if entry.kind == "operation_cancelled":
            return ["最终状态：原请求人已在交互卡片中取消，未进入 Muliu 执行。"]
        if entry.kind == "execution_result":
            return ["最终状态：执行结果已回传飞书，详情见上面的机器人回复。"]
        return []
