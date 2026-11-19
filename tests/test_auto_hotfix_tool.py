import json
import pytest

from tools.auto_hotfix_tool import (
    AutoHotfixTool,
    DEFAULT_PATCH_TYPE,
    SUPPORTED_PATCH_TYPES,
)


def test_hotfix_tool_no_longer_embeds_any_threshold_definition():
    """GS-1 工具不再内置阈值定义：唯一真源是 Gateway 的 /thresholds 注册表."""
    import tools.auto_hotfix_tool as hotfix_tool

    for removed in ("KNOWN_THRESHOLDS", "FIELD_NAME_TO_DEFINITION", "ThresholdDefinition"):
        assert not hasattr(hotfix_tool, removed), "不应再暴露内置定义 {}".format(removed)


def test_hotfix_tool_does_not_import_a_registry_module():
    """切断对 GS-1 注册表文件的依赖：删文件后工具仍可导入."""
    import pathlib

    source = pathlib.Path("tools/auto_hotfix_tool.py").read_text(encoding="utf-8")
    assert "from auto_hotfix_registry import" not in source
    assert "from tools.auto_hotfix_registry import" not in source


def test_apply_patch_requires_explicit_table():
    """强制手工调用也必须带 --table，不允许回退到内置定义."""
    tool = AutoHotfixTool(dry_run=True)
    with pytest.raises(ValueError, match="--table"):
        tool.apply_patch(server_id="5010", key_or_field="union_impeach_union_member_min", value="1")

    # 别名同样不再被解析：必须给真实字段名
    tool2 = AutoHotfixTool(dry_run=True)
    with pytest.raises(ValueError, match="字段名"):
        tool2.apply_patch(
            server_id="5010", key_or_field="弹劾人数", value="1", custom_table="union_config_setting"
        )


