# 配置与脚本职责索引

> **维护状态：当前事实入口（持续更新）**
>
> **最后核对：2026-10-27**
>
> **用途：** 回答“这个配置/脚本是干什么的、谁在读它、改了会影响什么”。
> **边界：** 本文只描述仓库内文件的职责与消费关系，不授予任何机器人执行权限，不证明远端部署状态。执行许可唯一来源是 [config/muliu_script_catalog.md](../config/muliu_script_catalog.md)。

## 如何使用本文

| 你想知道 | 看哪一节 |
| --- | --- |
| 某个配置文件是干什么的、谁读它 | §1 `config/` |
| 某个 Python 模块的职责和调用关系 | §2 `src/` |
| 某个工具是干什么的、会不会改文件 | §3 `tools/` |
| 某个脚本是机器人能调用的还是只是阅读副本 | §4 `knowledge/scripts/` |
| GS-1 部署目录里有什么、能不能当部署证据 | §5 `deployment/gs1/` |
| 某个测试覆盖什么 | §6 `tests/` |

## 项目演进与模块归属

本项目有明确的三阶段演进，读代码前先分清哪些文件属于哪一阶段：

| 阶段 | 说明 | 对应代码 |
| --- | --- | --- |
| **早期：知识库问答助手** | 最初定位是知识库问答。 | `knowledge_base.py`（Obsidian vault 双链检索）、`script_knowledge_base.py`、`conversation.py`、`conversation_log.py`；`docs/openagent-local-deployment.md` 是 OpenAgent 知识库平台试用记录（仅存于 `.worktrees/markdown-fallback-search/`）。 |
| **当前：测试服运维机器人** | 现阶段主线。 | `muliu_*` 系列、`message_jobs`、`message_worker`、`operation_message`、`worker_protocol`、`event_handler`、`app`、`config`；`config/muliu_script_catalog.md` 是唯一执行许可。 |
| **规划中：知识库增强** | 尚未实现，不得写成当前能力。方向一：服务器出问题时抽取日志、自动分析、给出建议；方向二：给出测试场景/需求时切换到专业智囊模式，给测试建议和帮助。 | 预计依托 `knowledge_base.py`、`script_knowledge_base.py` 与 `.worktrees/markdown-fallback-search/` 分支（已保留）继续扩展。 |

`knowledge/` 目录同时服务两阶段：早期做知识问答资料，现在主要说明运维脚本用途与风险。它不授予执行许可。

`knowledge_base.py`、`conversation.py` 等早期模块在**当前运维主线上仍被 `app.py` 加载**（配置里 `knowledge_base.enabled`、`conversation` 段），只是运维主线的核心链路不依赖它们。判断是否改动时，先确认属于哪一阶段的需求。

---

## 1. `config/` — 配置与静态资料

### 1.1 运行配置模板

| 文件 | 作用 | 消费者 | 敏感性 | 备注 |
| --- | --- | --- | --- | --- |
| `config.example.json` | **local 单机模式**配置模板：飞书 App、AI API、对话历史、知识库、任务队列。 | 复制为 `config/config.json` 后由 [src/config.py](../src/config.py) `load_config()` 读取。 | 含 App Secret / API Key 占位；真实值不得入库。 | 无 `runtime.role` 时默认 `local`。 |
| `config.gateway.example.json` | **gateway 模式**配置模板：飞书入口、AI、任务队列、Worker 租约与 token。 | 同上。 | 含 `runtime.worker_token` 占位。 | **不含** Muliu URL / 账号 / 密码 / Task ID。 |
| `config.worker.example.json` | **worker 模式**配置模板：Gateway 连接、轮询心跳、Muliu 路由 profile。 | 同上。 | 含 Worker token、Muliu 凭据占位、私有 profile。 | **不含**飞书 App Secret 与 AI API Key。 |
| `config.json` | 本机真实运行配置（Git 忽略）。 | 同上。 | **敏感**：真实凭据。 | 不得提交；从对应 example 复制后本地填值。 |

三种配置的**关键区别**：`runtime.role` 决定进程角色——

```text
local   = 飞书 + AI + SQLite + 确认 + Muliu 执行同进程
gateway = 飞书 + AI + SQLite + 确认 + Worker 分发（无 Muliu 凭据）
worker  = 主动连 Gateway 领任务 + 本地 Muliu 执行（无飞书/AI 凭据）
```

