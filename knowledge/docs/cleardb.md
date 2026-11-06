# cleardb.py

## 用途

清理某个**合服来源服**留下的合服数据，便于重新合服或回到合服前状态。它**不是清档**：不清空游戏服数据库，只处理公共库里与该来源服相关的合服记录。

机器人是否允许某个精确调用，只以 [../../config/muliu_script_catalog.md](../../config/muliu_script_catalog.md) 的当前合同为准（模式 L）。

## 受控调用形式

```text
python3.7 cleardb.py <server_id>
```

| 参数 | 含义 |
| --- | --- |
| `server_id` | 合服来源服（当初合服的起始服）编号；机器人合同只接受一个 3–8 位数字。 |

## 实际行为（2026-11-05 按国服 GS-1 实际版本核对）

1. 遍历 `nslg_game_common.Combine`，找出 `lastServerInfo.serverId` 等于该服的合服记录；
2. 对每条记录中的角色（`_id` 为角色 ID，`soul.account_name` 为账号）：
   - 从 `nslg_common.Account` 的 `role_list.<原服>` 中移除该角色；
   - 从 `role_list.0`（回归服）中移除该角色；
   - 删除 `nslg_game_common.AvatarToTargetServer` 中该角色的记录；
   - 删除 `nslg_game_common.Combine` 中该角色的记录。

## 自然语言

- “清除 7001 合服数据”“清理 7001 的合服数据”“删除 7001 合服残留”对应 `server_id=7001`；
- “清档 7001”“清空 7001”是清档（`clear`），不要对应到本脚本。

## 风险与边界

- 破坏性操作，必须经过精确合同、路由校验、防火墙和原请求人确认；
- 脚本成功只表示上述记录处理结束，不代表可以安全开始下一次合服，也不代表游戏内数据一致；
- 数据库地址、账号、密码不进入知识资料、模型上下文、飞书消息或 Git（阅读副本已脱敏且不入库）。
