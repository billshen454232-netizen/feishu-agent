# -*- coding: utf-8 -*-
"""测试服热更阈值的唯一注册表。

每一项同时定义：
- 执行端使用的 ASCII ``key`` 与 Lua Patch 目标；
- Planner 可见的中文标准名与受控别名。

Planner 不在运行时导入本模块。部署前由
``tools/sync_hotfix_planner_catalog.py`` 将本模块生成的中文路由和严格
key 白名单写入静态调用资料，确保 Gateway / Worker 只依赖已部署的资料。
"""

import re


class ThresholdDefinition(object):
    """一个可热更阈值的执行与中文路由元数据。"""

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
        aliases=(),
    ):
        if isinstance(aliases, str):
            raise ValueError("aliases 必须是字符串序列，不能是单个字符串")
        self.key = key
        self.name = name
        self.table_name = table_name
        self.field_name = field_name
        self.default_scenarios = tuple(default_scenarios)
        self.description = description
        self.default_patch_type = default_patch_type
        self.is_client = is_client
        self.is_server = is_server
        self.aliases = tuple(aliases)


# 从原 auto_hotfix_tool.py 的内嵌映射迁入；执行端的 key、table、field 与
# Patch 语义均以本表为准。中文 aliases 仅负责受控意图路由，不参与 Lua 写入。
KNOWN_THRESHOLDS = {
    # 1. 同盟与弹劾
    "union_impeach_min": ThresholdDefinition(
        key="union_impeach_min",
        name="弹劾发起同盟人数检查",
        table_name="union_config_setting",
        field_name="union_impeach_union_member_min",
        description="发起弹劾所需的同盟最低人数（原值通常为50）",
        aliases=("弹劾人数", "弹劾发起同盟人数"),
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
        aliases=("同盟令人数",),
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
        aliases=("S7专属建筑开启人数", "S7 专属建筑开启人数"),
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
        aliases=("霸业名额", "国都霸业名额", "州府霸业名额"),
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
        aliases=("剧本预选州战队人数显示阈值", "预选州战队人数显示阈值"),
    ),
    "faction_swap_team_member_min": ThresholdDefinition(
        key="faction_swap_team_member_min",
        name="战队交换人数",
        table_name="season_process_season_config",
        field_name="faction_swap_team_member_num_min",
        description="战队交换所需的最低成员人数（本地配置副本原值150）",
        aliases=("战队交换人数阈值",),
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

# 允许用户或已有脚本直接传真实 Lua field_name；中文名只在 Planner 侧解析，
# 不进入执行脚本。
FIELD_NAME_TO_DEFINITION = {
    item.field_name: item for item in KNOWN_THRESHOLDS.values()
}


def planner_term_to_key(thresholds=None):
    """返回中文标准名 / 别名到 ASCII key 的闭合映射。

    任一名称、别名或 key 重复都会立即报错，避免 Planner 将同一句中文路由到
    不确定的 Lua 字段。
    """
    if thresholds is None:
        thresholds = KNOWN_THRESHOLDS

    terms = {}
    seen_keys = set()
    for registered_key, definition in thresholds.items():
        if definition.key != registered_key:
            raise ValueError(
                "注册表 key 与定义不一致：{} != {}".format(registered_key, definition.key)
            )
        if definition.key in seen_keys:
            raise ValueError("热更 key 重复：{}".format(definition.key))
        seen_keys.add(definition.key)
        if re.fullmatch(r"[A-Za-z0-9_]{1,64}", definition.key) is None:
            raise ValueError("热更 key 不合法：{}".format(definition.key))

        for term in (definition.name,) + definition.aliases:
            if not isinstance(term, str) or not term or term != term.strip():
                raise ValueError("中文标准名或别名不能为空且不能含首尾空白：{}".format(definition.key))
            previous_key = terms.get(term)
            if previous_key is not None:
                raise ValueError(
                    "中文标准名或别名重复：{} 同时映射到 {} 和 {}".format(
                        term,
                        previous_key,
                        definition.key,
                    )
                )
            terms[term] = definition.key
    return terms


def hotfix_key_pattern(thresholds=None):
    """返回适用于调用合同的严格 ASCII key 正则。"""
    if thresholds is None:
        thresholds = KNOWN_THRESHOLDS
    planner_term_to_key(thresholds)
    return "(?:{})".format("|".join(re.escape(key) for key in sorted(thresholds)))


def render_planner_catalog_section(thresholds=None):
    """渲染给 Planner 读取的中文热更能力表，不泄露 Lua 内部结构。"""
    if thresholds is None:
        thresholds = KNOWN_THRESHOLDS
    planner_term_to_key(thresholds)

    lines = [
        "#### 已登记热更阈值中文路由（自动生成）",
        "",
        "> 唯一来源：`tools/auto_hotfix_registry.py`。仅本表中的中文标准名和别名可以映射到对应 `-k`；不得按相近词义创建别名。",
        "",
    ]
    for definition in thresholds.values():
        aliases = ""
        if definition.aliases:
            aliases = "；别名：{}".format("、".join("`{}`".format(item) for item in definition.aliases))
        lines.append(
            "- 中文标准名：`{}`{}；执行：`-k {}`；默认 Patch 类型：`{}`".format(
                definition.name,
                aliases,
                definition.key,
                definition.default_patch_type,
            )
        )
    return "\n".join(lines)


# 模块加载即校验，阻止带歧义的中文路由进入构建或部署流程。
PLANNER_TERM_TO_KEY = planner_term_to_key()
