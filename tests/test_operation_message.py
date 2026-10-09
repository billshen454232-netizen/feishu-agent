from src.muliu_executor import MuliuExecutionResult, MuliuStepResult
from src.muliu_plan import MuliuPlan, MuliuStep
from src.operation_message import (
    build_execution_status_card,
    build_plan_confirmation_card,
    format_execution_result,
    format_plan_for_confirmation,
)


def test_confirmation_hides_script_root_from_script_name():
    plan = MuliuPlan(
        summary="查询服务器信息",
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/script_helper/get_db_host.sh",
                args=["2003"],
                description="查询数据库地址",
            )
        ],
    )

    message = format_plan_for_confirmation(plan, "确认执行 ABC123")

    assert "脚本：script_helper/get_db_host.sh" in message
    assert "/home/serverGeneralScript" not in message


def test_execution_result_hides_script_root_from_script_name():
    result = MuliuExecutionResult(
        summary="查询服务器信息",
        step_results=[
            MuliuStepResult(
                step_number=1,
                description="查询基础信息",
                path="/home/serverGeneralScript/basic_info.sh",
                args=["2003"],
                succeeded=True,
                log="server info",
            )
        ],
    )

    message = format_execution_result(result)

    assert "脚本：basic_info.sh" in message
    assert "/home/serverGeneralScript" not in message


def test_successful_basic_info_result_hides_muliu_wrapper_metadata():
    result = MuliuExecutionResult(
        summary="查询 6000 服当前剧本",
        step_results=[
            MuliuStepResult(
                step_number=1,
                description="查询 6000 服当前剧本配置",
                path="/home/serverGeneralScript/basic_info.sh",
                args=["6000"],
                succeeded=True,
                log="""MULIU_RUN_VERSION: opaque-version
WORKSPACE_PATH: ../muliu_ws/workspace
TASK_ID: 89
START
bash ../../code/ok_/muliu_ai_ops.sh omitted
Running
AI_OP_START task_id=89 path=/home/serverGeneralScript/basic_info.sh
NSLG_HOSTNUM = 6000 -- 逻辑服编号
NSLG_SEASON = 3 --赛季
NSLG_SCENARIO = 2003 --剧本
************************
当前代码版本:
hk_noversion_260904
************************
开服时间 /data/app/game-server/server_common/Debug_ServerStartTime.lua 时间戳/时间如下：
1790820000 -> 2026-10-01 10:00:00
************************
开启中的skynet服务器进程组为:game
zone
************************
开服成功
AI_OP_END status=success
END""",
            )
        ],
    )

    message = format_execution_result(result)

    assert "查询结果：" in message
    assert "NSLG_SCENARIO = 2003 --剧本" in message
    assert "当前代码版本:\nhk_noversion_260904" in message
    assert "开服成功" in message
    assert "MULIU_RUN_VERSION" not in message
    assert "WORKSPACE_PATH" not in message
    assert "TASK_ID" not in message
    assert "AI_OP_START" not in message
    assert "AI_OP_END" not in message
    assert "************************" not in message


def test_successful_startup_diagnostic_hides_muliu_wrapper_metadata():
    result = MuliuExecutionResult(
        summary="检查 5000 服起服失败原因",
        step_results=[
            MuliuStepResult(
                step_number=1,
                description="读取 5000 服固定启动日志中的近期异常信号",
                path="/home/serverGeneralScript/server_log_query.py",
                args=["--server-id", "5000", "--profile", "startup_errors", "--max-lines", "50"],
                succeeded=True,
                log="""MULIU_RUN_VERSION: opaque-version
WORKSPACE_PATH: ../muliu_ws/workspace
TASK_ID: 89
START
AI_OP_START task_id=89 path=/home/serverGeneralScript/server_log_query.py
LOG_QUERY_PROFILE=startup_errors
===== 近期启动异常信号 =====
startup_log_match: game_6 start fail: scenario bootstrap error
LOG_QUERY_STATUS=OK
AI_OP_END status=success
END""",
            )
        ],
    )

    message = format_execution_result(result)

    assert "诊断结果：" in message
    assert "startup_log_match: game_6 start fail: scenario bootstrap error" in message
    assert "LOG_QUERY_STATUS=OK" in message
    assert "MULIU_RUN_VERSION" not in message
    assert "WORKSPACE_PATH" not in message
    assert "AI_OP_START" not in message
    assert "AI_OP_END" not in message


def test_execution_result_shows_submitted_result_unknown_without_claiming_failure():
    result = MuliuExecutionResult(
        summary="启动 5000 服",
        step_results=[],
        result_unknown_reason="Task 89 在 1800 秒内未返回脚本终态；操作可能仍在运行，请勿重复确认或重试",
    )

    message = format_execution_result(result)

    assert message.startswith("⏳ 已提交，结果尚未核验：启动 5000 服")
    assert "请勿重复确认或重试同一操作。" in message
    assert "❌ 执行失败" not in message
    assert "Muliu 尚未开始执行任何步骤。" not in message


def test_execution_result_numbers_post_start_verification_as_second_result():
    result = MuliuExecutionResult(
        summary="启动 5000 服",
        step_results=[
            MuliuStepResult(1, "启动 5000 服", "/home/serverGeneralScript/start", ["5000"], True, "ok"),
            MuliuStepResult(1, "核验 5000 服是否起服成功", "/home/serverGeneralScript/basic_info.sh", ["5000"], True, "开服成功"),
        ],
    )

    message = format_execution_result(result)

    assert "1/2 ✅ 启动 5000 服" in message
    assert "2/2 ✅ 核验 5000 服是否起服成功" in message


