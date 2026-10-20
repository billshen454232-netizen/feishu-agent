# Muliu 测试服脚本调用注册表

此文件是飞书机器人生成 Muliu `path` 与 `args` 的**唯一执行资料**，不是 Linux 原始脚本的完整说明书。

> **注册表规则**：只有本文件中明确登记的“允许调用模式”可以生成执行计划。每个模式都给出了脚本路径和参数数组模板；模型只能替换模板中声明的变量，不能从脚本名、相近词义、历史经验或原始脚本可能存在的其他分支推断额外参数。

## 全局生成规则

1. 将用户请求拆为按顺序执行的一个或多个“允许调用模式”。每个步骤都必须使用一个模式给出的精确 `path` 和 `args` 模板；不得编造脚本、参数或步骤。
2. `path` 必须逐字使用资料中的绝对路径；`args` 必须是 JSON 字符串数组，模板里的每个元素都要独立保存，不能拼成 Shell 命令。
3. 一个计划最多 10 步。AI 可以拆分用户明确提出且每一步都已登记的顺序动作；不得省略用户动作、补充用户未要求的动作，或把一个步骤的参数混入另一个步骤。步骤按数组顺序执行：任一步出现明确失败或结果未知时，后续步骤一律不执行。
4. `<server_id>` 必须是 3 至 8 位 ASCII 数字，正则为 `[0-9]{3,8}`；在 JSON `args` 中必须作为字符串单独传入，例如 `"6001"`。用户若把环境标签和服号连写，例如 `HMT5000`，其中 `HMT` 是环境描述、`5000` 才是 `<server_id>`；计划只能把 `"5000"` 传给脚本，环境标签仅可写入摘要或描述。其他变量也必须符合对应调用模式的精确格式。
5. 不得把未登记参数、`bash`/`python` 命令、工作目录或环境变量加入计划。Linux 原始脚本支持但本文件未登记的调用形状，仍视为不可调用。
6. 每个请求必须输出受限 JSON 决策：`kind:"operation"` 只在用户请求的每个动作都能精确匹配已登记合同、且步骤总数不超过上限时使用；咨询当前能力、用途、风险、参数或“能否执行”时使用 `kind:"knowledge"` 和空步骤；有执行意图但缺少必要信息时使用 `kind:"clarify"` 和空步骤；任一必要动作未登记时使用 `kind:"reject"` 和空步骤。所有非 `operation` 决策都不得进入确认或执行。
7. 请求缺少合法服务器编号、意图不明确、要求未登记功能，或任一必要动作无法精确匹配模式时，应返回例如：

   ```json
   {"kind":"clarify","summary":"需要补充合法服务器编号","steps":[]}
   ```

8. 本文件只规定可生成的调用形状；模型生成后仍会经过严格 JSON 校验、本地防火墙、调用合同校验和群内确认。资料中的“查询”不等于无需确认，也不表示不会接触远端服务器。

## 允许调用模式

### 模式 A：查询测试服基础信息与剧本数据（basic_info.sh）

- **用途**：读取测试服基础状态、当前时间、按剧本反查所有服务器、查询玩家账号历史赛季与积分。
- **精确脚本路径**：`/home/serverGeneralScript/basic_info.sh`
- **已登记参数数组模板**：
  - **A1. 单服基础信息**：`["<server_id>"]`
    - 匹配：`查询 6001 服基础信息`、`查看 6001 服务器当前状态`、`查看 6001 服开服信息`、`检查 6001 服版本和进程`、`查询 6000 服当前剧本`、`6000 服现在是什么剧本`。
  - **A2. 单服当前时间**：`["<server_id>", "ctime"]`
    - 匹配：`查询 5000 服当前时间`、`查看 5000 服服务器时间`。
  - **A3. 按剧本反查所有服务器**：`["ssinfo", "<scenario>"]`
    - 匹配：`查看目前所有服务器哪个是2001剧本`、`查询 2001 剧本有哪些服务器`、`哪些服是 1002 剧本`、`2001剧本服务器有哪些`。
  - **A4. 查询账号经历过的赛季**：`["ssnum", "<account>"]`
    - 匹配：`查询账号 xxx 经历过的赛季`、`查看玩家账号 xxx 历史赛季`。
  - **A5. 查询玩家赛季积分**：`["<server_id>", "<user_id>"]`
    - 匹配：`查询 5000 服玩家 123456 的赛季积分`。
