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

### AI 模型池

`ai` 段是一个**模型池**，随机/轮询分摊调用负载。`models` 数组里每项形状一致，**无主次之分**；共享生成参数放在独立的 `defaults` 块。

```json
"ai": {
  "strategy": "random",
  "defaults": {
    "system_prompt": "你是一个飞书里的 AI 助手，请用简洁、准确的中文回答。",
    "temperature": 0.2,
    "max_tokens": 2048,
    "timeout_seconds": 60
  },
  "models": [
    { "provider": "openai_compatible", "base_url": "https://模型A网关", "api_key": "...", "model": "模型A名" },
    { "provider": "openai_compatible", "base_url": "https://api.minimax.chat/v1", "api_key": "...", "model": "MiniMax-3.1-Flash" }
  ]
}
```

**三种 `strategy`（区别只在首选顺序，任一模型失败都会继续试下一个）：**

| 策略 | 首选 | 适合 |
| --- | --- | --- |
| `random` | 随机打乱 | 平均负载 |
| `round_robin` | 轮转起点 | 平均负载、可预测 |
| `fallback` | 恒从第一个开始 | 质量优先，前一个挂了才换 |

边界：

- `models[]` 每项只需 `provider` / `base_url` / `api_key` / `model`；生成参数从 `defaults` 继承；
- 某个模型要单独调参数（如更长的 `max_tokens`），在它自己那项加同名字段即可**覆盖 `defaults`**；
- **扩展就是加一项**——第 3、4 个模型往 `models` 数组塞对象即可，代码不用改；
- 池内每个模型各自带 5xx/超时重试（见 `_post_with_retry`），重试耗尽后才换下一个模型；
- **单模型旧写法兼容**：`ai` 直接写 `provider` / `base_url` 等（无 `models` 数组）时，视为单条目 `fallback` 池，现有配置不改也能跑；
- **不标注所用模型**：MiniMax 3.1 Flash 用于语言格式转化足够可靠，无需在回复里区分（用户 2026-10-29 决定）。如后续引入明显更弱的模型，再考虑按模型加标注；
- worker 角色可完全不配 `ai` 段（它不调用 AI）。

### 按用途分池（可选）

不同用途可以交给不同的池：便宜模型处理量大、不可复用的内容，强模型处理会沉淀为模板的研读。

```json
"ai": {
  "defaults": { "temperature": 0.2, "max_tokens": 2048, "timeout_seconds": 60 },
  "pools": {
    "fast":   { "strategy": "random",   "defaults": { "timeout_seconds": 20 }, "models": [ {…Luna…}, {…Flash…} ] },
    "strong": { "strategy": "fallback", "models": [ {…Terra…} ] }
  },
  "routing": { "planning": "fast", "chat": "fast", "interpretation": "strong" }
}
```

| 用途 | 内容 | 调用位置 |
| --- | --- | --- |
| `planning` | 自然语言 → 受限计划参数 | `MuliuPlanner` |
| `chat` | 聊天与知识问答 | 消息主循环 |
| `interpretation` | 执行结果研读：失败诊断、模板生成、查询解读 | 后台研读 |

- 池自己的 `defaults` 覆盖 `ai.defaults`（例如 fast 池更短的超时）；
- `routing` 未写的用途走 `routing.default`，没写 `default` 时走第一个池；
- `pools` 与 `models` 不能同时出现；旧的 `models` / 单模型写法仍是「所有用途共用一个池」；
- 共用同一个池的用途共用同一个客户端；
- 计划生成用弱模型不会产生不安全命令（catalog 合同 + firewall 拦截），风险是合法但读错语义的参数（合服方向、赛季/剧本号对调），由确认卡把关；错误偏多时把 `routing.planning` 改回强池即可。

### 执行结果展示与模板审批页

设计：[2026-11-02-execution-report-library.md](superpowers/specs/2026-11-02-execution-report-library.md)

```json
"execution_interpretation": {
  "enabled": true,
  "background_timeout_seconds": 45,
  "server_environments_path": "config/server_environments.json",
  "max_execution_data_chars": 12000,
  "max_concurrent": 2,
  "artifact_retention_days": 90
},
"admin": { "enabled": true, "port": 8901 }
```

