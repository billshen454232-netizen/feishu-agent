# 内网 Muliu Worker 拆分

> **状态：已实现的架构记录；真实部署状态需单独核验。**
>
> **日期：2026-10-08；最后复核：2026-10-27**
>
> 当前总体说明见 [当前项目说明](../../architecture/current-project.md)。

## 已实现的职责拆分

Muliu 仅在内网可达时，运行时分为以下角色：

```text
gateway
  飞书事件、AI 计划、确认卡、SQLite 状态、Worker 租约、结果回写与审计

worker
  主动 HTTPS 连接 Gateway
  领取已确认计划
  重新解析、重新执行调用合同 / 防火墙 / 路由校验
  使用本机私有 Muliu profile 调用 Task 89
  心跳续租并回传受限结果
```

Worker 只接收 Gateway 已持久化的计划和 `route_region`，不接收飞书原文、自由 Shell、Muliu URL、账号或密码。内网 Worker 主动发起到 Gateway 的连接；此架构不要求将 Muliu 暴露到公网。

## 角色配置

`runtime.role` 的当前职责：

|角色|用途|不应持有的敏感信息|
|---|---|---|
|`local`|本地开发或单机验证，飞书、AI、SQLite、确认和执行在同一进程。|不作为 Gateway 生产部署的替代描述。|
|`gateway`|飞书事件、AI、确认、任务状态、Worker claim / lease / 回传。|Muliu URL、账号、密码、Task ID。|
|`worker`|领取已确认操作、重校验、按本地 profile 调用 Muliu。|飞书 App Secret、AI API Key。|

真实机器上的配置、服务管理、反向代理与环境变量不由本仓库证明；`config/config.json` 应继续保持 Git 忽略。

## 任务状态与租约

操作状态链路包括：

```text
confirmed
→ queued_for_worker
→ leased
→ done | failed | submitted_result_unknown
```

- 同一 Task 89 使用全局单租约，防止“保存参数 → 执行”被并发任务覆盖；
- Worker 执行期间定期心跳；
- 租约到期转为 `submitted_result_unknown`，不自动重新入队或重放；
- Gateway 仅接受仍由该 Worker 持有租约的结果，并校验回传结果与已确认计划相符。

`submitted_result_unknown` 不表示失败；它表示操作可能已提交但无法取得可信终态。不得让用户再次确认同一操作作为自动替代。

## 内部接口与数据边界

当前内部 Worker API：

```text
POST /internal/worker/jobs/claim
POST /internal/worker/jobs/{message_id}/heartbeat
POST /internal/worker/jobs/{message_id}/result
```

请求使用 Worker token 与受限 Worker ID 认证。Gateway 下发与审计中不得出现实际 URL、账号、密码、Cookie、Token、IP 或私有连接配置；Worker 选择的 Muliu profile 只能保存在其本地配置中。

当前确认卡显示固定逻辑标识 `route-cn` / `route-overseas`，不属于 Gateway 下发给 Worker 的私有连接配置；但其公开展示与旧有“route identifier 不外露”规则存在待决冲突，不能在本文中把它表述为已合规。

## 不能由本文证明的事实

本文件和本地测试无法证明：

- Gateway 或 Worker 当前运行在任何具体机器；
- Worker 与 Gateway 的 HTTPS、token、代理或 systemd 服务已配置；
- Task 89、GS-1 dispatcher、目标脚本或 Muliu profile 已部署；
- 目标服实际执行效果或游戏内状态。

真实部署、配置覆盖、服务启动和 Muliu 调用均须由有权限维护者明确执行并独立记录。
