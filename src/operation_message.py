"""Format Muliu operation plans and outcomes for Feishu text replies."""

from __future__ import annotations

from .muliu_executor import MuliuExecutionResult, MuliuStepResult
from .muliu_plan import MuliuPlan


MAX_LOG_CHARS = 3500
SCRIPT_ROOT = "/home/serverGeneralScript"
BASIC_INFO_SCRIPT_PATH = "{}/basic_info.sh".format(SCRIPT_ROOT)
SERVER_LOG_QUERY_SCRIPT_PATH = "{}/server_log_query.py".format(SCRIPT_ROOT)
_EXECUTION_WRAPPER_PREFIXES = (
    "MULIU_RUN_VERSION:",
    "WORKSPACE_PATH:",
    "CODE_PATH:",
    "RUN_SCRIPT:",
    "TASK_WORKSPACE_PATH:",
    "ARGS_FILE_PATH:",
    "OUT_PUT_LOG_FILE:",
    "TASK_TO_CODE_PATH:",
    "TASK_ID:",
    "AI_OP_START",
    "AI_OP_END",
)


def format_plan_for_confirmation(plan: MuliuPlan, confirmation_text: str) -> str:
    """Format the legacy text confirmation instruction.

    New operation plans use :func:`build_plan_confirmation_card`; this remains for
    existing text confirmation messages and local diagnostic tools.
    """
    lines = [
        "⚠️ 待确认的测试服操作：{}".format(plan.summary),
        "",
    ]
    for step_number, step in enumerate(plan.steps, start=1):
        lines.extend([
            "{}/{} {}".format(step_number, len(plan.steps), step.description),
            "脚本：{}".format(_display_script_path(step.path)),
            "参数：{}".format(" ".join(step.args) if step.args else "（无）"),
            "",
        ])
    lines.extend([
        "若确认执行，请在本群发送：",
        confirmation_text,
        "未收到正确确认文本时，不会调用 Muliu。",
    ])
    return "\n".join(lines)


def build_plan_confirmation_card(
    plan: MuliuPlan,
    *,
    request_message_id: str,
    confirmation_token: str,
) -> dict[str, object]:
    """Build a card whose buttons only identify a stored pending plan.

    The card never carries script paths or arguments as executable callback data.
    The server re-loads and validates the persisted plan before any Muliu submission.
    """
    elements: list[dict[str, object]] = [
        {
            "tag": "div",
            "text": {"tag": "plain_text", "content": plan.summary},
        },
        {
            "tag": "hr",
        },
    ]
    for step_number, step in enumerate(plan.steps, start=1):
        elements.append({
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": "{}/{} {}\n脚本：{}\n参数：{}".format(
                    step_number,
                    len(plan.steps),
                    step.description,
                    _display_script_path(step.path),
                    " ".join(step.args) if step.args else "（无）",
                ),
            },
        })
    callback_reference = {
        "request_message_id": request_message_id,
        "confirmation_token": confirmation_token,
    }
    elements.extend([
        {"tag": "hr"},
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "仅发起该请求的成员可以确认或取消。确认后将提交已展示的完整计划。",
                }
            ],
        },
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "type": "primary",
                    "text": {"tag": "plain_text", "content": "确认执行"},
                    "value": {"action": "confirm_operation", **callback_reference},
                    "confirm": {
                        "title": {"tag": "plain_text", "content": "确认执行测试服操作"},
                        "text": {
                            "tag": "plain_text",
                            "content": "确认后会立即提交卡片中展示的操作计划。",
                        },
                    },
                },
                {
                    "tag": "button",
                    "type": "default",
                    "text": {"tag": "plain_text", "content": "取消"},
                    "value": {"action": "cancel_operation", **callback_reference},
                    "confirm": {
                        "title": {"tag": "plain_text", "content": "取消待确认操作"},
                        "text": {"tag": "plain_text", "content": "取消后不能恢复该待确认计划。"},
                    },
                },
            ],
        },
    ])
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "待确认的测试服操作"},
        },
        "elements": elements,
    }


