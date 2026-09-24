# 游戏服运行目录与日志布局

> 本文用于解释测试服日志诊断的环境边界，帮助后续机器人将自然语言映射为受控的诊断能力。它**不是**自由远程命令、任意文件路径或执行许可；真正可执行的能力仍必须单独登记在 `config/muliu_script_catalog.md` 中。

## 已确认的中转架构

```text
飞书机器人
→ Muliu Task 89
→ GS-1 上 /home/serverGeneralScript/ 的已部署脚本
→ 脚本从 GS-1 的 config.ini 以 server_id 解析目标游戏服
→ GS-1 用脚本内既有 SSH / SCP / expect 能力连接目标游戏服
→ 目标游戏服读取固定的日志或运行固定的辅助查询
→ Muliu 返回脚本输出
→ 飞书回传结果
```

- 飞书消息、AI 和执行计划只传递纯数字 `server_id`，不应读取、展示或让模型推断目标服务器 IP。
- `config.ini` 是 GS-1 的内部路由配置；它只应由已部署的 GS-1 脚本读取，不进入机器人资料、模型上下文、群消息或 Git。
- Muliu 实际执行的是 GS-1 上已有的 `.sh` / `.py` 文件；它不是直接在每台目标游戏服上执行自由命令。

## 已观察到的游戏服应用布局

以下目录来自一台代表性目标游戏服的人工目录清单。目录存在不代表机器人可以读取其中任意文件。

```text
/data/app/game-server/
├─ app.lua
├─ config.lua
├─ etc/
├─ server_common/
├─ service/
├─ skynet/
├─ supervisor_conf/
├─ release_bin/
├─ release_etc/
├─ release_install/
├─ release_mode/
├─ common/
├─ CommonRes/
├─ distserver/
├─ lualib/
├─ luaclib/
├─ tool/
└─ version_update_log.txt
```

当前已知的只读核对项：

- `release_etc/version.txt`：现有 `basic_info.sh` 会读取当前代码版本；
- `server_common/Debug_ServerStartTime.lua`：现有 `basic_info.sh` 会读取开服时间戳；
- 其余目录的具体业务作用、可读文件和日志格式尚未作为机器人能力登记，不能由模型自行推断参数。

## 已观察到的日志根目录

```text
/data/log/
├─ bglog/
│  ├─ game/
│  └─ zone/
├─ bilog/
├─ nginx/
├─ opslog/
└─ supervisor/
```

### 当前诊断优先级

|日志来源|用途|当前证据|机器人状态|
|---|---|---|---|
|`/data/log/bglog/game/`|游戏服运行异常的主要排查来源|运维方明确说明；现有 `basic_info.sh` 在启动异常时会读取该目录中的 Trace 信息|待登记专用只读查询能力|
|`/data/log/supervisor/supervisor_game_6.log`|近期启动状态与启动失败初步诊断|现有 `basic_info.sh` 已读取最近记录并检查启动成功标记|仅作为 `basic_info.sh <server_id>` 的固定内部检查，不能自由查询|
|`/data/log/bglog/zone/`|Zone 相关日志|已观察到目录存在|未登记|
|`/data/log/bilog/`、`nginx/`、`opslog/`|其他业务、访问或运维日志|已观察到目录存在|未登记|

## 已有基础检查脚本的日志行为

`basic_info.sh <server_id>` 已经以固定逻辑：

1. 从 `config.ini` 映射目标游戏服；
2. 读取目标服的版本、开服时间和 Skynet 进程；
3. 读取 `/data/log/supervisor/supervisor_game_6.log` 的最近少量记录，判断是否出现启动成功标记；
4. 若未检测到启动成功，确认 `/data/log/bglog/game/` 非空，并对近期日志中的 `trace` 进行既有的诊断；
5. 对 `run_patch error`、`Listen error`、`game_6 start fail` 输出固定的解释。

这说明 GS-1 到目标游戏服的只读日志查询路径已被现有脚本使用；但它**不能**替代一个通用日志查询能力：它不会返回任意日志目录树、任意文件尾部或任意搜索结果。

## 6000 服只读核验结果（2026-09-11）

已通过 Muliu Task 89 调用 GS-1 上既有的 `basic_info.sh 6000` 进行一次只读核验；未传递自由日志路径、Shell 命令、IP 或额外参数。

该次输出确认：

- `6000` 可以由 GS-1 的内部路由解析并完成远程基础信息读取；
- 目标服返回了剧本、版本、开服时间以及 `game`、`zone` 两类 Skynet 进程组；
- `/data/log/bglog/game/` 被既有脚本判定为非空；
- 既有脚本未在近期 supervisor 记录中识别到它期待的启动成功标记，因此退出状态为失败；这不是 SSH / 路由失败，也不等于已定位出游戏异常根因；
- 此调用没有列出 `bglog/game` 内的文件名、轮转形式或单个日志内容，因此这些事实仍需专用受控查询脚本核验。

