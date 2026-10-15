#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_hotfix_tool.py - 游戏服阈值配置一键热更工具.

通过动态生成 Lua Patch、挂载 patch_order_list.json 并触发 run_patch_game.sh，
实现对游戏测试服各种人数、名额、开启条件等阈值的秒级免重启热更与一键回滚。
兼容 Python 3.6+ / 3.7+ 运行环境。
"""

import argparse
import configparser
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("auto_hotfix")


class ThresholdDefinition(object):
    """阈值配置项元数据定义."""

    def __init__(
        self,
        key,
        name,
        table_name,
        field_name,
        default_scenarios=(),
        description="",
        default_patch_type="common",
        is_client=True,
        is_server=True,
    ):
        self.key = key
        self.name = name
        self.table_name = table_name
        self.field_name = field_name
        self.default_scenarios = tuple(default_scenarios)
        self.description = description
        self.default_patch_type = default_patch_type
        self.is_client = is_client
        self.is_server = is_server


# 仅允许目标服已有的、经人工确认的 Patch 类型；不可把用户输入直接拼入远端路径。
# common 类型可承载 IS_CLIENT 与 IS_SERVER 双端逻辑，是阈值热更的默认类型。
SUPPORTED_PATCH_TYPES = frozenset((
    "avatar",
    "battle",
    "center",
    "client_account",
    "client_avatar",
    "common",
    "login",
    "scene",
    "test",
    "union",
    "unionmirror",
))
DEFAULT_PATCH_TYPE = "common"


# 从 Excel《游戏服改配置.xlsx》提炼出的已知高频配置映射表
KNOWN_THRESHOLDS = {
    # 1. 同盟与弹劾
    "union_impeach_min": ThresholdDefinition(
        key="union_impeach_min",
        name="弹劾发起同盟人数检查",
        table_name="union_config_setting",
        field_name="union_impeach_union_member_min",
        description="发起弹劾所需的同盟最低人数（原值通常为50）",
    ),
    "union_impeach_power_min": ThresholdDefinition(
        key="union_impeach_power_min",
        name="弹劾发起者繁荣度检查",
        table_name="union_config_setting",
        field_name="union_impeach_avatar_power_min",
        description="发起弹劾所需的玩家最低繁荣度（原值20000）",
    ),
    "union_impeach_power_rank": ThresholdDefinition(
        key="union_impeach_power_rank",
        name="弹劾发起者同盟繁荣度排名要求",
        table_name="union_config_setting",
        field_name="union_impeach_avatar_power_rank",
        description="发起弹劾所需的同盟繁荣度排名（原值前20）",
    ),
    "union_rage_min_member": ThresholdDefinition(
        key="union_rage_min_member",
        name="同盟精锐AI大营人数要求",
        table_name="union_config_setting",
        field_name="union_rage_min_member_number",
        description="同盟精锐AI大营开启所需人数（S7通常为180）",
    ),
    "union_rage_min_level": ThresholdDefinition(
        key="union_rage_min_level",
        name="同盟精锐AI大营等级",
        table_name="union_config_setting",
        field_name="union_rage_min_level",
        description="开启同盟精锐AI大营等级限制（原值20）",
    ),
    "union_token_least_member": ThresholdDefinition(
        key="union_token_least_member",
        name="同盟令获取同盟人数阈值",
        table_name="game_config_common_config",
        field_name="union_token_least_member_num",
        description="获得同盟令所需的同盟人数（原值20）",
    ),
    "union_max_member_init": ThresholdDefinition(
        key="union_max_member_init",
        name="同盟初始人数上限",
        table_name="union_config_setting",
        field_name="init_maxMemberNum",
        description="同盟初始最大人数（原值220）",
    ),
    "union_max_member_limit": ThresholdDefinition(
        key="union_max_member_limit",
        name="同盟人数最大上限",
        table_name="union_config_setting",
        field_name="upperLimit_maxMemberNum",
        description="同盟等级提升后的绝对人数上限（原值220）",
    ),
    "union_merge_min_member": ThresholdDefinition(
        key="union_merge_min_member",
        name="合盟人数限制",
        table_name="game_config_common_config",
        field_name="union_merge_list_min_member_num",
        description="进行合盟时所需的同盟最低人数",
    ),

    # 2. 势力、阵营与剧本
    "faction_building_member_limit": ThresholdDefinition(
        key="faction_building_member_limit",
        name="S7烽火台和黄天纸马开启人数限制",
        table_name="game_config_common_config",
        field_name="faction_building_union_membernum_limit",
        description="S7阵营专属建筑烽火台和黄天纸马开启人数（原值180，改1方便测试）",
    ),
    "faction_building_palace_level": ThresholdDefinition(
        key="faction_building_palace_level",
        name="S7专属建筑宫殿等级限制",
        table_name="game_config_common_config",
        field_name="faction_building_union_palace_level_limit",
        description="专属建筑开启的同盟宫殿等级（原值20，改1方便测试）",
    ),
    "one_city_max_main_city_num": ThresholdDefinition(
        key="one_city_max_main_city_num",
        name="黄巾县域落城上限",
        table_name="game_config_common_config",
        field_name="one_city_max_single_block_main_city_num",
        description="单个县域可容纳黄巾主城上限（原值100）",
    ),
    "capital_city_extra_quota": ThresholdDefinition(
        key="capital_city_extra_quota",
        name="国都/州府额外霸业名额",
        table_name="season_process_season_config",
        field_name="capital_city_extra_quota",
        description="占领州府/国都额外奖励的霸业名额",
    ),
    "rank_quota": ThresholdDefinition(
        key="rank_quota",
        name="霸业名额服务端字段",
        table_name="season_process_season_config",
        field_name="rank_quota",
        description="服务端霸业名额计算阈值",
    ),
    "team_mini_choose_region_group": ThresholdDefinition(
        key="team_mini_choose_region_group",
        name="战队选边人数",
        table_name="season_process_season_config",
        field_name="team_mini_member_num_for_choose_region_group_show",
        description="系统自动分配势力的战队人数限制（如S6为10）",
    ),
    "auto_join_team_min_limit": ThresholdDefinition(
        key="auto_join_team_min_limit",
        name="散人匹配战队人数阈值",
        table_name="season_process_season_config",
        field_name="auto_join_team_team_member_min_limit",
        description="散人玩家自动加入战队的最小人数要求",
    ),
    "faction_team_show_member_limit": ThresholdDefinition(
        key="faction_team_show_member_limit",
        name="阵营预选战队人数显示阈值",
        table_name="season_process_season_config",
        field_name="faction_team_show_member_num_limit",
        description="预选州阶段可显示的战队最低人数（原值50）",
    ),
    "team_reward_member_count": ThresholdDefinition(
        key="team_reward_member_count",
        name="跨赛季组队成团人数阈值",
        table_name="season_process_season_config",
        field_name="team_reward_member_count",
        description="达成跨赛季组队成团奖励的人数条件",
    ),
    "final_default_open_scenario_limit": ThresholdDefinition(
        key="final_default_open_scenario_limit",
        name="自选剧本人数最低限制",
        table_name="season_process_season_config",
        field_name="final_default_open_scenario_num_limit",
        description="剧本报名满多少人才能正常开局",
    ),

    # 3. 回收与生态控制
    "week_suppress_camp_success_limit": ThresholdDefinition(
        key="week_suppress_camp_success_limit",
        name="俸禄发放人数阈值",
        table_name="game_config_common_config",
        field_name="week_suppress_camp_success_member_num_limit",
        description="阵营平乱成功发放周俸所需的达标人数",
    ),
    "soft_recycle_player_limit": ThresholdDefinition(
        key="soft_recycle_player_limit",
        name="软回收最低落堡人数要求",
        table_name="game_config_common_config",
        field_name="soft_recycle_real_player_num_limit",
        description="服务器启动软回收机制的真人数下限",
    ),
    "hard_recycle_player_limit": ThresholdDefinition(
        key="hard_recycle_player_limit",
        name="硬回收启动注册人数",
        table_name="game_config_common_config",
        field_name="hard_recycle_register_player_num_limit",
        description="服务器触发硬回收所需的总注册人数要求",
    ),
    "found_nation_reward_num_limit": ThresholdDefinition(
        key="found_nation_reward_num_limit",
        name="虎视中原立国活动人数",
        table_name="game_config_common_config",
        field_name="found_nation_reward_num_limit",
        description="虎视中原立国奖励所需人数",
    ),
}

# 增加字段名直通匹配（如果用户直接传 field_name 也能精准命中）
FIELD_NAME_TO_DEFINITION = {
    item.field_name: item for item in KNOWN_THRESHOLDS.values()
}


class AutoHotfixTool(object):
    """自动生成 Patch、安装到目标服、挂载清单并触发热更的完整控制器."""

    REMOTE_HOTFIX_ROOT = "/data/app/game-server/server_common/hotfix"
    REMOTE_PATCH_ROOT = "/data/app/game-server/server_common/hotfix/patch"
    REMOTE_PATCH_LIST = "/data/app/game-server/server_common/hotfix/patch_order_list.json"
    REMOTE_RUN_PATCH_CMD = "cd /data/app/game-server && bash release_bin/run_patch_game.sh"

    def __init__(self, ini_file="config.ini", dry_run=False):
        self.ini_file = ini_file
        self.dry_run = dry_run

    def resolve_server_ip(self, server_id):
        """从 config.ini 解析目标服务器 IP."""
        if not os.path.exists(self.ini_file):
            raise FileNotFoundError(f"配置文件不存在: {self.ini_file}")
        parser = configparser.ConfigParser()
        parser.optionxform = str
        parser.read(self.ini_file, encoding="utf-8")
        target_key = f"{server_id}_game"
        if not parser.has_section("serverName"):
            raise ValueError("config.ini 中缺少 [serverName] 配置段")
        try:
            ip = parser.get("serverName", target_key).strip()
            if not ip:
                raise ValueError(f"{target_key} 对应 IP 为空")
            return ip
        except configparser.NoOptionError as err:
            raise ValueError(f"未在 config.ini 找到 {target_key} 的 IP 配置") from err

    def run_remote_cmd(self, host, cmd):
        """通过 SSH 在远端执行命令并返回输出."""
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"root@{host}", cmd]
        if self.dry_run:
            logger.info("[DryRun SSH] %s", " ".join(ssh_cmd))
            return 0, "[DryRun Output]"
        try:
            res = subprocess.run(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=60,
            )
            output = res.stdout.decode("utf-8", "replace").strip() if res.stdout else ""
            return res.returncode, output
        except subprocess.TimeoutExpired:
            return -1, "SSH 执行超时（60s）"

    def scp_to_remote(self, host, local_file, remote_file):
        """通过 SCP 传输文件到远程服务器."""
        scp_cmd = ["scp", "-o", "StrictHostKeyChecking=no", local_file, f"root@{host}:{remote_file}"]
        if self.dry_run:
            logger.info("[DryRun SCP] %s", " ".join(scp_cmd))
            return 0, "[DryRun SCP]"
        try:
            res = subprocess.run(
                scp_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=30,
            )
            output = res.stdout.decode("utf-8", "replace").strip() if res.stdout else ""
            return res.returncode, output
        except subprocess.TimeoutExpired:
            return -1, "SCP 传输超时（30s）"

    def detect_server_scenario(self, host):
        """从目标服 /data/app/etc/nslg_game.lua 检测当前剧本编号."""
        cmd = "awk -F'=' '/NSLG_SCENARIO/ {gsub(/[^0-9]/, \"\", $2); print $2}' /data/app/etc/nslg_game.lua"
        code, out = self.run_remote_cmd(host, cmd)
        if code == 0 and out.isdigit():
            scenario = int(out)
            logger.info("目标服务器当前剧本编号探测成功: G_SCENARIO_ID = %d", scenario)
            return scenario
        logger.warning("未能自动解析出当前剧本，将默认通配全部剧本 (output: %s)", out)
        return 0

    def generate_lua_patch_content(
        self,
        table_name,
        field_name,
        value,
        scenarios=(),
    ):
        """生成安全严密的双端 Lua Patch 脚本文本."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scenario_array_str = "{" + ", ".join(str(s) for s in scenarios) + "}" if scenarios else "{}"

        return f"""-- ======================================================================
-- [Auto-Generated Hotfix Patch]
-- Target Field : {table_name}.{field_name} = {value}
-- Generated At : {now_str}
-- ======================================================================

local TARGET_SCENARIOS = {scenario_array_str}

local function is_target_scenario(scenario_id)
    if not TARGET_SCENARIOS or #TARGET_SCENARIOS == 0 then
        return true
    end
    for _, sid in ipairs(TARGET_SCENARIOS) do
        if sid == scenario_id then return true end
    end
    return false
end

-- 客户端热更逻辑
if IS_CLIENT then
    if is_target_scenario(G_SCENARIO_ID) then
        if {table_name} and {table_name}[ [=[{field_name}]=] ] then
            {table_name}[ [=[{field_name}]=] ].content = [=[{value}]=]
        end
    end
end

-- 服务端热更逻辑
if IS_SERVER then
    local ok, holder = pcall(require, "server_helper.serverInfoHolder")
    local cur_scenario = ok and holder and holder:GetServerScenario() or 0
    if is_target_scenario(cur_scenario) then
        if {table_name} and {table_name}[ [=[{field_name}]=] ] then
            {table_name}[ [=[{field_name}]=] ].content = [=[{value}]=]
        end
    end
end
"""

    def apply_patch(
        self,
        server_id,
        key_or_field,
        value,
        scenario=None,
        custom_table=None,
        patch_type=None,
    ):
        """主入口：生成补丁 -> 上传 -> 挂载 -> 热更验证."""
        # 1. 查找定义
        definition = KNOWN_THRESHOLDS.get(key_or_field) or FIELD_NAME_TO_DEFINITION.get(key_or_field)
        if definition:
            table_name = custom_table or definition.table_name
            field_name = definition.field_name
            desc = definition.name
        else:
            if not custom_table:
                raise ValueError(
                    f"未知的配置项 '{key_or_field}'。如果是自定义字段，请同时指定 --table 参数（如 --table game_config_common_config）"
                )
            table_name = custom_table
            field_name = key_or_field
            desc = f"自定义配置 {table_name}.{field_name}"

        # 2. 确定受白名单约束的 Patch 类型。阈值配置默认使用支持双端逻辑的 common。
        selected_patch_type = patch_type or (
            definition.default_patch_type if definition else DEFAULT_PATCH_TYPE
        )
        if selected_patch_type not in SUPPORTED_PATCH_TYPES:
            raise ValueError(
                "不支持的补丁类型 '{}'; 仅允许: {}".format(
                    selected_patch_type, ", ".join(sorted(SUPPORTED_PATCH_TYPES))
                )
            )

        # 3. 解析服务器 IP
        host = self.resolve_server_ip(server_id)
        logger.info(
            "准备对服务器 %s (%s) 打 %s 类型补丁: %s = %s",
            server_id,
            host,
            selected_patch_type,
            field_name,
            value,
        )

        # 4. 确定剧本限制
        scenarios = ()
        if scenario is not None:
            scenarios = (scenario,)
        elif definition and definition.default_scenarios:
            scenarios = definition.default_scenarios
        else:
            detected = self.detect_server_scenario(host)
            if detected > 0:
                scenarios = (detected,)

        # 5. 同服同字段同类型复用已有 Patch；仅首次创建时使用固定文件名。
        #    先读取清单再上传，避免把新文件写进远端目录后才发现已有同类 Patch。
        patch_file_name = self._select_patch_file_name(
            host, server_id, field_name, selected_patch_type
        )
        if not patch_file_name:
            return False
        local_temp_patch = f"/tmp/{patch_file_name}" if os.name != "nt" else os.path.join(os.environ.get("TEMP", "."), patch_file_name)

        patch_content = self.generate_lua_patch_content(
            table_name=table_name,
            field_name=field_name,
            value=value,
            scenarios=scenarios,
        )

        with open(local_temp_patch, "w", encoding="utf-8") as f:
            f.write(patch_content)
        logger.info("本地已生成 Lua 补丁代码: %s", local_temp_patch)

        # 6. 上传 Patch 到目标类型的远端热更目录
        remote_patch_dir = "{}/{}".format(self.REMOTE_PATCH_ROOT, selected_patch_type)
        remote_patch_file_path = f"{remote_patch_dir}/{patch_file_name}"
        logger.info("上传补丁文件到目标服: %s", remote_patch_file_path)
        # 确保目标类型目录存在
        self.run_remote_cmd(host, f"mkdir -p {remote_patch_dir}")
        code, out = self.scp_to_remote(host, local_temp_patch, remote_patch_file_path)
        if code != 0:
            logger.error("上传补丁文件失败: %s", out)
            return False

        # 7. 更新远程 patch_order_list.json
        logger.info("更新远端热更清单: %s", self.REMOTE_PATCH_LIST)
        mount_success = self._mount_patch_to_remote_json(
            host, patch_file_name, selected_patch_type
        )
        if not mount_success:
            return False

        # 8. 触发热更执行
        logger.info("正在目标服触发热更 (run_patch_game.sh)...")
        code, out = self.run_remote_cmd(host, self.REMOTE_RUN_PATCH_CMD)
        logger.info("热更执行结果返回 (Exit Code: %d):\n%s", code, out)

        if code == 0:
            logger.info("✅ 热更成功生效！配置 [%s] 已覆盖为 [%s]", desc, value)
            return True
        else:
            logger.error("❌ 远端热更执行异常，请检查上述日志！")
            return False

    def rollback_patch(self, server_id, patch_target):
        """兼容旧 CLI：字段名回滚改为完整还原；文件名仍仅卸载一个清单项。"""
        if not patch_target.endswith(".lua"):
            return self.restore_auto_patches(server_id, patch_target)

        host = self.resolve_server_ip(server_id)
        logger.info("准备从服务器 %s 的清单卸载补丁: %s", server_id, patch_target)
        if not self._unmount_patch_from_remote_json(host, patch_target):
            logger.warning("未能在清单中找到该补丁或卸载失败")
            return False
        code, out = self.run_remote_cmd(host, self.REMOTE_RUN_PATCH_CMD)
        logger.info("热更执行返回 (Exit Code: %d):\n%s", code, out)
        return code == 0

    def _resolve_auto_patch_field(self, key_or_field):
        """将别名或字段名转为受控的真实字段名与展示名称。"""
        definition = KNOWN_THRESHOLDS.get(key_or_field) or FIELD_NAME_TO_DEFINITION.get(key_or_field)
        if definition:
            return definition.field_name, definition.name
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", key_or_field):
            raise ValueError("配置字段只能包含字母、数字或下划线")
        return key_or_field, key_or_field

    def _get_auto_patch_matches(self, host, server_id, field_name, patch_type=None):
        """读取指定服和字段的自动 Patch；只依据清单，不扫描任意远端路径。"""
        data = self._read_remote_patch_data(host)
        if data is None:
            return None

        legacy_client_pattern = re.compile(
            r"^auto_patch_{}_{}(?:_\d{{8}}_\d{{6}})?\.lua$".format(
                re.escape(str(field_name)), re.escape(str(server_id))
            )
        )
        matches = []
        for key, item in data["list"].items():
            if not isinstance(item, dict):
                continue
            current_type = item.get("patch_type")
            file_name = item.get("file")
            if patch_type is not None and current_type != patch_type:
                continue
            if current_type not in SUPPORTED_PATCH_TYPES or not isinstance(file_name, str):
                continue
            typed_pattern = re.compile(
                r"^auto_{}_patch_{}_{}(?:_\d{{8}}_\d{{6}})?\.lua$".format(
                    re.escape(str(current_type)),
                    re.escape(str(field_name)),
                    re.escape(str(server_id)),
                )
            )
            is_legacy_client_patch = (
                current_type == "client_avatar" and legacy_client_pattern.match(file_name)
            )
            if typed_pattern.match(file_name) or is_legacy_client_patch:
                matches.append(
                    {
                        "key": str(key),
                        "patch_type": current_type,
                        "order": self._patch_order(item),
                        "file": file_name,
                    }
                )
        return sorted(matches, key=lambda item: (item["patch_type"], item["order"], item["file"]))

    def _remove_auto_patch_entries(self, host, matches):
        """从清单移除明确列出的条目；保留其他 Patch 的 ID 和 order 不变。"""
        if self.dry_run:
            logger.info("[DryRun] 模拟从清单移除 %d 个自动 Patch 条目", len(matches))
            return True
        data = self._read_remote_patch_data(host)
        if data is None:
            return False
        patch_list = data["list"]
        expected = {
            (item["key"], item["patch_type"], item["file"])
            for item in matches
        }
        actual = set()
        for key, item in patch_list.items():
            if not isinstance(item, dict):
                continue
            marker = (str(key), item.get("patch_type"), item.get("file"))
            if marker in expected:
                actual.add(marker)

        if actual != expected:
            logger.error("远端清单在清理确认后发生变化，已拒绝写入")
            return False
        for key, _, _ in expected:
            del patch_list[key]

        backup_cmd = f"cp {self.REMOTE_PATCH_LIST} {self.REMOTE_PATCH_LIST}.bak_{int(time.time())}"
        code, out = self.run_remote_cmd(host, backup_cmd)
        if code != 0:
            logger.error("备份远端清单失败: %s", out)
            return False

        new_json_str = json.dumps(data, indent=2, ensure_ascii=False)
        write_cmd = f"cat << 'EOF' > {self.REMOTE_PATCH_LIST}\n{new_json_str}\nEOF"
        code, out = self.run_remote_cmd(host, write_cmd)
        if code != 0:
            logger.error("写回远端 patch_order_list.json 失败: %s", out)
            return False
        return True

    def _delete_auto_patch_files(self, host, matches):
        """仅删除与已从清单移除条目对应的白名单类型文件。"""
        for item in matches:
            patch_type = item["patch_type"]
            file_name = item["file"]
            if patch_type not in SUPPORTED_PATCH_TYPES or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", file_name):
                logger.error("拒绝删除不合法的 Patch 路径")
                return False
            remote_file = "{}/{}/{}".format(self.REMOTE_PATCH_ROOT, patch_type, file_name)
            code, out = self.run_remote_cmd(host, "rm -f -- {}".format(remote_file))
            if code != 0:
                logger.error("删除自动 Patch 文件失败: %s", out)
                return False
        return True

    def cleanup_legacy_auto_patches(self, server_id, key_or_field):
        """清理旧类型自动 Patch，保留当前 common Patch；清单和文件都受字段精确匹配约束。"""
        field_name, desc = self._resolve_auto_patch_field(key_or_field)
        host = self.resolve_server_ip(server_id)
        matches = self._get_auto_patch_matches(host, server_id, field_name)
        if matches is None:
            return False
        # 仅清理最早期误挂到 client_avatar 的旧命名，不触碰用户后续显式选择的 battle、scene 等类型。
        legacy_name_pattern = re.compile(
            r"^auto_patch_{}_{}(?:_\d{{8}}_\d{{6}})?\.lua$".format(
                re.escape(str(field_name)), re.escape(str(server_id))
            )
        )
        stale_matches = [
            item
            for item in matches
            if item["patch_type"] == "client_avatar"
            and legacy_name_pattern.match(item["file"])
        ]
        if not stale_matches:
            logger.info("未找到服务器 %s 的配置 [%s] 可清理的旧版 client_avatar 自动 Patch", server_id, desc)
            return True

        logger.info(
            "准备清理服务器 %s 配置 [%s] 的 %d 个旧版 client_avatar 自动 Patch: %s",
            server_id,
            desc,
            len(stale_matches),
            ", ".join(item["file"] for item in stale_matches),
        )
        if not self._remove_auto_patch_entries(host, stale_matches):
            return False
        if not self._delete_auto_patch_files(host, stale_matches):
            logger.error("清单已更新，但旧 Patch 文件删除失败；请根据清单备份人工恢复或补删")
            return False
        code, out = self.run_remote_cmd(host, self.REMOTE_RUN_PATCH_CMD)
        logger.info("清理后热更执行返回 (Exit Code: %d):\n%s", code, out)
        return code == 0

    def restore_auto_patches(self, server_id, key_or_field):
        """完整还原一个字段：卸载并删除该服该字段的所有自动 Patch，再重新热更。"""
        field_name, desc = self._resolve_auto_patch_field(key_or_field)
        host = self.resolve_server_ip(server_id)
        matches = self._get_auto_patch_matches(host, server_id, field_name)
        if matches is None:
            return False
        if not matches:
            logger.info("未找到服务器 %s 的配置 [%s] 自动 Patch，无需还原", server_id, desc)
            return True

        logger.info(
            "准备还原服务器 %s 配置 [%s]：将清理 %d 个自动 Patch: %s",
            server_id,
            desc,
            len(matches),
            ", ".join(item["file"] for item in matches),
        )
        if not self._remove_auto_patch_entries(host, matches):
            return False
        if not self._delete_auto_patch_files(host, matches):
            logger.error("清单已更新，但自动 Patch 文件删除失败；请根据清单备份人工恢复或补删")
            return False
        code, out = self.run_remote_cmd(host, self.REMOTE_RUN_PATCH_CMD)
        logger.info("还原后热更执行返回 (Exit Code: %d):\n%s", code, out)
        return code == 0

    def list_installed_patches(self, server_id):
        """查询目标服务器当前挂载的所有补丁."""
        host = self.resolve_server_ip(server_id)
        code, out = self.run_remote_cmd(host, f"cat {self.REMOTE_PATCH_LIST}")
        if code != 0:
            logger.error("读取远端 patch_order_list.json 失败: %s", out)
            return []
        try:
            data = json.loads(out)
            items = list(data.get("list", {}).values())
            items.sort(key=lambda x: x.get("order", 0))
            return items
        except json.JSONDecodeError:
            logger.error("远端 patch_order_list.json 不是有效的 JSON 格式")
            return []

    def _read_remote_patch_data(self, host):
        """读取并校验远端 Patch 清单；失败时不做任何写入。"""
        code, out = self.run_remote_cmd(host, f"cat {self.REMOTE_PATCH_LIST}")
        if code != 0:
            logger.error("无法读取远端清单: %s", out)
            return None
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            logger.error("远端清单不是合法的 JSON")
            return None
        if not isinstance(data, dict):
            logger.error("远端清单根节点必须是对象")
            return None
        patch_list = data.get("list")
        if not isinstance(patch_list, dict):
            logger.error("远端清单缺少对象类型的 list 字段")
            return None
        return data

    @staticmethod
    def _patch_order(item):
        """将清单 order 转为可排序数值，异常条目排在最后。"""
        try:
            return int(item.get("order", -1))
        except (AttributeError, TypeError, ValueError):
            return -1

    def _select_patch_file_name(self, host, server_id, field_name, patch_type):
        """为同服同字段同类型选择既有 Patch 文件，或返回首次创建的稳定文件名。"""
        stable_name = "auto_{}_patch_{}_{}.lua".format(
            patch_type, field_name, server_id
        )
        if self.dry_run:
            return stable_name

        data = self._read_remote_patch_data(host)
        if data is None:
            return None

        # 兼容旧 client_avatar 的无类型命名；新文件以类型前缀隔离。
        stable_pattern = r"auto_{}_patch_{}_{}".format(
            re.escape(str(patch_type)),
            re.escape(str(field_name)),
            re.escape(str(server_id)),
        )
        old_client_pattern = r"auto_patch_{}_{}".format(
            re.escape(str(field_name)), re.escape(str(server_id))
        )
        accepted_prefixes = [stable_pattern]
        if patch_type == "client_avatar":
            accepted_prefixes.append(old_client_pattern)
        pattern = re.compile(
            r"^(?:{})(?:_\d{{8}}_\d{{6}})?\.lua$".format(
                "|".join(accepted_prefixes)
            )
        )
        matched = []
        for item in data["list"].values():
            if not isinstance(item, dict) or item.get("patch_type") != patch_type:
                continue
            file_name = item.get("file")
            if isinstance(file_name, str) and pattern.match(file_name):
                matched.append((self._patch_order(item), file_name))

        if not matched:
            return stable_name

        # 历史时间戳 Patch 可能已有多个。始终复用该类型中执行顺序最高的一个，
        # 以保证本次覆盖值会在其他历史条目之后生效；不隐式清理旧条目。
        matched.sort(key=lambda entry: (entry[0], entry[1]))
        selected = matched[-1][1]
        if len(matched) > 1:
            logger.warning(
                "检测到服务器 %s 的字段 %s 在 %s 类型中已有 %d 个历史自动 Patch；"
                "本次复用执行顺序最高的 %s，不会自动清理其他条目",
                server_id,
                field_name,
                patch_type,
                len(matched),
                selected,
            )
        else:
            logger.info("复用已挂载的 %s 类型自动 Patch: %s", patch_type, selected)
        return selected

    def _mount_patch_to_remote_json(self, host, patch_file_name, patch_type):
        """幂等挂载指定类型 Patch；相同类型同名条目已存在时不改写清单。"""
        if patch_type not in SUPPORTED_PATCH_TYPES:
            raise ValueError("不支持的补丁类型: {}".format(patch_type))
        if self.dry_run:
            logger.info(
                "[DryRun] 模拟确认 %s 类型补丁 %s 已挂载到 %s",
                patch_type,
                patch_file_name,
                self.REMOTE_PATCH_LIST,
            )
            return True

        data = self._read_remote_patch_data(host)
        if data is None:
            return False
        patch_list = data["list"]

        for item in patch_list.values():
            if (
                isinstance(item, dict)
                and item.get("patch_type") == patch_type
                and item.get("file") == patch_file_name
            ):
                logger.info("补丁已在远端清单中挂载，不新增条目: %s", patch_file_name)
                return True

        # 清单 ID 全局递增，但每个 patch_type 的执行顺序独立递增。
        max_order = -1
        max_key = -1
        for key, item in patch_list.items():
            if str(key).isdigit():
                max_key = max(max_key, int(key))
            if isinstance(item, dict) and item.get("patch_type") == patch_type:
                max_order = max(max_order, self._patch_order(item))

        next_key = str(max_key + 1)
        next_order = max_order + 1
        patch_list[next_key] = {
            "patch_type": patch_type,
            "order": next_order,
            "file": patch_file_name,
        }

        # 仅首次挂载才需要备份并改写清单。
        backup_cmd = f"cp {self.REMOTE_PATCH_LIST} {self.REMOTE_PATCH_LIST}.bak_{int(time.time())}"
        self.run_remote_cmd(host, backup_cmd)

        new_json_str = json.dumps(data, indent=2, ensure_ascii=False)
        write_cmd = f"cat << 'EOF' > {self.REMOTE_PATCH_LIST}\n{new_json_str}\nEOF"
        code, out = self.run_remote_cmd(host, write_cmd)
        if code != 0:
            logger.error("写回远端 patch_order_list.json 失败: %s", out)
            return False
        return True

    def _unmount_patch_from_remote_json(self, host, patch_file_name):
        """从远端清单卸载指定文件，并按类型独立保持原有 order 序列。"""
        if self.dry_run:
            logger.info("[DryRun] 模拟从 %s 卸载补丁 %s", self.REMOTE_PATCH_LIST, patch_file_name)
            return True

        data = self._read_remote_patch_data(host)
        if data is None:
            return False
        patch_list = data["list"]
        found_key = None
        for key, item in patch_list.items():
            if isinstance(item, dict) and item.get("file") == patch_file_name:
                found_key = key
                break

        if found_key is None:
            logger.warning("在清单中未找到名为 %s 的补丁", patch_file_name)
            return False

        del patch_list[found_key]

        # 清单 ID 全局重排；每种类型的 order 仍从其删除前的最小值开始独立排序。
        orders_by_type = {}
        for item in patch_list.values():
            if not isinstance(item, dict):
                continue
            patch_type = item.get("patch_type")
            orders_by_type.setdefault(patch_type, []).append(self._patch_order(item))
        type_start_order = {
            patch_type: min(orders)
            for patch_type, orders in orders_by_type.items()
        }
        type_order_counter = {}
        new_list = {}
        sorted_items = sorted(
            patch_list.items(),
            key=lambda entry: int(entry[0]) if str(entry[0]).isdigit() else -1,
        )
        for index, (_, item) in enumerate(sorted_items):
            if not isinstance(item, dict):
                logger.error("远端清单含有非对象条目，拒绝重排")
                return False
            patch_type = item.get("patch_type")
            if patch_type not in type_order_counter:
                type_order_counter[patch_type] = type_start_order[patch_type]
            new_item = dict(item)
            new_item["order"] = type_order_counter[patch_type]
            type_order_counter[patch_type] += 1
            new_list[str(index)] = new_item
        data["list"] = new_list

        new_json_str = json.dumps(data, indent=2, ensure_ascii=False)
        write_cmd = f"cat << 'EOF' > {self.REMOTE_PATCH_LIST}\n{new_json_str}\nEOF"
        code, out = self.run_remote_cmd(host, write_cmd)
        return code == 0


