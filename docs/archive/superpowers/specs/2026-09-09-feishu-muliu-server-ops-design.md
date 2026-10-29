# 飞书 Muliu 测试服操作机器人设计（MVP）

## 状态

- 日期：2026-09-09
- 状态：MVP 已实现并通过本地测试；待填写本机配置并做飞书群端到端验证
- 运行电脑：`CN210103420`
- 飞书项目目录：`D:\otherPrograms\feishu_agent`
- Muliu 本机调用链已验证：`D:\MySecondBrian\run_muliu_task.py` → Task 89 → Linux 脚本

## 目标

让组员在飞书群中 @ 机器人并用自然语言描述测试服操作需求。机器人生成执行计划并等待群内确认后完成：

```text
飞书群消息
→ AI 生成结构化执行计划
→ 检查计划中的危险关键词 / 参数模式
→ 在群内回显计划与一次性确认码
→ 群成员发送确认文本
→ 按步骤串行调用 Muliu Task 89
→ Muliu 执行 Linux 上已有的 .py / .sh 脚本
→ 收集每一步最新日志
→ 在原飞书群回复执行结果
```

示例输入：

```text
@测试服助手 给 6001 服执行 cc_patch.py，参数 -s 6001 -ck
```

AI 计划输出（内部 JSON，不直接由用户手写）：

```json
{
  "summary": "为 6001 服执行补丁检查",
  "steps": [
    {
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": ["-s", "6001", "-ck"],
      "description": "执行 6001 服补丁检查"
    }
  ]
}
```

## 不在本期范围

- 不接入 Dify 知识库，也不做知识库问答。
- 不实现审批、RBAC、飞书卡片确认、任务排队服务或跨机器部署。
- 不在本期改变 Linux 脚本的业务逻辑。
- 不把 Muliu 登录密码、飞书密钥或模型密钥写入 Git。

本期先跑通“群聊自然语言 → AI 计划 → Muliu → 群内结果”的最小闭环。

## 可复用的现有能力

### 飞书项目

`D:\otherPrograms\feishu_agent` 已具备：

```text
POST /feishu/events
→ 飞书 URL 校验、私聊和群聊 @ 消息解析
→ SQLite 去重 / 任务状态记录
→ 单个异步消息 Worker
→ AI 调用
→ 飞书文本回复
```

现有单 Worker 会顺序处理消息。这对单个 Muliu Task 很重要：同一 Task 的“保存参数 → 执行”不可被另一条请求插入覆盖。

### Muliu 执行链

已验证的请求顺序：

```text
登录 Muliu
→ GET task/detail 读取 version
→ POST task/argument/save 写入 JSON 参数
→ GET task/detail 再次读取 version
→ POST task/execute
→ 固定等待 5 秒
→ GET task/log?id=89&step=11&num=0
→ 读取 data[0].content 最新日志
```

Task 89 在 Linux 上通过 `muliu_ai_ops.sh` 调用 `muliu_ai_ops.py`，支持：

```json
{
  "path": "/home/serverGeneralScript/cc_patch.py",
  "args": ["-s", "6001", "-ck"]
}
```

Linux 端根据后缀执行：

```text
.py → python3.7
.sh → bash
```

并以目标脚本所在目录作为工作目录，解决脚本内部读取相对路径 `config.ini` 的问题。

## MVP 架构

```text
飞书群 @机器人
       │
       ▼
FeishuEventHandler
       │  写入 SQLite、立即 ACK 飞书回调
       ▼
MessageJobQueue（单 Worker）
       │
       ▼
MuliuOperationProcessor
       │
       ├─ AIPlanner：自然语言 → 严格 JSON 计划
       ├─ PlanValidator：JSON 结构、步骤数、字段类型校验
       ├─ MuliuFirewall：危险关键词 / 参数模式检查
       ├─ MuliuExecutor：逐步调用 Task 89
       └─ FeishuClient：回复成功、失败或日志摘要
```

## AI 输出约定

AI 只输出 JSON，不输出 Markdown、解释文本或 Shell 命令串：

