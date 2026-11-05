# 用 Server Manager API 替代木流网页执行

> **状态：方案 C 实施中（2026-11-05 用户选定：混合路线 + 后台逐项开关）**
> **日期：2026-11-04 初稿；2026-11-05 定稿方案 C**
> **关联计划：** 项目计划看板 STEP 35
> **原则：** 能力由注册表驱动（不新增第二套限制规则）；写操作永不自动重跑；脚本成功 ≠ 服务就绪 ≠ 游戏内生效

## 1. 背景

木流（Muliu Task 89）是网页会话：单会话、会话会被挤掉、提交无响应时只能靠 `MULIU_RUN_VERSION` 日志标记猜「到底提交了没有」。服务端提供了 NSLG Server Manager API，自带异步提交幂等与任务记录，可以替代其中一部分能力。

## 2. 已核实的事实（2026-11-04）

| 项 | 国服实例 | 海外实例 |
| --- | --- | --- |
| 文档 | `https://test-all-gm-nslg.bilibiligame.net/server_manager/api-docs` | `https://za-all-gm-nslghk.newslg.com/server_manager/api-docs` |
| 版本 | center 0.1.0，接口与工作流两边完全一致（52 接口 / 21 工作流） | 同左 |
| 可达性 | 开发机 200、**Worker 200**、Gateway 403 | 开发机 / Worker / Gateway 均 200 |
| 环境 | 仅 `qa_test` | 仅 `qa_test` |
| 服务器 | 85 台：game 38、zone 38、其他 9 | 62 台：game 31、zone 31 |
| 每台操作 | `start` / `stop` / `kill` / `clear` | 同左 |
| 鉴权 | 文档未写（靠内网隔离） | 同左 |

**服号映射**：`game_<服号>`；zone 为 `zone_<服号>01`（如 2006 → `zone_200601`）。

**覆盖**：机器人登记的 56 台中 54 台在 API 内。未覆盖：国服 2022、2026、2028、9800；韩服提审 6100、6101。其中 2022–2029 实为 gvg 机器（GS-1 `shutdown` 的 weeklygvg = 2022–2025、devgvg = 2026–2027、qagvg = 2028–2029）；qagvg 对应 API 的 `qa_gvg_zone`。API 另有机器人未登记的服（国服 2013、2100–2109、4001–4005；海外 5200–5203、6200、6202），不在本次范围。

## 3. 能力对照

### 3.1 语义核实（2026-11-04，读 `/api/servers` 的 operations 与 GS-1 上 `/data/app/nslg_server_manager` 源码；2026-11-05 用国服 GS-1 实际脚本复核：`clear` 与本地副本一致，`start` / `shutdown` 普通服路径不变）

| 动作 | GS-1 脚本 | Server Manager | 结论 |
| --- | --- | --- | --- |
| 起 game | `release_bin/start_game.sh` | 同一命令，之后校验进程状态到 ready | 一致（API 多一层就绪校验） |
| 起 zone | `release_bin/start_zone.sh` | `release_bin/start_zone.sh zone {nslg_mode}` | 底层相同；`nslg_mode` = 服务器配置 `NSLG_MODE`，缺省 `{env}_{group}`，试跑时核对 |
| 多目标起服顺序 | 先 zone 后 game | 同一阶段**同时**执行所有目标（`_run_start_action` 按 phase 循环、每轮全部 targets） | 需要顺序时**拆成两次提交**：先 zone，返回 ready 后再 game |
| 起服前检查 | 无 | `start_precheck`：库里有压测账号时才检查 `common_patch_press.lua`，否则跳过 | 普通服无影响 |
| 正常关服 | `shutdon_game.sh` + `shutdon_zone.sh zone qa_zone` | 同两条（zone 带 `{nslg_mode}`） | 一致 |
| kill | `bin/kill_server.sh` + `bin/kill_zone.sh` | 同两条 | 一致 |
| 清档 | 先 kill game / zone，再 `clear_logic_game.py` + `clear_zone.py` | **不停进程**：进程在跑直接返回 BLOCKED；随后清库 | 拆成 kill → clear 两次提交 |
| 清库范围 | `Account.role_list.<服号>` unset；drop `nslg_game_<服号>`、`nslg_game_<服号>01` 全部集合 | 同左，另加 `nslg_game_common.global_avatar`（`hostNum=服号`）、`UnionHistory`（`lastWriteSeasonDataHostNum=服号`） | API 是超集，**只删本服**（用户 2026-11-04 认可） |
| 4001 特殊分支 | `clear` 脚本里单独处理 | 无 | 4001 保留木流 |