| 终态 | 展示 | AI |
| --- | --- | --- |
| 写操作成功 / 结果未知 | 程序模板 | 不调用 |
| 查询（全部步骤 `risk=read`）成功 | 先发程序模板；审批页开关为「AI 后台解读」时后台读日志后更新卡片 | 按开关 |
| 失败且命中模板（待审核或可用） | 失败原因 + 模板诊断 + 来源标签 | 不调用 |
| 失败且未命中 | 先发失败卡，后台 AI 诊断 → 自动收集为待审核模板 | 后台一次 |

- `background_timeout_seconds`：后台研读总预算，**不阻塞** Worker `/result` 与首张结果卡。旧字段 `timeout_seconds` 已废弃并被忽略。
- `admin.port`：审批页端口，固定绑定 `127.0.0.1`，不可配置 host；安全组不要放行。访问：`ssh -L 8901:127.0.0.1:8901 <gateway>` 后浏览器开 `http://127.0.0.1:8901`。
- 公网访问（可选）：`https://ops.ttlink.asia` 经 Cloudflare Access（`owner-only` 策略、Google 登录）→ nginx → `127.0.0.1:8901`。审批页会**自己校验 Access JWT**（`admin.access_team_domain` + `admin.access_aud`，RS256、AUD、签发方、过期时间），所以直接请求源站 IP 也绕不过去：
  - nginx 对每个转发请求强制设置 `X-Admin-Proxy: cloudflare-access` 与 `Host: 127.0.0.1`；带该标记的请求必须携带有效的 `Cf-Access-Jwt-Assertion`（或 `CF_Authorization` cookie），否则 403；未配置 `access_*` 时公网请求一律 403；
  - SSH 隧道直连 `127.0.0.1:8901` 不带该标记，照常可用；
  - 审批页的改动日志记录操作人邮箱（`operator=`）；
  - 依赖 `PyJWT[crypto]`，只有 Gateway 需要（`requirements-gateway.txt`）；创建校验器时才导入，Worker 不装也能启动。
- 发起人昵称：收到群消息时后台调飞书群成员接口（应用需开通 `im:chat.members:read`），按 open_id 缓存，存入 `message_jobs.user_name`；只在审批页显示，不进 AI 提示词与飞书卡片。查询失败不影响请求处理。
- 需求分析模型与耗时：规划器记录每次 AI 调用的模型和耗时，存入 `message_jobs.planning_model / planning_ms / planning_calls`。
- 模板库、研读任务、执行记录与 `message_jobs` 同一个 SQLite 文件；执行记录按 `artifact_retention_days` 清理，模板不清理。

### 清档企微推送（可选）

计划里含清档脚本时，阿里云 Gateway 直接向企微群机器人推送两次消息（默认 @所有人），**按区服分群**。是否清档**按计划中的脚本路径判断**，不由 AI 判断。

```json
"clear_notification": {
  "enabled": true,
  "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…默认群（可选）",
  "region_webhooks": {
    "cn": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…国服群",
    "hmt": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…港澳台群"
  },
  "cc_webhooks": ["https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…QA 内部群"],
  "mention_all": true,
  "timeout_seconds": 5
}
```

| 时机 | 消息示例 |
| --- | --- |
| AI 生成含清档的计划、确认卡已发出 | `内网：沈涛 准备清档 港澳台繁体 5001 服（已生成计划，等待确认）` |
| 组员确认执行、任务进入执行队列 | `内网：沈涛 正在操作清档 港澳台繁体 5001 服（已确认，开始执行）` |

每条消息下面附「计划：…」「时间：…（北京时间）」两行。