def main():
    parser = argparse.ArgumentParser(description="游戏测试服阈值配置一键免重启热更工具")
    parser.add_argument("-s", "--server", required=True, help="目标服务器编号（如 5010）")
    parser.add_argument("-k", "--key", help="配置项别名或真实字段名（如 union_impeach_min）")
    parser.add_argument("-v", "--value", help="要设定的新数值（如 1）")
    parser.add_argument("--scenario", type=int, help="限定剧本编号（可选，默认自动探测当前剧本）")
    parser.add_argument("--table", help="自定义 Lua 全局表名（如修改未收录字段时必填）")
    parser.add_argument(
        "--patch-type",
        choices=sorted(SUPPORTED_PATCH_TYPES),
        help="Patch 类型（默认按配置项使用 common；仅允许已注册类型）",
    )
    parser.add_argument(
        "--cleanup-legacy",
        help="清理指定字段的旧类型自动 Patch，保留当前 common 自动 Patch",
    )
    parser.add_argument(
        "--restore-auto",
        help="还原指定字段：移除该字段所有自动 Patch 并重新热更",
    )
    parser.add_argument("--rollback", help="兼容旧用法：回滚字段或卸载指定补丁文件名")
    parser.add_argument("--list", action="store_true", help="列出目标服务器当前已挂载的全部补丁")
    parser.add_argument("--list-supported", action="store_true", help="查看所有已内置收录的常用阈值别名")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，仅打印将要执行的命令而不实际连接")
    parser.add_argument("--config-ini", default="config.ini", help="服务器 IP 配置文件路径 (默认 ./config.ini)")

    args = parser.parse_args()

    if args.list_supported:
        print("\n=== 已收录的高频热更阈值列表 ===")
        for key, item in sorted(KNOWN_THRESHOLDS.items()):
            print(f"- {key:<30} [{item.name}] 表: {item.table_name}.{item.field_name}")
        return

    tool = AutoHotfixTool(ini_file=args.config_ini, dry_run=args.dry_run)

    if args.list:
        patches = tool.list_installed_patches(args.server)
        print(f"\n=== 服务器 {args.server} 当前已挂载补丁列表 (共 {len(patches)} 个) ===")
        for p in patches:
            print(f"[{p.get('order', 0)}] {p.get('patch_type', '')} : {p.get('file', '')}")
        return

    if args.cleanup_legacy:
        success = tool.cleanup_legacy_auto_patches(args.server, args.cleanup_legacy)
        sys.exit(0 if success else 1)

    if args.restore_auto:
        success = tool.restore_auto_patches(args.server, args.restore_auto)
        sys.exit(0 if success else 1)

    if args.rollback:
        success = tool.rollback_patch(args.server, args.rollback)
        sys.exit(0 if success else 1)

    if not args.key or args.value is None:
        parser.error(
            "打补丁模式必须同时提供 --key 和 --value 参数，或使用 --list / --rollback / --cleanup-legacy / --restore-auto"
        )

    success = tool.apply_patch(
        server_id=args.server,
        key_or_field=args.key,
        value=args.value,
        scenario=args.scenario,
        custom_table=args.table,
        patch_type=args.patch_type,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