### 3.2 合同映射

| 机器人现有能力（catalog 合同） | Server Manager 提交序列 | 本次 |
| --- | --- | --- |
| `start-server` `start {id}` | `start [zone_{id}01]` → `start [game_{id}]` | 迁移 |
| `start-game-only` `start {id} noz` | `start [game_{id}]` | 迁移 |
| `shutdown-normal` `shutdown {id}` | `stop [game_{id}]` → `stop [zone_{id}01]`（保持 GS-1 先 game 后 zone 的顺序） | 迁移 |
| `shutdown-mode` `shutdown {id} kill` | `kill [game_{id}, zone_{id}01]` | 迁移 |
| `shutdown qagvg {normal/kill/autokill/gvgzone}` | `kill [qa_gvg_zone]`（主 = 2028、从 = 2029，IP 哈希已核对；`kill_gvg_zone.sh` 杀本 gvg_zone 分组全部 skynet lua 进程，已包含 daemon / gate，GS-1 先 zone 后 daemon 的两步等效于它） | 迁移 |
| `shutdown qagvg gvgdaemon`（只杀 room_daemon / room_gate） | 无对应命令 | 保留木流 |
| `start qagvg {normal/noz}`（国服 `start`：第一台起 zone 中心、其余起 daemon） | `start [qa_gvg_zone]`（主 `start_gvg_zone.sh`、从 `start_gvg_zone_daemon.sh`，与之对应） | 二期（机器人目前未登记 gvg 起服合同） |
| `shutdown weeklygvg / devgvg ...`（2022–2027） | API 无这两组 gvg | 保留木流 |
| `shutdown {id} autokill`（普通服）、环境名批量关服（dev / weekly / qa / kr*） | 无直接对应 | 保留木流（批量关服可在二期用多目标 `stop` 评估） |
| `clear-server` `clear {id}` | `kill [game_{id}, zone_{id}01]` → `clear [game_{id}, zone_{id}01]` | 迁移（4001 除外） |
| `clear-server-opening-time` `clear {id} by/noby` | 无参数对应 | 保留木流 |
| `startup-failure-diagnosis` | `task_type=log`（kind=error、since_minutes） | 第二期评估 |
| 合服 `exc_server_merge.sh` | `merge_servers` 工作流 | 第二期评估（参数模型不同） |
| 热更阈值、`cc_patch`、`control_is_pub`、`starttime`、`basic_info`、批量版本查询、`clear_zone` / `cleardb` 等 | 无对应 | 保留木流 |
| — | `run-debug-code`（任意代码执行） | **不接入** |

## 4. 架构（方案 C：混合路线，逐项开关）

用户 2026-11-05 决定：不要求服务端改接口；API 已覆盖的能力逐项切到 API，其余继续木流。每项能力、每个区域（国服 / 海外）单独开关，一个一个测试上线，避免整体崩溃。

```text
飞书 → Gateway（规划 / 校验 / 确认卡片，不变）→ SQLite → Worker
Worker 领取任务时，Gateway 按「执行路线」开关为每一步标注 muliu / api
Worker 逐步执行：
  标注 api 且 Worker 本地翻译成功、服号在该区域 API 清单内 → Server Manager
  否则                                                    → 木流（现有逻辑不变）
```