- **预期输出**：目标服配置版本；或指定剧本匹配到的服务器列表；或账号赛季积分记录。
- **远端影响**：均为只读查询，不修改任何远端数据。

标准计划示例：

```json
{
  "kind": "operation",
  "summary": "查询 6001 服基础信息",
  "steps": [
    {
      "path": "/home/serverGeneralScript/basic_info.sh",
      "args": ["6001"],
      "description": "查询 6001 服基础信息、版本、开服时间和进程状态"
    }
  ]
}
```

```json
{
  "kind": "operation",
  "summary": "查询 2001 剧本的所有服务器",
  "steps": [
    {
      "path": "/home/serverGeneralScript/basic_info.sh",
      "args": ["ssinfo", "2001"],
      "description": "查询归属 2001 剧本的所有服务器信息"
    }
  ]
}
```

### 模式 B：关服

- **用途**：关闭一个明确指定的测试服。
- **匹配的自然语言**：`关 5000 服`、`关闭 5000 服务器`、`5000 服正常关服`。
- **精确脚本路径**：`/home/serverGeneralScript/shutdown`
- **精确参数数组模板**：`["<server_id>"]` 或 `["<server_id>", "<shutdown_mode>"]`。
- **参数顺序**：第 1 项必须是 3 至 8 位数字服务器编号；第 2 项如提供，只能是 `normal`、`kill`、`autokill`、`gvgzone` 或 `gvgdaemon`。
- **远端影响**：停止目标服 game 与 Zone 服务；`kill`、`autokill` 等模式可能强制终止进程。此操作会造成目标测试服中断，仍必须先在群内确认。

标准计划示例：

```json
{
  "kind": "operation",
  "summary": "关闭 5000 服",
  "steps": [
    {
      "path": "/home/serverGeneralScript/shutdown",
      "args": ["5000"],
      "description": "正常关闭 5000 服 game 与 Zone 服务"
    }
  ]
}
```

### 模式 C：启动测试服

- **用途**：启动一个明确指定的测试服的 Zone 和游戏逻辑服务。
- **匹配的自然语言**：`启动 5000 服`、`开 5000 服务器`、`启动 5000 服游戏服务`。
- **精确脚本路径**：`/home/serverGeneralScript/start`
- **精确参数数组模板**：`["<server_id>"]` 或 `["<server_id>", "noz"]`
- **参数顺序**：第 1 项必须是 3 至 8 位数字服务器编号；第 2 项如提供，只能是 `noz`。
- **远端影响**：启动目标服服务；脚本返回成功不等同于服务已完全就绪，执行后应再查询基础信息核对。

### 模式 D：清服

- **用途**：停止一个明确指定的测试服并清理其逻辑与 Zone 数据。
- **匹配的自然语言**：`清 5000 服`、`清档 5000`、`重置 5000 测试服`。
- **精确脚本路径**：`/home/serverGeneralScript/clear`
- **精确参数数组模板**：`["<server_id>"]`、`["<server_id>", "by"]` 或 `["<server_id>", "noby"]`
- **参数顺序**：第 1 项必须是 3 至 8 位数字服务器编号；第 2 项如提供，只能是 `by` 或 `noby`。
- **远端影响**：会关服并清理目标逻辑与 Zone 数据，属于破坏性操作；仍必须先在群内确认。

### 模式 E：修改赛季和剧本

- **用途**：修改一个明确指定测试服的赛季和剧本配置。
- **匹配的自然语言**：`把 5000 服改为赛季 3、剧本 2003`、`设置 5000 服赛季和剧本`。
- **精确脚本路径**：`/home/serverGeneralScript/modify_game_config.sh`
- **精确参数数组模板**：`["<server_id>", "<season>", "<scenario>"]`
- **参数顺序**：三个参数均为独立的十进制数字字符串；不能加入其他参数。
- **远端影响**：会直接改写目标服 `NSLG_SEASON` 和 `NSLG_SCENARIO`；仍必须先在群内确认。

### 模式 F：检查或设置 Debug 开服时间

