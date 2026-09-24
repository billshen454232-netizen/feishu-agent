# 测试服脚本知识索引

> 此索引用于回答“某脚本做什么、参数怎么用、怎样查询某类信息”等问题。
> 面向问答的资料入口仅限本文件与 `docs/`；`scripts/`、`references/` 和实际 Linux 目录均不是自动检索来源。
> 机器人实际生成执行计划时仍以 `config/muliu_script_catalog.md` 为准。

## 已整理脚本

|脚本|用途概览|关键词|说明文档|当前执行资料状态|
|---|---|---|---|---|
|`basic_info.sh`|查询服务器基础状态；也支持部分时间、剧本和赛季查询分支|服务器信息、基础信息、开服时间、版本、进程、剧本、赛季|[basic_info.md](docs/basic_info.md)|已登记基础查询模式|
|`cc_patch.py`|查询、对比、备份/恢复、调整和热更 Patch 列表|patch、补丁、补丁列表、查询、查看、热更、patch list|[cc_patch.md](docs/cc_patch.md)|已登记单服 Patch 列表查询与缺失文件检查模式；其他模式待单独评审|
|`clear`|清服：停止服务、清理逻辑与 Zone 数据，并可调整开服时间|清服、清档、清理数据、开服时间|[clear.md](docs/clear.md)|不得登记：破坏性高风险操作|
|`clear_logic_game.py`|清理逻辑游戏数据|清档、逻辑服、游戏数据|[clear_logic_game.md](docs/clear_logic_game.md)|不得登记：敏感、破坏性操作|
|`clear_zone.py`|清理分区/Zone 数据|清档、分区、Zone、游戏数据|[clear_zone.md](docs/clear_zone.md)|不得登记：敏感、破坏性操作|
|`cleardb.py`|历史合服相关公共数据清理意图|清库、数据库、清档、合服|[cleardb.md](docs/cleardb.md)|不得登记：阅读副本待验证且敏感|
|`modify_game_config.sh`|修改目标服的赛季和剧本配置|改配置、游戏配置、赛季、剧本|[modify_game_config.md](docs/modify_game_config.md)|不得登记：高风险变更|
|`start`|启动游戏服务；默认会启动 Zone 与游戏逻辑服务|启动、开服、启动服务、Zone|[start.md](docs/start.md)|不得登记：服务状态变更|
|`shutdown`|常规或强制关闭游戏、Zone 或 GVG 相关进程|关闭、停服、关服、kill、GVG|[shutdown.md](docs/shutdown.md)|不得登记：高风险服务中断|
|`starttime`|检查或修改 Debug 开服时间配置和时间戳文件|开服时间、启动时间、时间戳、Debug|[starttime.md](docs/starttime.md)|不得登记：高风险配置变更|

## 配置与脱敏流程资料

|资料|用途|说明文档|当前状态|
|---|---|---|---|
|服务器路由配置|说明逻辑服务器标识和内部目标映射的维护边界，不包含实际映射值|[server-routing-configuration.md](docs/server-routing-configuration.md)|仅知识说明；不得读取原始映射|
|木流操作流程|从旧操作手册整理操作类别、前置核对和风险边界，不含账号、链接或环境定位|[muliu-operation-manual-sanitized.md](docs/muliu-operation-manual-sanitized.md)|仅问答流程资料|
|测试环境摘要|整理操作前的占用、环境和剧本核对原则，不含服务器、人员或拓扑|[server-environment-summary-sanitized.md](docs/server-environment-summary-sanitized.md)|仅问答流程资料|
|脚本调用关系与安全边界|记录脚本类别、依赖关系和资料使用边界|[script-dependency-and-safety.md](docs/script-dependency-and-safety.md)|仅知识说明|
|游戏服运行目录与日志布局|说明 GS-1 中转、目标游戏服应用目录、日志根目录与受控日志诊断边界|[game-server-runtime-and-log-layout.md](docs/game-server-runtime-and-log-layout.md)|本地已有待部署固定查询脚本；`bglog/game` 仍须 GS-1 实测后才可登记执行能力|

## 原始参考资料

|原件|用途|处理状态|
|---|---|---|
|`木流操作手册.xlsx`|历史操作流程参考|原件可能含门户、账号或人员信息；已忽略，不直接检索；仅使用脱敏流程文档|
|`服务器环境分布.xlsx`|历史环境分布参考|原件可能含服务器占用和环境拓扑；已忽略，不直接检索；仅使用脱敏摘要|

原始资料的存放规则见 [references/README.md](references/README.md)。

## 维护流程

1. 将原始脚本或参考资料放入 `scripts/`、`references/` 前，先检查敏感信息。
2. 只在 `docs/` 新增可提供给 AI 的脱敏说明，写清用途、参数、只读/变更影响和恢复方法。
3. 更新本索引的关键词、文档链接和当前执行资料状态。
4. 如需允许机器人执行，再单独更新 `config/muliu_script_catalog.md`；仅有知识文档不代表允许执行。