- **Planner、catalog、确认卡片都不变**：AI 仍输出 `path + args`；路线选择与翻译都是确定性代码，不经 AI；
- **开关存 Gateway SQLite**（`execution_routes`：合同名 × 区域 → muliu / api，含修改历史），默认全部木流；
- **领取时决定**：Gateway 在 `/internal/worker/jobs/claim` 返回 `step_routes`（与步骤一一对应），以领取那一刻的开关为准；
- **Worker 二次把关**：只有 Worker 本地翻译表认识的合同、且服号在 API 清单里，才真正走 API；否则降级木流并在结果日志注明原因（不报错）；
- **翻译表在代码里**（`src/server_manager.py`），有测试；开关只决定「用不用」，不决定「怎么翻译」；
- **Worker 配置**：`server_manager.enabled` 总开关 + 两个区域的 base URL；关掉总开关即全部回到木流。

## 5. 执行与结果（API 路线）

1. 一步可能对应多次 API 提交（如清档 = kill → clear）；每次提交的 `submission_id = fb-<message_id 摘要>-s<步号>-t<序号>`，`reason` 写「飞书机器人 第 N 步：<步骤说明>」；
2. `POST /api/tasks`（带 `submission_id`，立即返回）→ 轮询 `GET /api/task-submissions/:id` 直到终态 → `GET /api/tasks/:run_id/result` 取摘要；
3. **提交超时 / 连接中断**：用同一 `submission_id` 查询；返回 404 `submission_not_found` = 确定未提交（可报「未执行」并允许继续剩余步骤），否则按「已提交、结果未知」处理；**不改 ID 重派、不自动重跑写操作**；
4. 结果拼成与木流相同的日志格式（末尾 `AI_OP_END status=success|failed`），结果卡片、模板库、AI 研读沿用；日志首行注明「API 路线」与 `run_id`，便于对账；
5. 起服走 API 时以 API 的 `wait_until=ready` 校验代替木流的 `basic_info` 核验；为保持 Gateway 结果协议不变（起服步骤后必须紧跟固定的核验条目），执行器补一条同身份的核验结果，日志注明来自 API；`server_state` 原样写入日志，只有 API 明确返回 ready 才写「已就绪」。

## 6. 功能清单与上线顺序

### 6.1 一期：可切 API（后台可开关）

| # | 能力（catalog 合同） | 走 API 时的提交序列 | 不走 API 的情况 |
| --- | --- | --- | --- |
| 0 | 单服信息查询 `basic-server-info`（`basic_info.sh X`，只读） | 读 `server-config`（剧本 / 赛季 / 开服时间 / 对外 / 启动模式）+ `servers`（代码版本 `release_version`）+ `status [game_X, zone_X01]`（进程状态） | 4001；不在清单；API 不提供 supervisor / bglog 异常日志 |
| 1 | 起服 `start-server`（`start X`） | `start [zone_X01]` → `start [game_X]` | 4001；服号不在 API 清单 |
| 2 | 只起 game `start-game-only`（`start X noz`） | `start [game_X]` | 同上 |
| 3 | 正常关服 `shutdown-normal`（`shutdown X`） | `stop [game_X]` → `stop [zone_X01]` | 同上 |
| 4 | 指定方式关服 `shutdown-mode`（`shutdown X normal/kill`） | normal 同第 3 项；kill = `kill [game_X, zone_X01]` | `autokill` / `gvgzone` / `gvgdaemon`；4001；不在清单 |
| 5 | 清档 `clear-server`（`clear X`） | `kill [game_X, zone_X01]` → `clear [game_X, zone_X01]` | 4001（GS-1 有特殊分支）；不在清单 |

建议上线顺序（影响从小到大）：0（信息查询，只读）→ 2（只起 game）→ 1（起服）→ 3 / 4（关服）→ 5（清档）。每项先开国服或海外其中一个区域，用测试服跑通再开另一个。

### 6.2 二期：评估中（后台显示为「暂无 API 对应」）

