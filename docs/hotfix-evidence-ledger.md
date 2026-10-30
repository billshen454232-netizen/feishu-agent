# 热更阈值证据台账

> **维护状态：进行中（STEP 06）**  
> **最后核对：2026-10-29**  
> **用途：** 逐条记录每个热更阈值的需求来源、受控 key、Lua 目标、Patch 范围与三层证据状态。
> **边界：** 证据分三层，任一层不能替代另一层——需求来源 / 机器人调用许可 / 真实环境结果。

## 状态用词

| 状态 | 含义 |
| --- | --- |
| `已核对` | 需求来源、key、Lua 目标已对照确认；不代表已执行。 |
| `脚本层已验证` | 有可信回传表明该次脚本层有终态；不代表游戏内生效。 |
| `游戏内已验证` | 已由实际观察确认目标效果。 |
| `待确认` | 缺少决定性一手信息，禁止猜测。 |

## 台账

| # | key | 中文名 | 需求来源 | Lua 目标 | 侧别 | Patch 类型 | 脚本层证据 | 游戏内证据 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `union_impeach_min` | 弹劾发起同盟人数检查 | Excel 第 12 行：弹劾发起同盟人数检查（50） | `union_config_setting.union_impeach_union_member_min` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 2 | `union_impeach_power_min` | 弹劾发起者繁荣度检查 | Excel 第 13 行：弹劾发起者繁荣度检查（20000） | `union_config_setting.union_impeach_avatar_power_min` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 3 | `union_impeach_power_rank` | 弹劾发起者同盟繁荣度排名要求 | Excel 第 14 行：弹劾发起者在同盟繁荣度排名要求（20） | `union_config_setting.union_impeach_avatar_power_rank` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 4 | `union_rage_min_member` | 同盟精锐AI大营人数要求 | Excel 第 15 行：同盟精锐AI大营人数（怒气/战意值阈值）（S7/180） | `union_config_setting.union_rage_min_member_number` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 5 | `union_rage_min_level` | 同盟精锐AI大营等级 | Excel 第 16 行：同盟精锐AI大营等级（20） | `union_config_setting.union_rage_min_level` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 6 | `union_token_least_member` | 同盟令获取同盟人数阈值 | Excel 第 7 行：同盟令获取同盟人数阈值 | `game_config_common_config.union_token_least_member_num` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 7 | `union_max_member_init` | 同盟初始人数上限 | Excel 第 23 行：同盟人数最大上限（剧本1的和其他的）（220） | `union_config_setting.init_maxMemberNum` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 8 | `union_max_member_limit` | 同盟人数最大上限 | Excel 第 24 行（220） | `union_config_setting.upperLimit_maxMemberNum` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 9 | `union_merge_min_member` | 合盟人数限制 | Excel 第 55 行：合盟人数限制（原数值1） | `game_config_common_config.union_merge_list_min_member_num` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 10 | `faction_building_member_limit` | S7烽火台和黄天纸马开启人数限制 | Excel 第 5 行：S7汉室和黄巾两个势力的阵营专属建筑烽火台和黄天纸马，需要180人/20人才（180） | `game_config_common_config.faction_building_union_membernum_limit` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 11 | `faction_building_palace_level` | S7专属建筑宫殿等级限制 | Excel 第 6 行（20） | `game_config_common_config.faction_building_union_palace_level_limit` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 12 | `one_city_max_main_city_num` | 黄巾县域落城上限 | Excel 第 9 行：黄巾县域落城上限（S7/100） | `game_config_common_config.one_city_max_single_block_main_city_num` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 13 | `capital_city_extra_quota` | 国都/州府额外霸业名额 | Excel 第 3 行：霸业名额（客户端） | `season_process_season_config.capital_city_extra_quota` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 14 | `rank_quota` | 霸业名额服务端字段 | Excel 第 4 行（服务端） | `season_process_season_config.rank_quota` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 15 | `team_mini_choose_region_group` | 战队选边人数 | Excel 第 2 行：战队选边人数（系统自动分配势力）（S6/10） | `season_process_season_config.team_mini_member_num_for_choose_region_group_show` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 16 | `auto_join_team_min_limit` | 散人匹配战队人数阈值 | Excel 第 8 行：散人匹配战队人数阈值 | `season_process_season_config.auto_join_team_team_member_min_limit` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 17 | `faction_team_show_member_limit` | 阵营预选战队人数显示阈值 | Excel 第 17 行：阵营预选战队人数显示阈值（预选州）【黄巾汉室阵营-曹魏-蜀吴】（50） | `season_process_season_config.faction_team_show_member_num_limit` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 18 | `faction_swap_team_member_min` | 战队交换人数 | Excel 第 39 行：战队交换人数 | `season_process_season_config.faction_swap_team_member_num_min` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 19 | `team_reward_member_count` | 跨赛季组队成团人数阈值 | Excel 第 43 行：跨赛季组队成团人数阈值 | `season_process_season_config.team_reward_member_count` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 20 | `final_default_open_scenario_limit` | 自选剧本人数最低限制 | Excel 第 19 行：自选剧本人数最低限制（改目标剧本） | `season_process_season_config.final_default_open_scenario_num_limit` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 21 | `week_suppress_camp_success_limit` | 俸禄发放人数阈值 | Excel 第 28 行：俸禄发放人数阈值 | `game_config_common_config.week_suppress_camp_success_member_num_limit` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 22 | `soft_recycle_player_limit` | 软回收最低落堡人数要求 | Excel 第 26 行：软回收要求的最低落堡数（小游戏是默认20000） | `game_config_common_config.soft_recycle_real_player_num_limit` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 23 | `hard_recycle_player_limit` | 硬回收启动注册人数 | Excel 第 27 行：硬回收启动需要的服务器注册人数 | `game_config_common_config.hard_recycle_register_player_num_limit` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |
| 24 | `found_nation_reward_num_limit` | 虎视中原立国活动人数 | Excel 第 32 行：⻁视中原（⽴国活动） 预选值 | `game_config_common_config.found_nation_reward_num_limit` | 双端 | common + client_avatar | 待确认 | 待确认 | 已核对 |

