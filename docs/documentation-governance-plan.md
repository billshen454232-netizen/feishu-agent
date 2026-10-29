# 项目文档治理执行计划

> **状态：进行中**  
> **创建日期：2026-10-27**  
> **维护方式：每完成、发现或撤销一项文档治理结论时更新本文；不以聊天记录代替项目状态。**

## 1. 目标与完成定义

为测试服运维机器人建立一套能长期维护、方便新维护者阅读且不误导实际部署状态的文档体系。

完成时必须同时满足：

1. 维护者能从项目入口找到当前架构、配置、脚本、调用合同与部署资料；
2. 每类资料明确其用途、维护责任、消费者、输入输出与是否属于运行期事实；
3. 当前文档不把本地代码、候选部署物、测试结果或历史方案说成远端已部署或游戏内已生效；
4. 机器人执行许可只由 `config/muliu_script_catalog.md` 的精确 `path + args` 合同表达；
5. 历史方案与候选迁移设计不与当前说明混放；
6. 当前文档的关键事实与代码、配置和已确认环境事实一致；
7. 链接、Markdown 格式和本地检查结果清楚记录，未解决问题不可被隐藏为“完成”。

## 2. 事实来源与写作规则

### 2.1 事实优先级

1. 当前运行代码、测试和被加载的配置；
2. 用户已明确确认的环境事实；
3. 有权限维护者留下的可定位部署或环境证据；
4. 历史设计、截图、临时导出、旧脚本阅读副本与候选部署资料。

下位资料不能覆盖上位事实。无法核实的远端信息必须写明“仓库不能证明”，而不是补充推断。

### 2.2 固定边界

- 不把本地 `pytest`、`compileall`、生成器检查写成远端部署或游戏内验证；
- 不把仓库候选 manifest、脚本副本或部署资料写成 GS-1 当前文件；
- 不把脚本层成功写成服务就绪、数据正确或游戏内生效；
- 不让知识说明、历史文档或脚本文件名产生第二份机器人执行白名单；
- 不在 Git、AI 输入输出、飞书卡片或 Gateway 审计文本中记录真实 URL、账号、密码、Cookie、Token、IP 或其他私有连接配置；
- 永久删除前必须单独获得确认；在此之前优先归档或标明不可作为当前依据。

## 3. 文档体系与责任分层

| 位置 | 面向对象 | 应说明什么 | 不应说明什么 |
| --- | --- | --- | --- |
| `README.md` | 所有维护者 | 项目用途、最短启动路径、权威资料入口和关键边界。 | 逐个脚本的完整运行细节、未核实的部署现状。 |
| `docs/architecture/current-project.md` | 开发与运维维护者 | 当前代码架构、角色、信任边界、状态语义、部署边界、已知限制。 | 将候选物、历史方案写成当前事实。 |
| `docs/config-and-script-reference.md` | 日常维护者 | 所有关键配置、源码入口、工具、知识脚本和部署资料的职责、消费者、输入输出与边界。 | 机器人侧执行许可的第二份定义。 |
| `config/muliu_script_catalog.md` | Planner / Gateway / Worker 维护者 | 唯一机器人侧精确调用合同：`path + args` 模板、变量和风险等级。 | 原始 Linux 脚本的完整自由用法。 |
| `knowledge/` | 运维问答与脚本使用者 | 用途、参数、影响、前置、恢复和验证边界。 | 当前已登记 / 未登记 / 禁止执行的重复许可状态。 |
| `deployment/gs1/` | 环境维护者 | 候选部署物、生成关系和人工核验清单。 | GS-1 当前状态的无证据断言。 |
| `docs/archive/` | 历史追溯者 | 旧计划、旧设计、未接入候选路线的历史语境。 | 当前操作或安全结论。 |
| `docs/hotfix-requirements-progress.html` | 面向人的**项目计划看板**（已从热更专项扩展为全项目） | 平铺卡片网格（已完成 / 进行中 / 未开始），点击卡片切换详情：已完成、正在做、后续还需；支持手动批注与疑问（存浏览器本地，可导出 Markdown）。**已知待办统一记在此板，不在本文件重复维护。** | 不是单条证据台账；批注不入 Git，不得写成已持久化到仓库。 |

