#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_hotfix_tool.py - 游戏服阈值配置一键热更工具.

通过动态生成 Lua Patch、挂载 patch_order_list.json 并触发 run_patch_game.sh，
实现对游戏测试服各种人数、名额、开启条件等阈值的秒级免重启热更与一键回滚。
"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("auto_hotfix")


@dataclass(frozen=True)
class ThresholdDefinition:
    """阈值配置项元数据定义."""

    key: str
    name: str
    table_name: str
    field_name: str
    default_scenarios: tuple[int, ...] = ()
    description: str = ""
    is_client: bool = True
    is_server: bool = True


# 从 Excel《游戏服改配置.xlsx》提炼出的已知高频配置映射表
KNOWN_THRESHOLDS: dict[str, ThresholdDefinition] = {
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
FIELD_NAME_TO_DEFINITION: dict[str, ThresholdDefinition] = {
    item.field_name: item for item in KNOWN_THRESHOLDS.values()
}


class AutoHotfixTool:
    """自动生成 Patch、安装到目标服、挂载清单并触发热更的完整控制器."""

    REMOTE_HOTFIX_ROOT = "/data/app/game-server/server_common/hotfix"
    REMOTE_PATCH_DIR = "/data/app/game-server/server_common/hotfix/patch/client_avatar"
    REMOTE_PATCH_LIST = "/data/app/game-server/server_common/hotfix/patch_order_list.json"
    REMOTE_RUN_PATCH_CMD = "cd /data/app/game-server && bash release_bin/run_patch_game.sh"

    def __init__(self, ini_file: str = "config.ini", dry_run: bool = False):
        self.ini_file = ini_file
        self.dry_run = dry_run

    def resolve_server_ip(self, server_id: str) -> str:
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

    def run_remote_cmd(self, host: str, cmd: str) -> tuple[int, str]:
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
                text=True,
                check=False,
                encoding="utf-8",
                timeout=60,
            )
            return res.returncode, res.stdout.strip()
        except subprocess.TimeoutExpired:
            return -1, "SSH 执行超时（60s）"

    def scp_to_remote(self, host: str, local_file: str, remote_file: str) -> tuple[int, str]:
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
                text=True,
                check=False,
                encoding="utf-8",
                timeout=30,
            )
            return res.returncode, res.stdout.strip()
        except subprocess.TimeoutExpired:
            return -1, "SCP 传输超时（30s）"

    def detect_server_scenario(self, host: str) -> int:
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
        table_name: str,
        field_name: str,
        value: str,
        scenarios: tuple[int, ...] = (),
    ) -> str:
        """生成安全严密的双端 Lua Patch 脚本文本."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scenario_array_str = "{" + ", ".join(str(s) for s in scenarios) + "}" if scenarios else "{}"

        # 针对 table_name[ [=[field]=] ].content = [=[value]=] 的标准热更语法
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
        server_id: str,
        key_or_field: str,
        value: str,
        scenario: int | None = None,
        custom_table: str | None = None,
    ) -> bool:
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

        # 2. 解析服务器 IP
        host = self.resolve_server_ip(server_id)
        logger.info("准备对服务器 %s (%s) 打补丁: %s = %s", server_id, host, field_name, value)

        # 3. 确定剧本限制
        scenarios: tuple[int, ...] = ()
        if scenario is not None:
            scenarios = (scenario,)
        elif definition and definition.default_scenarios:
            scenarios = definition.default_scenarios
        else:
            detected = self.detect_server_scenario(host)
            if detected > 0:
                scenarios = (detected,)

        # 4. 生成本地 Patch 文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        patch_file_name = f"auto_patch_{field_name}_{server_id}_{timestamp}.lua"
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

        # 5. 上传 Patch 到远程热更目录
        remote_patch_file_path = f"{self.REMOTE_PATCH_DIR}/{patch_file_name}"
        logger.info("上传补丁文件到目标服: %s", remote_patch_file_path)
        # 确保目录存在
        self.run_remote_cmd(host, f"mkdir -p {self.REMOTE_PATCH_DIR}")
        code, out = self.scp_to_remote(host, local_temp_patch, remote_patch_file_path)
        if code != 0:
            logger.error("上传补丁文件失败: %s", out)
            return False

        # 6. 更新远程 patch_order_list.json
        logger.info("更新远端热更清单: %s", self.REMOTE_PATCH_LIST)
        mount_success = self._mount_patch_to_remote_json(host, patch_file_name)
        if not mount_success:
            return False

        # 7. 触发热更执行
        logger.info("正在目标服触发热更 (run_patch_game.sh)...")
        code, out = self.run_remote_cmd(host, self.REMOTE_RUN_PATCH_CMD)
        logger.info("热更执行结果返回 (Exit Code: %d):\n%s", code, out)

        if code == 0:
            logger.info("✅ 热更成功生效！配置 [%s] 已覆盖为 [%s]", desc, value)
            logger.info("💡 后续如需还原，请运行: python auto_hotfix_tool.py -s %s --rollback %s", server_id, patch_file_name)
            return True
        else:
            logger.error("❌ 远端热更执行异常，请检查上述日志！")
            return False

    def rollback_patch(self, server_id: str, patch_file_name: str) -> bool:
        """回滚指定补丁：从清单中移除 -> 重新热更."""
        host = self.resolve_server_ip(server_id)
        logger.info("准备回滚服务器 %s 上的补丁: %s", server_id, patch_file_name)

        # 从远端下载清单进行安全移除
        unmount_success = self._unmount_patch_from_remote_json(host, patch_file_name)
        if not unmount_success:
            logger.warning("未能在清单中找到该补丁或卸载失败")
            return False

        # 重新触发一次热更以更新运行期状态
        logger.info("补丁已从清单卸载，正在重新加载热更以复原原始配置...")
        code, out = self.run_remote_cmd(host, self.REMOTE_RUN_PATCH_CMD)
        logger.info("热更执行返回 (Exit Code: %d):\n%s", code, out)

        if code == 0:
            logger.info("✅ 补丁 [%s] 已成功回滚！", patch_file_name)
            return True
        else:
            logger.error("❌ 重新热更执行失败！")
            return False

    def list_installed_patches(self, server_id: str) -> list[dict[str, Any]]:
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

    def _mount_patch_to_remote_json(self, host: str, patch_file_name: str) -> bool:
        """从远端获取 patch_order_list.json，追加补丁后写回."""
        if self.dry_run:
            logger.info("[DryRun] 模拟将补丁 %s 追加到 %s", patch_file_name, self.REMOTE_PATCH_LIST)
            return True

        code, out = self.run_remote_cmd(host, f"cat {self.REMOTE_PATCH_LIST}")
        if code != 0:
            logger.error("无法读取远端清单: %s", out)
            return False

        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            logger.error("远端清单不是合法的 JSON")
            return False

        patch_list = data.setdefault("list", {})
        # 寻找当前最大的 key 和 order
        max_order = 0
        max_key = -1
        for k, v in patch_list.items():
            if str(k).isdigit():
                max_key = max(max_key, int(k))
            if isinstance(v, dict) and "order" in v:
                max_order = max(max_order, int(v["order"]))

        next_key = str(max_key + 1)
        next_order = max_order + 1

        patch_list[next_key] = {
            "patch_type": "client_avatar",
            "order": next_order,
            "file": patch_file_name,
        }

        # 备份远端文件
        backup_cmd = f"cp {self.REMOTE_PATCH_LIST} {self.REMOTE_PATCH_LIST}.bak_{int(time.time())}"
        self.run_remote_cmd(host, backup_cmd)

        # 写回远端
        new_json_str = json.dumps(data, indent=2, ensure_ascii=False)
        # 用 base64 或安全 EOF 管道写入远端，避免特殊字符转义问题
        write_cmd = f"cat << 'EOF' > {self.REMOTE_PATCH_LIST}\n{new_json_str}\nEOF"
        code, out = self.run_remote_cmd(host, write_cmd)
        if code != 0:
            logger.error("写回远端 patch_order_list.json 失败: %s", out)
            return False
        return True

    def _unmount_patch_from_remote_json(self, host: str, patch_file_name: str) -> bool:
        """从远端 patch_order_list.json 中删除指定 patch 并重新排序."""
        if self.dry_run:
            logger.info("[DryRun] 模拟从 %s 卸载补丁 %s", self.REMOTE_PATCH_LIST, patch_file_name)
            return True

        code, out = self.run_remote_cmd(host, f"cat {self.REMOTE_PATCH_LIST}")
        if code != 0:
            return False
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return False

        patch_list = data.get("list", {})
        found_key = None
        for k, v in patch_list.items():
            if isinstance(v, dict) and v.get("file") == patch_file_name:
                found_key = k
                break

        if found_key is None:
            logger.warning("在清单中未找到名为 %s 的补丁", patch_file_name)
            return False

        del patch_list[found_key]

        # 重新对剩余 patch 按原 order 排序重新编号
        sorted_patches = sorted(patch_list.values(), key=lambda x: x.get("order", 0))
        new_list = {}
        for idx, patch_item in enumerate(sorted_patches):
            patch_item["order"] = idx
            new_list[str(idx)] = patch_item

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
    parser.add_argument("--rollback", help="回滚指定补丁文件名（如 auto_patch_xxx.lua）")
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

    if args.rollback:
        success = tool.rollback_patch(args.server, args.rollback)
        sys.exit(0 if success else 1)

    if not args.key or args.value is None:
        parser.error("打补丁模式必须同时提供 --key 和 --value 参数，或使用 --list / --rollback")

    success = tool.apply_patch(
        server_id=args.server,
        key_or_field=args.key,
        value=args.value,
        scenario=args.scenario,
        custom_table=args.table,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
