# 项目计划看板服务端化

> **状态：已实现（2026-11-04，本地测试通过，待部署）**
> **起因：** 看板数据写死在仓库 HTML，状态与批注存在各浏览器 localStorage；多端看到的不一致，「导出批注」在只改了状态时无法导出，同步靠手动转交。

## 1. 目标

```text
数据：Gateway SQLite（唯一真源）
网页：https://ops.ttlink.asia/plan  列表视图，直接读写
AI：  GET /api/plan  结构化 JSON；Claude Code 经 MCP 在 Gateway 本机访问 127.0.0.1:8901
仓库：docs/plan-board.json  种子 + 定期导出的快照（git 历史、可回滚）
```

## 2. 数据

| 表 | 内容 |
| --- | --- |
| `plan_meta` | 标题、原则、项目标签 |
| `plan_steps` | `step_id`、`sort_order`、`status`（active 进行中 / verifying 待验收 / todo 未开始 / done 已完成；AI 处理 active 与 todo，做完标 verifying，用户验收后标 done）、`area`、`title`、`desc`、`done` / `current` / `remaining`（JSON 数组）、`flow`（时序图 JSON，只读）、`note`、`question`、`updated_at`、`updated_by` |
| `plan_events` | 每次修改的字段、旧值、新值、操作人、时间（审计与历史） |

首次启动 `plan_steps` 为空时，从 `docs/plan-board.json` 导入。

## 3. 接口（审批台服务，同一套 Host / Access 校验）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/plan` | 全部数据 + 各状态计数；**AI 读取入口**；`?status=active,todo` 只返回 AI 的工作队列 |
| PUT | `/api/plan/steps/{id}` | 改状态、标题、说明、领域、三段列表、批注、疑问 |
| POST | `/api/plan/steps` | 新增步骤，编号自动递增 |
| DELETE | `/api/plan/steps/{id}` | 删除（过时的想法）；删除前的内容留在 `plan_events` |
| GET | `/api/plan/events` | 修改历史，可按步骤过滤 |
| POST | `/api/plan/migrate-local` | 把浏览器里旧的 localStorage 状态覆盖与批注同步到服务器 |

操作人：公网为 Access 令牌中的邮箱；SSH 隧道 / 本机为 `ssh-tunnel`，本机请求可用 `X-Admin-Operator` 自报（如 `claude-code`）。

时序图的说明文字允许 `<code>`，其余 HTML 一律转义。

## 4. 页面

- 按状态分组：进行中 → 未开始 → 已完成（默认折叠）；
- 每行：状态（点击切换）、编号、标题、领域、进度（已完成项 / 全部项）、当前在做、最后修改；
- 点一行原地展开：说明、三段列表（可编辑，每行一项）、时序图、批注与疑问、修改历史、删除；
- 搜索、按领域筛选、新增步骤；
- 检测到旧 localStorage 数据时提示一键同步。

## 5. 维护方式变化

- 日常更新改为调接口（网页或 Claude Code 经 MCP），**不再编辑 HTML**；
- `docs/hotfix-requirements-progress.html` 改为跳转说明页；
- `tools/plan_board.py export` 导出快照写回 `docs/plan-board.json`，需要时提交；
- `plan-board` skill 同步更新。