- **用途**：检查或修改一个明确指定测试服的 Debug 开服时间配置。
- **匹配的自然语言**：`检查 5000 服开服时间配置`、`5000 服使用当前开服时间`、`设置 5000 服开服时间戳 1790820000`。
- **精确脚本路径**：`/home/serverGeneralScript/starttime`
- **精确参数数组模板**：`["<server_id>"]`、`["<server_id>", "ctime"]` 或 `["<server_id>", "<timestamp>", "--backup", "yes"]`
- **参数顺序**：服务器编号与时间戳必须为数字；修改时间时必须带 `--backup yes`。
- **远端影响**：带时间参数时会改写远端配置或时间戳文件；仍必须先在群内确认。

### 模式 G：检查起服失败原因

- **用途**：读取一个明确指定测试服的固定 supervisor 启动日志，并提取近期启动异常信号。
- **匹配的自然语言**：`检查 5000 服起服失败原因`、`查询 5000 服启动失败日志`、`5000 服为什么起不来`、`查看 5000 服启动报错`。
- **精确脚本路径**：`/home/serverGeneralScript/server_log_query.py`
- **精确参数数组模板**：`["--server-id", "<server_id>", "--profile", "startup_errors", "--max-lines", "50"]`
- **参数顺序**：必须逐字使用以上选项与固定 profile；只替换合法数字 `<server_id>`。
- **预期输出**：固定 supervisor 日志中的 `error`、`fail`、`exception`、`fatal`、`panic`、`listen` 或 `traceback` 信号；无匹配时会明确显示固定档案未发现匹配日志。
- **远端影响**：只读固定日志档案，不接受任意路径、文件名、关键词或 Shell 参数；仍须群内确认。

### 模式 H：管理 Patch 列表和热更

- **用途**：查询或管理明确指定测试服的 `patch_order_list.json`，以及执行 Patch 热更。
- **匹配的自然语言**：`查询 6001 服务器的 Patch 列表`、`检查 5010 是否存在 avatar_patch_79941 patch`、`核实 5010 服是否包含某个补丁`、`对比 6001 和 6002 的 Patch`、`备份 6001 服 Patch 列表`、`恢复 6001 服 Patch 列表`、`清理 6001 服缺失 Patch 条目`、`热更 6001 服 Patch`。
- **精确脚本路径**：`/home/serverGeneralScript/cc_patch.py`
- **已登记参数数组模板**：
  - 查询单服：`["-s", "<server_id>", "-ck"]`；
  - 对比两服：`["-s", "<server_id_a>", "<server_id_b>", "-ck"]`；
  - 备份或恢复：`["-s", "<server_id>", "-r", "bak"]` 或 `["-s", "<server_id>", "-r", "re"]`；
  - 检查并清理缺失条目：`["-s", "<server_id>", "-ck", "-m"]`；
  - 热更：`["-s", "<server_id>", "-u"]`；
  - 删除指定Patch：`["-s", "<server_id>", "-d", "<patch_file>"]`；
  - 上传/更新指定Patch：`["-s", "<server_id>", "-f", "<patch_name>"]`。
- **远端影响**：查询与对比会读取远端列表；清理缺失条目、恢复备份、删除Patch和热更会修改远端状态，仍须群内确认。

标准计划示例：

```json
{
  "kind": "operation",
  "summary": "查询 6001 服当前 Patch 列表",
  "steps": [
    {
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": ["-s", "6001", "-ck"],
      "description": "查询 6001 服当前 Patch 列表并检查缺失文件"
    }
  ]
}
```

### 模式 I：批量查询多服务器代码版本

- **用途**：读取指定多台服务器或指定环境的当前代码版本（通过并发 SSH 读取各服 release_etc/version.txt）。
- **匹配的自然语言**：`查看海外所有DEV环境服务器代码版本`、`查询海外所有DEV环境代码版本`、`查看港澳台DEV代码版本`、`查询韩服DEV代码版本`、`查看国服weekly代码版本`、`查看 5000,5001 服务器代码版本`。
- **精确脚本路径**：`/home/serverGeneralScript/batch_server_query.py`
- **已登记参数数组模板**：`["--servers", "<server_ids>", "--label", "<label>"]`
- **参数顺序**：固定为 4 个参数：
  1. `--servers`
  2. `<server_ids>`：逗号分隔的合法服号列表，例如 `"5000,5001,6000,6001"`。
  3. `--label`
  4. `<label>`：查询概述标签，例如 `"海外所有DEV环境"`。
