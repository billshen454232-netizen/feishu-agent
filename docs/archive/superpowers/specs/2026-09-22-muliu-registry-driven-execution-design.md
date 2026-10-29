# Muliu 注册表驱动执行：去除重复能力限制设计

**状态：待实施**  
**日期：2026-09-22**

## 问题

飞书机器人应允许 AI 理解自然语言，并在已审核能力中选择合适的结构化调用：

```text
用户需求
→ AI 基于注册表判断是否能做、缺什么信息或应回答资料
→ 精确 path + args 合同
→ 注入防护与群内确认
→ Muliu Task 89
→ GS-1 分发器固定 argv 执行
→ 可归因的执行终态
```

现状出现了多份彼此独立的能力规则，导致已登记调用仍被拦截：

- Intent Router 用关键词在 AI 前将请求分成资料/操作；
- Planner 提示词要求“多个服务器一律拒绝”，但注册表已登记双服 Patch 对比；
- Firewall 和 GS-1 分发器分别维护无后缀脚本名单；
- Task 89 真实代码包与本地参考副本可能版本漂移；
- 执行器用固定等待后读取“最新日志”，不能可靠归因到本次提交；
- 脚本输出能够与 dispatcher 文本终态标记混在同一日志，当前解析不应把这类自由输出视为可信控制通道；
- 一些已登记脚本的进程退出成功并不等于其远端业务后置条件已得到验证。

## 不变边界

以下不是可自由放宽的业务限制：

1. 执行请求必须是精确的已登记 `path + args` 合同，不能运行自由 Shell。
2. 仅允许脚本根目录内的绝对路径；禁止 NUL、换行、Shell 控制符、`-c`、`--command`、`-e`、`--eval`。
3. 目标脚本原始源码支持但未审核、未登记的参数形状不能由 AI 推导并执行。
4. 写操作、停服、清服、热更、配置修改仍须群内可审计确认。
5. 受控日志诊断继续禁止任意路径、文件名、关键词和 SSH 参数。

## 目标架构

### 1. 注册表成为唯一的调用权威

注册表按每个合同定义：

```json
{
  "name": "start-server",
  "path": "/home/serverGeneralScript/start",
  "args": ["{server_id}"],
  "variables": {"server_id": "[0-9]{3,8}"},
  "runner": "bash",
  "risk": "write"
}
```

- `path + args` 继续精确校验。
- `runner` 由合同/生成的 runner manifest 决定，不再根据文件扩展名猜测能力。
- `risk` 用于确认和审计策略，不能由 AI 自行填写。
- 先保留单步骤执行，直到完成 run 级结果关联；随后只开放显式注册的安全组合。

> `runner` / `risk` 属于待扩展 schema；修改时必须同步迁移解析器与测试。

### 2. AI 负责需求判断，不再由关键词裁定能力

对含目标服号的请求，AI 应同时看到：

- 已审核调用注册表；
- 无敏感的能力说明；
- 资料问答与执行计划的受限输出 schema。

AI 输出三种受限结果之一：

```text
knowledge：回答当前能力/风险/用法，不执行
operation：一个或多个精确合同步骤候选
clarify/reject：缺少服号、目标不唯一、能力未登记等机器可读原因
```

关键词只可作为提示或快速分类线索，不能在 Planner 前阻断注册表匹配。

### 3. 单一 runner manifest

生成一个不含凭据的部署产物，例如：

```json
{
  "/home/serverGeneralScript/start": ["bash"],
  "/home/serverGeneralScript/shutdown": ["bash"],
  "/home/serverGeneralScript/cc_patch.py": ["python3.7"]
}
```

GS-1 `muliu_ai_ops.py` 只能按该 manifest 构造：

```text
runner + [exact_registered_path] + exact_args
```

继续使用 `shell=False`。Firewall 不再维护另一份无后缀业务名单；它只做注入、根目录和长度防护，并确保计划已通过合同校验。

### 4. 实际 Task 89 代码包是部署对象

从执行日志可知 Task 89 运行的是：

```text
../../code/ok_/muliu_ai_ops.sh
```

因此部署核验的对象应是 Task 89 实际代码包内成对的：

```text
ok_/muliu_ai_ops.sh
ok_/muliu_ai_ops.py
```

而非仅检查 `/home/serverGeneralScript/` 目标脚本目录。每次部署应输出版本或 SHA-256 摘要，以便飞书审计日志与本地构建版本对应。

### 5. 执行状态必须区分“未提交”与“已提交待核验”

最小状态集合：

```text
not_submitted
submitted_result_unknown
terminal_success
terminal_failure
```

提交 Muliu 成功后，如果日志读取失败、尚未生成或未返回本次 nonce 对应终态，禁止声称“尚未开始”或“成功”。应返回：

