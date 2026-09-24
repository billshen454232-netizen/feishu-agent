# GS-1 部署：受控日志查询与 Task 89 runner manifest

本目录中的 [server_log_query.py](server_log_query.py) 是**待部署**到 GS-1 的受控日志诊断脚本。它不是通用 SSH、远程 Shell 或任意文件读取工具。

部署后的唯一预期位置：

```text
/home/serverGeneralScript/server_log_query.py
```

当前注册表已登记其固定 `startup_errors` 档案；其他 profile 仍未开放给飞书。实际 Task 89 部署仍须先完成下方核验，且不能把本目录的参考文件误认为已经部署到 GS-1。

## Task 89 runner manifest

[muliu_runner_manifest.json](muliu_runner_manifest.json) 是从飞书端调用注册表生成的无凭据部署产物。它同时登记每个精确 `path + args` 模板、变量正则、固定 runner 与风险级别；Task 89 分发器必须以它为唯一调用权威，例如：

```json
{
  "version": 1,
  "contracts": [
    {
      "path": "/home/serverGeneralScript/start",
      "args": ["{server_id}"],
      "runner": ["bash"]
    }
  ]
}
```

有权限维护者必须将它与实际 Task 89 `../../code/ok_/muliu_ai_ops.sh`、`../../code/ok_/muliu_ai_ops.py` 成对部署到同一代码包；分发器不得通过扩展名或另一份无后缀脚本名单推断 runner，也不得只校验 path 而放行未登记 args。本仓库根目录的 `muliu_ai_ops.py` 仅为本地参考副本，不等于远端已同步。

## 固定接口

```text
python3.7 server_log_query.py \
  --server-id <3-8 位数字> \
  --profile <固定 profile> \
  --max-lines <10-100>
```

允许的 `profile`：

|Profile|固定读取范围|目的|
|---|---|---|
|`layout`|`/data/log/` 下最多两层目录；`/data/log/bglog/game/` 中近期 `.log` 文件名|第一次核验日志目录和文件命名，不读取任意文件内容|
|`startup_recent`|`/data/log/supervisor/supervisor_game_6.log` 的末尾|查看近期启动记录|
|`startup_errors`|同一 supervisor 固定文件|提取固定的启动异常信号|
|`game_recent`|`/data/log/bglog/game/` 最新 `.log` 文件的末尾|查看近期 game 日志|
|`game_trace`|该固定目录中最多 20 个近期 `.log` 文件|提取固定 Trace / 异常信号|
|`patch_errors`|同一固定目录|提取固定 Patch 异常信号|

不接受也不会传递：IP、主机名、日志路径、文件名、关键词、正则、`grep` / `tail` 参数、Shell 片段或命令。

## 安全机制

1. 仅接受 3–8 位数字的 `server_id`；只读取同目录 `config.ini` 中精确的 `<server_id>_game` 路由。
2. 所有远程日志路径和匹配模式都写死在脚本内；调用者只能选择有限 profile 和输出行数。
3. 使用 `subprocess.run(..., shell=False)` 启动既有 SSH 客户端；远端仅接收静态 Bash 程序和已经本地校验的 profile / 数字行数。
4. SSH 与异常退出的原文不会回传，以免泄露内部主机信息；异常时仅允许回显固定 `LOG_QUERY_STATUS` 标记。成功输出会截断单行、总长度，并对 Authorization / Cookie、Bearer / Basic 凭据、常见 JSON 密钥字段、URL Basic Auth 与 IPv4 地址做二次脱敏。
5. 远端查询使用固定目录、最多 20 个近期 `.log` 文件、每个 profile 10–100 行；`game_trace` / `patch_errors` 对每个文件只读取最近 2,000 行并用固定 AWK 规则筛选，避免为有限输出扫描整份大日志，也不会把文件名拼进 `sed`、AWK 或 Shell 程序。
6. 每次成功远端调用都必须以唯一的 `LOG_QUERY_STATUS=<状态>` 结尾；本地会验证并保留该终态标记，即使日志正文被截断也不会丢失状态协议。

