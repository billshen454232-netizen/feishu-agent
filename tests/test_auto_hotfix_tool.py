import json
import pytest

from tools.auto_hotfix_registry import (
    FIELD_NAME_TO_DEFINITION as REGISTRY_FIELD_NAME_TO_DEFINITION,
)
from tools.auto_hotfix_registry import KNOWN_THRESHOLDS as REGISTRY_KNOWN_THRESHOLDS
from tools.auto_hotfix_registry import PLANNER_TERM_TO_KEY, ThresholdDefinition as RegistryThresholdDefinition
from tools.auto_hotfix_registry import hotfix_key_pattern, planner_term_to_key
from tools.auto_hotfix_tool import (
    AutoHotfixTool,
    DEFAULT_PATCH_TYPE,
    FIELD_NAME_TO_DEFINITION,
    KNOWN_THRESHOLDS,
    SUPPORTED_PATCH_TYPES,
    ThresholdDefinition,
)


def test_known_thresholds_are_reexported_from_registry_without_changes():
    assert ThresholdDefinition is RegistryThresholdDefinition
    assert KNOWN_THRESHOLDS is REGISTRY_KNOWN_THRESHOLDS
    assert FIELD_NAME_TO_DEFINITION is REGISTRY_FIELD_NAME_TO_DEFINITION

    exported_records = {
        key: (
            definition.key,
            definition.name,
            definition.table_name,
            definition.field_name,
            definition.default_scenarios,
            definition.description,
            definition.is_client,
            definition.is_server,
            definition.aliases,
        )
        for key, definition in KNOWN_THRESHOLDS.items()
    }
    registry_records = {
        key: (
            definition.key,
            definition.name,
            definition.table_name,
            definition.field_name,
            definition.default_scenarios,
            definition.description,
            definition.is_client,
            definition.is_server,
            definition.aliases,
        )
        for key, definition in REGISTRY_KNOWN_THRESHOLDS.items()
    }
    assert exported_records == registry_records

    for key, definition in KNOWN_THRESHOLDS.items():
        assert definition.key == key
        assert FIELD_NAME_TO_DEFINITION[definition.field_name] is definition


def test_known_thresholds_registration():
    # 验证弹劾人数别名与字段名映射
    assert "union_impeach_min" in KNOWN_THRESHOLDS
    def_item = KNOWN_THRESHOLDS["union_impeach_min"]
    assert def_item.table_name == "union_config_setting"
    assert def_item.field_name == "union_impeach_union_member_min"

    # 验证通过真实字段名也能反向查到
    assert "union_impeach_union_member_min" in FIELD_NAME_TO_DEFINITION
    assert DEFAULT_PATCH_TYPE == "common"
    assert {"common", "client_avatar", "battle"}.issubset(SUPPORTED_PATCH_TYPES)


def test_planner_routes_chinese_threshold_names_from_execution_registry():
    assert PLANNER_TERM_TO_KEY["剧本预选州战队人数显示阈值"] == "faction_team_show_member_limit"
    assert PLANNER_TERM_TO_KEY["战队交换人数"] == "faction_swap_team_member_min"
    assert "faction_team_show_member_limit" in hotfix_key_pattern()
    assert "faction_swap_team_member_min" in hotfix_key_pattern()


def test_planner_registry_rejects_duplicate_chinese_aliases():
    duplicate_alias = RegistryThresholdDefinition(
        key="another_threshold",
        name="另一个阈值",
        table_name="test_table",
        field_name="another_field",
        aliases=("剧本预选州战队人数显示阈值",),
    )
    thresholds = dict(REGISTRY_KNOWN_THRESHOLDS)
    thresholds[duplicate_alias.key] = duplicate_alias

    with pytest.raises(ValueError, match="中文标准名或别名重复"):
        planner_term_to_key(thresholds)


def test_team_swap_member_threshold_registration_and_patch_content():
    definition = KNOWN_THRESHOLDS["faction_swap_team_member_min"]
    assert definition.table_name == "season_process_season_config"
    assert definition.field_name == "faction_swap_team_member_num_min"
    assert FIELD_NAME_TO_DEFINITION[definition.field_name] is definition

    tool = AutoHotfixTool(dry_run=True)
    lua_code = tool.generate_lua_patch_content(
        table_name=definition.table_name,
        field_name=definition.field_name,
        value="1",
        scenarios=(),
    )
    assert (
        "season_process_season_config[ [=[faction_swap_team_member_num_min]=] ].content = [=[1]=]"
        in lua_code
    )