> Muliu 返回的部分中文日志在当前调用链中出现编码显示异常。新增日志查询能力前，应同时核验 GS-1 脚本输出编码与 Muliu 日志回传编码，避免飞书中不可读。

## 后续安全日志查询能力的设计边界

后续应由 GS-1 上的新专用只读脚本提供日志查询，例如 `server_log_query.py`。机器人和模型只提交结构化业务字段，不生成 SSH、Shell、日志路径或 `grep` / `tail` 参数。

建议的第一批固定诊断档案：

|档案|固定目标|预期用途|
|---|---|---|
|`startup_recent`|`/data/log/supervisor/supervisor_game_6.log`|查看近期启动记录|
|`startup_errors`|同上|提取已定义的启动失败信号|
|`game_recent`|`/data/log/bglog/game/`|列出近期日志文件并返回受限的最近记录|
|`game_trace`|同上|提取 Trace / 异常上下文|
|`patch_errors`|同上|提取 Patch 相关异常|

每个档案都应：

- 只接受 3 至 8 位数字 `server_id`；
- 在 GS-1 内部通过 `config.ini` 解析目标，不接收 IP 或任意路径；
- 使用脚本内部固定的远程读取逻辑，而非把用户文本拼接到远程 Shell；
- 限制文件数量、结果行数、单行长度和总输出字节数；
- 对空目录、SSH 失败、权限不足、没有匹配项、输出截断给出明确状态；
- 将普通关键词筛选与目录选择分开：若以后支持关键词，只能按普通文本匹配，限制字符集和长度，不能作为正则或 Shell 片段；
- 在注册到飞书机器人前，单独写入执行调用合同、通过本地校验、防火墙和群内确认。

## 本地待部署的固定日志查询实现

已在飞书项目的部署资料目录新增：

```text
D:\otherPrograms\feishu_agent\deployment\gs1\server_log_query.py
```

它是 Python 3.7 兼容的**待部署**脚本，不是当前 GS-1 上已验证的文件，也尚未加入机器人调用合同。固定 profile 为：

```text
layout
startup_recent
startup_errors
game_recent
game_trace
patch_errors
```

该实现只接受 3 至 8 位数字 `server_id`、上述固定 profile 和 10–100 的 `max_lines`。它在 GS-1 从同目录 `config.ini` 精确读取 `<server_id>_game`，再以 `shell=False` 的固定 SSH argv 将静态 Bash 查询传至目标服。所有日志路径、文件筛选、异常模式、单文件读取行数、文件数量和输出上限均固定在脚本中；不接受 IP、路径、文件名、关键词、正则、Shell 或 grep 参数。成功结果必须保留固定终态状态标记；回传正文会对常见认证/令牌字段、URL 凭据和 IPv4 二次脱敏，且不会将日志文件名插入任何可解释的远端程序文本。

部署说明与人工核验清单见：

```text
D:\otherPrograms\feishu_agent\deployment\gs1\README.md
```

本地单元测试覆盖参数、路由、SSH argv、失败回显脱敏、输出截断和动态解释器缺失；截至 2026-09-11 已通过 21 项测试。它们不能替代 GS-1 与目标服的真实权限、日志格式、性能和编码核验。

## 当前可调用性结论

截至 2026-09-11，没有发现一个已经部署、已核对且已登记在当前 Muliu 调用合同中的 GS-1 脚本，可以列出目标游戏服的 `/data/log/` 或 `/data/log/bglog/game/` 目录树。

当前唯一已登记的相关入口是：

```json
{
  "path": "/home/serverGeneralScript/basic_info.sh",
  "args": ["6000"]
}
```

它只会在自己的固定启动异常诊断分支中判断 `bglog/game` 是否为空，并按固定逻辑提取 Trace；不输出目录清单，也不接受日志路径、文件名或筛选参数。因此不能把它伪装成通用日志目录查询能力。

## 仍需实际核验的事实

在把 `bglog/game` 注册为机器人能力前，应使用受控的 GS-1 只读脚本对目标服实际核对：

1. 日志文件名模式、轮转方式与时间字段；
2. 哪些日志包含 Trace、ERROR、Patch 与启动失败信息；
3. 单个日志文件和目录的典型体积，便于设置输出上限；
4. 读取路径和 SSH 账户的权限边界；
5. 目标服务器 `6000` 与其他测试服是否使用一致布局；
6. 返回日志时需要脱敏的字段类型。

在这些事实未核验前，模型不得生成 `bglog/game` 的自由路径、文件名或查询参数。

## 资料来源与维护边界

- 游戏服目录与日志根目录：运维方于 2026-09-11 提供的代表性目录清单；
- GS-1 到目标服的路由与现有日志检查：`knowledge/scripts/basic_info.sh` 的阅读副本；
- 实际执行必须以 GS-1 的已部署脚本和目标服实时结果为准。