```json
{
  "summary": "不超过 120 个字符的执行概述",
  "steps": [
    {
      "path": "/home/serverGeneralScript/example.py",
      "args": ["--server-id", "6001"],
      "description": "不超过 120 个字符的单步说明"
    }
  ]
}
```

字段规则：

| 字段 | 规则 |
|---|---|
| `summary` | 必填字符串，用于飞书回显。 |
| `steps` | 必填数组，MVP 最多 10 步。 |
| `path` | 必填绝对路径；Linux 端最终只接受 `/home/serverGeneralScript` 下的 `.py` 或 `.sh`。 |
| `args` | 必填数组；元素只能是字符串或数字，调用时以 argv 传递。 |
| `description` | 必填字符串，用于展示当前执行的步骤。 |

不允许模型输出：

```text
shell
command
bash -c
python -c
管道、重定向或一整段待 Shell 解释的命令文本
```

这是结构化配置，不是固定 `operation` 白名单：只要 Linux 上已有符合条件的脚本，AI 都可以通过 `path + args` 生成执行计划。

## 脚本资料

AI 需要知道已有脚本的用途和参数含义，否则容易虚构脚本名或参数。新增一个本地、可编辑的脚本资料文件：

```text
config/muliu_script_catalog.md
```

示例内容：

```markdown
## 查询服务器信息
- 路径：/home/serverGeneralScript/basic_info.sh
- 用法：basic_info.sh <server_id>
- 示例：{"path":"/home/serverGeneralScript/basic_info.sh","args":["6001"]}

## 补丁检查
- 路径：/home/serverGeneralScript/cc_patch.py
- 用法：cc_patch.py -s <server_id> -ck
- 示例：{"path":"/home/serverGeneralScript/cc_patch.py","args":["-s","6001","-ck"]}
```

该文件是提供给 AI 的操作说明和示例，不是 Linux 端的硬编码操作清单。Linux 的实际执行边界仍由 `muliu_ai_ops.py` 决定。

## 防火墙（MVP）

在 AI 输出计划后、请求 Muliu 前执行 `MuliuFirewall`。它检查每一个 `path`、`args` 与 `description`：

```text
1. 拒绝空字节、换行、Shell 控制符和命令替换形式。
2. 拒绝 bash/sh/python 的 -c 类动态代码执行参数。
3. 按本地配置的 blocked_keywords / blocked_patterns 拦截危险操作词和参数组合。
4. 限制单条请求的步骤数、单个参数长度和总参数长度。
5. 把被拦截的原因回复到飞书，但不发送到 Muliu。
```

防火墙规则放在本地配置，而不是写死在 prompt：

```json
{
  "muliu": {
    "blocked_keywords": ["示例危险关键词"],
    "blocked_patterns": ["示例危险参数模式"]
  }
}
```

规则由后续实际测试服操作需求逐步补充。MVP 不实现审批系统，但会先在群内回显计划和一次性确认码；只有群成员在时限内发送正确确认文本后，才串行执行。

## Muliu 执行器

新增 `src/muliu_executor.py`，把 `D:\MySecondBrian\run_muliu_task.py` 中已经验证的 HTTP 逻辑抽成可导入类：

```python
result = await executor.execute_plan(plan.steps)
```

执行器职责：

```text
- 使用本地 Muliu 配置登录。
- 每个步骤动态构造 {"path": ..., "args": [...]} JSON。
- 逐步执行 save → refresh version → execute → wait → latest log。
- 一条步骤失败时停止后续步骤，并返回已完成步骤与失败日志。
- 使用 asyncio.Lock 串行化 Muliu 调用；即使未来增加多个 Worker，也不能让同一 Task 的参数互相覆盖。
- 阻塞的 urllib 调用通过 asyncio.to_thread 运行，避免阻塞 FastAPI 事件循环。
```

MVP 固定使用：

```text
Task ID = 89
API step = 11
script index = 0
parameter index = 0
log wait seconds = 5
```

## 本地配置

扩展 `config/config.example.json`，增加无密钥示例：