> 这些输出限制不能替代实际压测。首次部署后必须在 6000 等明确测试服核验命令耗时、日志体积与 Muliu 120 秒任务时限。

## 部署前逐项确认

以下项目缺任何一项，都不要复制脚本、改调用合同或让机器人执行：

1. **实际入口**：Task 89 当前实际调用入口是 `../../code/ok_/muliu_ai_ops.sh`；该 wrapper、同目录 `muliu_ai_ops.py`、`muliu_runner_manifest.json` 与脚本根目录 `/home/serverGeneralScript` 已逐项核对为同一代码包。
2. **运行时**：GS-1 上存在 `python3.7`、`bash`、`ssh`、`find`、`sort`、`grep`、`tail`、`sed` 和 `cut`；目标服 SSH 使用的已批准身份允许只读 `/data/log`。
3. **路由格式**：GS-1 的 `config.ini` 与既有 `basic_info.sh` 一致，且 `<server_id>_game=<host>` 的值是单一主机名或 IPv4 地址；不得把配置原文复制到本仓库、飞书或模型上下文。
4. **主机密钥**：运行 Task 89 的账户已为目标服建立正确 SSH known_hosts。脚本强制 `StrictHostKeyChecking=yes`，不会在自动化中接受新主机密钥。
5. **编码链路**：确认 GS-1 stdout、Muliu 日志 API 和飞书回传能正确显示中文；如果仍乱码，先定位编码问题，不要为了“显示正常”删除脱敏或输出限制。
6. **目标结构**：先运行 `layout`，核对 6000 的实际 `.log` 文件名、轮转方式、目录权限和各异常信号的位置；不能仅按已有人工目录清单假设所有服一致。

## 受控人工核验顺序

部署动作会向 GS-1 写入新文件，必须由有权限的环境维护者执行并明确确认。部署完成后，使用 Muliu 或 GS-1 控制台按以下顺序人工核验：

```text
# 1. 只返回固定目录/文件概览
python3.7 server_log_query.py --server-id 6000 --profile layout --max-lines 20

# 2. 固定启动日志尾部
python3.7 server_log_query.py --server-id 6000 --profile startup_recent --max-lines 30

# 3. 固定 game 日志尾部
python3.7 server_log_query.py --server-id 6000 --profile game_recent --max-lines 30

# 4. 固定异常/Trace 档案
python3.7 server_log_query.py --server-id 6000 --profile game_trace --max-lines 30

# 5. 固定 Patch 异常档案
python3.7 server_log_query.py --server-id 6000 --profile patch_errors --max-lines 30
```

每一步记录：耗时、返回状态、编码、实际文件名、是否含敏感数据、是否出现 `[LOG_QUERY_TRUNCATED]`。若发生 SSH、权限、目录或编码问题，先停在该证据上修正脚本或环境；不要改成任意路径、`StrictHostKeyChecking=no`、自由 grep 或自由命令来绕过。

## 注册到飞书机器人之前

只有所有人工核验通过后，才可在同一次变更中：

1. 更新 [../../config/muliu_script_catalog.md](../../config/muliu_script_catalog.md) 的人类可读模式；
2. 在 `MULIU_CALL_CONTRACTS` 中登记**完全精确**的 argv 模板；
3. 增加 Planner、调用合同和消息展示的测试；
4. 保持群内一次性确认机制；
5. 重启 Uvicorn 后在飞书群做一次只读验证。

建议第一批合同不开放 `layout` 给日常用户，只根据实际核验结果登记一到两个最有价值的诊断 profile。例如：

```json
{
  "path": "/home/serverGeneralScript/server_log_query.py",
  "args": [
    "--server-id",
    "{server_id}",
    "--profile",
    "game_trace",
    "--max-lines",
    "50"
  ]
}
```

上例仅说明合同形状，**目前尚未登记，不能执行**。