完整配置字段定义见 [src/config.py](../src/config.py) 的 `AppConfig` 及各 `*Config` dataclass。

### AI 兜底模型（可选）

`ai` 段可选配 `fallback`：主模型重试耗尽后降级到的模型。**未配置则没有兜底**；因此它不写进 `config.example.json`（JSON 无注释，放占位值会被误启用）。

```json
"ai": {
  "provider": "openai_compatible",
  "base_url": "https://主模型网关",
  "api_key": "主模型密钥",
  "model": "主模型名",
  "fallback": {
    "provider": "openai_compatible",
    "base_url": "https://兜底模型网关",
    "api_key": "兜底密钥",
    "model": "minimax-3.1"
  }
}
```

边界：

- 只有 `provider` / `base_url` / `api_key` / `model` 必填；`system_prompt`、`temperature`、`max_tokens`、`timeout_seconds` 未填时**继承主模型值**；
- 兜底只在主模型**重试耗尽后**才调用，不是负载均衡；
- 兜底产出的回复**必然带降级标注**（末尾注明「本次回复由兜底模型 X 生成…请谨慎采信」），避免弱模型结论被当主力模型结论；
- 标注只进飞书展示层，不回流到提示词或计划解析（计划解析走 `generate()`，返回纯净文本）；
- 兜底也失败时走失败错误卡片，不会静默。

### 1.2 服务环境与路由资料

| 文件 | 作用 | 消费者 | 敏感性 | 备注 |
| --- | --- | --- | --- | --- |
| `server_environments.json` | **服号 → 区域 / 环境归属**的唯一资料源。含 `regions`（cn / hmt / global / kr / vn）、`environment_labels`、跨区域 `groups` 选择器。 | [src/server_environments.py](../src/server_environments.py) 加载并校验；[src/muliu_routing.py](../src/muliu_routing.py) 从它派生 `route_region`；[src/message_worker.py](../src/message_worker.py) 用它构造 AI 研读的环境上下文。 | 仅服号与环境标签，无私密连接信息。 | 改服号归属只改这里；catalog 中的环境服号列表由 `tools/sync_hotfix_planner_catalog.py` 从它生成。 |
| `muliu_regions.example.json` | 旧式 Muliu profile 模板：以 `profiles.{china,overseas}` 和 `server_regions` 表示服号路由。 | **当前仓库代码没有读取它**。 | **敏感字段**：URL / 账号 / 密码 / Task ID 占位。 | 历史 / 候选资料，不能替代当前 worker 的 `muliu.routes` 配置。 |
| `muliu_regions.json` | 上述旧式模板的占位副本。当前副本仅含占位值。 | **当前仓库代码没有读取它**。 | 含敏感字段结构，但当前无真实凭据。 | 被 Git 跟踪；用户已决定保持现状、不加入 `.gitignore`。如未来填入真实值，必须先重新评估存放方式。 |

### 1.3 执行合同与热更资料

| 文件 | 作用 | 消费者 | 敏感性 | 备注 |
| --- | --- | --- | --- | --- |
| `muliu_script_catalog.md` | **机器人侧唯一精确执行合同**。登记每个可调用脚本的 `path + args` 模板、变量正则、风险等级 `read/write`。 | [src/muliu_script_catalog.py](../src/muliu_script_catalog.py) 解析；[src/muliu_planner.py](../src/muliu_planner.py) 生成计划时约束；[src/muliu_firewall.py](../src/muliu_firewall.py) 二次校验。 | 无私密连接信息。 | **知识文档不能替代它**；模型不能按脚本名或近义词扩大调用能力。 |
| `hotfix_thresholds.json` | 热更阈值的 **JSON 资料模型**（含可执行候选与不可执行条目的结构化区分）。 | 仅由 [tools/hotfix_threshold_registry.py](../tools/hotfix_threshold_registry.py) 加载；**当前不在 catalog 同步主链中**。 | 业务字段名，无私密连接信息。 | **已被 `.gitignore` 排除**，属本地资料副本。不得称为“当前唯一运行时来源”；它是候选迁移资料。 |

### 1.4 其他

| 文件 | 作用 | 备注 |
| --- | --- | --- |
| `data/feishu_bot.sqlite3` | 任务状态机、去重、审计、确认令牌的 SQLite 数据库。 | 由 `job_queue.database_path` 指定；含真实请求与状态，属内部运行数据，不入 Git。 |
| `logs/conversations/` | 按日期追加的可读对话 Markdown 归档。 | 由 [src/conversation_log.py](../src/conversation_log.py) 写入；含群内原文，属内部数据。 |