def test_confirmation_formats_ordered_plan_with_each_script_and_argument_list():
    plan = MuliuPlan(
        summary="清档 5000 服并改为赛季 10、剧本 5",
        steps=[
            MuliuStep("/home/serverGeneralScript/clear", ["5000"], "清档 5000 服"),
            MuliuStep(
                "/home/serverGeneralScript/modify_game_config.sh",
                ["5000", "10", "5"],
                "将 5000 服改为赛季 10、剧本 5",
            ),
        ],
    )

    message = format_plan_for_confirmation(plan, "确认执行 ABC123")

    assert "1/2 清档 5000 服" in message
    assert "脚本：clear\n参数：5000" in message
    assert "2/2 将 5000 服改为赛季 10、剧本 5" in message
    assert "脚本：modify_game_config.sh\n参数：5000 10 5" in message


def test_confirmation_card_uses_only_pending_plan_identity_in_button_values():
    plan = MuliuPlan(
        summary="关闭 5000 服",
        steps=[MuliuStep("/home/serverGeneralScript/shutdown", ["5000"], "关闭 5000 服")],
    )

    card = build_plan_confirmation_card(
        plan,
        request_message_id="om_request",
        confirmation_token="ABC123",
    )

    actions = card["elements"][-1]["actions"]
    confirm_value = actions[0]["value"]
    cancel_value = actions[1]["value"]
    assert confirm_value == {
        "action": "confirm_operation",
        "request_message_id": "om_request",
        "confirmation_token": "ABC123",
    }
    assert cancel_value == {
        "action": "cancel_operation",
        "request_message_id": "om_request",
        "confirmation_token": "ABC123",
    }
    assert "/home/serverGeneralScript/shutdown" not in str(confirm_value)
    assert "5000" not in str(confirm_value)


def test_execution_status_card_has_no_action_controls():
    card = build_execution_status_card(
        MuliuExecutionResult(summary="启动 5000 服", step_results=[])
    )

    assert card["header"]["template"] == "green"
    assert "action" not in str(card["elements"])


def test_execution_result_marks_returned_dispatcher_failure_as_failed_step():
    result = MuliuExecutionResult(
        summary="核对日志诊断入口",
        step_results=[
            MuliuStepResult(
                step_number=1,
                description="查询 6000 服基础信息",
                path="/home/serverGeneralScript/basic_info.sh",
                args=["6000"],
                succeeded=False,
                log="AI_OP_END status=failed error=脚本执行失败，exit_code=1",
            )
        ],
        failed_step_number=1,
        failure_reason="脚本执行失败，exit_code=1",
    )

    message = format_execution_result(result)

    assert "❌ 执行失败：核对日志诊断入口" in message
    assert "1/1 ❌ 查询 6000 服基础信息" in message
    assert "AI_OP_END status=failed" in message
    assert "最新日志：" in message


def test_successful_batch_query_hides_muliu_wrapper_metadata():
    raw_muliu_log = """MULIU_RUN_VERSION: 3d62564e-90cc-47e9-8ac1-8aededab74b7
WORKSPACE_PATH: ../muliu_ws/workspace
CODE_PATH: ../muliu_ws/workspace/code
RUN_SCRIPT: ok_/muliu_ai_ops.sh
TASK_WORKSPACE_PATH: ../muliu_ws/workspace/taskDir/task_89
ARGS_FILE_PATH: args/s_11_0_0_261009_152047_40
OUT_PUT_LOG_FILE: log/step_11_param_0/run_0_261009_152047_39.log
TASK_TO_CODE_PATH: ../../code
TASK_ID: 89
PYTHON_CODE_ROOT: /data/app/muliu_ws/workspace/code
/data/app/muliu_ws/workspace/taskDir/task_89
START
bash ../../code/ok_/muliu_ai_ops.sh
Running
AI_OP_START task_id=89 path=/home/serverGeneralScript/batch_server_query.py
AI_OP_START
============================================================
【批量查询】海外所有DEV环境
目标服务器：5000、5001、6000、6001（共 4 台）
------------------------------------------------------------
汇总状态：全部成功（4/4）｜ 代码版本完全一致：hk_noversion_260928
------------------------------------------------------------
服号     | 状态 | IP主机           | 版本信息 / 错误详情
------------------------------------------------------------
5000     | ✅   | 10.202.28.150    | hk_noversion_260928
5001     | ✅   | 10.202.28.144    | hk_noversion_260928
============================================================
AI_OP_END status=success
END
AI_OP_END status=success
END"""
    result = MuliuExecutionResult(
        summary="批量查询海外所有DEV环境服务器代码版本",
        step_results=[
            MuliuStepResult(
                step_number=1,
                description="并发查询 5000、5001、6000、6001 服务器代码版本",
                path="/home/serverGeneralScript/batch_server_query.py",
                args=["--servers", "5000,5001,6000,6001", "--label", "海外所有DEV环境"],
                succeeded=True,
                log=raw_muliu_log,
            )
        ],
    )

    message = format_execution_result(result)

    assert "查询结果：" in message
    assert "【批量查询】海外所有DEV环境" in message
    assert "汇总状态：全部成功（4/4）" in message
    assert "5000     | ✅" in message
    assert "MULIU_RUN_VERSION" not in message
    assert "WORKSPACE_PATH" not in message
    assert "AI_OP_START" not in message
    assert "AI_OP_END" not in message
    assert "bash ../../code/" not in message
    assert "Running" not in message

