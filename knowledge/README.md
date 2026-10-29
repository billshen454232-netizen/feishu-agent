# 测试服脚本知识资料

本目录保存供飞书机器人**检索和解释**的脱敏资料；它不是 Linux 上的实际执行目录，也不是脚本自动执行的允许清单。

## 目录约定

```text
knowledge/
├─ index.md                 # 面向检索的脚本与流程总索引
├─ scripts/                 # 原始脚本阅读副本，仅供人工核对
├─ docs/                    # 可提供给 AI 的脱敏使用说明
├─ references/              # 原始手册和环境资料，仅供人工整理
└─ README.md
```

## 当前检索与执行边界

- [index.md](index.md) 和 `docs/` 是当前 `ScriptKnowledgeBase` 的问答资料入口。
- 每个配置 / 脚本 / 工具的职责、消费者与输入输出，见 [../docs/config-and-script-reference.md](../docs/config-and-script-reference.md)。
- `scripts/` 和 `references/` 不进入自动检索；它们只用于人工阅读、核对依赖与整理脱敏文档。
- 实际执行发生在 Linux / GS-1 的已部署脚本中；阅读副本不证明远端文件存在或版本一致。
- [../config/muliu_script_catalog.md](../config/muliu_script_catalog.md) 是机器人侧唯一执行合同。只有知识文档不代表机器人可以执行，反之亦然。
- 知识文档只说明用途、影响、前置条件、恢复方式和结果验证边界；不应维护另一套“已登记 / 未登记 / 不得登记”的执行许可状态。

## 敏感资料处理

以下文件或类型已知包含，或可能包含，不应进入 AI 上下文、向量库、群消息或 Git：

- `scripts/config.ini`：内部服务器路由映射；
- `scripts/clear_logic_game.py`、`scripts/clear_zone.py`、`scripts/cleardb.py`：数据库认证、连接信息或敏感清理实现；
- `references/*.xlsx`：原始手册、环境分布及其中可能的门户链接、账号、人员或环境拓扑信息。

新增资料时，先检查是否含密码、Token、数据库连接串、内部主机/IP、账号、人员或精确环境映射；仅将完成问答所需的最小脱敏信息写入 `docs/`。

## 高风险操作资料规则

高风险操作的说明必须写明实际影响、前置检查、恢复方式和验证边界，不能只给出执行命令。脚本退出成功不自动等于服务已就绪、数据已恢复或游戏内效果已生效。

是否可由机器人执行始终以 catalog 的精确调用合同为准；真实 GS-1 部署、Task 89 配置和目标服业务效果仍须由独立环境证据核验。