---

## 2. `src/` — 运行期 Python 模块

### 2.1 入口与基础设施

| 模块 | 职责 | 关键符号 | 读取的配置 |
| --- | --- | --- | --- |
| [app.py](../src/app.py) | FastAPI 应用工厂。按 `runtime.role` 创建 local / gateway / worker 三种入口；gateway 注册 Worker 租约、claim、result 路由。 | `create_app`, `_create_worker_app`, `_add_gateway_worker_routes` | `config/config.json` |
| [config.py](../src/config.py) | 配置加载与校验。定义 `AppConfig` 及各段 dataclass，校验正数、路径、必填项。 | `load_config`, `AppConfig`, `ConfigError` | `config/config.json` |
| [ai_client.py](../src/ai_client.py) | AI 调用抽象层。支持 OpenAI 兼容协议与 Claude Messages 协议。 | `create_ai_client`, `OpenAICompatibleClient`, `ClaudeMessagesClient` | `ai.*` |
| [feishu_client.py](../src/feishu_client.py) | 飞书 OpenAPI 封装：发消息、更新卡片、处理事件。 | `FeishuClient`, `FeishuAPIError` | `feishu.*` |

### 2.2 飞书交互与任务状态

| 模块 | 职责 | 关键符号 |
| --- | --- | --- |
| [event_handler.py](../src/event_handler.py) | 飞书事件与卡片回调入口：去重、解析请求、分发到消息队列、处理确认/取消按钮。 | `FeishuEventHandler` |
| [message_jobs.py](../src/message_jobs.py) | 任务状态机与持久化：`awaiting_confirmation → confirmed → submission_in_progress → queued_for_worker/leased → done/failed/submitted_result_unknown`。 | `MessageJob`, `MessageJobStore` |
| [message_worker.py](../src/message_worker.py) | 消息处理主循环：AI 计划生成、确认卡投递、任务领取、结果回写、AI 结果研读层。 | `MessageJobProcessor`, `MessageJobQueue`, `interpret_execution_result` |
| [operation_message.py](../src/operation_message.py) | 计划与结果的飞书文本/卡片格式化，含敏感信息遮罩。 | `format_plan_for_confirmation`, `build_plan_confirmation_card`, `format_execution_result`, `mask_sensitive_operation_data` |
| [conversation.py](../src/conversation.py) | 会话历史存取。 | `ConversationStore` |
| [conversation_log.py](../src/conversation_log.py) | 对话归档为按日期的可读 Markdown。 | `ConversationLog` |

### 2.3 Muliu 执行链

```text
用户请求
→ muliu_intent.py        （非权威意图提示，仅供可观测性）
→ muliu_planner.py       （AI 生成受限 JSON 计划）
→ muliu_plan.py          （严格 JSON 解析）
→ muliu_script_catalog.py（精确调用合同校验）
→ muliu_firewall.py      （安全属性二次校验）
→ muliu_routing.py       （派生 route_region）
→ operation_message.py   （确认卡）
→ [确认后]
→ muliu_executor.py      （调用 Muliu Task 89）
→ worker_protocol.py     （结果序列化与计划比对）
→ message_worker.py      （回写状态 + AI 研读）
```

