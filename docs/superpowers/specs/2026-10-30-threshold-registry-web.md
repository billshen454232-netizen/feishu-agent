# 热更阈值注册表网页管理

> **状态：实施中（2026-11-04 改版）**
> **日期：2026-10-30 初版；2026-11-04 按「网页挂 Gateway」改版**
> **关联计划：** 项目计划看板 STEP 22（https://ops.ttlink.asia/plan）
> **前置原则：** 配置与代码分离（`KNOWN_THRESHOLDS` 塞在 Python 里是违规）

## 1. 目标

热更阈值的增删改在 `https://ops.ttlink.asia/thresholds` 完成，保存后**立即可用于飞书热更**：不改代码、不 git 推拉、不碰 GS-1、不重启服务。

```text
网页填表单 → 确定性校验 →（可选）AI 审核建议 → 写入 Gateway SQLite → 下一条飞书请求即可用
```

过期字段可**停用**（保留数据）或**删除**（仅限从未执行过的）。

## 2. 2026-11-04 改版要点

| 项 | 初版 | 改版 | 原因 |
| --- | --- | --- | --- |
| 数据真源 | 开发机 `config/hotfix_thresholds.json` + git pull | **Gateway SQLite**，`config/hotfix_thresholds.seed.json` 只做种子 / 快照 | 网页挂在 Gateway；保存即生效，不需要推拉 |
| GS-1 | 新增 `-t table.field`，一次性部署 | **不部署**：沿用 GS-1 现有的 `-k <真实字段名> --table <表名>` | GS-1 工具本来就支持未收录字段（`apply_patch` 的 `custom_table` 分支） |
| 白名单 | sync 工具写进 catalog | Gateway 每次规划时从库实时生成 | 同上 |
| Worker 复验 | 精确匹配同一份白名单 | **结构校验**：表名 / 字段名必须是合法 Lua 标识符 | Gateway 已精确匹配；Worker 不需要随阈值变更同步 |
| 剧本范围 / 侧别 | `scenarios` 字段 | 去掉 | 24 条现有数据全部为空；侧别由执行时自然语言决定 |

## 3. 两段式合同（安全边界）

AI 不接触 Lua 表名 / 字段名，只能选已登记的 ASCII key；Gateway 确定性展开成执行形态。

```text
Planner 看到（动态 catalog）      -s 5010 -k union_impeach_min -v 1 [--patch-type common]
        │  Gateway：key 必须在库内且 active，否则拒绝
        ▼  确定性展开（不经 AI）
执行形态（静态 catalog）          -s 5010 -k union_impeach_union_member_min --table union_config_setting -v 1 --patch-type common
                                  -s 5010 -k union_impeach_union_member_min --table union_config_setting -v 1 --patch-type client_avatar
```

- 不说侧别 → 展开成 `common` + `client_avatar` 两步（双端，宁可多写不漏写）；说了侧别 → 一步；
- 清理 / 还原：`--cleanup-legacy <key>` / `--restore-auto <key>` 展开为对应真实字段名；
- 静态 `config/muliu_script_catalog.md` 只登记**执行形态**（Worker 与 Gateway 防火墙用），表名 / 字段名为结构正则 `[A-Za-z_][A-Za-z0-9_]{0,63}`；
- 动态 catalog（仅 Gateway Planner 使用）把热更合同替换成 Planner 形态，key 正则由库内 active 条目实时生成，并渲染中文路由表；
- 规划结果先按动态合同校验，展开后再按静态合同（防火墙）校验，确认卡片与入库计划都是执行形态。

## 4. 数据模型（Gateway SQLite）

| 表 | 内容 |
| --- | --- |
| `hotfix_thresholds` | `key`（主键，创建后不可改）、`name`、`aliases`（JSON 数组）、`table_name`、`field_name`、`description`、`state`（active / disabled）、`source`、`notes`、`sort_order`、`created_at`、`updated_at`、`updated_by` |
| `hotfix_threshold_events` | 每次修改的字段、旧值、新值、操作人、时间 |

首次启动表为空时从 `config/hotfix_thresholds.seed.json` 导入（24 条，迁自 `tools/auto_hotfix_registry.py`）。

### 4.1 确定性校验（不通过拒绝保存）

- `key`：`^[a-z][a-z0-9_]{0,63}$`，全表唯一；
- `name`、`aliases`：中文路由词全表唯一（含停用条目，避免启用后冲突）；别名必须是数组；
- `table_name` / `field_name`：`^[A-Za-z_][A-Za-z0-9_]{0,63}$`；`table.field` 全表唯一；
- 已执行过的条目：表名 / 字段名不可改、不可删除，只能停用（服务器上可能已有对应补丁，改路径会让还原找不到）。

「执行过」= `message_jobs` 中进入过执行的任务（done / failed / submitted_result_unknown / submission_in_progress / leased / queued_for_worker），其计划步骤引用该 key 或字段名。

### 4.2 AI 审核（建议，不阻断）

网页「AI 审核」按钮：把条目与现有条目的中文名 / 生效路径交给报告模型池，返回字段说明与字段名是否自洽、疑似重复的 key、侧别建议。AI 只读元数据、不输出 Lua、不决定保存。

## 5. 网页

`/thresholds`（同一套 Cloudflare Access + Host 校验）：

- 列表：中文名、key、生效路径、别名、说明、状态、来源、最后修改；搜索、状态筛选；
- 新增 / 编辑（行内展开）、停用 / 启用、删除（仅未执行过）、AI 审核、修改历史；
- 接口：`GET /api/thresholds`、`POST /api/thresholds`、`PUT /api/thresholds/{key}`、`DELETE /api/thresholds/{key}`、`POST /api/thresholds/review`、`GET /api/thresholds/events`。

## 6. 影响面

| 位置 | 变化 |
| --- | --- |
| Gateway | 新表；Planner 用动态 catalog；规划后展开；新页面 |
| Worker | 拉新静态 catalog（执行形态合同）后重启；之后加阈值不再需要动 Worker |
| GS-1 | **不变**。前提：GS-1 上的 `auto_hotfix_tool.py` 支持 `--table`（需人工确认一次） |
| `tools/auto_hotfix_registry.py` | 仅作为 GS-1 部署件保留（GS-1 工具仍 import 它）；机器人不再读取 |
| `tools/sync_hotfix_planner_catalog.py` | 只同步环境映射与候选 manifest，不再处理热更白名单 |

## 7. 验证方式

1. 网页新增一条测试阈值 → 下一条飞书请求能识别中文名 → 确认卡片是 `-k <字段> --table <表>` 两步；
2. 停用该条 → 飞书不再识别；
3. 新增一条 GS-1 本地注册表里**没有**的字段 → 直接热更成功（验证「加完即用」）；
4. 确定性校验各负例被拒绝；已执行条目不能删、不能改路径；
5. 既有测试不回归，并在 Gateway 的 Python 3.9 上跑全量。

> **边界：** 网页保存成功、白名单更新成功、热更脚本退出成功，**都不等于游戏内已生效**；客户端效果仍须实际观察。

## 8. 不在本次范围

- 不改其它脚本的调用合同；不改 Muliu Task 89 调用链；不引入新 Patch 类型；
- 不改 GS-1 上的 `auto_hotfix_tool.py`（`SUPPORTED_PATCH_TYPES` 等 GS-1 侧硬编码留待以后单独评估）。
