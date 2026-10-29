# 热更阈值 JSON 注册表设计记录

> **状态：未接入当前执行主链；仅保留为候选迁移设计与资料模型。**  
> **日期：2026-10-15**  
> **当前替代资料：** [当前项目说明](../../../architecture/current-project.md) 的热更资料边界，以及 [热更执行计划总纲](../../../hotfix-execution-master-plan.md)。

## 归档说明

本文设计过将热更阈值统一迁移至 [config/hotfix_thresholds.json](../../../../config/hotfix_thresholds.json)，再由 [tools/hotfix_threshold_registry.py](../../../../tools/hotfix_threshold_registry.py) 加载并生成 Planner catalog。该迁移**尚未成为当前运行主链**，不能据此判断当前机器人允许或拒绝任何热更调用。

当前代码事实是：

```text
当前 catalog 同步主链
config/muliu_script_catalog.md
← tools/sync_hotfix_planner_catalog.py
← tools/auto_hotfix_registry.py

独立的候选 JSON 注册表
config/hotfix_thresholds.json
→ tools/hotfix_threshold_registry.py
（当前未被 catalog 同步器或 auto_hotfix_tool.py 使用）
```

因此，本文先前的以下断言均已过期，不能作为操作或安全结论：

- “JSON registry 没有可执行条目”；
- “catalog / manifest 不登记 `auto_hotfix_tool.py`”；
- “所有热更写请求均应拒绝”；
- “`auto_hotfix_tool.py` 运行时加载 JSON registry”；
- “生产 CLI 不支持 `--table`、`--scenario`、`--patch-type` 或维护动作”；
- “JSON registry 是唯一业务来源或唯一 fail-closed 白名单”。

## 仍有参考价值的设计原则

下列原则仍可作为将来统一注册表的验收条件，但不是当前实现声明：

1. 业务中文名、稳定 ASCII key、目标字段、剧本范围、Patch 类型和值域需要可追溯的明确资料；
2. 模型只能选择已登记的 key，不能传 Lua 表名、字段名、路径、自由 Patch 类型或 Shell；
3. 单值热更与结构化配置、文件编辑、服务操作应使用不同的受控模板，不能因同属“热更”而混用；
4. 未核实条目只能记录资料与待确认项，不得被自然语言相似性自动转为可执行调用；
5. 热更调用链路成功与游戏内效果验证必须分开记录。

## JSON 注册表当前定位

[config/hotfix_thresholds.json](../../../../config/hotfix_thresholds.json) 仍是可阅读、可校验的候选资料源，保存了可执行候选与不可执行资料条目的结构化区分。其加载器会对 JSON 形态、字段与值域进行失败关闭校验；但在它真正接入 catalog 同步和 GS-1 实际工具前：

- 不得将其描述为运行时执行权威；
- 不得只修改 JSON 后就宣称机器人新增或移除了热更能力；
- 不得因 JSON 与当前 Python registry 不同而自行修改 catalog 或远端脚本；
- 不得将本地测试、候选 manifest 或配置文件存在表述为 GS-1 已部署。

## 若要完成统一迁移

迁移必须作为单独变更完成，至少包括：

1. 明确唯一权威来源，并迁移 `auto_hotfix_tool.py`、Planner alias 渲染与 catalog 同步器至该来源；
2. 为 Python 旧 registry 与 JSON registry 的每个差异建立可追溯的决策和测试；
3. 从 catalog 精确合同、Planner、Gateway、Worker、工具运行时到候选 artifact 进行端到端一致性测试；
4. 仅在真实 GS-1 部署与受控调用验证后，更新远端部署事实；
5. 对每项写操作保留确认、审计、失败未知不重放，以及独立游戏内验证。

迁移前，唯一机器人侧调用许可仍是 [config/muliu_script_catalog.md](../../../../config/muliu_script_catalog.md) 的精确合同。