| 能力 | 候选 API | 需要先确认 |
| --- | --- | --- |
| 起服失败诊断 `startup-failure-diagnosis` | `task_type=log`（kind=error、since_minutes） | 输出与现有诊断是否等价 |
| 单服基础信息 `basic-server-info` | `task_type=status` / `server-config` 读取 | 只能覆盖部分字段 |
| 合服 `merge-single-source-single-target` | `POST /api/merge/tasks`（`source_games` / `source_zone` / `next_game`） | 见 6.5 |
| qagvg 起服 / 关服 | `start` / `kill [qa_gvg_zone]` | 机器人尚未登记 gvg 合同 |

### 6.3 无 API 对应（始终木流）

热更阈值（`auto_hotfix_tool.py`）、补丁清单（`cc_patch.py` 6 项）、GM 面板 / 对外开关（`control_is_pub.sh`）、开服时间（`starttime`）、`basic_info.sh` 的剧本 / 赛季 / 积分查询、批量版本查询、`clear X by/noby`、`clear_logic_game` / `clear_zone` / `cleardb`、`modify_game_config.sh`、`stop_combine_tool.sh`。

### 6.5 合服（用户 2026-11-05 列为高频，待确认后接入）

API 合服是完整编排，步骤（来自运行记录）：`precheck-dbs` → `resolve` → **`kill-next-game` → `clear-next-game`** → `stop-source-games` → `stop-source-zone` → `write-combine-config`（服务端：在战区服所在机器生成）→ `prepare-zone-data` → `start-combine` → `wait-combine`（默认最长 7200 秒）。

与现有 `exc_server_merge.sh`（只写配置并启动合服工具）的差异：

- **会先 kill 并清空目标服**；
- 没有赛季 / 剧本参数（从服务器配置读取）；
- 不支持 `submission_id`：提交超时无法判定是否已提交，只能按「结果未知」处理；
- 截至 2026-11-05 只跑过 2 次，均失败（一次在 `stop-source-games`，一次在 `wait-combine`）。

接入前需要用户确认：语义（「5000 合到 5001」= `source_games=[game_5000]`、`source_zone=zone_500001`、`next_game=game_5001`，目标服会被清空）；赛季 / 剧本不再需要；先用两台可清空的测试服通过 Server Manager 后台手工跑通一次。

### 6.4 管理后台 `ops.ttlink.asia/routes`

- 每行一个 catalog 合同：能力说明、API 提交序列（或「暂无 API 对应」）、国服开关、海外开关、最后修改人与时间、修改历史；
- 只有 6.1 的合同能切换；切换即时生效（下一次 Worker 领取任务）；
- 同一套 Cloudflare Access + Host 校验；写操作带 `X-Admin-Request: 1`。

## 7. 待确认（需要用户 / 服务端回答）

1. ~~语义是否一致~~：已核实，见 §3.1。剩余一项在试跑中确认：zone 的 `{nslg_mode}` 是否解析为 `qa_zone`；`/api/servers` 显示的 `clear.host_num` 为 null，确认运行时会补全（源码有 `hydrate_clear_operation`）。
2. **并发与归属**：机器人和人工同时用这套 API 有没有冲突 / 限流？`reason` 字段写发起人昵称是否合适？
3. **未覆盖的 6 台**（2022、2026、2028、9800、6100、6101）是否会加入 API；在加入前继续走木流。

## 8. 安全边界

- API 文档未写鉴权，Worker 是唯一调用方；Gateway 不直接调用（国服本来就 403）；
- 只接入翻译表登记的 `task_type`，不暴露 `run-debug-code`、配置写入、文件上传、Agent 部署等接口；
- 写操作不自动重跑；只读查询（status / log）沿用「通信失败自动重跑一次」；
- 不在日志、飞书卡片、AI 输入中输出 API 返回里的 Mongo 账号等敏感字段（`/api/servers` 含 `mongo_hosts.users`，服号清单只取 `id` / `group`）。