- **环境服号映射表（AI 根据用户提到的环境自动展开）**：
  - `海外所有DEV环境`：`5000,5001,6000,6001,7001,7002`（包含港澳台 5000/5001、韩服 6000/6001、越南 7001/7002）
  - `海外所有weekly环境`：`5010,5011,5012,5013,6010,6011,7010,7011`
  - `海外所有QA环境`：`5020,5021,6020,6021,7020`
  - `港澳台DEV环境`：`5000,5001`
  - `港澳台weekly环境`：`5010,5011,5012,5013`
  - `港澳台QA环境`：`5020,5021`
  - `韩服DEV环境`：`6000,6001`
  - `韩服weekly环境`：`6010,6011`
  - `韩服QA环境`：`6020,6021`
  - `韩服提审环境`：`6100,6101`
  - `越南DEV环境`：`7001,7002`
  - `越南weekly环境`：`7010,7011`
  - `越南QA环境`：`7020`
  - `国服weekly环境`：`2003,2005,2010,2015,2016,2017,2019,2213,2018,2020,2022`
  - `国服QA环境`：`2006,2007,2008,2011,2212,2028`
  - `国服noversion环境`：`2110,2009,2004,2002,2214,2021,2030,2031,2026,2032`
- **预期输出**：按目标服务器列表并发汇总版本、对齐表格与版本一致性统计。
- **远端影响**：只并发读取版本文件，不修改任何远端数据。

标准计划示例：

```json
{
  "kind": "operation",
  "summary": "批量查询海外所有DEV环境服务器代码版本",
  "steps": [
    {
      "path": "/home/serverGeneralScript/batch_server_query.py",
      "args": ["--servers", "5000,5001,6000,6001,7001,7002", "--label", "海外所有DEV环境"],
      "description": "并发查询 5000,5001,6000,6001,7001,7002 服务器代码版本"
    }
  ]
}
```

### 模式 J：清理逻辑服数据库（clear_logic_game.py）

- **用途**：单独清理指定测试服的 Game 游戏逻辑数据库（MongoDB）。
- **匹配的自然语言**：`清理 5000 逻辑服数据库`、`单独清 5000 游戏服数据`。
- **精确脚本路径**：`/home/serverGeneralScript/clear_logic_game.py`
- **精确参数数组模板**：`["<server_id>"]`
- **远端影响**：删除目标服游戏逻辑库集合，破坏性操作，必须群内确认。

### 模式 K：清理战区数据库（clear_zone.py）

- **用途**：单独清理指定测试服的 Zone 战区数据库（MongoDB）。
- **匹配的自然语言**：`清理 5000 战区数据库`、`单独清 5000 zone数据`。
- **精确脚本路径**：`/home/serverGeneralScript/clear_zone.py`
- **精确参数数组模板**：`["<server_id>"]`
- **远端影响**：删除目标服战区库集合，破坏性操作，必须群内确认。

### 模式 L：底层数据库单项清理（cleardb.py）

- **用途**：对指定测试服执行底层数据库单项重置与集合清理。
- **匹配的自然语言**：`清理 5000 数据库`、`重置 5000 数据库集合`。
- **精确脚本路径**：`/home/serverGeneralScript/cleardb.py`
- **精确参数数组模板**：`["<server_id>"]`
- **远端影响**：清理数据库，破坏性操作，必须群内确认。

### 模式 M：免重启热更服务器阈值配置（auto_hotfix_tool.py）

- **用途**：通过自动生成内存 Patch 并触发热更，免重启秒级修改测试服的人数限制、开启阈值、名额等数值配置。
- **匹配的自然语言**：
  - `给 5010 服热更弹劾人数为 1`
  - `帮我设置 5010 服霸业名额为 1`
  - `修改 5010 服弹劾发起同盟人数为 1`
  - `热更 5010 服务器同盟令人数为 1`
  - `把 5010 服 S7 专属建筑开启人数改为 1`
  - `给 5010 服以 common 类型热更霸业名额为 1`
  - `以 battle 类型给 5010 服热更弹劾人数为 1`
  - `清理 5010 服霸业名额的旧版自动补丁`
  - `还原 5010 服霸业名额的自动热更`