```text
已提交至 Muliu，执行结果尚未核验；请勿重复确认同一操作。
```

最终方案需要 Muliu 可关联的 run/log ID，或由分发器把本次随机 nonce 写入 `AI_OP_START/AI_OP_END` 并由执行器有界轮询匹配。本次请求的 path/args 摘要、提交时间、nonce、终态原文和解析结论均应保存到受控审计记录。

终态不能只从混合的 child stdout 中用宽松正则搜索。分发器应使用独立结果文件/API 字段，或至少在捕获 child stdout 后由父进程最后单独写出包含 nonce 的、全行锚定且唯一的终态记录；解析器必须拒绝重复、缺失或 nonce 不匹配的标记。

### 6. 脚本完成与业务生效分离

目前 `AI_OP_END status=success` 仅能证明分发器认为目标脚本正常退出，不能自动证明远端服务已就绪或配置已生效。例如 `start` 的 SSH 调用成功返回也不等于 Zone/Game 完全启动完成。

对写操作合同应定义可审核的后置条件和状态等级：

```text
transport_accepted
script_terminal_success
postcondition_verified
```

群内只有 `postcondition_verified` 才能展示“已生效/完成”。如果只有脚本成功，应展示“脚本已返回成功，尚未核验远端最终状态”，并给出受控的后续核验调用或人工核验提示。脚本本身应对关键 SSH/写入命令检查退出状态并在失败时非零退出。

### 7. 确认状态机

- 确认有效期从 `confirmation_issued_at` 计算，而不是原请求的 `created_at`。
- token 使用至少 128-bit 随机值；同一群内强制唯一。
- 确认应记录确认者身份；策略可为“仅发起人”或显式审批人列表。
- 私聊若不支持确认，则不生成 token，明确要求用户到指定群重新发起。
- 已提交后的飞书回信失败只能重试通知，不能把远端动作当新请求重放。

## 分期实施

### Phase 0：立即校验部署一致性

1. 由有 GS-1 权限的维护者定位 Task 89 的 `ok_/` 实际代码包。
2. 成对更新 `muliu_ai_ops.sh` 与 `muliu_ai_ops.py`：
   - shell 消费 `-a/-b` 并读取 `-p`；
   - Python 按受控 runner 运行已登记无后缀入口。
3. 记录实际部署版本或 SHA-256。
4. 先在受控环境执行一个只读已登记调用，核验 `AI_OP_END` 协议。

### Phase 1：移除本地重复能力阻断

1. 修正 Planner：允许一个合同本身表达的双服 Patch 对比；禁止的是 AI 自行扩展为多个未登记步骤。
2. 将 Intent Router 改为非阻断式：资料意图、执行意图和不确定意图均交给受限 AI 判定。
3. 为资料问答提供由注册表生成的当前能力清单，删除知识文档中会过期的“是否登记”表述。
4. 消除 Firewall 的无后缀脚本硬编码，改为从调用合同/runner manifest 派生。
5. 为每个合同增加“合同 → Firewall → Task 参数 → runner 选择”的参数化测试矩阵。

### Phase 2：执行归因与确认可靠性

1. 为每次提交添加并校验 run nonce / run ID。
2. 使用独立 dispatcher 结果通道或严格唯一、全行锚定的 nonce 终态，禁止 child stdout 伪造/覆盖控制标记。
3. 有界轮询本次日志至终态，而不是固定 5 秒读取 `logs[0]`。
4. 引入 `submitted_result_unknown` 与通知 outbox。
5. 修复确认过期依据、token 唯一性、确认人授权和私聊死路径。
6. 为写操作定义后置条件；仅在实际状态核验后展示“已生效”。
7. 保存脱敏后的原始 Muliu 终态审计记录；群内继续展示截断后的安全摘要。

### Phase 3：受控多步骤能力

仅在 Phase 2 后开放。每个组合必须在注册表显式登记顺序、前置、失败处理与风险，例如：

```text
启动 → 基础信息核验
备份 Patch → 热更
```

仍逐步展示、一次确认；若第 N 步失败，后续步骤不执行。

## 验收标准

1. 每个已登记合同都可通过合同、Firewall、Task 89 参数序列化与 runner 选择测试。
2. 注册无后缀 Bash 入口时，不需要在 Firewall 与分发器手工维护第二份脚本名名单。
3. “关/开/清/重置”、双服 Patch 对比和“能否执行”类请求都能由 AI 基于注册表判断，而不是被关键词硬分流。
4. 没有本次 run ID/nonce 的终态日志时，机器人绝不报“执行完成”。
5. 已提交但结果未知的操作不可自动重放。
6. 私聊不会产生无法确认的 token。