| `.claude/rules/core-governance.md` | 所有执行和审查 Agent | 项目稳定核心规则、事实边界、授权边界和失败处理规则。 | 阶段状态、具体实现方案或远端事实。 |
| `.claude/agents/core-rule-guardian.md` | 协调 Agent | 只读 PASS / BLOCK 守门角色的权限、证据要求和裁决格式。 | 实现、部署、提交或推送操作。 |
| `.claude/skills/project-governance/` | 复杂任务执行者 | 取证 → 实现 → 守门 → 验证 → 汇总的固定治理流程。 | 小任务不必要的重流程。 |
| `SESSION-HANDOFF.md` | 跨会话协作 | 阶段状态、下一步、未解决阻塞和验证结果。 | 稳定架构规则或长期说明。 |

## 4. 当前执行清单

> **本节的未完成项已统一迁入项目计划看板 [docs/hotfix-requirements-progress.html](hotfix-requirements-progress.html)（STEP 09–20）。** 下表保留事实背景与决策记录；**新的待办请直接写进看板，不要只写在本文件。**

### 4.1 当前入口与架构

| 项目 | 状态 | 结果 / 下一步 |
| --- | --- | --- |
| 根 README | 已完成初稿 | 已改为当前项目入口；最终与职责索引互链。 |
| 当前项目说明 | 已完成初稿 | 已记录执行链、角色、调用合同、路由、终态和部署边界；需随已核实代码差异复核。 |
| Gateway / Worker 架构记录 | 已复核 | `docs/superpowers/specs/2026-10-08-intranet-muliu-worker.md` 已改为实现记录，不等同真实部署证明。 |
| 区域路由记录 | 已复核 | `docs/superpowers/specs/2026-10-22-worker-route-selection.md` 已区分 `route_scope`、`route_region` 与真实 profile 配置。 |
| AI 结果研读记录 | **已修订** | 已改为“提示词期望而非代码强制保证”：当前代码不校验 AI 标签或终态一致性，结果卡会优先展示 AI 摘要；持久化终态仍为可信来源。 |

### 4.2 配置、脚本与工具职责索引

> **主交付物：已创建。** `docs/config-and-script-reference.md` 已落地，覆盖 `config/`、`src/`、`tools/`、`knowledge/scripts/`、`deployment/gs1/`、`tests/` 六大范围的职责、消费者、输入输出与边界。已从 README、当前项目说明、知识入口三处互链。

| 范围 | 状态 | 要完成的内容 |
| --- | --- | --- |
| `config/` | **已完成** | 运行配置模板、服务环境资料、调用合同、热更 JSON 资料的消费者、加载时机、敏感性、权威范围和派生关系均已说明。 |
| `src/` 运行入口 | **已完成** | FastAPI、消息处理、任务状态、Planner、catalog / firewall、executor、路由、Worker 的职责与调用链已说明。 |
| `tools/` | **已完成** | 每个工具的输入、输出、是否改写仓库文件、是否运行期依赖、生成物是否候选部署资料已说明。 |
| `knowledge/scripts/` | **已完成** | 区分了机器人可调用脚本（对应 catalog 登记项）与仅作阅读参考的脚本；敏感 `config.ini` 边界已标注。 |
| `deployment/gs1/` | **已完成** | README 与候选部署物已纳入索引，边界为候选 artifact。 |
| 热更双注册表 | **已完成** | Python 主链与 JSON 候选链的用途与边界已分开说明。 |

### 4.3 知识资料与归档

