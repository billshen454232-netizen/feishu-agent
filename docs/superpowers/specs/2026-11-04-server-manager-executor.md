# 用 Server Manager API 替代木流网页执行

> **状态：设计草案，待用户确认第 7 节问题**
> **日期：2026-11-04**
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

### 3.1 语义核实（2026-11-04，读 `/api/servers` 的 operations 与 GS-1 上 `/data/app/nslg_server_manager` 源码）

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
| `shutdown weeklygvg / devgvg ...`（2022–2027） | API 无这两组 gvg | 保留木流 |
| `shutdown {id} autokill`（普通服）、环境名批量关服（dev / weekly / qa / kr*） | 无直接对应 | 保留木流（批量关服可在二期用多目标 `stop` 评估） |
| `clear-server` `clear {id}` | `kill [game_{id}, zone_{id}01]` → `clear [game_{id}, zone_{id}01]` | 迁移（4001 除外） |
| `clear-server-opening-time` `clear {id} by/noby` | 无参数对应 | 保留木流 |
| `startup-failure-diagnosis` | `task_type=log`（kind=error、since_minutes） | 第二期评估 |
| 合服 `exc_server_merge.sh` | `merge_servers` 工作流 | 第二期评估（参数模型不同） |
| 热更阈值、`cc_patch`、`control_is_pub`、`starttime`、`basic_info`、批量版本查询、`clear_zone` / `cleardb` 等 | 无对应 | 保留木流 |
| — | `run-debug-code`（任意代码执行） | **不接入** |

## 4. 架构

```text
飞书 → Gateway（规划 / 校验 / 确认卡片，不变）→ SQLite → Worker
Worker 逐步执行：
  步骤匹配「已迁移合同」且服号在该区域 API 清单内 → Server Manager 后端
  否则                                              → 木流后端（现有逻辑不变）
```

- **Planner、catalog、确认卡片都不变**：AI 仍然输出 `path + args`；后端选择是 Worker 上的确定性翻译，不经过 AI；
- **翻译表放配置**（注册表驱动）：`server_manager.capabilities` 按合同名登记 `task_type`、targets 规则（`game` / `zone+game`）、固定 params；未登记的合同一律走木流；
- **区域 → 实例**：沿用现有 `route_region`（cn / overseas）选择 base URL；
- **服号清单**：Worker 启动时与每 N 分钟读 `GET /api/servers?env=qa_test` 缓存；不在清单里的服号走木流（不报错）；
- **开关**：`server_manager.enabled` 与每项能力单独开关，可随时退回木流。

## 5. 执行与结果

1. 每步生成稳定 `submission_id = fb-<message_id 摘要>-s<步号>`（≤96 字符，满足 `^[A-Za-z0-9][A-Za-z0-9._-]*$`），`reason` 写「飞书机器人：<发起人>：<请求摘要>」（脱敏）；
2. `POST /api/tasks`（带 `submission_id` → 立即返回 handle）；
3. 轮询 `GET /api/task-submissions/:id` 拿 `run_id`，再轮询 `GET /api/tasks/:run_id` 直到终态，最后 `GET /api/tasks/:run_id/result`；
4. **超时 / 连接中断**：只用同一 `submission_id` 查询，**不改 ID 重派**；同 ID 重复 POST 时 API 只返回原任务（文档保证），所以「提交了没有」不再需要日志标记猜测；
5. 结果映射到现有 `MuliuStepResult`（成功 / 失败 / 结果未知 + summary、stdout/stderr 摘要、`next_actions`），后续的模板库、AI 研读、确认卡片沿用；
6. `server_state`（如 ready）写进结果摘要；仍遵守「脚本成功 ≠ 服务就绪」——只有 API 明确返回 ready 才写「已就绪」。

## 6. 分期

| 期 | 内容 |
| --- | --- |
| 一 | Worker 客户端 + 翻译表 + 服号清单缓存；国服、海外的起服 / 关服 / 清档（§7 确认后）；默认关闭，逐项打开 |
| 二 | 日志诊断（`log`）、合服工作流评估 |
| 三 | 已全部迁移的能力去掉木流兼容逻辑（会话重登、运行标记核对仅对木流后端保留） |

## 7. 待确认（需要用户 / 服务端回答）

1. ~~语义是否一致~~：已核实，见 §3.1。剩余一项在试跑中确认：zone 的 `{nslg_mode}` 是否解析为 `qa_zone`；`/api/servers` 显示的 `clear.host_num` 为 null，确认运行时会补全（源码有 `hydrate_clear_operation`）。
2. **并发与归属**：机器人和人工同时用这套 API 有没有冲突 / 限流？`reason` 字段写发起人昵称是否合适？
3. **未覆盖的 6 台**（2022、2026、2028、9800、6100、6101）是否会加入 API；在加入前继续走木流。

## 8. 安全边界

- API 文档未写鉴权，Worker 是唯一调用方；Gateway 不直接调用（国服本来就 403）；
- 只接入翻译表登记的 `task_type`，不暴露 `run-debug-code`、配置写入、文件上传、Agent 部署等接口；
- 写操作不自动重跑；只读查询（status / log）沿用「通信失败自动重跑一次」；
- 不在日志、飞书卡片、AI 输入中输出 API 返回里的 Mongo 账号等敏感字段（`/api/servers` 含 `mongo_hosts.users`，服号清单只取 `id` / `group`）。
