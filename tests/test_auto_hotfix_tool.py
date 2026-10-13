import json
import pytest

from tools.auto_hotfix_tool import AutoHotfixTool, KNOWN_THRESHOLDS, FIELD_NAME_TO_DEFINITION


def test_known_thresholds_registration():
    # 验证弹劾人数别名与字段名映射
    assert "union_impeach_min" in KNOWN_THRESHOLDS
    def_item = KNOWN_THRESHOLDS["union_impeach_min"]
    assert def_item.table_name == "union_config_setting"
    assert def_item.field_name == "union_impeach_union_member_min"

    # 验证通过真实字段名也能反向查到
    assert "union_impeach_union_member_min" in FIELD_NAME_TO_DEFINITION


def test_generate_lua_patch_content_with_specific_scenario():
    tool = AutoHotfixTool(dry_run=True)
    lua_code = tool.generate_lua_patch_content(
        table_name="union_config_setting",
        field_name="union_impeach_union_member_min",
        value="1",
        scenarios=(2001,),
    )

    assert "TARGET_SCENARIOS = {2001}" in lua_code
    assert "IS_CLIENT" in lua_code
    assert "IS_SERVER" in lua_code
    assert "union_config_setting[ [=[union_impeach_union_member_min]=] ].content = [=[1]=]" in lua_code
    assert "pcall(require, \"server_helper.serverInfoHolder\")" in lua_code


def test_generate_lua_patch_content_with_all_scenarios():
    tool = AutoHotfixTool(dry_run=True)
    lua_code = tool.generate_lua_patch_content(
        table_name="game_config_common_config",
        field_name="week_suppress_camp_success_member_num_limit",
        value="1",
        scenarios=(),
    )

    assert "TARGET_SCENARIOS = {}" in lua_code
    assert "game_config_common_config[ [=[week_suppress_camp_success_member_num_limit]=] ].content = [=[1]=]" in lua_code


def test_dry_run_apply_patch():
    tool = AutoHotfixTool(dry_run=True)
    # mock resolve_server_ip
    tool.resolve_server_ip = lambda server_id: "10.0.0.1"
    tool.detect_server_scenario = lambda host: 7

    success = tool.apply_patch(
        server_id="5010",
        key_or_field="union_impeach_min",
        value="1",
    )
    assert success is True


def test_dry_run_custom_table_patch():
    tool = AutoHotfixTool(dry_run=True)
    tool.resolve_server_ip = lambda server_id: "10.0.0.1"

    success = tool.apply_patch(
        server_id="5010",
        key_or_field="custom_threshold_field",
        value="99",
        scenario=10,
        custom_table="my_custom_table",
    )
    assert success is True


def test_mount_and_unmount_patch_json_logic():
    tool = AutoHotfixTool(dry_run=False)
    fake_json = {
        "version": "1.0",
        "list": {
            "0": {"patch_type": "avatar", "order": 0, "file": "base_patch.lua"},
            "1": {"patch_type": "client_avatar", "order": 1, "file": "patch_1.lua"},
        },
    }

    stored_json = json.dumps(fake_json)

    def mock_run_cmd(host, cmd):
        nonlocal stored_json
        if f"cat {tool.REMOTE_PATCH_LIST}" in cmd:
            return 0, stored_json
        if "cat << 'EOF'" in cmd:
            lines = cmd.split("\n")
            json_text = "\n".join(lines[1:-1])
            stored_json = json_text
            return 0, ""
        return 0, ""

    tool.run_remote_cmd = mock_run_cmd

    # 1. 挂载新 patch
    mounted = tool._mount_patch_to_remote_json("10.0.0.1", "auto_patch_impeach.lua")
    assert mounted is True

    parsed_after_mount = json.loads(stored_json)
    items = parsed_after_mount["list"]
    assert len(items) == 3
    assert "2" in items
    assert items["2"]["file"] == "auto_patch_impeach.lua"
    assert items["2"]["order"] == 2

    # 2. 卸载该 patch
    unmounted = tool._unmount_patch_from_remote_json("10.0.0.1", "auto_patch_impeach.lua")
    assert unmounted is True

    parsed_after_unmount = json.loads(stored_json)
    items_unmount = parsed_after_unmount["list"]
    assert len(items_unmount) == 2
    assert all(v["file"] != "auto_patch_impeach.lua" for v in items_unmount.values())

