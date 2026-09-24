# modify_game_config.sh

## 用途

修改指定测试服游戏配置中的赛季和剧本值，然后输出修改后的配置文件内容。

## 调用形式

```text
modify_game_config.sh <server_id> <season> <scenario>
```

示例：

```text
modify_game_config.sh 2003 12 345
```

## 参数说明

|参数|含义|
|---|---|
|`server_id`|目标测试服编号。|
|`season`|要写入的赛季值。|
|`scenario`|要写入的剧本值。|

## 实际影响

脚本会直接修改目标服务器的游戏配置文件中的以下配置项：

```text
NSLG_SEASON
NSLG_SCENARIO
```

它使用远程 `sed -i` 修改后再读取文件输出结果，因此不是查询脚本。

## 使用边界

- **高风险变更操作**：会直接更改服务器配置；
- 当前仅作为知识问答资料，**不得登记到机器人执行资料**；
- 若将来需要接入执行，至少需要先明确赛季和剧本的合法取值、变更后的重载/重启要求、回滚方法和确认人规则。

## 前置条件

- 脚本运行目录需要有 `config.ini`，用于解析服务器地址；
- 目标服务器需要可通过脚本已有的 SSH 访问方式连接；
- 实际执行环境使用 Linux 上已部署的版本。

## 资料来源

- 脚本阅读副本：[../scripts/modify_game_config.sh](../scripts/modify_game_config.sh)
