# 当前项目说明

> **维护状态：当前事实入口（持续更新）**  
> **最后核对：2026-10-27**  
> 本文描述仓库代码、配置与已确认环境事实；不把设计目标、仓库候选部署物或本地测试结论写成远端已部署事实。

## 1. 项目目的

本项目是一个面向测试服运维的飞书机器人：将群内自然语言请求转化为**已登记的精确脚本调用合同**，经原请求人确认后，通过 Muliu Task 89 调用 GS-1 上已有的受控入口执行，并将可信终态回传飞书。

它也保留脱敏脚本知识问答能力，但知识问答不授予执行权限。

```text
飞书请求
→ AI 受限 JSON 计划
→ 调用合同 + 防火墙校验
→ 路由派生与确认卡
→ Gateway / Worker 队列
→ Muliu Task 89
→ GS-1 dispatcher
→ 已部署的目标脚本
→ 可信终态与结果回传
```

## 2. 当前运行架构

### 角色

|角色|职责|不应持有的内容|
|---|---|---|
|`local`|本地开发或单机验证：飞书入口、AI、SQLite、确认与 MuliuExecutor 同进程运行。|不适合作为公网生产网关的替代描述。|
|`gateway`|接收飞书回调；生成计划；保存 SQLite 状态；展示确认卡；向 Worker 分发已确认计划；回写结果。|Muliu URL、账号、密码、Task ID。|
|`worker`|仅主动 HTTPS 连接 Gateway；领取已确认计划；重新校验与重新路由；使用本地私有 Muliu profile 调用 Task 89。|飞书 App Secret、AI API Key。|
|GS-1 / Task 89|接收 `path + args`，通过实际 dispatcher 调用 `/home/serverGeneralScript` 下脚本。|不是本仓库 Python 运行期的一部分。|

### 信任与数据流

```text
群成员自然语言请求
→ Gateway 保存原始请求与受限 plan
→ 原请求人确认卡
→ Gateway 持久化 route_region 并投递任务
→ Worker claim
→ Worker 重新 parse plan、重新过 firewall、重新推导 route_region
→ Worker 本机按 cn / overseas 选择私有 Muliu profile
→ Task 89 保存 {"path", "args"} 并执行
→ Worker 回传受限执行结果
→ Gateway 绑定已确认计划、更新卡片与审计记录
```

- Gateway 到 Worker 只下发已持久化的 `plan_json` 与 `route_region`；真实 URL、账号、密码、Cookie、Token、IP、Task ID 和私有连接配置均不得进入 AI、飞书卡片、计划、Gateway API 审计文本或 Git。确认卡目前展示固定逻辑标识 `route-cn` / `route-overseas`；其公开级别仍是已记录的规则冲突，不得当作已合规处理。
- Worker 的 `cn` / `overseas` 路由必须精确配置；缺少 profile 或配置不完整时必须失败，不能静默回退。
- 同一 Task 89 使用全局单租约，避免“保存参数 → 执行”被其他任务插入覆盖。

相关实现：[src/app.py](../../src/app.py)、[src/message_worker.py](../../src/message_worker.py)、[src/muliu_worker.py](../../src/muliu_worker.py)、[src/message_jobs.py](../../src/message_jobs.py)、[src/muliu_routing.py](../../src/muliu_routing.py)。

## 3. 当前执行权威与安全边界

### 机器人侧唯一执行合同

[config/muliu_script_catalog.md](../../config/muliu_script_catalog.md) 中的 `MULIU_CALL_CONTRACTS` 是机器人侧允许执行的唯一权威。每一步必须精确匹配已登记的：

```text
path + args 模板 + 变量正则 + risk
```

- AI 不能按脚本名、文件扩展名、自然语言近义词或历史资料推断额外参数。
- `runner` 是 catalog 的元数据与派生产物内容；Task 89 payload 不携带 `runner`。
- `risk` 只能是 `read` 或 `write`；写操作仍须确认卡。
- 知识文档说明用途、影响和前后置条件；它们不维护另一套“允许/禁止执行”结论。

