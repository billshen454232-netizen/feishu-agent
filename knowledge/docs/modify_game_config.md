# modify_game_config.sh

## 用途

修改指定测试服游戏配置中的赛季和剧本值，然后输出修改后的配置文件内容。

机器人是否允许某个精确调用，只以 [../../config/muliu_script_catalog.md](../../config/muliu_script_catalog.md) 的当前合同为准。本文不把合同登记或脚本退出成功表述为远端配置已生效。

## 调用形式

```text
modify_game_config.sh <server_id> <season> <scenario>
```

|参数|含义|
|---|---|
|`server_id`|明确的单个目标测试服编号。|
|`season`|要写入的赛季值。|
|`scenario`|要写入的剧本值。|

不得根据自然语言补猜赛季、剧本，或添加未在合同中登记的额外参数。

## 实际影响

脚本会直接修改目标服务器游戏配置文件中的：

```text
NSLG_SEASON
NSLG_SCENARIO
```

它通过远程 `sed -i` 修改后读取文件输出，因此属于**高风险配置写操作**。

## 前置检查、确认与验证

- 先确认目标服、赛季、剧本值的来源、当前环境占用、重载 / 重启要求与回滚方式；
- 计划必须精确匹配 catalog，并经路由校验、防火墙和原请求人确认；
- 脚本输出修改后的内容，只能证明该层读取到相应文本；不能直接证明运行中服务已重新加载配置，或游戏内剧本、赛季已切换；
- 实际执行环境以 Linux 已部署版本为准；内部 `config.ini` 映射和 SSH 目标不能进入 AI、飞书或 Git。

## 资料来源

- 脚本阅读副本：[../scripts/modify_game_config.sh](../scripts/modify_game_config.sh)