## 侧别处理原则（用户 2026-10-29 决定）

需求来源里对侧别有标注（如 `capital_city_extra_quota` 标「客户端」、`rank_quota` 标「服务端」），但**不据此预先区分**。用户明确：

> 都别管，先按双端写。实际执行的时候跟飞书 AI 说「热更服务端」就只写 common（或客户端），不说明就双端一起。**宁可多做，只要省心。**

因此：

| 用户说法 | 生成的补丁 |
| --- | --- |
| 不说明侧别 | `common` + `client_avatar` 两份（默认，宁多勿漏） |
| 「热更服务端」 | 仅 `common` |
| 「热更客户端」 | 仅 `client_avatar` |

侧别由**执行时的自然语言**决定，不由注册表预判。`is_client` / `is_server` 保持双端默认值即可。

**历史注记**：24 个阈值的 `is_client` / `is_server` 全是默认 `True/True`，无一显式声明——这与上述原则一致，不需要改。

## 已取得的真实证据

| 日期 | key | 服号 | 证据 | 边界 |
| --- | --- | --- | --- | --- |
| 2026-10-29 | `union_impeach_min` | 海外 7011 | 热更生成 `common` + `client_avatar` 两份补丁，`patch_order_list.json` 两条目齐全 | 仅证明补丁生成与挂载，**未做游戏内数值验证**。 |

## 待办

- 无匹配需求来源的条目保持「待确认」，不得由中文相似词或旧代码猜测字段。
- 逐条补充脚本层与游戏内证据；两者分开记录，不可互相替代。
- 存量 `common` 阈值补丁需补发 `client_avatar` 副本后，客户端侧才算触达。

## 维护规则

- 本表只记**证据与来源**，不维护机器人执行许可——唯一许可是 [config/muliu_script_catalog.md](../config/muliu_script_catalog.md)。
- key / 中文名 / Lua 目标以 [tools/auto_hotfix_registry.py](../tools/auto_hotfix_registry.py) 为准，不在此复制维护。
- 需求来源以原始 Excel `游戏服改配置.xlsx` 为准；`docs/hotfix_thresholds_excel_export.json` 是本地导出副本。
