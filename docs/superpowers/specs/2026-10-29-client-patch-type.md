# 阈值热更客户端 Patch 类型改造

> **状态：本地实现完成；真实客户端生效待环境验证。**  
> **日期：2026-10-29；实施完成：2026-10-29**  
> **来源：** 服务端开发确认「客户端的都要打 `client_avatar` patch」，用户实测定案机制。  
> **关联计划：** [项目计划看板](../../hotfix-requirements-progress.html) STEP 21。

## 1. 问题

给测试服热更阈值配置时，AI 生成的 Lua Patch 含 `IS_CLIENT` 与 `IS_SERVER` 双端逻辑，但 Patch 类型默认打成 `common`。服务端开发确认：**客户端侧的配置变更必须用 `client_avatar` 类型才能生效**。

后果：改「客户端也需要读取」的阈值（如 `union_impeach_min` 弹劾发起同盟人数）时，**服务端生效、客户端不生效**，客户端表现仍是旧值。

## 2. 现状（代码证据）

### 2.1 注册表的 `is_client` / `is_server` 是死字段

[tools/auto_hotfix_registry.py](../../../tools/auto_hotfix_registry.py) 的 `ThresholdDefinition` 定义了 `is_client=True`、`is_server=True`（第 28-29 行），并存入 `self.is_client` / `self.is_server`（第 41-42 行）。

**全仓库搜索确认：没有任何代码读取这两个字段来决定 patch 类型。** 它们是写后即忘的元数据。

### 2.2 Patch 类型硬编码为 common

```python
DEFAULT_PATCH_TYPE = "common"
...
selected_patch_type = patch_type or (
    definition.default_patch_type if definition else DEFAULT_PATCH_TYPE
)
```

24 个阈值全部 `default_patch_type="common"`，无一例外。

### 2.3 Patch 生成无条件输出双端逻辑

[tools/auto_hotfix_tool.py](../../../tools/auto_hotfix_tool.py) 的 `generate_lua_patch_content()` 总是生成：

```lua
if IS_CLIENT then ... end   -- 无论该阈值是否需要客户端
if IS_SERVER then ... end   -- 无论该阈值是否需要服务端
```

第 45 行的注释是**错误假设**：

```python
# common 类型可承载 IS_CLIENT 与 IS_SERVER 双端逻辑，是阈值热更的默认类型。
```

### 2.4 类型决定远端目录

```python
remote_patch_dir = "{}/{}".format(self.REMOTE_PATCH_ROOT, selected_patch_type)
```

`common` 与 `client_avatar` 落在不同目录。客户端加载链路只读 `client_avatar`，所以 `common` 里的 `IS_CLIENT` 分支是死代码。

### 2.5 JSON 注册表反而已有 sides 概念

[tools/hotfix_threshold_registry.py](../../../tools/hotfix_threshold_registry.py) 第 365-366 行：

```python
is_client="client" in sides,
is_server="server" in sides,
```

说明「按侧别分类」的思路在候选 JSON 注册表里已存在，但未接入主链。

## 3. 影响面

| 层 | 需要改动 |
| --- | --- |
| `tools/auto_hotfix_registry.py` | 用 `is_client` / `is_server` 推导或显式声明 patch 类型；默认值不再是 `common` |
| `tools/auto_hotfix_tool.py` | 按类型只生成对应侧别的 Lua 逻辑；修正第 45 行错误注释 |
| `config/muliu_script_catalog.md` 模式 O | `--patch-type` 的默认值、变量规则与说明需同步 |
| `knowledge/scripts/auto_hotfix_registry.py` | 镜像副本需同步 |
| `tests/test_auto_hotfix_tool.py` | 按侧别生成、类型推导、双端场景的测试 |
| `deployment/gs1/muliu_runner_manifest.json` | 若合同变化需重新生成 |
| 知识文档 | `auto_hotfix_tool` 用途与类型规则说明 |

## 4. 实证样本（用户提供 `C:\Users\v_shentao\Desktop\PATCH`）