```json
{
  "muliu": {
    "enabled": true,
    "base_url": "https://example.com",
    "username": "填写本机 Muliu 账号",
    "password": "填写本机 Muliu 密码",
    "task_id": 89,
    "step": 11,
    "script_index": 0,
    "parameter_index": 0,
    "log_wait_seconds": 5,
    "script_catalog_path": "config/muliu_script_catalog.md",
    "blocked_keywords": [],
    "blocked_patterns": []
  }
}
```

实际凭据只放：

```text
D:\otherPrograms\feishu_agent\config\config.json
```

该文件必须继续保持不提交。不要再依赖 `D:\python_program\muliu_task_config.py` 的固定 `SCRIPT_COMMAND`；飞书每条消息都需要动态传入计划。

## 群内确认

自然语言请求通过计划校验和防火墙后，机器人不会立即调用 Muliu，而是在原群回显：

```text
⚠️ 待确认的测试服操作：查询 6001 服服务器信息

1/1 查询 6001 服基础信息
脚本：/home/serverGeneralScript/basic_info.sh
参数：6001

若确认执行，请在本群发送：
确认执行 A1B2C3
未收到正确确认文本时，不会调用 Muliu。
```

默认确认有效期为 600 秒，由 `muliu.confirmation_timeout_seconds` 配置。确认文本前缀由 `muliu.confirmation_prefix` 配置。确认成功后才调用 Task 89；错误、过期或来自其他群的确认文本不会触发执行。

## 飞书回复

MVP 每条请求至少回复一次最终结果：

```text
✅ 已执行：为 6001 服执行补丁检查

1/1 ✅ 执行 6001 服补丁检查
脚本：/home/serverGeneralScript/cc_patch.py
参数：-s 6001 -ck
最新日志：
...
```

若生成计划、检查或执行失败：

```text
❌ 未执行
阶段：计划解析 / 防火墙 / Muliu 第 N 步
原因：...
```

后续可补充“已接收，正在执行第 N/M 步”的进度消息；MVP 先确保最终日志能稳定回到原群。

## 需要修改的文件

| 文件 | 变更 |
|---|---|
| `src/config.py` | 添加 `MuliuConfig` 与配置解析。 |
| `config/config.example.json` | 添加无敏感值的 `muliu` 配置示例。 |
| `src/muliu_executor.py` | 新增动态 Muliu 客户端与串行执行器。 |
| `src/muliu_plan.py` | 新增计划数据结构、严格 JSON 解析和校验。 |
| `src/muliu_firewall.py` | 新增关键词 / 参数模式拦截。 |
| `src/muliu_script_catalog.py` | 读取脚本资料并拼入 AI planner prompt。 |
| `src/message_worker.py` | 去除知识库问答分支，改为“AI 计划 → 检查 → Muliu → 飞书回复”。 |
| `src/app.py` | 初始化 Muliu 组件并注入 Worker。 |
| `tests/test_muliu_*.py` | 覆盖计划解析、拦截、串行调用和失败停止。 |
| `config/muliu_script_catalog.md` | 新增可维护的脚本说明。 |

## 实施顺序

1. 抽取 Muliu 客户端，先用命令行或单元测试验证动态 `path + args` 能取回日志。
2. 实现计划 JSON 数据结构、AI planner 与脚本资料注入。
3. 实现防火墙并覆盖拦截测试。
4. 把 MessageJobProcessor 改为操作 Worker，接入飞书最终回复。
5. 用 `basic_info.sh 6001` 做群聊端到端验证。
6. 再验证 `cc_patch.py -s 6001 -ck` 和多步骤计划。

## 验收标准

```text
[ ] 群成员 @机器人并发送自然语言操作请求。
[ ] 机器人只接受合法 JSON 计划，不执行模型的自由文本命令。
[ ] 计划通过防火墙后，能动态写入 Task 89 参数并执行。
[ ] 多步骤按顺序执行，任一步失败会停止后续步骤。
[ ] 机器人在原群回复每一步结果和最新日志摘要。
[ ] 相同飞书 message_id 重试不会重复执行。
[ ] 并发飞书消息不会交叉覆盖 Task 89 参数。
[ ] 原知识库检索路径不参与服务器操作流程。
```