| 项目 | 状态 | 结果 / 下一步 |
| --- | --- | --- |
| `knowledge/README.md` 与 `knowledge/index.md` | 已完成初稿 | 已改为用途、风险和查询入口，不再维护第二份许可。 |
| 核心运维脚本文档 | 部分完成 | `basic_info`、起停服、清档、Patch、配置、日志和 combine 相关说明已重写；需从职责索引统一核对遗漏。 |
| 历史 plans / specs | 部分完成 | 多份 2026-06、2026-07、2026-09 历史资料已移入 archive。 |
| 热更 JSON 候选设计 | **已归档** | `2026-10-15-hotfix-threshold-registry.md` 已移入 `docs/archive/superpowers/specs/`，相对链接已修正并通过解析检查。 |
| 无用文件清理 | **已删除** | `calc.py`、`date.py`、`longmem.txt`、`docs/_tmp_hotfix_excel_extract.json`、`docs/gemini-code-1792483526256.html` 已删除。 |
| 归档索引链接文字 | **已修正** | `docs/archive/superpowers/plans/README.md` 的可见路径文字已与实际链接目标统一。 |
| 敏感数据历史遗留 | **已决定不清理历史** | `date.py` 内容为游戏充值流水且已进入 Git 历史；工作区文件已删除。用户决定不重写 Git 历史。 |
| `config/muliu_regions.json` 跟踪风险 | **已决定保持现状** | 该文件被 Git 跟踪，当前仅含占位值。用户决定不加入 `.gitignore`；当前代码也不读取该旧式配置。未来若要填入真实凭据，须先重新评估存放方式。 |
| 未合并 worktree | **已决定保留** | `.worktrees/markdown-fallback-search/` 实现“结构化索引未命中时扫描 vault Markdown / txt 原文兜底”的知识库功能：9 个提交修改 `src/config.py`、`src/knowledge_base.py`、`src/app.py`、配置模板、README 与测试；另有未提交改动。用户决定保留，作为未来知识库增强飞书机器人能力的基础。其内部 `docs/openagent-local-deployment.md` 含历史服务器连接/部署细节，合并前需脱敏或移出。 |
| 知识库增强方向 | **已记录，规划中** | 用户确认后续要依靠知识库增强飞书机器人：① 服务器出问题时抽取日志、自动分析、给建议；② 给出测试场景/需求时切换专业智囊模式给测试建议。当前尚未实现，文档中已标注为规划方向，不写成当前能力。已存入长期记忆 `feishu_bot_knowledge_enhanced_direction.md`。 |
| `docs/hotfix_thresholds_excel_export.json` | 保留 | 虽被 Git 忽略，但它是热更字段的本地 Excel 导出，为 `hotfix-execution-master-plan.md` 引用的核对依据。 |

### 4.4 已确认的代码—文档差异

| 问题 | 证据 | 处置原则 | 状态 |
| --- | --- | --- | --- |
| `route identifier` 的展示边界 | `src/operation_message.py` 当前在确认卡展示 `route-cn` / `route-overseas`，相应测试固定断言。 | README、当前项目说明与 Worker / 路由记录现已按真实代码明确标为**待决规则冲突**；需用户决定它是可公开的固定逻辑标识，还是应从确认卡移除的敏感标识。 | 待产品安全决定。 |
| AI 研读标签与终态一致性 | `src/message_worker.py` 将 AI 返回文本用于展示，当前没有验证标签或与持久化终态一致性。 | AI 研读记录与当前架构说明现已表述为提示词期望与展示层限制；不得当作强制保证。若要强制，必须另行改代码并测试。 | **文档已修订；代码强化待决定。** |
| AI 研读的原始请求输入 | `src/message_worker.py` 当前直接将 `pending_job.text` 传入 AI 研读提示词，方法内未先遮罩。 | AI 研读记录已改为准确说明：日志和输出会脱敏，但原始请求的安全性依赖上游不持久化敏感内容；本方法不提供独立脱敏保证。 | **文档已修订；上游约束待持续核验。** |

## 5. 接下来的固定顺序

1. ~~创建 `docs/config-and-script-reference.md`~~ **已完成**；
2. ~~将索引链接加入 README、当前项目说明和知识入口~~ **已完成**；
3. ~~修订 AI 结果研读资料与当前架构说明~~ **已完成**；当前代码未做 AI 输出与终态一致性校验，已准确记录；
4. 归档未接入的热更 JSON 候选设计，并修正 archive 索引的可见链接文字 **已完成**；
5. 对全量当前文档进行一次链接、职责遗漏和代码事实复核；
6. 记录检查结果：测试、编译、派生产物检查与 Markdown 格式问题分别说明，不混同为环境验证；
7. 更新 `SESSION-HANDOFF.md`，供跨会话继续。

