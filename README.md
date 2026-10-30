# 飞书测试服运维机器人

通过飞书群内自然语言请求，将测试服运维动作约束为**已登记的精确脚本调用合同**；操作必须经过原请求人确认，并通过 Gateway / Worker 分离链路调用内网 Muliu Task 89。

```text
飞书请求
→ AI 受限计划
→ catalog 精确合同 + 防火墙
→ 确认卡
→ Gateway SQLite 队列
→ 内网 Worker 重校验与路由
→ Muliu Task 89
→ GS-1 dispatcher
→ 飞书结果与本地审计
```

> 当前架构、信任边界、已知限制和资料维护规则见 [docs/architecture/current-project.md](docs/architecture/current-project.md)。历史方案已移至 [docs/archive/README.md](docs/archive/README.md)，不能作为当前部署或操作手册。

## 当前能力边界

- 接收飞书事件：`POST /feishu/events`；支持飞书 URL 校验、私聊消息和群聊 @ 机器人消息。
- AI 只能返回受限 JSON 计划，不能生成自由 Shell。
- 实际可执行范围唯一由 [config/muliu_script_catalog.md](config/muliu_script_catalog.md) 的精确 `path + args` 合同决定。
- 写操作必须展示确认卡；只有原请求人可以确认或取消。卡片按钮不携带脚本路径、参数、工作目录或 Shell。
- Gateway 只负责飞书、AI、确认和任务状态；Worker 主动领取已确认任务，在本机重跑计划校验、防火墙和路由推导后使用私有 Muliu 凭据执行。
- 已提交但无法可信核验结果的操作会标记为 `submitted_result_unknown`，**不会自动重放**。
- 脚本成功不自动等于游戏内生效；起服以固定后置核验为例外，其他业务效果需独立证据。

## 运行角色

|`runtime.role`|运行位置与职责|不应配置的敏感信息|
|---|---|---|
|`local`|本地开发或单机验证；飞书、AI、SQLite、确认和 Muliu 执行同进程。|不作为公网 Gateway 的替代方案。|
|`gateway`|公网入口；飞书回调、AI 计划、确认卡、SQLite 队列、Worker claim 与结果回写。|Muliu URL、账号、密码、Task ID。|
|`worker`|可访问 Muliu 的内网机器；只主动 HTTPS 连接 Gateway 并执行已确认任务。|飞书 App Secret、AI API Key。|

Worker 按可信 `route_region` 选择本机 `cn` / `overseas` 私有 Muliu profile。真实 URL、账号、密码、Cookie、Token、IP、Task ID 和私有连接配置不得进入 AI 计划、飞书卡片、Gateway 审计文本或 Git。

当前确认卡代码会展示固定逻辑标识 `route-cn` / `route-overseas`。其是否可以公开展示与既有“route identifier 不外露”规则冲突，尚待明确决定；在决定前，不应将其称为已合规的非敏感信息。

示例配置：

- [config/config.example.json](config/config.example.json)：local；
- [config/config.gateway.example.json](config/config.gateway.example.json)：gateway；
- [config/config.worker.example.json](config/config.worker.example.json)：worker。

## 执行合同、知识资料与 GS-1 的边界

### 执行合同

[config/muliu_script_catalog.md](config/muliu_script_catalog.md) 是机器人侧唯一的调用许可来源。每个步骤须精确匹配已登记的路径、参数模板、变量正则和风险级别；知识文档、脚本文件名、自然语言近义词和历史方案均不能扩大许可范围。

`route_scope` 仅用于已登记、无服号的只读范围查询；`route_region` 是 Gateway 派生、Worker 二次核验的可信路由快照。两者都不是脚本参数。

### 脱敏知识资料

[knowledge/](knowledge/) 供问答解释脚本用途、风险、前置条件和验证边界。它不等于 Linux 已部署脚本，也不授予执行权限。敏感脚本阅读副本、`config.ini` 与原始表格不进入 AI 检索。

### Task 89 和 runner manifest

任务执行器向 Task 89 发送的内容仅为：

```json
{"path":"/home/serverGeneralScript/...","args":["..."]}
```

[deployment/gs1/muliu_runner_manifest.json](deployment/gs1/muliu_runner_manifest.json) 是 catalog 派生的仓库候选部署 artifact；当前应用运行期不读取它。该文件存在不代表 GS-1 已有该文件、Task 89 dispatcher 消费它，或远端已完成精确 argv 拒绝。GS-1 的真实部署状态必须由有权限维护者按 [deployment/gs1/README.md](deployment/gs1/README.md) 核验。

## 本地安装与配置

```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash
python -m pip install -r requirements.txt
cp config/config.example.json config/config.json
```

`config/config.json` 被 Git 忽略。真实飞书凭据、AI Key、Muliu URL、账号、密码和 Worker token 只能存在于相应机器的本地配置中。

AI 配置当前支持 `openai_compatible` 与 `claude_messages` 协议；按所使用的服务填写 `base_url`、`api_key` 和模型名。不要把真实凭据提交到仓库。

## 启动与健康检查

```bash
python -m uvicorn src.app:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health
```

未创建 `config/config.json` 时，`/health` 返回 `missing_config`，用于测试和模块导入。

飞书回调需要一个公网 HTTPS 地址，并将其配置为：

```text
https://你的公网域名/feishu/events
```

飞书应用需启用机器人能力，并订阅：

```text
im.message.receive_v1
card.action.trigger
```

实际权限名称、发布和重新授权以飞书开放平台提示为准。`card.action.trigger` 用于确认、取消和更新操作卡片，服务端需在飞书时限内返回。

## 本地验证

```bash
python -m pytest -v
python -m compileall src tests
python tools/generate_runner_manifest.py --check
```

上述命令仅验证本地代码、测试和派生 artifact 一致性；不验证真实 Muliu 会话、GS-1 文件、Task 89 配置、SSH 权限、目标服状态或游戏内效果。

## 文档维护

|资料|用途|
|---|---|
|[docs/config-and-script-reference.md](docs/config-and-script-reference.md)|**每个配置、脚本、工具是干什么的、谁消费它**——日常维护首选入口。|
|[docs/architecture/current-project.md](docs/architecture/current-project.md)|当前架构、执行与部署事实边界、已知限制。|
|[config/muliu_script_catalog.md](config/muliu_script_catalog.md)|机器人侧唯一执行合同。|
|[knowledge/README.md](knowledge/README.md)|脱敏知识资料与检索边界。|
|[deployment/gs1/README.md](deployment/gs1/README.md)|候选部署物和远端人工核验清单。|
|[docs/hotfix-execution-master-plan.md](docs/hotfix-execution-master-plan.md)|热更证据、状态用词与推进规则。|
|[docs/hotfix-evidence-ledger.md](docs/hotfix-evidence-ledger.md)|**热更阈值证据台账**：逐条的需求来源、key、Lua 目标、Patch 范围与三层证据状态。|
|[docs/hotfix-requirements-progress.html](docs/hotfix-requirements-progress.html)|**热更推进计划看板**（浏览器打开）：平铺卡片展示各计划状态，点开看详情与批注。新增计划只改其中的 `PLAN_DATA`。|
|[docs/documentation-governance-plan.md](docs/documentation-governance-plan.md)|文档治理任务的进度、待办与检查状态。|
|[docs/archive/README.md](docs/archive/README.md)|已归档的历史计划与设计。|

执行能力变更必须先更新 catalog、对应测试和知识边界；只有真实部署完成并获得独立证据后，才能在当前项目说明中记录为远端事实。