- `region_webhooks` 的 key 是 `server_environments.json` 中 `regions` 的区服名（`cn`、`hmt`、`global`、`kr`、`vn`）；
- 一次清档涉及多个区服时按群拆开发送，每个群只收到自己负责的服号；
- 区服没有单独配置时发到 `webhook_url`（默认群）；默认群也没填时不发，只记日志；
- `cc_webhooks` 抄送群：每次清档都发，不分区服，一次只发一条，列出全部服号（如 `国服 2213，港澳台繁体 5001、5002`）；抄送群地址与某区服群相同时，该群只收合并的那一条；
- `enabled` 为 true 时 `webhook_url`、`region_webhooks`、`cc_webhooks` 至少填一个；
- 只配置在 **Gateway**；Worker 不参与，不用改；
- 企微地址含 key，属于密钥：只写在服务器的 `config.json`（已 `.gitignore`），不要写进示例配置或提交；
- `trigger_paths` 默认包含 `clear`、`clear_logic_game.py`、`clear_zone.py`、`cleardb.py`；
- 推送在后台进行，失败（网络错误、企微返回非 0 errcode）只记日志，不影响确认卡和执行；
- 组员取消或确认超时时，只会有「准备清档」一条。

### 木流请求超时、重试与「继续执行剩余步骤」

执行链按「是否已提交到木流」区分处理，已提交的写操作永不自动重放：

| 情况 | 处理 |
| --- | --- |
| 提交前的请求失败（登录、读任务版本、保存参数；超时 / 连接中断 / 5xx） | 自动重试，共 3 次，间隔 `log_wait_seconds` |
| 提交请求无响应 | 每步提交前记下木流最新运行标记 `MULIU_RUN_VERSION`；之后最多轮询 6 次：出现新运行 → 视为已提交、继续等结果；没出现 → 判定「本步未执行」；日志读不到 → 结果未知 |
| 轮询日志超时 / 5xx | 继续重试直到 `log_completion_timeout_seconds`；仍带上一步运行标记的日志一律忽略，避免误把上一步的结果当成本步 |
| 只读查询（全部步骤 `risk=read`）因木流通信失败 | Worker 自动整体重跑一次 |
| 木流会话失效（重定向到 `/muliu/login`，或返回登录页） | 读请求、保存参数：重新登录后重试一次；提交请求：不直接重提，按运行标记核对（核对时会自动重新登录） |
| 第 N 步**确认未提交** | 结果为失败，保留前面已完成的步骤；失败卡出现「继续执行剩余 K 步」按钮 |
| 第 N 步已提交但结果未知 | 不重试，提示先查服务器状态 |

「继续执行剩余步骤」：

- 剩余步骤由程序从原计划按步骤截取，不经过 AI；点击后重新做合同、防火墙与路由校验，路由必须与原计划一致；
- 仅原发起人可点，30 分钟内有效，只能使用一次；生成新任务 `<原 message_id>-r<N>` 直接进入执行队列，结果更新同一张卡片；
- 剩余步骤含清档时，照常推送企微「正在操作清档」。

木流 HTTP 错误只保留状态码与页面标题，内部域名（`*.newslg.com`）与服务器主机名（`nslg-ali-…`）会脱敏。木流账号只支持单会话：机器人账号不要在别处同时登录。失败原因与未知原因现在带步骤前缀，例如 `第 2/4 步「设置赛季 7 剧本 14」未执行：…`。读超时会被识别为木流通信失败。Worker 遇到未预期的异常时会记录完整堆栈。

**部署顺序：先更新 Gateway，再更新 Worker。** Worker 新增回传字段 `resumable_from_step`；旧 Gateway 不认识这个字段，会以 422 拒收结果。

## 2. `src/` — 运行期 Python 模块