def build_operation_status_card(
    title: str,
    summary: str,
    detail: str,
    *,
    template: str,
) -> dict[str, object]:
    """Build a terminal or in-progress card without actionable controls."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": summary}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "plain_text", "content": detail}},
        ],
    }


def build_execution_status_card(result: MuliuExecutionResult) -> dict[str, object]:
    if result.succeeded:
        return build_operation_status_card(
            "测试服操作已完成",
            result.summary,
            "执行结果和日志已作为本卡片的回复发送。",
            template="green",
        )
    if result.result_unknown:
        return build_operation_status_card(
            "测试服操作结果尚未核验",
            result.summary,
            "{}\n请勿重复确认或重试同一操作。".format(result.result_unknown_reason),
            template="yellow",
        )
    return build_operation_status_card(
        "测试服操作执行失败",
        result.summary,
        "{}\n执行结果和日志已作为本卡片的回复发送。".format(result.failure_reason),
        template="red",
    )


def format_execution_result(result: MuliuExecutionResult) -> str:
    if result.succeeded:
        lines = ["✅ 执行完成：{}".format(result.summary), ""]
    elif result.result_unknown:
        lines = [
            "⏳ 已提交，结果尚未核验：{}".format(result.summary),
            "原因：{}".format(result.result_unknown_reason),
            "请勿重复确认或重试同一操作。",
            "",
        ]
    else:
        lines = [
            "❌ 执行失败：{}".format(result.summary),
            "失败步骤：第 {} 步".format(result.failed_step_number),
            "原因：{}".format(result.failure_reason),
            "",
        ]

    total_steps = len(result.step_results)
    if result.failed_step_number is not None:
        total_steps = max(total_steps, result.failed_step_number)
    if result.result_unknown:
        total_steps = max(total_steps, 1)

    for result_index, step in enumerate(result.step_results, start=1):
        status = "✅" if step.succeeded else "❌"
        display_step_number = result_index if result.step_results else step.step_number
        lines.extend([
            "{}/{} {} {}".format(display_step_number, total_steps, status, step.description),
            "脚本：{}".format(_display_script_path(step.path)),
            "参数：{}".format(" ".join(step.args) if step.args else "（无）"),
            _result_label(step),
            _format_step_log(step),
            "",
        ])

    if not result.step_results and not result.succeeded and not result.result_unknown:
        lines.append("Muliu 尚未开始执行任何步骤。")

    return "\n".join(lines).rstrip()


def format_rejection(reason: str) -> str:
    return "❌ 未执行\n原因：{}".format(reason)


def _display_script_path(path: str) -> str:
    prefix = "{}/".format(SCRIPT_ROOT)
    if path.startswith(prefix):
        return path[len(prefix):]
    return path


def _result_label(step: MuliuStepResult) -> str:
    if step.path == BASIC_INFO_SCRIPT_PATH and step.succeeded:
        return "查询结果："
    if step.path == SERVER_LOG_QUERY_SCRIPT_PATH and step.succeeded:
        return "诊断结果："
    return "最新日志："


def _format_step_log(step: MuliuStepResult) -> str:
    log = step.log
    if step.path == BASIC_INFO_SCRIPT_PATH and step.succeeded:
        return _format_basic_info_result(log)
    if step.path == SERVER_LOG_QUERY_SCRIPT_PATH and step.succeeded:
        return _format_server_log_query_result(log)
    return _truncate_log(log)


def _format_basic_info_result(log: str) -> str:
    """Show stable basic-info fields while retaining unrecognized diagnostics.

    Task 89 prepends workspace metadata to every returned log. Basic-info output is
    line-oriented and already contains the fields a group user needs, so remove only
    known wrapper lines and visual separators. Unknown lines remain visible rather
    than being silently discarded if the deployed shell script changes. The complete
    unfiltered Muliu output remains in the local conversation audit log.
    """
    visible_lines: list[str] = []
    for raw_line in log.splitlines():
        line = raw_line.strip()
        if not line or _is_execution_wrapper_line(line) or _is_visual_separator(line):
            continue
        visible_lines.append(line)

    if not visible_lines:
        return "未从 Muliu 返回日志中提取到可展示的基础信息。"
    return _truncate_log("\n".join(visible_lines))


def _format_server_log_query_result(log: str) -> str:
    visible_lines: list[str] = []
    for raw_line in log.splitlines():
        line = raw_line.strip()
        if not line or _is_execution_wrapper_line(line):
            continue
        visible_lines.append(line)

    if not visible_lines:
        return "未从 Muliu 返回日志中提取到可展示的启动诊断信息。"
    return _truncate_log("\n".join(visible_lines))


def _is_execution_wrapper_line(line: str) -> bool:
    return line in {"START", "Running", "END"} or line.startswith("bash ../../code/") or line.startswith(
        _EXECUTION_WRAPPER_PREFIXES
    )


def _is_visual_separator(line: str) -> bool:
    return len(line) >= 3 and set(line) == {"*"}


def _truncate_log(log: str) -> str:
    if len(log) <= MAX_LOG_CHARS:
        return log
    return "{}\n…（日志已截断，共 {} 字符）".format(log[:MAX_LOG_CHARS], len(log))
