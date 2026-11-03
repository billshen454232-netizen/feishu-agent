# 热更阈值注册表网页管理

> **状态：设计已定，待实施**  
> **日期：2026-10-30**  
> **关联计划：** [项目计划看板](../../hotfix-requirements-progress.html) STEP 22  
> **前置原则：** 配置与代码分离（用户长期要求；`KNOWN_THRESHOLDS` 塞在 Python 里是违规）

## 1. 目标

让热更阈值的增删改通过网页完成，加一条字段后**立即可用于飞书热更**，全程不需要碰 GS-1。

```text
网页填表单 → 校验 → AI 审核 → 入库 → 自动同步白名单 → 飞书即可用
```

反向支持：过期或不用的字段可**停用**（软删除）或**删除**（仅限未执行过的）。

## 2. 现状与问题

### 2.1 配置硬编码在 Python（违反配置/代码分离）

`tools/auto_hotfix_registry.py` 324 行里约 270 行是 `KNOWN_THRESHOLDS` 的 24 个条目——key、中文名、表名、字段名、别名、侧别全部写死在代码里。加一个字段要：改 Python → 跑测试 → git push → **跳板机手工覆盖 GS-1**。

同类问题还有：

| 文件 | 硬编码 |
| --- | --- |
| `tools/auto_hotfix_tool.py` | `SUPPORTED_PATCH_TYPES`、`DEFAULT_PATCH_TYPE` |
| `src/operation_message.py` | `SCRIPT_ROOT`、各脚本路径 |
| `src/muliu_executor.py` | `SCRIPT_ROOT`、`START_SCRIPT_PATH`、`BASIC_INFO_SCRIPT_PATH` |

### 2.2 GS-1 部署是昂贵操作

GS-1 是游戏服，**只能跳板机登录，不能 scp**。部署脚本 = 跳板机里手工复制，易出错（2026-10-29 曾因终端复制混入 `0x1B` 控制字符导致 `SyntaxError`）。

因此：**任何需要更新 GS-1 才能生效的方案都不可接受**。

### 2.3 现有数据流

```text
飞书 → Gateway（有 catalog 白名单）→ Muliu Task 89（HTTP）
     → GS-1 dispatcher → 跑 /home/serverGeneralScript 下脚本
```

数据走 HTTP，**只有脚本文件才需要部署**。所以只要让 GS-1 的脚本不依赖本地配置，加阈值就不再需要碰 GS-1。

## 3. 目标架构

### 3.1 单一入口、自动派生

```text
┌──────────────────────────────┐
│ 网页：阈值字段管理            │ ← 唯一维护入口
│  新增 / 编辑 / 停用 / 删除     │
└──────────────┬───────────────┘
               ↓ 保存时自动生成
┌──────────────────────────────┐
│ config/hotfix_thresholds.json │ ← 数据真源
└──────────────┬───────────────┘
               ↓ sync 工具
┌──────────────────────────────┐
│ catalog 模式 O 自动生成区      │
│  ·生效路径白名单（正则）        │ ← 不手工维护
│  ·中文名 / 别名路由表          │
│  ·候选 manifest               │
└──────────────┬───────────────┘
               ↓ git pull
      Gateway          Worker          GS-1
     （校验）        （复验）        （只跑脚本，永不更新）
```

**网页只维护一处，其余全是派生物。** 不会存在「网页加了但白名单没放行」。

### 3.2 各位置持有什么

| 位置 | 持有 | 更新方式 |
| --- | --- | --- |
| 开发机 | `config/hotfix_thresholds.json`（真源） | 网页写入 |
| Gateway | catalog 白名单 + 运行配置 | git pull |
| Worker | 同一份 catalog（复验）+ 自己的运行配置 | git pull |
| **GS-1** | **只有脚本，不持任何配置** | **不更新** |

## 4. 数据模型

`config/hotfix_thresholds.json`（**新 schema，覆盖现有的同名候选文件**——那套从未接入主链）：

```json
{
  "schema_version": 1,
  "thresholds": [
    {
      "key": "union_impeach_min",
      "name": "弹劾发起同盟人数检查",
      "aliases": ["弹劾人数", "弹劾发起同盟人数"],
      "table_name": "union_config_setting",
      "field_name": "union_impeach_union_member_min",
      "description": "发起弹劾所需的同盟最低人数（原值通常为50）",
      "scenarios": [],
      "state": "active",
      "source": "Excel 第 12 行",
      "notes": "需求来源标注：客户端",
      "created_at": "2026-10-30",
      "updated_at": "2026-10-30"
    }
  ]
}
```

字段说明：

| 字段 | 含义 | 约束 |
| --- | --- | --- |
| `key` | 受控简称，`-k` 用 | `^[a-z][a-z0-9_]{0,63}$`，全表唯一 |
| `name` | 中文标准名 | 全表唯一，AI 路由用 |
| `aliases` | 中文别名 | 全表唯一（含 name 不冲突） |
| `table_name` / `field_name` | Lua 生效路径 | Lua 标识符规则 |
| `description` | 字段说明 | 自由文本，脱敏 |
| `scenarios` | 默认剧本范围 | 数字数组，空 = 全部 |
| `state` | `active` / `disabled` | 停用不删数据 |
| `source` / `notes` | 需求来源与备注 | 证据台账用 |

**不设 `is_client` / `is_server`**：按用户 2026-10-29 决定，侧别由执行时自然语言决定（不说即双端），注册表不预判。Excel 里的侧别标注记入 `notes` 留痕。

## 5. 调用合同变更：`-k` → `-t <table.field>`