- **精确脚本路径**：`/home/serverGeneralScript/auto_hotfix_tool.py`
- **精确参数数组模板**：
  - 默认类型：`["-s", "<server_id>", "-k", "<key>", "-v", "<value>"]`，阈值配置默认写入 `common` 类型。
  - 显式类型：`["-s", "<server_id>", "-k", "<key>", "-v", "<value>", "--patch-type", "<patch_type>"]`。
  - 清理旧版：`["-s", "<server_id>", "--cleanup-legacy", "<key>"]`，仅移除早期误挂在 `client_avatar` 类型的旧命名自动 Patch，保留当前 `common` Patch。
  - 完整还原：`["-s", "<server_id>", "--restore-auto", "<key>"]`，移除该服该字段的全部自动 Patch，让原始配置重新加载。
- **变量规则**：
  - `<server_id>`：目标测试服编号（3-8位数字，如 `"5010"`）。
  - `<key>`：只能使用下方自动生成中文路由表映射出的已登记 ASCII key；不能直接传中文、真实 Lua 字段名或未登记 key。
  - `<value>`：目标数值（数字字符串，如 `"1"`、`"20"`）。
  - `<patch_type>`：仅在明确指定类型时填写，只允许 `avatar`、`battle`、`center`、`client_account`、`client_avatar`、`common`、`login`、`scene`、`test`、`union`、`unionmirror`。类型决定远端目录和清单 `patch_type`，不能填写路径或任意文本。
- **清理与还原规则**：执行前按服号和字段精确筛选清单中的自动 Patch，先备份清单；清理旧版只处理历史 `client_avatar/auto_patch_...` 文件，完整还原才会移除该字段的所有自动 Patch。两种动作都会更新清单、删除对应自动 `.lua` 文件并重新触发热更。
- **远端影响**：生成、清理或还原 Patch 都会修改目标服运行时配置，高风险操作，必须群内卡片确认。

<!-- HOTFIX_THRESHOLD_REGISTRY:BEGIN -->
#### 已登记热更阈值中文路由（自动生成）

> 唯一来源：`tools/auto_hotfix_registry.py`。仅本表中的中文标准名和别名可以映射到对应 `-k`；不得按相近词义创建别名。

- 中文标准名：`弹劾发起同盟人数检查`；别名：`弹劾人数`、`弹劾发起同盟人数`；执行：`-k union_impeach_min`；默认 Patch 类型：`common`
- 中文标准名：`弹劾发起者繁荣度检查`；执行：`-k union_impeach_power_min`；默认 Patch 类型：`common`
- 中文标准名：`弹劾发起者同盟繁荣度排名要求`；执行：`-k union_impeach_power_rank`；默认 Patch 类型：`common`
- 中文标准名：`同盟精锐AI大营人数要求`；执行：`-k union_rage_min_member`；默认 Patch 类型：`common`
- 中文标准名：`同盟精锐AI大营等级`；执行：`-k union_rage_min_level`；默认 Patch 类型：`common`
- 中文标准名：`同盟令获取同盟人数阈值`；别名：`同盟令人数`；执行：`-k union_token_least_member`；默认 Patch 类型：`common`
- 中文标准名：`同盟初始人数上限`；执行：`-k union_max_member_init`；默认 Patch 类型：`common`
- 中文标准名：`同盟人数最大上限`；执行：`-k union_max_member_limit`；默认 Patch 类型：`common`
- 中文标准名：`合盟人数限制`；执行：`-k union_merge_min_member`；默认 Patch 类型：`common`
- 中文标准名：`S7烽火台和黄天纸马开启人数限制`；别名：`S7专属建筑开启人数`、`S7 专属建筑开启人数`；执行：`-k faction_building_member_limit`；默认 Patch 类型：`common`
- 中文标准名：`S7专属建筑宫殿等级限制`；执行：`-k faction_building_palace_level`；默认 Patch 类型：`common`
- 中文标准名：`黄巾县域落城上限`；执行：`-k one_city_max_main_city_num`；默认 Patch 类型：`common`
- 中文标准名：`国都/州府额外霸业名额`；别名：`霸业名额`、`国都霸业名额`、`州府霸业名额`；执行：`-k capital_city_extra_quota`；默认 Patch 类型：`common`
- 中文标准名：`霸业名额服务端字段`；执行：`-k rank_quota`；默认 Patch 类型：`common`
- 中文标准名：`战队选边人数`；执行：`-k team_mini_choose_region_group`；默认 Patch 类型：`common`
- 中文标准名：`散人匹配战队人数阈值`；执行：`-k auto_join_team_min_limit`；默认 Patch 类型：`common`
- 中文标准名：`阵营预选战队人数显示阈值`；别名：`剧本预选州战队人数显示阈值`、`预选州战队人数显示阈值`；执行：`-k faction_team_show_member_limit`；默认 Patch 类型：`common`
- 中文标准名：`战队交换人数`；别名：`战队交换人数阈值`；执行：`-k faction_swap_team_member_min`；默认 Patch 类型：`common`
- 中文标准名：`跨赛季组队成团人数阈值`；执行：`-k team_reward_member_count`；默认 Patch 类型：`common`
- 中文标准名：`自选剧本人数最低限制`；执行：`-k final_default_open_scenario_limit`；默认 Patch 类型：`common`
- 中文标准名：`俸禄发放人数阈值`；执行：`-k week_suppress_camp_success_limit`；默认 Patch 类型：`common`
- 中文标准名：`软回收最低落堡人数要求`；执行：`-k soft_recycle_player_limit`；默认 Patch 类型：`common`
- 中文标准名：`硬回收启动注册人数`；执行：`-k hard_recycle_player_limit`；默认 Patch 类型：`common`
- 中文标准名：`虎视中原立国活动人数`；执行：`-k found_nation_reward_num_limit`；默认 Patch 类型：`common`
<!-- HOTFIX_THRESHOLD_REGISTRY:END -->