| 模块 | 职责 | 关键符号 | 不负责什么 |
| --- | --- | --- | --- |
| [muliu_intent.py](../src/muliu_intent.py) | 非权威请求提示，供观测与 UI 文案使用。 | `MuliuIntentRouter` | **不**决定是否执行。 |
| [muliu_planner.py](../src/muliu_planner.py) | 把飞书请求转成注册表校验过的 Muliu 决策（operation / knowledge / clarify / reject）。 | `MuliuPlanner`, `MuliuPlanGeneration` | **不**执行脚本。 |
| [muliu_plan.py](../src/muliu_plan.py) | AI 生成计划的严格 JSON 解析：`kind`、`route_scope`、`steps[]`。 | `parse_plan`, `MuliuPlan`, `MuliuStep` | **不**判断合同是否合法。 |
| [muliu_script_catalog.py](../src/muliu_script_catalog.py) | 解析并校验 catalog 中的精确调用合同（`path + args` 模板 + 变量正则 + risk）。 | `MuliuScriptCatalog`, `MuliuCallContracts`, `parse_call_contracts` | **不**推断未登记的脚本或参数。 |
| [muliu_firewall.py](../src/muliu_firewall.py) | 合同校验后的安全属性检查：根目录约束、长度限制、Shell 注入原语拒绝。 | `MuliuFirewall` | **不**是第二份业务白名单。 |
| [muliu_routing.py](../src/muliu_routing.py) | 从已验证计划派生唯一 `route_region`（cn / overseas），失败关闭。 | `MuliuPlanRouteResolver` | **不**生成脚本参数。 |
| [muliu_executor.py](../src/muliu_executor.py) | 调用 Muliu Task 89：提交 `{"path","args"}`，轮询日志，解释终态。 | `MuliuExecutor`, `MuliuStepResult` | **不**修改计划或扩大能力。 |
| [muliu_worker.py](../src/muliu_worker.py) | 内网 Worker：主动连接 Gateway，claim 已确认任务，本地重校验后执行。 | `MuliuWorker`, `GatewayWorkerClient` | **不**持有飞书 / AI 凭据。 |
| [worker_protocol.py](../src/worker_protocol.py) | Gateway 与 Worker 之间的结果序列化、反序列化与计划比对。 | `serialize_execution_result`, `validate_execution_result_matches_plan` | **不**接触 Muliu。 |

### 2.4 知识问答与环境

| 模块 | 职责 | 关键符号 |
| --- | --- | --- |
| [knowledge_base.py](../src/knowledge_base.py) | 双链 vault 知识库检索（可选启用）。 | `DualChainVaultKnowledgeBase` |
| [script_knowledge_base.py](../src/script_knowledge_base.py) | 只检索本地脱敏脚本文档，回答“脚本是干什么的”。 | `ScriptKnowledgeBase` |
| [server_environments.py](../src/server_environments.py) | 加载并校验 `server_environments.json`，派生服号归属。 | `ServerEnvironments`, `EnvironmentMapping` |
| [muliu_script_registry.py](../src/muliu_script_registry.py) | **预留空壳**：为未来本地脚本注册表保留。当前无实现、无消费者。 | — |

---

## 3. `tools/` — 开发与部署工具

| 工具 | 作用 | 输入 | 输出 | 是否改写仓库文件 | 是否运行期依赖 |
| --- | --- | --- | --- | --- | --- |
| [auto_hotfix_registry.py](../tools/auto_hotfix_registry.py) | **当前热更阈值 Python 注册表**。同时定义执行端 ASCII key + Lua Patch 目标，以及 Planner 可见的中文名 / aliases。 | 无外部输入（代码即数据）。 | 供 `sync_hotfix_planner_catalog.py` 导入；同名阅读副本可由 `auto_hotfix_tool.py` 使用。 | 否 | 否（FastAPI 运行期不导入；它是 catalog 同步主链的资料源）。 |
| [auto_hotfix_tool.py](../tools/auto_hotfix_tool.py) | 游戏服阈值免重启热更工具：生成 Lua Patch、写入 `patch_order_list.json`、调用 `run_patch_game.sh`。 | `config.ini`（服号 → IP）、命令行参数。 | Patch / 热更执行结果。 | 否 | 否（独立 CLI；catalog 期望远端同路径脚本，但仓库不能证明其已部署）。 |
| [sync_hotfix_planner_catalog.py](../tools/sync_hotfix_planner_catalog.py) | 把 `auto_hotfix_registry` 的中文路由和严格 key 白名单同步进 catalog 静态资料，并生成候选 manifest。 | `tools/auto_hotfix_registry.py`、`config/server_environments.json`。 | **改写** `config/muliu_script_catalog.md` 自动区块、候选 manifest。 | **是** | 否（开发期工具）。 |
| [generate_runner_manifest.py](../tools/generate_runner_manifest.py) | 从 catalog 派生**候选** GS-1 runner manifest。 | `config/muliu_script_catalog.md`。 | `deployment/gs1/muliu_runner_manifest.json`。 | **是** | 否（开发期工具）。 |
| [hotfix_threshold_registry.py](../tools/hotfix_threshold_registry.py) | 加载 `config/hotfix_thresholds.json`，做失败关闭的 JSON 形态校验。 | `config/hotfix_thresholds.json`。 | 校验后的元数据对象。 | 否 | **否**（未接入当前主链）。 |

**当前热更主链**（唯一）：