def test_generate_lua_patch_content_with_specific_scenario():
    tool = AutoHotfixTool(dry_run=True)
    lua_code = tool.generate_lua_patch_content(
        table_name="union_config_setting",
        field_name="union_impeach_union_member_min",
        value="1",
        scenarios=(2001,),
    )

    assert "if G_SCENARIO_ID == 2001 then" in lua_code
    assert "IS_CLIENT" in lua_code
    assert "IS_SERVER" in lua_code
    assert "union_config_setting[ [=[union_impeach_union_member_min]=] ].content = [=[1]=]" in lua_code
    # 与线上手写补丁一致：直接 require，不用 pcall 吞掉错误
    assert 'require "server_helper.serverInfoHolder"' in lua_code
    assert "pcall" not in lua_code
    # 通用内容：不再判空，字段写错应当显式暴露而不是静默不生效
    assert "if union_config_setting and" not in lua_code


def test_generate_lua_patch_content_with_all_scenarios():
    tool = AutoHotfixTool(dry_run=True)
    lua_code = tool.generate_lua_patch_content(
        table_name="game_config_common_config",
        field_name="week_suppress_camp_success_member_num_limit",
        value="1",
        scenarios=(),
    )

    # 空剧本 = 全部剧本，不生成任何场景守卫
    assert "G_SCENARIO_ID ==" not in lua_code
    assert "TARGET_SCENARIOS" not in lua_code
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

    # 1. 挂载新的 common patch；order 仅相对 common 类型计算。
    mounted = tool._mount_patch_to_remote_json("10.0.0.1", "auto_common_patch_impeach.lua", "common")
    assert mounted is True

    parsed_after_mount = json.loads(stored_json)
    items = parsed_after_mount["list"]
    assert len(items) == 3
    assert "2" in items
    assert items["2"] == {
        "patch_type": "common",
        "order": 0,
        "file": "auto_common_patch_impeach.lua",
    }

    # 2. 卸载该 patch
    unmounted = tool._unmount_patch_from_remote_json("10.0.0.1", "auto_common_patch_impeach.lua")
    assert unmounted is True

    parsed_after_unmount = json.loads(stored_json)
    items_unmount = parsed_after_unmount["list"]
    assert len(items_unmount) == 2
    assert all(v["file"] != "auto_common_patch_impeach.lua" for v in items_unmount.values())


def test_reuses_latest_same_type_patch_without_rewriting_list():
    tool = AutoHotfixTool(dry_run=False)
    stored_json = json.dumps(
        {
            "version": "1.0",
            "list": {
                "17": {
                    "patch_type": "client_avatar",
                    "order": 7,
                    "file": "auto_patch_capital_city_extra_quota_5010_20261013_195607.lua",
                },
                "18": {
                    "patch_type": "client_avatar",
                    "order": 8,
                    "file": "auto_patch_capital_city_extra_quota_5010_20261013_195804.lua",
                },
                "19": {
                    "patch_type": "common",
                    "order": 3,
                    "file": "auto_common_patch_capital_city_extra_quota_5010.lua",
                },
            },
        }
    )
    remote_commands = []
    uploaded_paths = []

    tool.resolve_server_ip = lambda server_id: "10.0.0.1"
    tool.detect_server_scenario = lambda host: 2001

    def mock_run_cmd(host, cmd):
        remote_commands.append(cmd)
        if f"cat {tool.REMOTE_PATCH_LIST}" in cmd:
            return 0, stored_json
        return 0, ""

    def mock_scp(host, local_file, remote_file):
        uploaded_paths.append(remote_file)
        return 0, ""

    tool.run_remote_cmd = mock_run_cmd
    tool.scp_to_remote = mock_scp

    assert tool.apply_patch("5010", "capital_city_extra_quota", "2") is True
    # 双端阈值各写一份，且都复用已有文件名，不产生新条目
    assert uploaded_paths == [
        tool.REMOTE_PATCH_ROOT + "/common/auto_common_patch_capital_city_extra_quota_5010.lua",
        tool.REMOTE_PATCH_ROOT + "/client_avatar/auto_patch_capital_city_extra_quota_5010_20261013_195804.lua",
    ]
    assert not any("cat << 'EOF'" in cmd for cmd in remote_commands)