标准计划示例：

```json
{
  "kind": "operation",
  "summary": "热更 5010 服弹劾发起同盟人数为 1",
  "steps": [
    {
      "path": "/home/serverGeneralScript/auto_hotfix_tool.py",
      "args": ["-s", "5010", "-k", "union_impeach_min", "-v", "1"],
      "description": "热更 5010 服弹劾发起同盟人数阈值为 1"
    }
  ]
}
```

显式类型标准计划示例：

```json
{
  "kind": "operation",
  "summary": "以 common 类型热更 5010 服霸业名额为 1",
  "steps": [
    {
      "path": "/home/serverGeneralScript/auto_hotfix_tool.py",
      "args": ["-s", "5010", "-k", "capital_city_extra_quota", "-v", "1", "--patch-type", "common"],
      "description": "以 common 类型热更 5010 服霸业名额为 1"
    }
  ]
}
```

## 维护要求

新增可执行模式时，在同一次修改中更新本文件的自然语言映射、精确模板和文件末尾的机器可校验合同，并为该模式及其与其他已登记动作的组合补充本地测试。本地资料和调用合同负责让 AI 生成合规 `path + args`；任务入口脚本只负责根目录、普通文件、固定 argv 与 `shell=False`，不应再维护第二份服务器端业务白名单或按扩展名决定能否执行。脚本必须已放入本机 `knowledge/scripts/` 作为阅读参考，并确认实际部署在 GS-1 的相同路径；通过本地测试后才可供机器人执行。

