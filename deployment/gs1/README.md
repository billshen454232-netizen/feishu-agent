# GS-1 候选部署资料与人工核验清单

> **资料状态：仓库候选部署物，不是 GS-1 当前状态证明。**
>
> 本目录中的文件只能说明仓库希望部署什么；它们不证明文件已复制到 GS-1、Task 89 已调用它们，或目标服已完成验证。

## 1. 本目录包含什么

|文件|仓库中的定位|不能证明什么|
|---|---|---|
|[server_log_query.py](server_log_query.py)|待部署的受控日志诊断脚本候选。|它已存在于 `/home/serverGeneralScript`，或任何 profile 已经远端验收。|
|[batch_server_query.py](batch_server_query.py)|待部署的批量版本查询脚本候选。|它已存在于 GS-1，或 GS-1 的路由与 SSH 权限可用。|
|[muliu_runner_manifest.json](muliu_runner_manifest.json)|从 catalog 派生的无凭据候选 artifact。|GS-1 有该文件、dispatcher 读取它，或 dispatcher 据此拒绝未登记 argv。|

机器人侧当前唯一执行合同仍是 [../../config/muliu_script_catalog.md](../../config/muliu_script_catalog.md)。该合同通过 Gateway 和 Worker 校验；Worker 向 Task 89 发送的 payload 只有 `path + args`。

## 2. runner manifest 的准确边界

[muliu_runner_manifest.json](muliu_runner_manifest.json) 记录当前 catalog 的精确 `path + args` 模板、变量正则、`runner` 与 `risk`，且不包含凭据。它由下列本地工具生成：

```bash
python tools/generate_runner_manifest.py --check
python tools/sync_hotfix_planner_catalog.py --check
```

这些命令只验证**本地仓库**的生成物是否与当前 catalog / registry 同步。

当前已确认的事实是：本仓库运行期不会读取 manifest，也不会将 `runner` 或 manifest 传给 Task 89。GS-1 的 dispatcher 源码与 Task 89 实际代码包不在本仓库中；是否消费 manifest、如何选择解释器、是否实施精确 argv 校验，都必须在 GS-1 上由有权限的维护者独立核验。

因此，不要把本地 artifact 同步检查写成“GS-1 已部署”或“Task 89 已受 manifest 保护”。

## 3. `server_log_query.py` 的候选接口

若有权限维护者审核并部署 [server_log_query.py](server_log_query.py)，其预期调用接口为：

```text
python3.7 server_log_query.py \
  --server-id <3-8 位数字> \
  --profile <固定 profile> \
  --max-lines <10-100>
```

脚本只允许固定 profile：

|profile|固定读取范围|用途|
|---|---|---|
|`layout`|`/data/log/` 下受限目录与近期文件名|首次核对日志布局。|
|`startup_recent`|固定 supervisor 日志尾部|查看近期启动记录。|
|`startup_errors`|同一固定 supervisor 日志|提取固定启动异常信号。|
|`game_recent`|固定 game 日志目录中的近期日志|查看近期 game 日志。|
|`game_trace`|固定 game 日志目录中的受限近期日志|提取 Trace / 异常信号。|
|`patch_errors`|同一固定目录|提取 Patch 异常信号。|

脚本不接受 IP、主机名、日志路径、文件名、关键词、正则、`grep` / `tail` 参数或 Shell 片段。它从同目录受控 `config.ini` 解析 `<server_id>_game`，使用固定 argv 和 `shell=False` 调用 SSH。

本地 catalog 目前只登记 `startup_errors` 的精确调用形态。其他 profile 即使出现在候选脚本中，也不是机器人当前许可；是否真的已部署仍需下述核验。

## 4. 真实部署前必须核验的事实

以下任何一项缺证据时，应停止在该证据上处理；不要用自由 Shell、任意路径或跳过 SSH 安全检查绕过。

