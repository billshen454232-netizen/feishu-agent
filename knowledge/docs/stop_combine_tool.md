# stop_combine_tool.sh

## 用途

仅关闭一个**明确的合服目标服**上、由合服工具拉起的 combine 进程。它不是普通游戏服关闭，也不接收 PID、进程名或自由停止命令。

机器人是否允许某个精确调用，只以 [../../config/muliu_script_catalog.md](../../config/muliu_script_catalog.md) 的当前合同为准；本文不证明该合同或脚本已部署到 GS-1。

## 受控调用形式

```text
bash stop_combine_tool.sh <target_server_id>
```

| 参数 | 含义 |
| --- | --- |
| `target_server_id` | 合服**目标服**编号；仅接受一个 3–8 位数字。 |

脚本阅读副本显示的固定行为是：

1. 从同目录受控 `config.ini` 读取 `<target_server_id>_game` 路由；
2. 仅连接该目标服的 game 节点；
3. 固定执行：

   ```text
   cd /data/app/game-server && bash bin/kill_combine.sh
   ```

4. 除目标服号外不接受其他参数。

## 自然语言和操作边界

- “关闭 2018 的合服进程”“停止 2018 服合服工具”对应 `target_server_id=2018`；
- 合服**来源服**用于启动合服流程；关闭 combine 必须在合服**目标服**执行；
- 不接受 PID、主机、进程名、`kill` / `autokill` 模式或额外参数；
- 普通 `game`、`zone`、GVG 服务关闭仍由 `shutdown` 负责；不能把普通“关服”推断为停止 combine；
- 脚本内的实际 IP、账号、SSH 身份和 `config.ini` 映射不进入知识资料、模型上下文、飞书消息或 Git。

## 风险、确认与验证边界

这是高影响写操作。生成计划后，仍必须经过精确合同、路由校验、防火墙和原请求人确认。

即使本地 catalog 已登记、候选 manifest 已生成或脚本阅读副本存在，也**不能**证明：

- GS-1 已复制 `stop_combine_tool.sh`；
- Task 89 当前 dispatcher 会调用该脚本；
- GS-1 的 `config.ini` 存在目标服路由；
- 目标服具备执行 `kill_combine.sh` 的权限；
- combine 已完全退出，或合服数据已经完成、验证或在游戏内生效。

真实调用的最高准确表述是：`kill_combine.sh` 以成功状态结束。combine 的退出状态需要由独立、受控的环境证据核验。

## 资料来源

- 脚本阅读副本：[../scripts/stop_combine_tool.sh](../scripts/stop_combine_tool.sh)
- 候选部署与人工核验边界：[../../deployment/gs1/README.md](../../deployment/gs1/README.md)
- 当前架构与执行事实入口：[../../docs/architecture/current-project.md](../../docs/architecture/current-project.md)
