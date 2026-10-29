# Gateway 执行结果 AI 研读层

> **状态：本地实现完成；远端运行与效果待独立核验。**
>
> **日期：2026-10-09；最后复核：2026-10-27**
>
> 当前架构与终态用词见 [当前项目说明](../../architecture/current-project.md)。

## 目的

任务有可信终态后，Gateway 可将**脱敏、清洗和长度受限**的执行输出交给 AI，生成适合飞书阅读的结论；原始格式化结果仍作为执行明细保留。该层仅改善展示，不改变计划、执行、状态或审计事实。

```text
Worker / local executor 产生受限终态
→ Gateway 校验结果与已确认计划匹配
→ 生成格式化的保底结果
→（可选）AI 研读脱敏后的环境摘要和脚本输出
→ 飞书结论 + 执行明细
```

## 当前实现边界

相关实现位于：

- [src/message_worker.py](../../../src/message_worker.py)：`interpret_execution_result()`、清洗与提示词构造；
- [src/app.py](../../../src/app.py)：Worker 回传结果持久化和匹配校验后调用研读层；
- [src/config.py](../../../src/config.py)：`execution_interpretation` 配置。

当前配置默认值：

```text
enabled = true
timeout_seconds = 8
server_environments_path = config/server_environments.json
max_execution_data_chars = 12000
```

AI 调用超时、异常、空输出、功能关闭或没有 AI 客户端时，都会回退到常规格式化结果。研读层不能把已持久化的 `done`、`failed`、`submitted_result_unknown` 改为其他状态。

## 输入与安全边界

AI 输入由以下受限信息组成：

- 用户的原始请求文本；当前调用点直接传入持久化的 `pending_job.text`，**没有在本方法内先做敏感信息遮罩**；
- `server_environments.json` 的区域 / 环境显示信息；
- 每个已回传步骤的脚本名称、说明与清洗后的日志输出。

因此，输出和执行日志会经过 `mask_sensitive_operation_data()` 处理，但用户原始请求能否安全进入 AI，仍依赖请求入口和计划层不接受 / 不持久化敏感内容；本方法本身不构成对原始请求的独立脱敏保证。

不会传入：

```text
Muliu URL、账号、密码、Cookie、Token、私网 IP、主机地址、数据库连接、
工作目录、脚本完整路径、Task ID 或 Worker 私有 profile。
```

输出也经过敏感信息遮罩。若环境资料读取失败，AI 会看到“环境资产不可用”的受限说明，不能据此推断区域。

## 终态和业务效果的区分

当前提示词**要求** AI 的首行使用下列标签之一：

```text
【执行完成】
【执行失败】
【结果尚未核验】
```

提示词也要求它只复述执行结果直接支持的事实：

- dispatcher / Task 89 成功只是对应脚本层终态；
- 普通写操作成功不代表数据正确、服务就绪或游戏内已生效；
- `start` 只有在受控 `basic_info.sh` 核验出现独立行“开服成功”后，才会报告起服完成；
- `submitted_result_unknown` 不是失败，且不应建议重复确认或自动重试；
- 多服 `basic_info.sh` 结果应按“已开服 / 已关闭 / 查询报错或无法确认”互斥分组。

这是**提示词约束，不是当前代码强制保证**。`interpret_execution_result()` 会脱敏并拼接非空 AI 输出，但不会校验首行标签，也不会将 AI 文本与 `MuliuExecutionResult` 的 `succeeded` / `result_unknown` 状态逐项比对。结果卡在存在“执行明细”时还会优先展示该 AI 摘要。

因此：任务数据库中的持久化终态仍是唯一可信状态；AI 文本只是未经状态一致性校验的展示层摘要。若二者冲突，必须以持久化终态、受控日志和人工环境证据为准。若要把上述标签和未知终态规则变成强制保证，需另行实现输出校验与对应测试。

## 不应从本文推导的结论

本地代码和单元测试不能证明：

- Gateway 上当前已启用 AI 研读；
- 某次飞书结果已经由 AI 处理；
- AI 结论正确地解释了远端环境；
- GS-1、Task 89 或目标服已部署、可达或业务生效。

真实性始终优先于可读性：AI 结论与可信执行结果不一致时，以可信终态、受控日志和人工环境证据为准。
