from datetime import datetime

from src.conversation_log import ConversationLog, ConversationLogEntry


def test_writes_date_based_human_readable_question_log(tmp_path):
    log = ConversationLog(tmp_path / "logs")
    local_timezone = datetime.now().astimezone().tzinfo
    entry = ConversationLogEntry(
        occurred_at=datetime(2026, 9, 10, 16, 16, 8, tzinfo=local_timezone),
        chat_id="oc_group",
        message_id="om_question",
        kind="script_knowledge",
        user_text="我要怎么查询某个服务器现在的 Patch 列表？",
        reply_text="使用 cc_patch.py 的检查模式。",
        source_paths=("knowledge/docs/cc_patch.md",),
    )

    log_path = log.append(entry)

    assert log_path == tmp_path / "logs" / "2026-09-10.md"
    content = log_path.read_text(encoding="utf-8")
    assert "# 飞书机器人日志｜2026-09-10" in content
    assert "## 2026-09-10 16:16:08｜脚本资料问答" in content
    assert "### 用户消息" in content
    assert "我要怎么查询某个服务器现在的 Patch 列表？" in content
    assert "### 机器人回复" in content
    assert "使用 cc_patch.py 的检查模式。" in content
    assert "- `knowledge/docs/cc_patch.md`" in content


def test_writes_initial_repair_and_semantic_review_trace_in_order(tmp_path):
    log = ConversationLog(tmp_path)
    entry = ConversationLogEntry(
        occurred_at=datetime(2026, 9, 10, 16, 16, 8, tzinfo=datetime.now().astimezone().tzinfo),
        chat_id="oc_group",
        message_id="om_request",
        kind="operation_plan",
        user_text="查询 6001 服务器的 Patch 列表",
        reply_text="请确认执行。",
        plan_raw_responses=(
            '{"kind":"operation","summary":"检查","steps":"bad"}',
            '{"kind":"operation","summary":"检查","steps":[]}',
            '{"kind":"operation","summary":"检查","steps":[]}',
            '{"kind":"operation","summary":"检查","steps":[]}',
        ),
        plan_validation_errors=("steps 必须是数组", None, None, "steps 必须是数组"),
        plan_response_stages=(
            "initial",
            "initial_repair",
            "semantic_review",
            "semantic_review_repair",
        ),
        plan_repair_attempts=2,
    )

    content = log.append(entry).read_text(encoding="utf-8")

    assert "### AI 计划生成过程" in content
    assert "#### AI 第 1 次输出（初始决策）" in content
    assert "- 本地校验：未通过：steps 必须是数组" in content
    assert "#### AI 第 2 次输出（初始决策格式修复）" in content
    assert "#### AI 第 3 次输出（独立语义复核）" in content
    assert "#### AI 第 4 次输出（语义复核格式修复）" in content
    assert "- 自动纠错：此输出是针对前一次不合规结果的受限修复。" in content
    assert "- 本地校验：通过严格 JSON 计划校验。" in content
    assert "最终状态：计划已通过本地校验与防火墙检查，正在等待原请求人在交互卡片中确认。" in content


def test_writes_rejected_plan_trace_and_clips_long_text(tmp_path):
    log = ConversationLog(tmp_path, max_text_chars=12)
    entry = ConversationLogEntry(
        occurred_at=datetime(2026, 9, 10, 16, 16, 8, tzinfo=datetime.now().astimezone().tzinfo),
        chat_id="oc_group",
        message_id="om_request",
        kind="operation_rejected",
        user_text="查询 6001",
        reply_text="未执行",
        plan_raw_responses=("这是一个超过限制的模型原始响应",),
        plan_validation_errors=("steps 必须是数组",),
        plan_repair_attempts=1,
    )

    content = log.append(entry).read_text(encoding="utf-8")

    assert "[已截断：原始内容超过 12 个字符。]" in content
    assert "- 本地校验：未通过：steps 必须是数组" in content
    assert "最终状态：计划已拒绝，未进入 Muliu 执行。" in content
