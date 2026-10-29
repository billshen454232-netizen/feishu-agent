# Muliu 长操作生命周期与结果归因设计

**状态：实施中**  
**日期：2026-09-23**

## 术语边界

- **Muliu Task 89**：Muliu 中负责启动既有入口脚本的任务配置。
- **任务入口脚本**：由 Task 89 调用的 `muliu_ai_ops.sh` / `muliu_ai_ops.py` 等脚本。
- **目标运维脚本**：`/home/serverGeneralScript/` 内由入口脚本以固定 argv 调起的脚本，例如 `start`、`basic_info.sh`。

本设计不把“修改入口脚本”表述为“修改 Task 89”。Task 89 的任务定义、入口脚本和目标运维脚本是不同对象。

## 目标

处理长时间起服时，机器人必须区分：

```text
Muliu 已接受提交
→ 目标脚本仍在运行
→ 目标脚本已返回终态
→ 起服后置核验完成
```

不允许把尚未取得终态误报为脚本失败，也不允许无限期占用飞书消息 worker。

## 交互确认卡片

自然语言请求通过 AI、调用合同与本地防火墙后，机器人返回一张 CardKit 待确认卡片，展示完整步骤、脚本名与参数，并提供“确认执行 / 取消”按钮。

```text
卡片按钮 value
→ request_message_id + confirmation_token + 固定 action 名
→ 服务端读取数据库已保存的 plan_json
→ 再次严格解析计划
→ 后台提交 Muliu
```

按钮 value 不得包含、覆盖或决定脚本路径、参数、工作目录、环境变量或 Shell 命令。卡片回调还必须同时匹配：原请求消息 ID、机器人发送的卡片消息 ID、群 ID、原请求人的飞书用户 ID、确认 token、待确认状态和确认有效期。任何一项不匹配、重复点击、过期卡片或非原请求人点击都不会执行。

回调必须在飞书的 3 秒限制内返回。确认时只原子地占用本地计划并启动既有后台执行任务；卡片立即替换为“已提交”，执行完成后由机器人更新为不可再操作的最终状态卡片，并回复详细结果。取消会原子地写入 `cancelled`，不接触 Muliu。

飞书应用需要订阅 `card.action.trigger` 事件，并按开放平台提示补齐交互卡片与更新消息所需权限。

## 状态模型

`message_jobs.status` 使用以下操作相关状态：

```text
awaiting_confirmation
→ confirmed
→ submission_in_progress
→ submitted_result_unknown
→ done | failed
```

含义：

- `submission_in_progress`：本地已开始提交请求；进程中断时无法证明请求是否到达 Muliu。
- `submitted_result_unknown`：已收到 Muliu 接受执行的响应但观察期内未获得可确认的脚本终态，或执行请求未取得可确认的接受结果（连接中断、HTTP 错误、响应格式异常）、提交阶段被中断而结果无法确认。
- `done`：脚本终态成功；对 `start` 还必须完成 `basic_info.sh` 的明确“开服成功”核验。
- `failed`：仅用于可确认的提交前错误、脚本终态失败或后置核验明确失败。

`submitted_result_unknown` 不能自动重放，也不能在服务重启时被标记为 `expired`。

## 运行模型

```text
飞书确认
→ 单独的后台操作任务
→ MuliuExecutor 串行持有 Task 89 锁
→ 每 5 秒读取日志，最多观察 1800 秒
→ `start` 终态成功后，等待 60 秒让后台 game / Zone 进程与启动配置完成初始化
→ 再执行一次 `basic_info.sh`，仅独立行出现“开服成功”才视为成功
→ 飞书 worker 继续处理其他消息
```

Task 89 的参数保存、执行和终态观察仍串行，避免同一任务的参数互相覆盖；但普通飞书消息不再等待长操作结束。

服务停止或重启时：

```text
submission_in_progress
→ submitted_result_unknown

submitted_result_unknown
→ 保留原状态
```

两者均不自动重新提交目标脚本。

## 日志归因协议

仅凭“最新日志”不能证明它属于本次提交。为此，入口脚本支持可选 `run_id`：

```json
{
  "path": "/home/serverGeneralScript/start",
  "args": ["5000"],
  "run_id": "32 位十六进制随机值"
}
```

入口脚本不把 `run_id` 传给目标运维脚本，只在父进程最后输出：

```text
AI_OP_END run_id=<run_id> status=success
```

机器人仅在 `run_nonce_enabled=true` 时发送并校验该字段。默认关闭，保证尚未支持该协议的入口脚本不会因额外字段而失败。

启用前提是**单独更新并人工验证任务入口脚本**；这不要求修改 Task 89 的任务定义。未启用时，机器人仍可执行和轮询，但群内结果必须标明“日志未绑定本次提交”，不能作为可归因成功审计证据。

## 配置约束

以下时间配置必须是有限正数，`0` 或负数会导致启动失败：

```text
ai.timeout_seconds
job_queue.recovery_window_seconds
muliu.log_wait_seconds
muliu.log_completion_timeout_seconds
muliu.start_verification_wait_seconds
muliu.request_timeout_seconds
muliu.confirmation_timeout_seconds
```

当前默认观察上限为 1800 秒。达到上限后的结果是 `submitted_result_unknown`，不是“执行失败”。

`muliu.start_verification_wait_seconds` 默认 60 秒，只在 `start` 已成功结束后、自动 `basic_info.sh` 核验之前等待；它不是无限等待，也不改变起服脚本本身的最大观察时长。该等待来自已验证的实际运行时序：`start` 退出时，后台服务与 `Debug_ServerStartTime.lua` 仍可能尚未就绪。

`job_queue.recovery_window_seconds` 只控制尚未确认、尚未提交的普通消息恢复窗口，维持默认 600 秒；已确认的操作会在进程重启时直接标为 `submitted_result_unknown`，不依赖该窗口覆盖长操作时长。

## 非目标

- 不自动重试或重放已确认的写操作。
- 不让 AI 自由拼接 Shell。
- 不把本地调用资料或 legacy runner manifest 变成服务器端第二份业务白名单。
- 不在本设计中猜测目标脚本的合理最大运行时间。