用户一开始提供过示范 patch 文件夹，其中**包含与本次故障同一字段的正确写法**。逐个核对：

### 4.1 与本次故障同字段的正确样例

`client_avatar_patch_test_impeach.lua`：

```lua
if IS_CLIENT then
    do
        if G_SCENARIO_ID == 2001 then
            union_config_setting[ [=[union_impeach_union_member_min]=] ].content = [=[1]=]
        end
    end
end
```

对照本次 AI 生成的 patch：字段同为 `union_impeach_union_member_min`，但类型打成 `common`，且额外生成了 `IS_SERVER` 分支。

`client_avatar_patch_test_rank_quo.lua` 同型：

```lua
if IS_CLIENT then
    do
        if G_SCENARIO_ID == 10 or G_SCENARIO_ID == 11 then
            season_process_season_config[ [=[capital_city_extra_quota]=] ].content = [=[2]=]
        end
    end
end
```

### 4.2 样本统计

| 文件 | 类型 | 分支 |
| --- | --- | --- |
| `client_avatar_patch_test_impeach.lua` | `client_avatar` | 仅 `IS_CLIENT` |
| `client_avatar_patch_test_rank_quo.lua` | `client_avatar` | 仅 `IS_CLIENT` |
| `client_avatar_patch_1.lua` | `client_avatar` | 仅 `IS_CLIENT` |
| `client_avatar_patch_36666.lua` | `client_avatar` | 仅 `IS_CLIENT` |
| `client_avatar_patch_worldcup.lua` | `client_avatar` | 仅 `IS_CLIENT` |
| `client_avatar/*.txt`（5 个） | `client_avatar` | 仅 `IS_CLIENT`（或 `do` 块 + `IS_CLIENT`） |
| `avatar_patch_worldcup.lua` | `avatar` | 仅 `IS_SERVER` |
| `common_patch_36666(1).lua` | `common` | 仅 `IS_SERVER` |
| **`common_patch_38944.lua`** | **`common`** | **`IS_CLIENT` + `IS_SERVER` 双分支** |

**结论：`client_avatar` = 仅客户端，`avatar` = 仅服务端，样本 100% 一致。**

### 4.3 线上真实 patch 佐证

用户提供的线上 `client_avatar_patch_81097.lua`（`patch_order_list.json` 中 `client_avatar_patch_0.lua` 至 `client_avatar_patch_81366.lua` 共 6 个 client_avatar 条目）首行即：

```lua
if not IS_CLIENT then return end
```

显式在非客户端环境早退，与样本一致。

### 4.4 样本给出的正确 Lua 写法

样本风格与当前生成器有差异，改造时应向样本对齐：

| 项 | 样本写法 | 当前生成器 |
| --- | --- | --- |
| 场景判断 | `G_SCENARIO_ID == 2001` 直接比较 | `TARGET_SCENARIOS` 数组 + `is_target_scenario()` 函数 |
| 值写入 | `do ... end` 包裹 | 无包裹 |
| 防御性判空 | 无 | `if union_config_setting and ... then` |
| 双端 | 分文件各写一侧 | 单文件写双分支 |

## 5. 实测已定案（用户 2026-10-29 验证通过）

### 5.1 实测过程

用户在测试服挂载了两份补丁：

```json
"2": { "patch_type": "common",        "order": 0, "file": "auto_common_patch_union_impeach_union_member_min_2002.lua" },
"3": { "patch_type": "client_avatar", "order": 2, "file": "client_avatar_patch_test_impeach_3001.lua" }
```

其中 `"3"` 是按示范样本风格生成的客户端补丁：

```lua
if IS_CLIENT then
    do
        if G_SCENARIO_ID == 3001 then
            union_config_setting[ [=[union_impeach_union_member_min]=] ].content = [=[2]=]
        end
    end
end
```

### 5.2 实测结论

**服务端和客户端都生效了。** 由此确认：

| Patch 类型 | 加载方 | 生效分支 |
| --- | --- | --- |
| `common` | 服务端 | `IS_SERVER` |
| `client_avatar` | 客户端 | `IS_CLIENT` |