相关实现：[src/muliu_script_catalog.py](../../src/muliu_script_catalog.py)、[src/muliu_firewall.py](../../src/muliu_firewall.py)。

### 防火墙

在精确合同校验之后，防火墙继续负责：

- 仅接受 `kind: "operation"` 的有步骤计划；
- 限制步骤数、参数数和参数长度；
- 限制脚本位于 `/home/serverGeneralScript`；
- 拒绝 NUL、换行、Shell 控制符与 `-c` / `--command` / `-e` / `--eval`；
- 应用部署配置的阻断关键字和模式。

防火墙不是第二份脚本业务白名单，也不会根据扩展名推导许可。

### 路由

`route_scope` 与 `route_region` 不同：

|字段|来源与用途|是否进入脚本 argv|
|---|---|---|
|`route_scope`|仅限已登记、无服号、只读范围查询的计划根字段；值只能为 `cn` 或 `overseas`。|否。|
|`route_region`|Gateway 从已验证计划派生并持久化的可信 Worker 路由快照；Worker 必须本地重新派生并严格比对。|否。|

目标服号只从已匹配调用合同中的声明变量提取；不会扫描自然语言、说明文本或普通数字参数。跨 `cn` / `overseas` 的单计划、未知服号或无显式范围的范围查询都应被拒绝。

## 4. 确认、状态与结果表述

### 确认原则

确认卡按钮只携带请求/确认身份引用。脚本路径、参数、工作目录、环境变量和 Shell 均从 SQLite 中已保存的计划读取，且只允许原请求人对未过期的待确认卡片确认或取消。

### 关键状态

```text
awaiting_confirmation
→ confirmed
→ submission_in_progress
→ queued_for_worker / leased
→ done | failed | submitted_result_unknown
```

`submitted_result_unknown` 表示：操作已经提交或提交阶段无法可靠判断，但没有取得可信结果。它不是失败，也**绝不自动重放**；用户不应重复确认同一操作。

### 成功语义

- Task 89 / dispatcher 的终态成功，仅能证明该层报告的脚本执行成功。
- 对 `start`，执行器还会等待配置时间并使用固定 `basic_info.sh` 核验；仅独立行出现“开服成功”才报告起服完成。
- 其他写操作除非有单独后置验证证据，不能表述为“游戏内已生效”“数据已验证”或“业务已完成”。
- AI 对结果的研读是辅助展示。持久化终态不会被 AI 文本改写；但当前代码未校验 AI 摘要的标签或其与终态的一致性，且结果卡会优先展示 AI 摘要。任何冲突以持久化终态、受控日志和人工环境证据为准；详情见 [AI 研读记录](../superpowers/specs/2026-10-09-ai-result-interpretation-design.md)。

## 5. Task 89、GS-1 与 runner manifest 的实际边界

### 可由仓库代码证明的事实

1. [src/muliu_executor.py](../../src/muliu_executor.py) 向 Task 89 保存的 payload 只有：

   ```json
   {"path":"/home/serverGeneralScript/...","args":["..."]}
   ```

2. [deployment/gs1/muliu_runner_manifest.json](../../deployment/gs1/muliu_runner_manifest.json) 是由 catalog 派生的、无敏感信息的**候选部署 artifact**；[tools/generate_runner_manifest.py](../../tools/generate_runner_manifest.py) 可检查它是否与当前 catalog 一致。
3. 本仓库应用运行期不读取这个 manifest。

### 不能由仓库证明的事实

以下必须由具备 GS-1 权限的维护者在真实 Task 89 代码包中核验：

- GS-1 是否存在该 manifest；
- dispatcher 是否消费该 manifest；
- dispatcher 当前版本、hash 与实际 runner 策略；
- 新脚本是否已复制至 `/home/serverGeneralScript`；
- 目标服 SSH、权限、日志布局与业务效果。

因此：**仓库中有 manifest 不等于已部署，不等于 Task 89 会读取它，也不等于远端已拒绝未登记 argv。**

