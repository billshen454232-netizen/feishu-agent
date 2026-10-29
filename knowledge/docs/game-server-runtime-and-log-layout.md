# 游戏服运行目录与日志布局

> 本文解释测试服日志诊断的环境边界，帮助将自然语言映射为受控诊断能力。它不是自由远程命令、任意文件路径或执行许可；是否可执行只以 [../../config/muliu_script_catalog.md](../../config/muliu_script_catalog.md) 的当前精确合同为准。

## 已确认的中转架构

```text
飞书机器人
→ Muliu Task 89
→ GS-1 上 /home/serverGeneralScript/ 的已部署脚本
→ 脚本从 GS-1 的 config.ini 以 server_id 解析目标游戏服
→ GS-1 用脚本内既有 SSH / SCP / expect 能力连接目标游戏服
→ 目标游戏服读取固定日志或运行固定辅助查询
→ Muliu 返回脚本输出
→ 飞书回传结果
```

- 飞书消息、AI 和执行计划只传递业务字段或数字 `server_id`，不读取、展示或推断目标服务器 IP；
- `config.ini` 是 GS-1 的内部路由配置，只应由已部署脚本读取，不进入机器人资料、模型上下文、群消息或 Git；
- Muliu 执行的是 GS-1 上已有的受控脚本，不是直接在每台目标游戏服上执行自由命令；
- 本仓库不能证明当前 GS-1 的实际 dispatcher、候选脚本或 `config.ini` 已部署并匹配本文描述。

## 已观察到的应用与日志布局

以下目录来自代表性目标游戏服的人工目录清单。目录存在不代表机器人可以读取其中任意文件，也不代表所有测试服一致。

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

/data/log/
├─ bglog/
│  ├─ game/
│  └─ zone/
├─ bilog/
├─ nginx/
├─ opslog/
└─ supervisor/
```

已知的固定只读核对项：

- `release_etc/version.txt`：现有 `basic_info.sh` 会读取当前代码版本；
- `server_common/Debug_ServerStartTime.lua`：现有 `basic_info.sh` 会读取开服时间戳；
- 其他目录的具体业务作用、可读文件和日志格式不能由模型自行推断。

## `basic_info.sh` 的固定日志行为

`basic_info.sh <server_id>` 以固定逻辑：

1. 从受控 `config.ini` 映射目标游戏服；
2. 读取目标服版本、开服时间和 Skynet 进程；
3. 读取 `/data/log/supervisor/supervisor_game_6.log` 的少量近期记录，判断是否出现启动成功标记；
4. 未检测到启动成功标记但仍有 `game` 或 `zone` 进程时，可能以警告返回基础信息；
5. 对固定的 `run_patch error`、`Listen error`、`game_6 start fail` 输出解释。

它不能替代通用日志查询能力：不会接收任意目录、文件名、关键字、正则、`grep` / `tail` 参数或 Shell 片段。

## 候选的受控日志查询能力

仓库包含 [../../deployment/gs1/server_log_query.py](../../deployment/gs1/server_log_query.py) 作为候选部署脚本。该候选脚本固定了下列 profile：

```text
layout
startup_recent
startup_errors
game_recent
game_trace
patch_errors
```

### 当前本地合同状态

- 本地 catalog 已登记 `startup_errors` 的精确调用形态；
- 其他 profile 即使存在于候选脚本中，也不在当前 robot 合同中；
- 这只代表本地 catalog 与候选 artifact，不代表 GS-1 有该脚本、Task 89 会调用它，或任何目标服的日志布局和权限已通过验收。

候选脚本设计为只接受数字 `server_id`、固定 profile 和有限 `max_lines`；它在 GS-1 内部通过 `config.ini` 定位目标服，使用固定 SSH argv 与 `shell=False`，不接受 IP、路径、文件名、关键词、正则或 Shell 参数。

## 仍需真实环境核验的事实

在扩大日志合同或将日志诊断结果表述为可靠事实前，必须在明确目标服独立核验：

1. Task 89 实际 dispatcher 与脚本部署版本；
2. 日志文件名模式、轮转方式、时间字段、目录权限与编码；
3. Trace、ERROR、Patch 与启动失败信号的实际位置；
4. 单个日志文件和目录的典型体积及 Muliu 时限；
5. 固定 SSH 身份和 `known_hosts` 的权限边界；
6. 返回内容的脱敏与截断效果。

证据不足时，应停止并记录缺少的环境事实；不能开放任意路径、自由 grep 或自由远程命令作为替代方案。

## 结果与维护边界

- `server_log_query.py` 或 `basic_info.sh` 成功，只证明相应脚本层返回；不能直接证明服务根因已定位或游戏业务已恢复；
- 实际执行以 GS-1 已部署版本和目标服实时结果为准；
- 部署前人工核验步骤见 [../../deployment/gs1/README.md](../../deployment/gs1/README.md)。