```text
tools/auto_hotfix_registry.py
→ tools/sync_hotfix_planner_catalog.py
→ config/muliu_script_catalog.md
→ /home/serverGeneralScript/auto_hotfix_tool.py
```

`config/hotfix_thresholds.json` + `tools/hotfix_threshold_registry.py` 是**另一套未接入的候选资料模型**，两者不能并称“唯一来源”。

---

## 4. `knowledge/scripts/` — 脚本阅读副本

> **重要边界：** 本目录是**脱敏阅读副本**，供知识问答和文档核对使用。它**不等于** GS-1 上 `/home/serverGeneralScript/` 的实际文件，也不代表机器人能调用的范围。机器人能调用什么，只看 catalog。

### 4.1 已登记调用合同且有阅读副本的脚本

下表列出本仓库存在阅读副本、且 catalog 已登记的调用合同。**阅读副本 + catalog 登记不证明 GS-1 已部署该脚本**；目标文件、hash、权限与可达性仍由环境维护者独立核验。

| 副本 | catalog 中的远端路径 | 作用 | 风险 |
| --- | --- | --- | --- |
| `basic_info.sh` | `/home/serverGeneralScript/basic_info.sh` | 查询单服基础信息、当前时间、按剧本反查服务器、玩家赛季数据。 | `read` |
| `shutdown` | `/home/serverGeneralScript/shutdown` | 关闭指定测试服 game 与 Zone 服务。 | `write` |
| `start` | `/home/serverGeneralScript/start` | 启动指定测试服的 Zone 与游戏逻辑服务。 | `write` |
| `clear` | `/home/serverGeneralScript/clear` | 停服并清理逻辑与 Zone 数据（破坏性）。 | `write` |
| `modify_game_config.sh` | `/home/serverGeneralScript/modify_game_config.sh` | 改写目标服赛季与剧本配置。 | `write` |
| `starttime` | `/home/serverGeneralScript/starttime` | 检查/设置 Debug 开服时间。 | `write` |
| `cc_patch.py` | `/home/serverGeneralScript/cc_patch.py` | 查询、对比、备份、恢复、清理、热更 Patch 列表。 | `read` / `write` |
| `batch_server_query.py` | `/home/serverGeneralScript/batch_server_query.py` | 批量查询多服代码版本。 | `read` |
| `clear_logic_game.py` | `/home/serverGeneralScript/clear_logic_game.py` | 单独清理逻辑服数据库。 | `write` |
| `clear_zone.py` | `/home/serverGeneralScript/clear_zone.py` | 单独清理 Zone 战区数据库。 | `write` |
| `cleardb.py` | `/home/serverGeneralScript/cleardb.py` | 底层数据库单项重置与集合清理。 | `write` |
| `control_is_pub.sh` | `/home/serverGeneralScript/control_is_pub.sh` | 控制发布模式、GM 面板或外部体验开服；只允许 `k`、`re`、`op` 三种精确参数形态。 | `write` |
| `auto_hotfix_tool.py` | `/home/serverGeneralScript/auto_hotfix_tool.py` | 阈值免重启热更。 | `write` |
| `stop_combine_tool.sh` | `/home/serverGeneralScript/stop_combine_tool.sh` | 在**合服目标服**执行 `kill_combine.sh` 关闭合服进程。 | `write` |
| `exc_server_merge.sh` | `/home/serverGeneralScript/exc_server_merge.sh` | 启动单源单目标合服。 | `write` |

### 4.2 仅作阅读/参考的脚本

| 文件 | 作用 | 说明 |
| --- | --- | --- |
| `start_combine_tool.sh` | 启动合服进程。 | 由 `exc_server_merge.sh` 链路使用；catalog 未单独登记。 |
| `gen_combine_config.py`、`combine_demo_new.lua` | 合服配置生成与其 Lua 模板。 | 被 `exc_server_merge.sh` 调用；非独立入口。 |
| `config.ini` | 服号 → 目标主机的映射配置。 | **敏感**：含内网主机标识。由脚本读取，不进入模型 / 飞书 / Git。 |
| `auto_hotfix_registry.py` | `tools/` 同名文件的镜像副本。 | 供 GS-1 侧 `auto_hotfix_tool.py` 导入；以 `tools/` 为准。 |

---

## 5. `deployment/gs1/` — 候选部署资料

> **边界：** 本目录所有内容都是**仓库候选部署物**，不是 GS-1 当前状态的证明。文件存在 ≠ 已部署 ≠ dispatcher 消费它。

