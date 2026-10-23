# 国服 / 海外服 Worker 路由选择

**状态：实施中**
**日期：2026-10-22**

## 目标

为每一条已通过调用合同、防火墙并等待人工确认的测试服计划，派生唯一且无敏感信息的 Worker 执行路由：

```text
可信服号归属
→ route_region: cn | overseas
→ 固定派生：国服 | 海外服、route-cn | route-overseas
→ 确认卡、SQLite、Gateway claim
→ Worker 重新派生并比对
→ Worker 私有 routes 配置中的 URL / 凭据 / task_id
→ 对应 Muliu 任务
```

AI、飞书卡片、`plan_json`、Gateway API、审计文本和 Git 都不得携带实际 URL、主机、账号、密码、Cookie 或 Token。

## 可信输入和派生规则

- 唯一服号归属来源是 `config/server_environments.json`。
- `regions.cn` 中登记的服号映射为 `cn`；其余已登记区域（港澳台、全球、韩服、越南）映射为 `overseas`。
- 服号只能从已匹配的 Muliu 调用合同变量中提取：`server_id`、`server_id_*`、`server_ids`。不扫描自然语言、步骤描述或任意数字。
- 缺少目标服号、未登记服号、未知 `route_region`、或跨 `cn` / `overseas` 的同一计划，均在确认前或 Worker 网络请求前阻断。
- 不得将 `route_region` 或 route identifier 加入 AI 生成的 `plan_json`。

## 受控快照

Gateway 在展示确认卡前派生并持久化唯一字段：

```text
route_region: cn | overseas
```

`route_label`（国服 / 海外服）和 `route_identifier`（`route-cn` / `route-overseas`）均由固定代码从 `route_region` 派生：前者只展示在确认卡，后者只用于 Worker 选择本地 profile。它们不写入 SQLite，也不在 Gateway claim 中传输；按钮 callback 仍只携带请求与确认身份标识。

## Worker 私有配置

Worker 的 `muliu.routes` 必须精确包含两个 profile：

```text
cn       → route-cn       → 本地国服 URL / 凭据 / task_id
overseas → route-overseas → 本地海外服 URL / 凭据 / task_id
```

只允许精确命中，不允许默认值、`get(..., overseas)`、任意 profile key 或静默回退。profile 缺失、禁用、URL / 凭据缺失或 `task_id` 非正整数，均在构造 Muliu HTTP 请求前返回失败。每个 profile 的 `task_id` 是对应 Muliu 实例中已人工创建、且步骤结构一致的受控任务 ID。

## 双端校验

1. Gateway 在确认卡前从已校验 plan 派生并持久化 `route_region`。
2. Gateway 仅将 `route_region` 和已持久化 `plan_json` 下发给 Worker。
3. Worker 重新解析 plan、重新执行防火墙校验、重新从本地环境资产派生 `route_region`。
4. Worker 仅在“本地重新派生的 `route_region` == Gateway 下发的 `route_region`”时，才从固定映射得到 route identifier 并选择私有连接配置。
5. 最终 `MuliuHttpClient` 继续只拼接固定 endpoint；其 `base_url` 和 `task_id` 只能来自 Worker 本地选择的 profile。

## 不在本次范围

- 通过自然语言显式传递剧本号到 hotfix 脚本；
- Patch Lua 模板简化；
- 服务器或 GS-1 的部署、配置覆盖、服务重启；
- 真实 Muliu 请求或游戏内效果验证。