### 5.1 为什么要改

GS-1 的 `auto_hotfix_tool.py` 现在靠本地 `KNOWN_THRESHOLDS` 把 `-k union_impeach_min` 解成表/字段。要让 GS-1 不持配置，**生效路径必须随任务下发**。

### 5.2 改法

| | 现在 | 改后 |
| --- | --- | --- |
| args | `["-s","5010","-k","union_impeach_min","-v","2"]` | `["-s","5010","-t","union_config_setting.union_impeach_union_member_min","-v","2"]` |
| 白名单 | `-k` 的 24 个 key 正则 | `-t` 的 24 个 `table.field` 正则 |
| GS-1 | 查本地注册表 | 直接用下发的生效路径 |

**安全边界不变**：`<table.field>` 的正则白名单是 24 个精确值，与现在 `-k` 的 24 个 key 白名单等价。AI 依然只能选已登记项，不能注入任意表/字段。

### 5.3 侧别

沿用 [catalog 模式 O 的侧别约定](../../../config/muliu_script_catalog.md)：不说即双端两份，「热更服务端」传 `--patch-type common`，「热更客户端」传 `--patch-type client_avatar`。

## 6. 网页功能

### 6.1 列表

| 列 | 内容 |
| --- | --- |
| key / 中文名 | 受控简称与标准名 |
| 生效路径 | `table_name.field_name` |
| 别名 | 逗号分隔 |
| 说明 | `description` |
| 状态 | `active` / `disabled` |
| 来源 | `source` |

支持按状态筛选、按名称/字段搜索。

### 6.2 新增 / 编辑

表单字段：生效路径（表名 + 字段名）、字段说明、中文名、别名、默认剧本范围、来源。

**提交流程**：

```text
表单 → 确定性校验（硬门槛）→ AI 审核（建议）→ 写入 JSON
     → 跑 sync 生成白名单 / 路由表 / manifest → 显示结果
```

### 6.3 停用 / 删除

| 动作 | 条件 | 效果 |
| --- | --- | --- |
| **停用** | 任意 | `state: disabled`，白名单不再包含它，AI 无法调用；数据保留 |
| **删除** | **仅限从未执行过**的条目 | 物理删除；避免留下 GS-1 上的孤儿补丁 |

已执行过的条目只能停用——因为服务器上可能已有对应补丁文件，物理删除会让它们无法还原。

## 7. 校验与 AI 审核

### 7.1 确定性校验（不通过则拒绝保存）

- `key`、`name`、`aliases` 全表唯一（含与停用条目比对）
- `key` / `table_name` / `field_name` 符合标识符规则
- `aliases` 是数组且非单字符串
- `scenarios` 是数字数组

### 7.2 AI 审核（建议，不阻断）

| 检查 | 输出 |
| --- | --- |
| 字段说明与字段名是否自洽 | 一致 / 疑似不一致 + 理由 |
| 是否像已有条目的重复描述 | 与哪个 key 疑似重复 |
| 侧别建议 | 客户端 / 服务端 / 双端，仅供记录到 `notes` |

AI 只读取条目元数据，**不输出 Lua、不决定执行**。审核结论显示在网页上，由人决定是否继续保存。

## 8. 同步链

保存后自动执行：

```text
python tools/sync_hotfix_planner_catalog.py --write
python tools/generate_runner_manifest.py
```

生成：

1. catalog 模式 O 的生效路径白名单（正则）
2. catalog 的中文名 / 别名路由表
3. `deployment/gs1/muliu_runner_manifest.json`（候选 artifact）

## 9. 实施清单

| # | 项 | 涉及 |
| --- | --- | --- |
| 1 | 新 schema 的 `config/hotfix_thresholds.json`，24 条迁入 | 数据 |
| 2 | `tools/auto_hotfix_registry.py` 改为读 JSON，API 不变 | 后端 |
| 3 | `auto_hotfix_tool.py` 改为接受 `-t <table.field>`，不再查注册表 | **GS-1 一次性部署** |
| 4 | catalog 合同：`-k` → `-t`，白名单正则同步 | 合同 |
| 5 | 网页：列表 / 新增 / 编辑 / 停用 / 删除 + 校验 + AI 审核 | 前后端 |
| 6 | 同步链集成 | 工具 |
| 7 | `SUPPORTED_PATCH_TYPES`、脚本路径提入配置 | 配置分离 |
| 8 | 测试：数据校验、同步、合同、工具行为 | 测试 |

### 9.1 一次性部署成本

第 3 项改 `auto_hotfix_tool.py` 的行为，**必须部署一次 GS-1**（跳板机手工覆盖）。这是本次改造唯一一次 GS-1 操作——之后加/改/停用阈值**永不碰 GS-1**。

## 10. 不在本次范围

- 不改起服 / 关服 / 清档等其它脚本的调用合同（模式 A–N 保持手写骨架）
- 不自动部署 GS-1（保留人工确认）
- 不改 Muliu Task 89 调用链
- 不引入新的 Patch 类型

## 11. 验证方式

1. 网页新增一条测试阈值 → 白名单正则出现该生效路径 → 飞书能识别中文名 → 生成的 args 是 `-t <table.field>`
2. 停用该条 → 白名单不再包含 → 飞书无法识别
3. 网页新增**未经 GS-1 部署**的阈值 → 直接热更成功（验证「加完即用」）
4. 确定性校验各负例均被拒绝
5. 420+ 既有测试不回归

> **边界：** 网页保存成功、白名单更新成功、热更脚本退出成功，**都不等于游戏内已生效**；客户端效果仍须实际观察。
