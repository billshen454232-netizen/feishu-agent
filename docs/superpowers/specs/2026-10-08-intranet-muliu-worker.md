# 内网 Muliu Worker 拆分

**状态：已实现并通过单元测试**  
**日期：2026-10-08**

## 问题

阿里云可以接收飞书回调并调用 AI，但 Muliu 地址只在内网可达。当前确认后的执行如果在同一进程：

```text
message_worker._start_claimed_confirmation()
→ message_worker._execute_confirmed_plan()
→ muliu_executor.MuliuExecutor.execute_plan()
→ MuliuHttpClient 直接请求 muliu.base_url
```

因此不能把整份 `config/config.json` 放到阿里云后直接启动。

## 目标边界

```text
阿里云 gateway
  飞书事件、AI 计划、卡片确认、任务数据库、租约管理、结果回写飞书与审计

内网 worker
  主动连接阿里云 HTTPS
  原子领取已确认计划
  防火墙二次校验
  使用现有 MuliuExecutor 访问内网 Muliu
  周期心跳续租并在结束时回传执行结果
```

内网机器只出站访问阿里云 HTTPS，不开放入站端口，也不把 Muliu 暴露到公网。

## 不改的部分

- `MuliuHttpClient`、`MuliuExecutor.execute_plan()`、起服后 60 秒核验、终态判断保持原样。
- AI、调用合同、防火墙、卡片按钮协议保持原样。
- Worker 只接收 gateway 数据库里已确认的 `plan_json`，不接收飞书用户文本或 Shell。
- 已领取但没有最终结果的任务不自动重放。

## 角色

配置增加 `runtime.role`：

```text
local     单进程行为，测试和本机继续使用
gateway   阿里云：不创建 MuliuExecutor，不要求 Muliu 账号密码
worker    内网：只运行领取循环，不启动飞书服务
```

## 状态机与租约设计

在现有状态后增加：

```text
confirmed
→ queued_for_worker      已确认，尚未被内网领取；重启后仍可领取
→ leased                 已被某个 worker 领取；持有租约与心跳
→ done | failed | submitted_result_unknown
```

### 1. 全局 Task 89 串行化单租约
由于 Task 89 是全局单任务（保存参数 → 执行），数据库领取逻辑 `claim_next_worker_job` 在事务中强制检查全局是否存在 `status='leased'` 任务。只要有任意任务在执行中，其他 Worker 不会抢领下一条任务，杜绝并发参数覆盖。

### 2. 心跳与租约保护
- Gateway 暴露 `POST /internal/worker/jobs/{message_id}/heartbeat`。
- Worker 在后台执行 Muliu 操作时，定期（默认 30 秒）续租。
- 若 Worker 失联或异常崩溃，超过 `lease_timeout_seconds`（默认 180 秒）后租约过期。
- 超期任务被转为 `submitted_result_unknown`（“已提交，结果尚未核验；请勿重复确认或重试”），**绝不重新入队自动重放**。
- Gateway 租约观察器与 claim 路由会在租约超时时自动投递飞书卡片更新，避免静默丢失通知。

## 接口协议

内部通信仅走 HTTPS，使用请求头认证：
- `Authorization: Bearer <worker_token>`（使用常量时间比较 `hmac.compare_digest`）
- `X-Feishu-Worker-ID: <worker_id>`（严格正则校验 `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`）

```text
POST /internal/worker/jobs/claim
POST /internal/worker/jobs/{message_id}/heartbeat
POST /internal/worker/jobs/{message_id}/result
```

### 回传结果与已持久化计划匹配
Gateway 收到 Worker 回传的 `result` 时，不仅校验 JSON Schema，还强制比对：
- `result.summary` 必须与数据库中保存的计划 summary 一致；
- 回传的步骤序列（`path`、`args`、`description`）必须是已确认计划及其确定性验证步（如起服的 basic_info.sh）的严格前缀；
- 失败结果的失败步骤必须合法，防止恶意或错误 Worker 篡改审计日志。

## Worker 循环

```text
claim
→ parse_plan(plan_json)
→ 现有防火墙再次校验
→ MuliuExecutor.execute_plan(plan)（带并发心跳）
→ POST result
```

Worker 自己保存 `muliu.base_url`、账号和密码。Gateway 不保存这些值。

## 部署分工

```text
阿里云：/opt/feishu-agent
  runtime.role = gateway
  飞书与 AI 配置
  不放 Muliu 账号密码
  Nginx 反代 127.0.0.1:8000

内网 Linux（Jenkins 机器）：同一 Git 仓库的独立部署目录
  runtime.role = worker
  gateway 地址、共享令牌、Muliu 配置
  systemd 常驻服务；只出站访问 https://你的公网域名
```

两台机器的 `config/config.json` 都继续被 Git 忽略。