def test_patch_type_isolated_from_other_types_and_uses_type_specific_order():
    tool = AutoHotfixTool(dry_run=False)
    stored_json = json.dumps(
        {
            "list": {
                "1": {
                    "patch_type": "client_avatar",
                    "order": 9,
                    "file": "auto_patch_capital_city_extra_quota_5010_20261013_195804.lua",
                },
                "2": {"patch_type": "common", "order": 3, "file": "common_patch_1.lua"},
            }
        }
    )

    def mock_run_cmd(host, cmd):
        nonlocal stored_json
        if f"cat {tool.REMOTE_PATCH_LIST}" in cmd:
            return 0, stored_json
        if "cat << 'EOF'" in cmd:
            stored_json = "\n".join(cmd.split("\n")[1:-1])
        return 0, ""

    tool.run_remote_cmd = mock_run_cmd
    assert tool._select_patch_file_name(
        "10.0.0.1", "5010", "capital_city_extra_quota", "common"
    ) == "auto_common_patch_capital_city_extra_quota_5010.lua"
    assert tool._mount_patch_to_remote_json(
        "10.0.0.1", "auto_common_patch_capital_city_extra_quota_5010.lua", "common"
    ) is True

    new_item = json.loads(stored_json)["list"]["3"]
    assert new_item == {
        "patch_type": "common",
        "order": 4,
        "file": "auto_common_patch_capital_city_extra_quota_5010.lua",
    }


def test_selects_stable_name_for_first_patch():
    tool = AutoHotfixTool(dry_run=False)
    tool.run_remote_cmd = lambda host, cmd: (0, json.dumps({"list": {}}))

    assert tool._select_patch_file_name(
        "10.0.0.1", "5010", "capital_city_extra_quota", "common"
    ) == "auto_common_patch_capital_city_extra_quota_5010.lua"


def test_rejects_unsupported_patch_type():
    tool = AutoHotfixTool(dry_run=True)
    tool.resolve_server_ip = lambda server_id: "10.0.0.1"

    with pytest.raises(ValueError, match="不支持的补丁类型"):
        tool.apply_patch("5010", "capital_city_extra_quota", "1", patch_type="../../etc")


def test_cleanup_legacy_auto_patches_preserves_common_patch():
    tool = AutoHotfixTool(dry_run=False)
    stored_json = json.dumps(
        {
            "list": {
                "17": {
                    "patch_type": "client_avatar",
                    "order": 7,
                    "file": "auto_patch_capital_city_extra_quota_5010_20261013_195607.lua",
                },
                "18": {
                    "patch_type": "client_avatar",
                    "order": 8,
                    "file": "auto_patch_capital_city_extra_quota_5010_20261013_195804.lua",
                },
                "19": {
                    "patch_type": "common",
                    "order": 4,
                    "file": "auto_common_patch_capital_city_extra_quota_5010.lua",
                },
            }
        }
    )
    removed_files = []
    commands = []

    tool.resolve_server_ip = lambda server_id: "10.0.0.1"

    def mock_run_cmd(host, cmd):
        nonlocal stored_json
        commands.append(cmd)
        if f"cat {tool.REMOTE_PATCH_LIST}" in cmd:
            return 0, stored_json
        if "cat << 'EOF'" in cmd:
            stored_json = "\n".join(cmd.split("\n")[1:-1])
        if cmd.startswith("rm -f -- "):
            removed_files.append(cmd)
        return 0, ""

    tool.run_remote_cmd = mock_run_cmd

    assert tool.cleanup_legacy_auto_patches("5010", "capital_city_extra_quota") is True
    remaining_items = json.loads(stored_json)["list"]
    assert list(remaining_items.values()) == [
        {
            "patch_type": "common",
            "order": 4,
            "file": "auto_common_patch_capital_city_extra_quota_5010.lua",
        }
    ]
    assert removed_files == [
        "rm -f -- {}/client_avatar/auto_patch_capital_city_extra_quota_5010_20261013_195607.lua".format(
            tool.REMOTE_PATCH_ROOT
        ),
        "rm -f -- {}/client_avatar/auto_patch_capital_city_extra_quota_5010_20261013_195804.lua".format(
            tool.REMOTE_PATCH_ROOT
        ),
    ]
    assert commands[-1] == tool.REMOTE_RUN_PATCH_CMD


