# 国服 / 海外服 Worker 路由选择

> **状态：已实现的路由规则记录；真实 profile 配置与远端连通性需独立核验。**
>
> **日期：2026-10-22；最后复核：2026-10-27**
>
> 当前总体说明见 [当前项目说明](../../architecture/current-project.md)。

## 目标

每一个已通过精确调用合同、防火墙并等待确认的计划，都必须派生唯一、无敏感信息的 Worker 执行区域：

```text
已验证调用合同中的服号或已登记 route_scope
→ route_region: cn | overseas
→ 确认卡展示区域
→ Gateway 持久化路由快照
→ Worker 重新推导并严格比对
→ Worker 本地选择私有 Muliu profile
```

AI、飞书卡片、`plan_json`、Gateway API 与 Git 不得携带实际 URL、主机、账号、密码、Cookie、Token、IP 或私有连接配置。`route_region` 和实际 profile 仍不进入脚本 argv。

当前确认卡代码会显示固定逻辑标识 `route-cn` / `route-overseas`。此行为不携带连接地址或凭据，但与旧有“route identifier 不外露”规则存在待决冲突；在用户明确其公开级别或代码移除它之前，不能将其写成已合规。

## 当前规则

1. [config/server_environments.json](../../../config/server_environments.json) 是服号区域归属的唯一资料来源；
2. `regions.cn` 中的已登记服号派生为 `cn`，其他已登记区域派生为 `overseas`；
3. 服号只能从已经匹配的调用合同变量提取，例如 `server_id`、`server_id_*`、`server_ids`；不扫描自然语言、步骤说明或普通数字；
4. 唯一例外是合同明确声明 `route_scopes` 的无服号只读范围查询。此类计划只能在根字段使用 `route_scope: "cn" | "overseas"`；它不是脚本 argv；
5. 服号未知、同一计划跨区域、范围查询缺少显式 `route_scope`，或 `route_scope` / `route_region` 非法时，必须在执行前拒绝；
6. `route_region` 与 route identifier 不由 AI 填写，也不进入 `plan_json` 或脚本参数。

## Gateway 与 Worker 双端校验

```text
Gateway
  校验 plan
  → 从合同变量 / route_scope 派生 route_region
  → 保存 route_region，展示国服 / 海外服确认信息
  → 向 Worker 下发 plan_json + route_region

Worker
  重新 parse plan
  → 重新调用合同校验与 firewall
  → 从本地环境注册表重新派生 route_region
  → 与 Gateway 快照严格一致才继续
  → 由固定映射选择本地 profile
```

固定映射为：

```text
cn       → route-cn       → Worker 本地国服 profile
overseas → route-overseas → Worker 本地海外服 profile
```

缺少、禁用或不完整的 profile 必须在发起 Muliu HTTP 请求前失败；不能使用默认值、跨区回退或任意 profile key。

## 资料与部署边界

本规则由代码和本地资料表达，但不能证明：

- 任何 Worker 已配置 `cn` / `overseas` 两个真实 profile；
- 对应 profile 的 Muliu URL、凭据、Task ID 或任务步骤已验收；
- GS-1、Task 89 或目标服已部署并可达；
- 实际查询或写操作已经在正确区域生效。

新增路由类别、将剧本号加入热更参数、修改 Patch 模板、覆盖服务器配置、重启服务或真实 Muliu 验证均不属于本文记录的实现结论。