def test_team_swap_member_patch_content_from_explicit_arguments():
    """字段定义由调用方给出（真源是 Gateway 注册表），工具只负责渲染."""
    tool = AutoHotfixTool(dry_run=True)
    lua_code = tool.generate_lua_patch_content(
        table_name="season_process_season_config",
        field_name="faction_swap_team_member_num_min",
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
        key_or_field="union_impeach_union_member_min",
        value="1",
        custom_table="union_config_setting",
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

    assert tool.apply_patch(
        "5010", "capital_city_extra_quota", "2",
        custom_table="capital_city_config", patch_type="client_avatar",
    ) is True
    # 显式 patch_type 时只写该类型一份，并复用已有文件名（取执行顺序最高者）
    assert uploaded_paths == [
        tool.REMOTE_PATCH_ROOT + "/client_avatar/auto_patch_capital_city_extra_quota_5010_20261013_195804.lua",
    ]
    assert not any("cat << 'EOF'" in cmd for cmd in remote_commands)


def test_dual_side_threshold_requires_two_explicit_patch_types():
    """双端阈值不再由内置定义自动推导：调用方必须分别用两个 --patch-type 各打一次."""
    tool = AutoHotfixTool(dry_run=True)
    tool.resolve_server_ip = lambda server_id: "10.0.0.1"
    tool.detect_server_scenario = lambda host: 2001
    uploaded = []
    tool.run_remote_cmd = lambda host, cmd: (0, "")
    tool.scp_to_remote = lambda host, local_file, remote_file: (uploaded.append(remote_file), (0, ""))[1]

    for patch_type in ("common", "client_avatar"):
        assert tool.apply_patch(
            "5010", "union_impeach_union_member_min", "1",
            custom_table="union_config_setting", patch_type=patch_type,
        ) is True

    assert uploaded == [
        tool.REMOTE_PATCH_ROOT + "/common/auto_common_patch_union_impeach_union_member_min_5010.lua",
        tool.REMOTE_PATCH_ROOT + "/client_avatar/auto_client_avatar_patch_union_impeach_union_member_min_5010.lua",
    ]


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
        tool.apply_patch(
            "5010", "capital_city_extra_quota", "1",
            custom_table="union_config_setting", patch_type="../../etc",
        )


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


def test_patch_type_is_no_longer_derived_from_embedded_sides():
    """双端推导已随内置注册表移除：类型只能由调用方显式给出，默认 common."""
    tool = AutoHotfixTool(dry_run=True)
    tool.resolve_server_ip = lambda server_id: "10.0.0.1"
    tool.detect_server_scenario = lambda host: 2001
    uploaded = []
    tool.run_remote_cmd = lambda host, cmd: (0, "")
    tool.scp_to_remote = lambda host, local_file, remote_file: (uploaded.append(remote_file), (0, ""))[1]

    # 不给 --patch-type：只写默认的 common
    assert tool.apply_patch(
        "5010", "union_impeach_union_member_min", "1", custom_table="union_config_setting",
    ) is True
    assert uploaded == [
        tool.REMOTE_PATCH_ROOT + "/common/auto_common_patch_union_impeach_union_member_min_5010.lua",
    ]
    assert DEFAULT_PATCH_TYPE == "common"


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
    assert tool.apply_patch(
        "5010", "union_impeach_union_member_min", "1",
        custom_table="union_config_setting", patch_type="client_avatar",
    ) is True
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


def test_multi_table_patch_writes_every_assignment_into_one_file():
    """同字段多表：必须落在同一个补丁文件里，否则后一次会覆盖前一次的赋值."""
    tool = AutoHotfixTool(dry_run=True)
    lua_code = tool.generate_lua_patch_content(
        table_name=("activity_champion_server_define_config", "activity_champion_server_define_config2"),
        field_name="champion_badge_need_member",
        value="1",
        scenarios=(14,),
    )

    first = "activity_champion_server_define_config[ [=[champion_badge_need_member]=] ].content = [=[1]=]"
    second = "activity_champion_server_define_config2[ [=[champion_badge_need_member]=] ].content = [=[1]=]"
    # 两条赋值都要在，且客户端 / 服务端各一份
    assert lua_code.count(first) == 2
    assert lua_code.count(second) == 2
    # 剧本守卫仍然只有一处声明、两侧各一次
    assert lua_code.count("G_SCENARIO_ID == 14") == 2
    # 头部两表都如实记录，便于事后核对
    assert "-- Target Field : activity_champion_server_define_config.champion_badge_need_member = 1" in lua_code
    assert "-- Target Field : activity_champion_server_define_config2.champion_badge_need_member = 1" in lua_code


def test_multi_table_apply_uploads_one_file_per_patch_type():
    """两次 --table 只应产生一个文件，不能按表各生成一个同名文件."""
    tool = AutoHotfixTool(dry_run=True)
    tool.resolve_server_ip = lambda server_id: "10.0.0.1"
    tool.detect_server_scenario = lambda host: 14

    written = {}
    tool.run_remote_cmd = lambda host, cmd: (0, "")
    tool.scp_to_remote = lambda host, local_file, remote_file: (0, "")

    original_open = open

    def capture_local_write(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if isinstance(path, str) and path.endswith(".lua"):
            written[path] = True
            original_write = handle.write

            def spy(content):
                written[path] = content
                return original_write(content)

            handle.write = spy
        return handle

    import builtins

    builtins.open = capture_local_write
    try:
        success = tool.apply_patch(
            server_id="2016",
            key_or_field="champion_badge_need_member",
            value="1",
            custom_table=("activity_champion_server_define_config", "activity_champion_server_define_config2"),
            patch_type="common",
        )
    finally:
        builtins.open = original_open

    assert success is True
    lua_files = [path for path in written if path.endswith(".lua")]
    assert len(lua_files) == 1
    content = written[lua_files[0]]
    assert "activity_champion_server_define_config2[ [=[champion_badge_need_member]=] ].content = [=[1]=]" in content
    assert "activity_champion_server_define_config[ [=[champion_badge_need_member]=] ].content = [=[1]=]" in content


def test_normalize_tables_rejects_injected_table_names():
    """表名会拼进 Lua，必须只接受裸标识符."""
    tool = AutoHotfixTool(dry_run=True)
    for bad in ("a]; os.execute('x')", "1bad", "", "tab-ble"):
        with pytest.raises(ValueError):
            tool.normalize_tables(bad)
    # 顺序保留、去重
    assert tool.normalize_tables(["t2", "t1", "t2"]) == ("t2", "t1")
