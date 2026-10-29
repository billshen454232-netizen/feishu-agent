# 测试服脚本知识索引

> 此索引用于回答“某脚本做什么、参数怎么用、怎样查询某类信息”等问题。
>
> 面向问答的资料入口仅限本文件与 `docs/`；`scripts/`、`references/` 和实际 Linux 目录均不是自动检索来源。
>
> **机器人是否可以执行某个精确调用，只以 [../config/muliu_script_catalog.md](../config/muliu_script_catalog.md) 的当前合同为准。** 本索引不维护另一套执行许可状态，也不证明 GS-1 已部署相应脚本。

## 已整理脚本

|脚本|用途概览|关键词|说明文档|
|---|---|---|---|
|`basic_info.sh`|查询服务器基础状态，以及部分时间、剧本、赛季与玩家查询分支|服务器信息、基础信息、开服时间、版本、进程、剧本、赛季|[basic_info.md](docs/basic_info.md)|
|`cc_patch.py`|查询、对比、备份/恢复、调整和热更 Patch 列表|patch、补丁、补丁列表、查询、查看、热更、patch list|[cc_patch.md](docs/cc_patch.md)|
|`clear`|清服：停止服务、清理逻辑与 Zone 数据，并可调整开服时间|清服、清档、清理数据、开服时间|[clear.md](docs/clear.md)|
|`clear_logic_game.py`|清理逻辑游戏数据|清档、逻辑服、游戏数据|[clear_logic_game.md](docs/clear_logic_game.md)|
|`clear_zone.py`|清理分区 / Zone 数据|清档、分区、Zone、游戏数据|[clear_zone.md](docs/clear_zone.md)|
|`cleardb.py`|合服相关公共数据清理意图|清库、数据库、清档、合服|[cleardb.md](docs/cleardb.md)|
|`modify_game_config.sh`|修改目标服的赛季和剧本配置|改配置、游戏配置、赛季、剧本|[modify_game_config.md](docs/modify_game_config.md)|
|`start`|启动游戏服务；默认会启动 Zone 与游戏逻辑服务|启动、开服、启动服务、Zone|[start.md](docs/start.md)|
|`shutdown`|常规或强制关闭游戏、Zone 或 GVG 相关进程|关闭、停服、关服、kill、GVG|[shutdown.md](docs/shutdown.md)|
|`starttime`|检查或修改 Debug 开服时间配置和时间戳文件|开服时间、启动时间、时间戳、Debug|[starttime.md](docs/starttime.md)|
|`control_is_pub.sh`|独立控制调试 / GM 面板、发布模式或外部体验开服|GM面板、调试面板、发布模式、外部开服、public_game|[control_is_pub.md](docs/control_is_pub.md)|
|`exc_server_merge.sh`|生成合服配置并启动合服工具|合服、服务器合并、合到、合服工具|[exc_server_merge.md](docs/exc_server_merge.md)|
|`stop_combine_tool.sh`|仅关闭目标服合服工具拉起的 combine 进程|关闭合服进程、停止合服工具、关闭 combine|[stop_combine_tool.md](docs/stop_combine_tool.md)|

所有变更、清理、停止、启动、配置、Patch 与合服操作都可能产生高影响；即使 catalog 已登记，仍须精确合同校验、路由校验、防火墙与原请求人确认。脚本成功不自动代表业务效果已验证。

## 配置与脱敏流程资料

|资料|用途|说明文档|
|---|---|---|
|服务器路由配置|说明逻辑服务器标识和内部目标映射的维护边界，不包含实际映射值|[server-routing-configuration.md](docs/server-routing-configuration.md)|
|服务器环境配置|说明环境服号的可信来源与生成规则|[server_environments.md](docs/server_environments.md)|
|木流操作流程|从旧操作手册整理操作类别、前置核对和风险边界，不含账号、链接或环境定位|[muliu-operation-manual-sanitized.md](docs/muliu-operation-manual-sanitized.md)|
|测试环境摘要|整理操作前的占用、环境和剧本核对原则，不含服务器、人员或拓扑|[server-environment-summary-sanitized.md](docs/server-environment-summary-sanitized.md)|
|脚本调用关系与安全边界|记录脚本类别、依赖关系和资料使用边界|[script-dependency-and-safety.md](docs/script-dependency-and-safety.md)|
|游戏服运行目录与日志布局|说明 GS-1 中转、目标游戏服应用目录、日志根目录与受控日志诊断边界|[game-server-runtime-and-log-layout.md](docs/game-server-runtime-and-log-layout.md)|

## 原始参考资料

|原件|用途|处理状态|
|---|---|---|
|`木流操作手册.xlsx`|历史操作流程参考|可能含门户、账号或人员信息；已忽略，不直接检索；仅使用脱敏流程文档。|
|`服务器环境分布.xlsx`|历史环境分布参考|可能含服务器占用和环境拓扑；已忽略，不直接检索；仅使用脱敏摘要。|

原始资料的存放规则见 [references/README.md](references/README.md)。

## 维护流程

1. 将原始脚本或参考资料放入 `scripts/`、`references/` 前，先检查敏感信息。
2. 只在 `docs/` 新增可提供给 AI 的脱敏说明，写清用途、参数、只读/变更影响、恢复方式与验证边界。
3. 更新本索引的关键词和文档链接；不要在这里重复维护执行许可状态。
4. 如需新增或变更机器人能力，单独更新 [../config/muliu_script_catalog.md](../config/muliu_script_catalog.md) 的人类说明、机器合同与测试。
5. 真实 GS-1 部署或目标服验证完成后，才可在当前项目说明中记录为远端事实。