def test_restore_auto_patches_removes_all_types_for_one_field():
    tool = AutoHotfixTool(dry_run=False)
    stored_json = json.dumps(
        {
            "list": {
                "17": {
                    "patch_type": "client_avatar",
                    "order": 7,
                    "file": "auto_patch_capital_city_extra_quota_5010_20261013_195804.lua",
                },
                "19": {
                    "patch_type": "common",
                    "order": 4,
                    "file": "auto_common_patch_capital_city_extra_quota_5010.lua",
                },
                "20": {
                    "patch_type": "common",
                    "order": 5,
                    "file": "auto_common_patch_rank_quota_5010.lua",
                },
            }
        }
    )
    removed_files = []

    tool.resolve_server_ip = lambda server_id: "10.0.0.1"

    def mock_run_cmd(host, cmd):
        nonlocal stored_json
        if f"cat {tool.REMOTE_PATCH_LIST}" in cmd:
            return 0, stored_json
        if "cat << 'EOF'" in cmd:
            stored_json = "\n".join(cmd.split("\n")[1:-1])
        if cmd.startswith("rm -f -- "):
            removed_files.append(cmd)
        return 0, ""

    tool.run_remote_cmd = mock_run_cmd

    assert tool.restore_auto_patches("5010", "capital_city_extra_quota") is True
    remaining_items = json.loads(stored_json)["list"]
    assert list(remaining_items.values()) == [
        {
            "patch_type": "common",
            "order": 5,
            "file": "auto_common_patch_rank_quota_5010.lua",
        }
    ]
    assert len(removed_files) == 2


def test_rollback_by_key_or_alias_delegates_to_full_restore():
    tool = AutoHotfixTool(dry_run=True)
    calls = []
    tool.restore_auto_patches = lambda server_id, key: calls.append((server_id, key)) or True

    assert tool.rollback_patch("5010", "capital_city_extra_quota") is True
    assert calls == [("5010", "capital_city_extra_quota")]


def test_derived_patch_types_follow_declared_sides():
    both = RegistryThresholdDefinition(
        key="k_both", name="双端", table_name="t", field_name="f_both",
        is_client=True, is_server=True,
    )
    client_only = RegistryThresholdDefinition(
        key="k_client", name="仅客户端", table_name="t", field_name="f_client",
        is_client=True, is_server=False,
    )
    server_only = RegistryThresholdDefinition(
        key="k_server", name="仅服务端", table_name="t", field_name="f_server",
        is_client=False, is_server=True,
    )

    assert both.derived_patch_types() == ("common", "client_avatar")
    assert client_only.derived_patch_types() == ("client_avatar",)
    assert server_only.derived_patch_types() == ("common",)


def test_explicit_patch_type_overrides_derived_sides():
    tool = AutoHotfixTool(dry_run=False)
    stored_json = json.dumps({"version": "1.0", "list": {}})
    uploaded_paths = []

    tool.resolve_server_ip = lambda server_id: "10.0.0.1"
    tool.detect_server_scenario = lambda host: 2001

    def mock_run_cmd(host, cmd):
        if f"cat {tool.REMOTE_PATCH_LIST}" in cmd:
            return 0, stored_json
        return 0, ""

    tool.run_remote_cmd = mock_run_cmd
    tool.scp_to_remote = lambda host, local_file, remote_file: (uploaded_paths.append(remote_file) or (0, ""))

    # 显式指定类型时只写该类型，不按 is_client/is_server 展开
    assert tool.apply_patch("5010", "union_impeach_min", "1", patch_type="client_avatar") is True
    assert len(uploaded_paths) == 1
    assert "/client_avatar/" in uploaded_paths[0]
    assert uploaded_paths[0].endswith("auto_client_avatar_patch_union_impeach_union_member_min_5010.lua")


def test_generated_patch_has_both_guards_but_no_side_specific_content():
    tool = AutoHotfixTool(dry_run=True)
    lua_code = tool.generate_lua_patch_content(
        table_name="union_config_setting",
        field_name="union_impeach_union_member_min",
        value="1",
        scenarios=(3001,),
    )

    # 双 guard 都在，内容对两侧通用
    assert "if IS_CLIENT then" in lua_code
    assert "if IS_SERVER then" in lua_code
    # 客户端侧直接用 G_SCENARIO_ID；服务端侧重新取服端剧本
    assert lua_code.count("G_SCENARIO_ID == 3001") == 2
    assert "server_helper.serverInfoHolder" in lua_code
