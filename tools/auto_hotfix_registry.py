# -*- coding: utf-8 -*-
"""当前 ``auto_hotfix_tool`` 内嵌阈值定义的原样迁移注册表。

本模块仅承载既有脚本行为基线：不新增、删除或校正字段语义。
原始 Excel 的新增条目及字段核对不属于本阶段。
"""


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


# 从原 auto_hotfix_tool.py 的内嵌映射原样迁移；本阶段不以 Excel 纠正或扩展。
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
    "faction_swap_team_member_min": ThresholdDefinition(
        key="faction_swap_team_member_min",
        name="战队交换人数",
        table_name="season_process_season_config",
        field_name="faction_swap_team_member_num_min",
        description="战队交换所需的最低成员人数（本地配置副本原值150）",
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