**`common` 本身是生效的**，它承担服务端一侧；此前失败是因为**客户端那一侧没有 `client_avatar` 补丁**。

### 5.3 最终机制

```text
内容写成一份（含 IS_CLIENT + IS_SERVER 双 guard）
  → 复制到两个文件
  → common_patch_xxx.lua          → 服务端加载 → IS_SERVER 分支生效
  → client_avatar_patch_xxx.lua   → 客户端加载 → IS_CLIENT 分支生效
```

**guard 让内容通用，文件名前缀 / 类型决定谁加载。** 两个文件内容可以完全一样，只改文件名——这正是开发说的「写一份脚本放到两个文件里面，改名字就行，节省时间」。

## 6. 方案（已定型）

### 6.1 核心规则

1. **`is_client` / `is_server` 驱动 patch 类型**，不再是死字段；
2. 客户端可见阈值 → `client_avatar`；仅服务端阈值 → `common`；
3. **双端阈值 → 生成两份内容完全相同的 patch**，同 key 分落 `common/` 与 `client_avatar/` 两个目录，仅文件名前缀不同；
4. Patch 内容**向示范样本对齐**：`G_SCENARIO_ID` 直接比较、`do ... end` 包裹、去掉多余判空；
5. **保留 `IS_CLIENT` / `IS_SERVER` 双 guard**——内容通用的前提，也是误放目录时的保险。

### 6.2 为什么不裁剪分支

早先考虑过「按侧别只生成对应分支」。实测证明**不必要且有害**：

- 不必要：guard 已经保证另一侧不执行；
- 有害：内容不再通用，双端值要维护两份不同脚本，反而比「复制+改名」更易出错。

因此**保留双 guard 的通用内容**，靠文件类型分发。

## 7. 实施清单（已完成）

| # | 项 | 状态 |
| --- | --- | --- |
| 1 | `is_client` / `is_server` 驱动 patch 类型（`derived_patch_types()`），删除误导性的 `default_patch_type` | ✅ |
| 2 | 双端阈值生成两份内容相同的 patch，同 key 分落 `common/` 与 `client_avatar/` | ✅ |
| 3 | 修正 `auto_hotfix_tool.py` 类型语义注释（原第 45 行错误假设） | ✅ |
| 4 | Lua 写法对齐示范样本：`G_SCENARIO_ID ==` 直接比较、`do ... end` 包裹、去掉判空、保留双 guard | ✅ |
| 5 | 测试：类型推导、显式类型覆盖、双端双文件、复用已有文件、双 guard 结构 | ✅ 417 passed |
| 6 | catalog 模式 O 路由表改为显示实际类型（`common` + `client_avatar`） | ✅ |
| 7 | 同步镜像副本 `knowledge/scripts/` | ✅ |
| 8 | 迁移存量 `common` 阈值 Patch | ⬜ 待环境操作 |

### 7.1 附带解决

本次重新生成 catalog 时顺带修复了 **STEP 19（catalog 同步检查未通过）**：环境名从「越南DEV环境」等同步为 `server_environments.json` 的「越南服DEV环境」等。`sync_hotfix_planner_catalog.py --check` 现已通过。

## 8. 不在本次改造范围

- 不改 `cc_patch.py` 的 Patch 列表管理；
- 不引入新的 Patch 类型；
- 不改 Task 89 / dispatcher 调用链；
- 不动 `hotfix_thresholds.json` 候选 JSON 注册表（除非单独决定统一注册表）。

## 9. 验证方式

改造后必须逐项验证：

1. 客户端可见阈值（如 `union_impeach_min`）改完后，**客户端界面数值实际变化**；
2. 服务端可见阈值改完后，服务端逻辑实际变化；
3. 双端阈值两侧都变化；
4. `--restore-auto` 后两侧都回到原始值；
5. `patch_order_list.json` 中的 `patch_type` 与文件落位一致。

> **关键边界：** 脚本退出成功、Patch 生成、上传、挂载、热更请求成功，**都不等于客户端已生效**。客户端效果必须以游戏内/客户端实际观察为证据。