`src/utils/` 放与飞书、Muliu、配置都无关的通用工具（如时间换算）；它们不依赖 `src/` 里的业务模块，可单独测试与复用。新增此类工具一律放这里。

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
| [message_worker.py](../src/message_worker.py) | 消息处理主循环：AI 计划生成、确认卡投递、任务领取、结果回写；结果展示先投递可信卡，再按需后台 AI 研读并只更新卡片说明。 | `MessageJobProcessor`, `MessageJobQueue`, `present_execution_result` |
| [execution_report.py](../src/execution_report.py) | 执行结果展示分流与典型故障模板库：错误特征（匹配键）计算、模板 / 后台研读任务 / 执行记录 / 运行时设置的 SQLite 存储。不改变任何终态。 | `compute_error_signature`, `ExecutionReportStore`, `is_read_only_result` |
| [plan_board.py](../src/plan_board.py) / [plan_page.py](../src/plan_page.py) | 项目计划看板：SQLite 存储（步骤、元信息、修改历史），首次从 `docs/plan-board.json` 导入；`/plan` 列表页与 `/api/plan` 读写接口（AI 读取入口）。 | `PlanStore`, `PLAN_HTML` |
| [hotfix_thresholds.py](../src/hotfix_thresholds.py) / [threshold_page.py](../src/threshold_page.py) | 热更阈值注册表：SQLite 存储（首次从 `config/hotfix_thresholds.seed.json` 导入）、确定性校验、已执行保护；`HotfixPlannerCatalog` 给 Planner 实时生成中文路由与 key 白名单，`expand_plan` 把 `-k <key>` 展开成 `-k <字段> --table <表>`；`/thresholds` 页面与 `/api/thresholds` 接口（含 AI 审核建议）。 | `ThresholdStore`, `HotfixPlannerCatalog`, `THRESHOLDS_HTML` |
| [qa_library.py](../src/qa_library.py) | 知识问答评审与归档：问答记录、多轮纠正、已审核问答库、资料修改建议的存储；归档检索打分（字符二元组覆盖率 + 脚本 / 类别加分）；问答提示词与 AI 调用（回答、纠正重答、归档草稿）。资料优先级：catalog > 已审核问答 > knowledge/docs。 | `QaStore`, `QaService`, `rank_archive` |
| [admin_app.py](../src/admin_app.py) | 模板审批页：待审核 / 可用模板两个页签（通过 / 退回 / 删除 / 编辑 / 批量）、只读「最近执行」页签（发起人昵称、需求分析模型与耗时、终态、说明来源、结果研读模型与耗时、AI 说明）、查询成功模式开关。独立服务，**只监听 127.0.0.1**，经 SSH 端口转发访问。 | `create_admin_app`, `AdminServer` |
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
→ message_worker.py      （回写状态 + 结果展示分流）
→ execution_report.py    （故障模板库 / 后台研读任务）
```

| 模块 | 职责 | 关键符号 | 不负责什么 |
| --- | --- | --- | --- |
| [muliu_intent.py](../src/muliu_intent.py) | 非权威请求提示，供观测与 UI 文案使用。 | `MuliuIntentRouter` | **不**决定是否执行。 |
| [muliu_planner.py](../src/muliu_planner.py) | 把飞书请求转成注册表校验过的 Muliu 决策（operation / knowledge / clarify / reject）。 | `MuliuPlanner`, `MuliuPlanGeneration` | **不**执行脚本。 |
| [utils/text_match.py](../src/utils/text_match.py) | 中英文混合短问句的词项切分：英文单词 + 中文字符二元组（去掉常见无信息二元组），用于资料检索与归档问答检索。 | `terms`, `coverage`, `normalize` | **不**做语义理解。 |
| [utils/time_text.py](../src/utils/time_text.py) | 时间文本 → 北京时间 Unix 时间戳的确定性换算。规划器把合同 `{timestamp}` 位置上 AI 写出的时间文本（如 `2026-11-07T10:00`）换算成数字，并在步骤说明追加可读时间；没有具体时刻、相对日期、不存在的日期一律拒绝，交给 AI 澄清。AI 不再自行换算时间戳。 | `parse_time_text`, `muliu_planner.convert_time_arguments` | **不**猜测缺失的时刻或时区。 |
| [muliu_plan.py](../src/muliu_plan.py) | AI 生成计划的严格 JSON 解析：`kind`、`route_scope`、`steps[]`。 | `parse_plan`, `MuliuPlan`, `MuliuStep` | **不**判断合同是否合法。 |
| [muliu_script_catalog.py](../src/muliu_script_catalog.py) | 解析并校验 catalog 中的精确调用合同（`path + args` 模板 + 变量正则 + risk）。 | `MuliuScriptCatalog`, `MuliuCallContracts`, `parse_call_contracts` | **不**推断未登记的脚本或参数。 |
| [muliu_firewall.py](../src/muliu_firewall.py) | 合同校验后的安全属性检查：根目录约束、长度限制、Shell 注入原语拒绝。 | `MuliuFirewall` | **不**是第二份业务白名单。 |
| [muliu_routing.py](../src/muliu_routing.py) | 从已验证计划派生唯一 `route_region`（cn / overseas），失败关闭。 | `MuliuPlanRouteResolver` | **不**生成脚本参数。 |
| [muliu_executor.py](../src/muliu_executor.py) | 调用 Muliu Task 89：提交 `{"path","args"}`，轮询日志，解释终态。 | `MuliuExecutor`, `MuliuStepResult` | **不**修改计划或扩大能力。 |
| [muliu_worker.py](../src/muliu_worker.py) | 内网 Worker：主动连接 Gateway，claim 已确认任务，本地重校验后执行。 | `MuliuWorker`, `GatewayWorkerClient` | **不**持有飞书 / AI 凭据。 |
| [clear_notifier.py](../src/clear_notifier.py) | 清档企微推送：计划含清档脚本时，在生成计划和确认执行两个时机推送企微群消息。 | `ClearNotifier` | **不**影响操作执行结果。 |
| [worker_protocol.py](../src/worker_protocol.py) | Gateway 与 Worker 之间的结果序列化、反序列化与计划比对。 | `serialize_execution_result`, `validate_execution_result_matches_plan` | **不**接触 Muliu。 |

### 2.4 知识问答与环境

| 模块 | 职责 | 关键符号 |
| --- | --- | --- |
| [knowledge_base.py](../src/knowledge_base.py) | 双链 vault 知识库检索（可选启用）。 | `DualChainVaultKnowledgeBase` |
| [script_knowledge_base.py](../src/script_knowledge_base.py) | 只检索本地脱敏脚本文档（`knowledge/index.md`、`knowledge/docs/*.md`），回答“脚本是干什么的”。打分：中文按字符二元组、按文档频率加权（常见词几乎不计分）；`index.md` 表格的「用途 / 关键词」列作为对应文档的同义说法并入标题，命中加倍。 | `ScriptKnowledgeBase` |
| [server_environments.py](../src/server_environments.py) | 加载并校验 `server_environments.json`，派生服号归属。 | `ServerEnvironments`, `EnvironmentMapping` |
| [muliu_script_registry.py](../src/muliu_script_registry.py) | **预留空壳**：为未来本地脚本注册表保留。当前无实现、无消费者。 | — |

---

## 3. `tools/` — 开发与部署工具

| 工具 | 作用 | 输入 | 输出 | 是否改写仓库文件 | 是否运行期依赖 |
| --- | --- | --- | --- | --- | --- |
| [auto_hotfix_registry.py](../tools/auto_hotfix_registry.py) | GS-1 侧 `auto_hotfix_tool.py` 导入的旧版字段表（24 条）。机器人**不再读取**；新阈值走 Gateway 注册表，靠 `--table` 传生效路径，不需要改它。 | 无外部输入（代码即数据）。 | 镜像到 `knowledge/scripts/`，供 GS-1 部署参考。 | 否 | 否。 |
| [auto_hotfix_tool.py](../tools/auto_hotfix_tool.py) | 游戏服阈值免重启热更工具：生成 Lua Patch、写入 `patch_order_list.json`、调用 `run_patch_game.sh`。 | `config.ini`（服号 → IP）、命令行参数。 | Patch / 热更执行结果。 | 否 | 否（独立 CLI；catalog 期望远端同路径脚本，但仓库不能证明其已部署）。 |
| [sync_hotfix_planner_catalog.py](../tools/sync_hotfix_planner_catalog.py) | 把 `config/server_environments.json` 的环境映射同步进 catalog 静态资料，并生成候选 manifest、镜像 `auto_hotfix_registry.py`。热更阈值不经过它。 | `config/server_environments.json`。 | **改写** `config/muliu_script_catalog.md` 环境区块、候选 manifest。 | **是** | 否（开发期工具）。 |
| [plan_board.py](../tools/plan_board.py) | 项目计划快照：`export` 导出 JSON、`import` 恢复、`summary` 文字概览。 | Gateway SQLite。 | 否（export 输出到标准输出）。 | 否 | 否（运维工具）。 |
| [doc_fixes.py](../tools/doc_fixes.py) | 已采纳的资料修改建议回写仓库：Gateway 上 `export` 导出 JSON；开发机 `apply` 修改 `knowledge/index.md`、`knowledge/docs/*.md`（原文需恰好出现 1 次，保留换行风格）；Gateway `git pull` 后 `mark-applied`。 | Gateway SQLite、导出的 JSON。 | 改写 `knowledge/` 下的 MD。 | **是**（apply） | 否（运维工具）。 |
| [generate_runner_manifest.py](../tools/generate_runner_manifest.py) | 从 catalog 派生**候选** GS-1 runner manifest。 | `config/muliu_script_catalog.md`。 | `deployment/gs1/muliu_runner_manifest.json`。 | **是** | 否（开发期工具）。 |

**当前热更主链**（唯一，2026-11-04 起；设计见 `docs/superpowers/specs/2026-10-30-threshold-registry-web.md`）：

```text
Gateway SQLite 热更阈值注册表（网页 /thresholds 编辑，src/hotfix_thresholds.py）
→ 每次规划实时生成 Planner 中文路由 + key 白名单（-k <key>）
→ Gateway 确定性展开：-k <真实字段名> --table <表名> -v <值> --patch-type <类型>
→ 静态 catalog 执行形态合同（Worker / 防火墙结构校验）
→ /home/serverGeneralScript/auto_hotfix_tool.py（GS-1 现有版本，不需更新）
```

`config/hotfix_thresholds.json` 与 `tools/hotfix_threshold_registry.py` 是**已否决的旧方案遗留物**：只在原开发机本地存在，已被 `.gitignore` 忽略，不在仓库里，也没有任何运行代码引用（对应测试已于 2026-11-04 删除）。STEP 22 已用 Gateway SQLite 另起实现，种子数据见 `config/hotfix_thresholds.seed.json`。

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
| `test_config.py` | 配置加载、校验、默认值、`execution_interpretation` 与 `admin` 段。 |
| `test_app.py` | FastAPI 三种角色入口、健康检查、Worker 结果路由、AI 研读接线。 |
| `test_ai_client.py` | OpenAI 兼容与 Claude Messages 两种客户端的请求构造。 |
| `test_event_handler.py` | 飞书事件、卡片回调、去重与分发。 |
| `test_message_jobs.py` | 任务状态机、租约、`route_region` 持久化、未知终态。 |
| `test_message_worker.py` | 消息处理主循环；结果展示分流（写成功/未知零 AI、查询成功后台研读与模式开关、失败模板复用、未命中收集、超时保留可信卡、重启恢复）。 |
| `test_execution_report.py` | 错误特征稳定性（跨服号同键、不同错误不同键）、归一化、只读判定、模板生命周期、恢复与清理、落库脱敏。 |
| `test_admin_app.py` | 审批页 Host / 请求头防护、通过 / 退回 / 编辑 / 删除、模式切换、只绑定 127.0.0.1。 |
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
| `test_generate_runner_manifest.py` | 候选 manifest 与 catalog 一致性。 |
| `test_sync_hotfix_planner_catalog.py` | catalog 环境映射同步产物、执行形态热更合同。 |
| `test_gs1_inventory.py` | GS-1 盘点脚本（负责人自用）：不泄露 `config.ini` 值与文件内容、只接受 `summary`、不进 catalog / 知识库。 |
| `test_hotfix_thresholds.py` | 热更阈值注册表：种子迁移、确定性校验、已执行保护、key → 字段 / 表展开、Planner 视图、网页接口。 |
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