不在本任务中执行：真实 Muliu 调用、GS-1 连接或脚本覆盖、服务重启、生产或测试服配置改写、Git 提交或推送。除非用户另行明确授权。

## 6. 本轮已知检查结果

| 检查 | 结果 | 准确含义 |
| --- | --- | --- |
| `python -m pytest -q` | 386 passed，1 skipped，1 warning | 本地 Python 测试通过；不代表远端部署或游戏内效果。 |
| `python -m compileall src tests` | 通过 | 本地 Python 编译通过。 |
| `python tools/generate_runner_manifest.py --check` | 通过 | 仓库候选 manifest 与当前 catalog 一致。 |
| `python tools/sync_hotfix_planner_catalog.py --check` | 未通过 | 当前 Python 热更注册表与 catalog 的自动生成区不同步；尚未写入或同步调用合同。属于已知待办，不阻塞文档治理交付。 |
| `git diff --check` | **通过** | 本轮已清理全部尾随空格与多余空行。 |
| 新文档链接解析 | **通过** | `config-and-script-reference.md` 及三处互链文件的全部本地链接可解析。 |

## 7. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-10-27 | 创建本计划，收敛文档治理目标、边界、已完成资料、待处理清单和检查状态。 |
| 2026-10-27 | 新增项目内治理三件套：核心规则源、只读守门 Agent 与复杂任务治理 skill；规则覆盖事实边界、避免能力收缩、失败处理、授权门槛、执行合同、敏感信息、文档与验证表述。 |
| 2026-10-27 | 复查计划与用户需求的匹配度：确认 `docs/config-and-script-reference.md`（配置/脚本职责索引）仍是未创建的主交付物，已在 §4.2 标注为最高优先级缺口。确认 5 个无用文件清单、`date.py` 敏感流水数据历史遗留问题、未合并 worktree 与本地 Excel 导出的保留决定。 |
| 2026-10-27 | 删除 5 个无用文件（`calc.py`、`date.py`、`longmem.txt`、`docs/_tmp_hotfix_excel_extract.json`、`docs/gemini-code-1792483526256.html`）；归档热更 JSON 候选设计至 `docs/archive/`；修正归档 plans 索引的可见链接文字；验证归档链接全部可解析。 |
| 2026-10-27 | **主交付物落地**：创建 `docs/config-and-script-reference.md`（配置/脚本/工具职责索引），覆盖 config、src、tools、knowledge/scripts、deployment/gs1、tests 六大范围；从 README、当前项目说明、知识入口三处互链。清理全部 Markdown 尾随空格，`git diff --check` 通过；链接解析通过；测试 386 passed、compileall 通过、manifest 检查通过。 |
| 2026-10-27 | 修订 AI 结果研读与架构资料：明确 AI 首行标签、未知终态与业务效果限制目前只由提示词约束，未由代码校验；持久化终态才是可信来源。将确认卡展示 `route-cn` / `route-overseas` 的代码行为统一标为待决规则冲突，避免文档把它误写为已合规或与私有连接信息混同。 |
| 2026-10-27 | 用户决定：不重写 `date.py` 的 Git 历史；保持 `config/muliu_regions.json` 被跟踪的现状。复核后更正职责索引：`muliu_regions*.json` 是当前代码未读取的旧式占位资料；补全所有 catalog 已登记且有阅读副本的脚本；清理失效 `.gitignore` 条目。 |
| 2026-10-28 | 用户确认项目演进：早期为知识库问答助手，后改为运维机器人；决定保留 markdown-fallback worktree 作为未来知识库增强基础。规划方向：服务器日志问题诊断 + 测试智囊模式（尚未实现）。已在职责索引增加「项目演进与模块归属」三阶段说明，并存入长期记忆。 |