<!-- MULIU_CALL_CONTRACTS
{
  "max_plan_steps": 10,
  "contracts": [
    {
      "name": "basic-server-info",
      "path": "/home/serverGeneralScript/basic_info.sh",
      "args": [
        "{server_id}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "bash",
      "risk": "read"
    },
    {
      "name": "basic-server-ctime",
      "path": "/home/serverGeneralScript/basic_info.sh",
      "args": [
        "{server_id}",
        "ctime"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "bash",
      "risk": "read"
    },
    {
      "name": "basic-scenario-servers-info",
      "path": "/home/serverGeneralScript/basic_info.sh",
      "args": [
        "ssinfo",
        "{scenario}"
      ],
      "variables": {
        "scenario": "[0-9]{1,8}"
      },
      "runner": "bash",
      "risk": "read"
    },
    {
      "name": "basic-account-season-history",
      "path": "/home/serverGeneralScript/basic_info.sh",
      "args": [
        "ssnum",
        "{account}"
      ],
      "variables": {
        "account": "[A-Za-z0-9_.-]{1,64}"
      },
      "runner": "bash",
      "risk": "read"
    },
    {
      "name": "basic-player-season-score",
      "path": "/home/serverGeneralScript/basic_info.sh",
      "args": [
        "{server_id}",
        "{user_id}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "user_id": "[0-9]{1,16}"
      },
      "runner": "bash",
      "risk": "read"
    },
    {
      "name": "shutdown-normal",
      "path": "/home/serverGeneralScript/shutdown",
      "args": [
        "{server_id}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "shutdown-mode",
      "path": "/home/serverGeneralScript/shutdown",
      "args": [
        "{server_id}",
        "{shutdown_mode}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "shutdown_mode": "normal|kill|autokill|gvgzone|gvgdaemon"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "start-server",
      "path": "/home/serverGeneralScript/start",
      "args": [
        "{server_id}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "start-game-only",
      "path": "/home/serverGeneralScript/start",
      "args": [
        "{server_id}",
        "noz"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "clear-server",
      "path": "/home/serverGeneralScript/clear",
      "args": [
        "{server_id}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "clear-server-opening-time",
      "path": "/home/serverGeneralScript/clear",
      "args": [
        "{server_id}",
        "{clear_mode}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "clear_mode": "by|noby"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "modify-game-config",
      "path": "/home/serverGeneralScript/modify_game_config.sh",
      "args": [
        "{server_id}",
        "{season}",
        "{scenario}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "season": "[0-9]{1,8}",
        "scenario": "[0-9]{1,8}"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "check-start-time",
      "path": "/home/serverGeneralScript/starttime",
      "args": [
        "{server_id}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "set-start-time-current",
      "path": "/home/serverGeneralScript/starttime",
      "args": [
        "{server_id}",
        "ctime",
        "--backup",
        "yes"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "set-start-time-timestamp",
      "path": "/home/serverGeneralScript/starttime",
      "args": [
        "{server_id}",
        "{timestamp}",
        "--backup",
        "yes"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "timestamp": "[0-9]{9,12}"
      },
      "runner": "bash",
      "risk": "write"
    },
    {
      "name": "startup-failure-diagnosis",
      "path": "/home/serverGeneralScript/server_log_query.py",
      "args": [
        "--server-id",
        "{server_id}",
        "--profile",
        "startup_errors",
        "--max-lines",
        "50"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "python3.7",
      "risk": "read"
    },
    {
      "name": "single-server-patch-list-check",
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": [
        "-s",
        "{server_id}",
        "-ck"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "python3.7",
      "risk": "read"
    },
    {
      "name": "two-server-patch-list-compare",
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": [
        "-s",
        "{server_id_a}",
        "{server_id_b}",
        "-ck"
      ],
      "variables": {
        "server_id_a": "[0-9]{3,8}",
        "server_id_b": "[0-9]{3,8}"
      },
      "runner": "python3.7",
      "risk": "read"
    },
    {
      "name": "patch-list-backup-or-restore",
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": [
        "-s",
        "{server_id}",
        "-r",
        "{patch_recovery_mode}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "patch_recovery_mode": "bak|re"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "patch-list-check-and-clean-missing",
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": [
        "-s",
        "{server_id}",
        "-ck",
        "-m"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "patch-hot-update",
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": [
        "-s",
        "{server_id}",
        "-u"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "batch-servers-version-query",
      "path": "/home/serverGeneralScript/batch_server_query.py",
      "args": [
        "--servers",
        "{server_ids}",
        "--label",
        "{label}"
      ],
      "variables": {
        "server_ids": "[0-9]{3,8}(,[0-9]{3,8})*",
        "label": "[^\"'\\n\\r]{1,64}"
      },
      "runner": "python3.7",
      "risk": "read"
    },
    {
      "name": "patch-delete-item",
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": [
        "-s",
        "{server_id}",
        "-d",
        "{patch_file}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "patch_file": "[A-Za-z0-9_.-]{1,64}"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "patch-upload-item",
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": [
        "-s",
        "{server_id}",
        "-f",
        "{patch_name}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "patch_name": "[A-Za-z0-9_.-]{1,64}"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "clear-logic-game",
      "path": "/home/serverGeneralScript/clear_logic_game.py",
      "args": [
        "{server_id}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "clear-zone",
      "path": "/home/serverGeneralScript/clear_zone.py",
      "args": [
        "{server_id}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "clear-db",
      "path": "/home/serverGeneralScript/cleardb.py",
      "args": [
        "{server_id}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "hotfix-server-threshold",
      "path": "/home/serverGeneralScript/auto_hotfix_tool.py",
      "args": [
        "-s",
        "{server_id}",
        "-k",
        "{key}",
        "-v",
        "{value}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "key": "(?:auto_join_team_min_limit|capital_city_extra_quota|faction_building_member_limit|faction_building_palace_level|faction_swap_team_member_min|faction_team_show_member_limit|final_default_open_scenario_limit|found_nation_reward_num_limit|hard_recycle_player_limit|one_city_max_main_city_num|rank_quota|soft_recycle_player_limit|team_mini_choose_region_group|team_reward_member_count|union_impeach_min|union_impeach_power_min|union_impeach_power_rank|union_max_member_init|union_max_member_limit|union_merge_min_member|union_rage_min_level|union_rage_min_member|union_token_least_member|week_suppress_camp_success_limit)",
        "value": "[0-9]{1,16}"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "cleanup-legacy-auto-hotfix",
      "path": "/home/serverGeneralScript/auto_hotfix_tool.py",
      "args": [
        "-s",
        "{server_id}",
        "--cleanup-legacy",
        "{key}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "key": "(?:auto_join_team_min_limit|capital_city_extra_quota|faction_building_member_limit|faction_building_palace_level|faction_swap_team_member_min|faction_team_show_member_limit|final_default_open_scenario_limit|found_nation_reward_num_limit|hard_recycle_player_limit|one_city_max_main_city_num|rank_quota|soft_recycle_player_limit|team_mini_choose_region_group|team_reward_member_count|union_impeach_min|union_impeach_power_min|union_impeach_power_rank|union_max_member_init|union_max_member_limit|union_merge_min_member|union_rage_min_level|union_rage_min_member|union_token_least_member|week_suppress_camp_success_limit)"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "restore-auto-hotfix",
      "path": "/home/serverGeneralScript/auto_hotfix_tool.py",
      "args": [
        "-s",
        "{server_id}",
        "--restore-auto",
        "{key}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "key": "(?:auto_join_team_min_limit|capital_city_extra_quota|faction_building_member_limit|faction_building_palace_level|faction_swap_team_member_min|faction_team_show_member_limit|final_default_open_scenario_limit|found_nation_reward_num_limit|hard_recycle_player_limit|one_city_max_main_city_num|rank_quota|soft_recycle_player_limit|team_mini_choose_region_group|team_reward_member_count|union_impeach_min|union_impeach_power_min|union_impeach_power_rank|union_max_member_init|union_max_member_limit|union_merge_min_member|union_rage_min_level|union_rage_min_member|union_token_least_member|week_suppress_camp_success_limit)"
      },
      "runner": "python3.7",
      "risk": "write"
    },
    {
      "name": "hotfix-server-threshold-with-patch-type",
      "path": "/home/serverGeneralScript/auto_hotfix_tool.py",
      "args": [
        "-s",
        "{server_id}",
        "-k",
        "{key}",
        "-v",
        "{value}",
        "--patch-type",
        "{patch_type}"
      ],
      "variables": {
        "server_id": "[0-9]{3,8}",
        "key": "(?:auto_join_team_min_limit|capital_city_extra_quota|faction_building_member_limit|faction_building_palace_level|faction_swap_team_member_min|faction_team_show_member_limit|final_default_open_scenario_limit|found_nation_reward_num_limit|hard_recycle_player_limit|one_city_max_main_city_num|rank_quota|soft_recycle_player_limit|team_mini_choose_region_group|team_reward_member_count|union_impeach_min|union_impeach_power_min|union_impeach_power_rank|union_max_member_init|union_max_member_limit|union_merge_min_member|union_rage_min_level|union_rage_min_member|union_token_least_member|week_suppress_camp_success_limit)",
        "value": "[0-9]{1,16}",
        "patch_type": "avatar|battle|center|client_account|client_avatar|common|login|scene|test|union|unionmirror"
      },
      "runner": "python3.7",
      "risk": "write"
    }
  ]
}
MULIU_CALL_CONTRACTS -->