当前已确认的 dispatcher 行为由实际部署代码决定；当仓库资料与真实 dispatcher 冲突时，以核验到的实际 dispatcher 为准，并回写本文及部署资料。

## 6. 热更资料边界

当前热更调用合同的机器人侧权威仍是：

```text
config/muliu_script_catalog.md
→ tools/auto_hotfix_registry.py
→ tools/sync_hotfix_planner_catalog.py
```

[config/hotfix_thresholds.json](../../config/hotfix_thresholds.json) 与 [tools/hotfix_threshold_registry.py](../../tools/hotfix_threshold_registry.py) 是另一套 JSON 注册表实现；当前不在 catalog 同步主链中。它们不得再被表述为“当前唯一运行时来源”或“当前 fail-closed 的唯一执行白名单”。

在统一两套注册表前：

- 不得同时将两者称为唯一来源；
- 不得仅凭 JSON 资料否定或扩大当前 catalog 已登记能力；
- 每项热更仍须区分“调用链路成功”与“游戏内效果已验证”。

热更推进原则见 [docs/hotfix-execution-master-plan.md](../hotfix-execution-master-plan.md)。

## 7. 文档体系与维护规则

|资料|职责|维护方式|
|---|---|---|
|[README.md](../../README.md)|项目入口、快速启动与关键边界摘要。|随当前架构变化更新。|
|[docs/config-and-script-reference.md](../config-and-script-reference.md)|逐个配置、脚本、工具的职责、消费者、输入输出与边界。|新增或修改文件时同步更新。|
|本文|当前架构、信任边界、已知限制与权威来源。|实现、部署事实或安全边界变化时更新。|
|[config/muliu_script_catalog.md](../../config/muliu_script_catalog.md)|唯一机器人侧精确调用合同。|变更能力必须同步人类说明、合同与测试。|
|[knowledge/](../../knowledge/)|脱敏用途、风险、前后置与恢复说明。|不重复维护执行许可状态。|
|[deployment/gs1/](../../deployment/gs1/)|候选部署文件、生成物与远端核验清单。|不把仓库文件说成远端事实。|
|[docs/archive/](../archive/README.md)|已被当前架构取代的设计和实施记录。|保留历史，不作为当前操作手册。|

修改执行能力时的最小步骤：

1. 先更新 catalog 的人类说明和精确合同；
2. 更新 Planner / 合同 / firewall 的相应测试；
3. 如 artifact 有变化，重新生成并仅说明其为仓库候选部署物；
4. 更新知识说明中的用途、风险和验证边界；
5. 只有在有权限维护者完成真实部署和验证后，才记录远端部署事实；
6. 不把脚本退出成功升级为游戏内生效。

## 8. 开发与本地验证

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python -m pytest -v
python -m compileall src tests
```

配置从示例复制，真实配置保持在被 Git 忽略的 `config/config.json`：

```bash
cp config/config.example.json config/config.json
```

本地启动：

```bash
python -m uvicorn src.app:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
```

本地测试和静态生成检查不能替代：GS-1 文件覆盖、Task 89 配置核验、真实 Muliu 调用或目标服业务验证。

## 9. 当前已知限制

1. Task 89 与 GS-1 dispatcher 的真实部署状态不由本仓库证明。
2. 现有执行结果尚未通过每次请求唯一的 nonce / run ID 与 Muliu 日志做强绑定；不要将“最新日志的终态”描述为密码学或请求级归因证据。
3. 已提交但未核验结果的操作不会自动重放。
4. 远端脚本、SSH、数据库、日志读取与游戏内效果都必须以实际环境的独立证据核验。
5. 任何真实 URL、账号、密码、Cookie、Token、IP、私有 profile、Task ID 和私有连接配置都不得写入 Git、AI 输入输出、飞书卡片或 Gateway 审计文本。固定逻辑标识 `route-cn` / `route-overseas` 当前会显示在确认卡中；其是否可公开展示尚未决，不得与私有连接资料混同或写成已合规。