| 文件 | 作用 | 谁生成 | 谁消费 | 当前状态 |
| --- | --- | --- | --- | --- |
| `README.md` | 候选部署物清单与人工核验清单。 | 人工维护 | 环境维护者 | 当前入口。 |
| `muliu_runner_manifest.json` | catalog 派生的 runner manifest（`path + args` 白名单候选）。 | `tools/generate_runner_manifest.py` | **本仓库运行期不读取**；远端是否消费需人工核验。 | 候选 artifact。 |
| `server_log_query.py` | 受控日志诊断候选脚本（固定 supervisor 日志档案）。 | 人工编写 | 待部署到 GS-1 | 候选，未证实已部署。 |
| `batch_server_query.py` | 批量版本查询候选脚本。 | 人工编写 | 待部署到 GS-1 | 候选，未证实已部署。 |

---

## 6. `tests/` — 测试覆盖

| 测试文件 | 覆盖对象 |
| --- | --- |
| `test_config.py` | 配置加载、校验、默认值、`execution_interpretation` 段。 |
| `test_app.py` | FastAPI 三种角色入口、健康检查、Worker 结果路由、AI 研读接线。 |
| `test_ai_client.py` | OpenAI 兼容与 Claude Messages 两种客户端的请求构造。 |
| `test_event_handler.py` | 飞书事件、卡片回调、去重与分发。 |
| `test_message_jobs.py` | 任务状态机、租约、`route_region` 持久化、未知终态。 |
| `test_message_worker.py` | 消息处理主循环、AI 结果研读、超时回退、敏感信息遮罩。 |
| `test_operation_message.py` | 计划卡、确认卡、结果卡的格式化与遮罩。 |
| `test_operation_flow.py` | 端到端：飞书请求 → 计划 → 确认 → 执行 → 回写。 |
| `test_muliu_planner.py` | AI 计划生成与受限 JSON 决策。 |
| `test_muliu_plan.py` | 计划 JSON 解析与 `route_scope` 校验。 |
| `test_muliu_script_catalog.py` | catalog 精确调用合同匹配与拒绝。 |
| `test_muliu_firewall.py` | 防火墙安全属性检查。 |
| `test_muliu_executor.py` | Muliu Task 89 提交、轮询、终态解释。 |
| `test_muliu_worker.py` | Worker claim、重校验、结果回传。 |
| `test_muliu_intent.py` | 非权威意图提示。 |
| `test_server_environments.py` | 服号归属派生、跨区域拒绝、`route_region` 推导。 |
| `test_server_log_query.py` | 日志查询候选脚本的参数约束与输出脱敏。 |
| `test_auto_hotfix_tool.py` | 热更注册表映射、Lua Patch 生成、Planner 中文路由。 |
| `test_hotfix_threshold_registry.py` | JSON 注册表加载与失败关闭校验。 |
| `test_generate_runner_manifest.py` | 候选 manifest 与 catalog 一致性。 |
| `test_sync_hotfix_planner_catalog.py` | catalog 同步产物与标记完整性。 |
| `test_knowledge_base.py` / `test_script_knowledge_base.py` | 知识检索。 |
| `test_conversation.py` / `test_conversation_log.py` | 会话历史与对话归档。 |
| `test_feishu_client.py` | 飞书 API 封装。 |

---

## 7. 修改配置或脚本时的最小检查

```bash
python -m pytest -v
python -m compileall src tests
python tools/generate_runner_manifest.py --check
python tools/sync_hotfix_planner_catalog.py --check
git diff --check
```

这些检查只说明本地代码与候选资料的一致性；不能替代实际部署、Muliu 调用、目标服状态或游戏内效果核验。

---

## 8. 维护规则

1. 新增或修改配置字段：同步更新 [src/config.py](../src/config.py) 的校验、对应 `config.*.example.json`、本文件与相关测试。
2. 新增可调用脚本：先登记 [config/muliu_script_catalog.md](../config/muliu_script_catalog.md) 的精确合同，再实现 / 部署，最后补 `knowledge/docs/` 的用途说明；**知识文档不产生执行许可**。
3. 改变热更注册表：确认它属于 Python 主链还是 JSON 候选链，不要把两者混称唯一来源。
4. 改变路由或服号归属：只改 `config/server_environments.json`，并重新运行 catalog 同步。
5. 任何“已部署 / 已生效”的表述，必须有独立环境证据；否则写“仓库候选物，待人工核验”。
