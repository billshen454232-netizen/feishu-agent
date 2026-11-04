---
name: plan-board
description: 维护飞书机器人的项目计划看板（存于 Gateway SQLite，网页 https://ops.ttlink.asia/plan）。用户说「加计划 / 更新计划 / 计划同步 / 推进计划 / 改看板 / 加步骤 / 进度更新 / 查进度 / 配时序图」时使用；涵盖读写接口、状态规则、批注、快照与完成判据。
---

# 项目计划看板维护

看板是**全项目**的待办集中处（标题「飞书机器人 · 项目计划」）。2026-11-04 起数据存在 Gateway SQLite，**不再编辑 HTML**。

```text
网页：https://ops.ttlink.asia/plan        （Cloudflare Access，owner-only）
接口：http://127.0.0.1:8901/api/plan        （Gateway 本机；Claude Code 经 MCP myaliyun 访问）
快照：docs/plan-board.json                 （种子 + 定期导出，git 历史）
设计：docs/superpowers/specs/2026-11-04-plan-board-server.md
```

`docs/hotfix-requirements-progress.html` 只是跳转说明页。

## 0. 第一原则：计划即备忘

**已知待办默认上板，不要反问「要不要记」。** 上下文有限——不写进计划就是「用户忘、AI 也忘」。排查中发现的问题、待定方案、待核验事项、待问服务端的问题，只要已知且未完成，就新增 `todo` 步骤。

## 1. 读取进度（先读再说）

查进度、回答「还有什么没做」之前先读接口，不凭记忆：

```bash
# MCP myaliyun exec_bash（Gateway 本机，无需凭据）
curl -s http://127.0.0.1:8901/api/plan
cd /opt/feishu-agent && .venv/bin/python tools/plan_board.py summary   # 文字概览
```

返回结构：`meta`（标题、原则）、`areas`、`counts`（done/active/todo）、`total`、`steps[]`。每个 step：

```json
{"id": "29", "status": "active", "area": "执行链", "title": "…", "desc": "…",
 "done": ["…"], "current": ["…"], "remaining": ["…"],
 "flow": null, "note": "用户批注", "question": "用户疑问",
 "updated_at": 1793690000, "updated_by": "owner@example.com"}
```

**用户在网页上写的批注 / 疑问在 `note` / `question` 里，读进度时要一并看。**

## 2. 写入（Claude Code 经 MCP 调本机接口）

写请求必须带 `X-Admin-Request: 1`；用 `X-Admin-Operator: claude-code` 标明操作人（进修改历史）。

```bash
H='-H Content-Type:application/json -H X-Admin-Request:1 -H X-Admin-Operator:claude-code'
curl -s -X PUT  $H http://127.0.0.1:8901/api/plan/steps/29 -d '{"status":"done","current":[]}'
curl -s -X POST $H http://127.0.0.1:8901/api/plan/steps    -d '{"title":"…","area":"执行链","status":"todo","remaining":["…"]}'
curl -s -X DELETE $H http://127.0.0.1:8901/api/plan/steps/16
curl -s "http://127.0.0.1:8901/api/plan/events?step_id=29"            # 修改历史
```

可改字段：`status`、`title`、`desc`、`area`、`done`、`current`、`remaining`（文本数组）、`note`、`question`。`flow` 只读（随快照导入）。JSON 里的中文要保证 UTF-8；复杂内容先写临时文件再 `-d @file`。

## 3. 状态规则

- **固定三态**：`done` / `active` / `todo`。禁止第四态（接口会拒绝）；
- 过时的想法**直接删除**（删除前内容留在修改历史）；
- `done` 只写有可定位证据的事项；不把候选部署物、计划或推断写成已完成。

## 4. 领域

`执行链`、`热更`、`AI`、`审批台`、`知识库`、`部署`、`文档`、`其他`。新增步骤时选最贴近的一个。

## 5. 时序图（flow）

按步骤配置的 `{title, actors[{id,name,sub}], msgs[{from,to,s,t,d}]}`；`s` 取 `done/active/todo`（绿实线 / 蓝 / 灰虚线），`d` 只允许 `<code>`。改 flow 需改快照后 `tools/plan_board.py import`（会整体覆盖步骤，先 export 备份）。

## 6. 快照

重要变更后导出快照提交到仓库：在 Gateway 执行 `.venv/bin/python tools/plan_board.py export`，把输出写入本机仓库 `docs/plan-board.json`，提交（推送需用户说「推送」）。

## 7. 完成判据

步骤 `active → done` 时：

- `done` 每项都有可定位证据（代码、测试、配置、用户确认、环境观察）；
- 真实环境类事项写清「本地实现完成」与「远端 / 游戏内待核验」的边界；
- **脚本成功 ≠ 服务就绪 ≠ 游戏内已生效**，不可混写。

## 8. 已知待办不许散落

新增待办写进看板，不要只写在聊天、治理计划或记忆里；其他文档已有的待办同步到看板。