1. **Task 89 入口**：在 GS-1 确认 Task 89 实际调用的 wrapper 和 dispatcher 路径、版本与 hash。
2. **dispatcher 行为**：确认它当前接受的 payload、路径根目录校验、解释器选择方式，以及是否实际读取 manifest。不得从本目录推断。
3. **脚本文件**：确认计划部署的候选脚本已复制到实际 `/home/serverGeneralScript`，并与审核版本逐项核对。
4. **运行环境**：确认所需 Python、Bash、SSH 和其他固定依赖的版本及可用性。
5. **路由与主机密钥**：确认已部署 `config.ini` 中目标服映射、SSH identity 与 `known_hosts`；不得将原文复制到 Git、飞书或模型上下文。
6. **目标服权限与结构**：确认固定日志目录、文件模式、读取权限、输出体积、耗时和编码。候选脚本的假设不能替代实测。
7. **Task 89 结果协议**：确认返回结果如何对应本次请求；本仓库当前没有 run nonce / run ID 的请求级日志绑定实现。

## 5. 脚本覆盖到 GS-1 的正确做法

GS-1 上被调用的脚本改动后需要**手动覆盖**（仓库 git push 不会自动部署到 GS-1）。必须用字节级复制：

```bash
cd D:\otherPrograms\feishu_agent\tools
scp auto_hotfix_tool.py     root@<GS-1>:/home/serverGeneralScript/
scp auto_hotfix_registry.py root@<GS-1>:/home/serverGeneralScript/
```

**两个文件必须同时覆盖且在同一目录**：`auto_hotfix_tool.py` 靠同目录的 `auto_hotfix_registry.py` 找字段定义，只换一个会退回旧行为。

### 为什么不能手动复制

**踩过的坑（2026-10-29）**：手动从编辑器/终端复制粘贴时误按 ESC，文件里混入 `0x1B` 控制字符，位置正好在标识符中间（`file<ESC>_name`），导致 GS-1 上 Python 报 `SyntaxError: invalid syntax`。这类污染是**静默的**——本地 `py_compile` 通过、复制时看不出，只在目标机执行时才炸。

`scp` / `rsync` 是字节级复制，不受影响。

### 覆盖后必做的验证

```bash
# 1. 语法必须通过
python3 -c "import ast; ast.parse(open('/home/serverGeneralScript/auto_hotfix_tool.py').read())" && echo "语法 OK"

# 2. 确认无 ESC 污染（应输出 0）
grep -c $'\x1b' /home/serverGeneralScript/auto_hotfix_tool.py
grep -c $'\x1b' /home/serverGeneralScript/auto_hotfix_registry.py
```

国内 GS-1 与海外 GS-1 都要覆盖并验证。

## 6. 受控人工核验顺序

部署会向 GS-1 写入或覆盖文件，应由有权限的环境维护者明确执行。复制后，先在单个明确测试服进行只读人工核验；记录耗时、返回状态、编码、敏感数据、截断行为和实际文件布局。

若该候选日志脚本已部署，可按低风险到高信息量的顺序核验：

```text
python3.7 server_log_query.py --server-id <明确测试服> --profile layout --max-lines 20
python3.7 server_log_query.py --server-id <明确测试服> --profile startup_recent --max-lines 30
python3.7 server_log_query.py --server-id <明确测试服> --profile startup_errors --max-lines 30
```

只有上述环境事实与输出边界核验完成后，才考虑在本地 catalog 登记额外 profile、更新测试，并通过飞书做一次受控的只读确认验证。

## 7. 结果表述边界

- 成功复制候选文件，只代表文件复制完成；不代表 Task 89 已调用它。
- Task 89 或脚本返回成功，只代表已获得对应层的终态；不代表日志诊断结论、服务就绪或游戏内效果已经验证。
- 本地 `--check` 通过，只代表仓库生成物一致；不代表任何远端事实。

当前运行架构、确认与路由边界见 [../../docs/architecture/current-project.md](../../docs/architecture/current-project.md)。
