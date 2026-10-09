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
- **匹配的自然语言**：`查询 6001 服务器的 Patch 列表`、`对比 6001 和 6002 的 Patch`、`备份 6001 服 Patch 列表`、`恢复 6001 服 Patch 列表`、`清理 6001 服缺失 Patch 条目`、`热更 6001 服 Patch`。
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
    }
  ]
}
MULIU_CALL_CONTRACTS -->
