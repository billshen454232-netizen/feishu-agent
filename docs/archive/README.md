# 历史资料归档

> **这里的资料不是当前操作、部署或安全结论的来源。** 它们保存已完成、被替代、未实施或仅部分实施的决策过程，供追溯使用。

## 当前权威入口

|需要确认什么|应阅读的资料|
|---|---|
|当前项目架构、角色、信任边界、部署事实与已知限制|[../architecture/current-project.md](../architecture/current-project.md)|
|机器人可以生成的精确脚本调用|[../../config/muliu_script_catalog.md](../../config/muliu_script_catalog.md)|
|脚本用途、风险、前置条件与验证边界|[../../knowledge/README.md](../../knowledge/README.md)|
|GS-1 候选部署物与人工远端核验|[../../deployment/gs1/README.md](../../deployment/gs1/README.md)|
|热更资料推进与状态用词|[../hotfix-execution-master-plan.md](../hotfix-execution-master-plan.md)|

## 归档判定

归档资料可能出现以下任一种情况：

- 描述的是当时的 MVP、方案或实施计划，后来已由代码和当前架构替代；
- 记录未落地的候选技术路线；
- 部分原则仍有参考价值，但状态机、配置、执行合同或部署边界已变化；
- 含有当时的测试示例、路径或工作步骤，不能直接作为当前运维命令。

阅读历史资料时，必须先以当前入口核对。若两者冲突：

```text
当前代码 + 当前调用合同 + 已核验的真实环境事实
优先于
历史计划、设计、截图、旧脚本阅读副本或候选部署资料
```

## 已归档分类

- `superpowers/plans/`：早期实施计划、POC 与已完成的任务拆解；
- `superpowers/specs/`：被后续实现替代或只部分落地的设计说明；
- 根目录 `plan.md`：早期知识库机器人演进路线，不再代表当前测试服运维机器人主线。

归档并不证明当时结论错误；它只表示不能绕过当前资料，直接用于新的执行、部署或安全判断。
