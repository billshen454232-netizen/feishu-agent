# cc_patch.py

## 用途

管理测试服的 `patch_order_list.json` 与 Patch 文件。脚本支持：

- 查询单个服务器的 Patch 列表和缺失文件；
- 对比两个服务器的 Patch 列表；
- 备份或恢复 Patch 列表；
- 删除指定 Patch 并重新排序；
- 上传 Patch 文件或更新列表；
- 触发服务器热更。

> 除“检查”外，其余模式均可能修改远端文件或触发热更，不应因为知识问答而自动执行。

## 查询单个服务器 Patch 列表

```text
cc_patch.py -s <server_id> -ck
```

示例：

```text
cc_patch.py -s 2003 -ck
```

`-ck` 会将检查辅助脚本传到目标服务器并执行，输出 Patch 列表内容和缺失文件检查结果。因此它不修改 Patch 列表本身，但也不是完全不接触远端的本地只读查询。

在飞书中可以描述为：

```text
查询 2003 服当前 Patch 列表
检查 2003 服 Patch 状态
查看 2003 服补丁列表
```

当前机器人执行资料中已登记此模式。包含明确服务器编号的“查询 / 查看 / 检查 Patch 列表”请求会先生成 `-s <server_id> -ck` 计划，再经过防火墙和群内确认；不会因为这条说明而直接执行。

## 对比两个服务器的 Patch 列表

```text
cc_patch.py -s <server_id_1> <server_id_2> -ck
```

示例：

```text
cc_patch.py -s 2001 2003 -ck
```

会下载两台服务器的 `patch_order_list.json`，输出：

- 两边 Patch 总数；
- 仅一边存在的 Patch；
- 两边共有但 `id`、`order` 或 `patch_type` 不一致的 Patch。

此模式目前仅作为知识资料，尚未登记为机器人可执行计划。

## 参数说明

|参数|含义|影响|
|---|---|---|
|`-s` / `--server`|指定一个或两个服务器 ID|必填；两个 ID 配合 `-ck` 进入对比模式。|
|`-ck` / `--check`|查询 Patch 列表并检查缺失文件|查询模式。|
|`-m` / `--modify-json`|检查时移除 JSON 中无对应文件的条目|会修改远端 Patch 列表。|
|`-r bak`|备份远端 Patch 列表|会创建远端备份文件。|
|`-r re`|恢复远端 Patch 列表备份|会覆盖当前远端列表。|
|`-d <patch_file>`|删除指定 Patch 并重新排序|会修改并覆盖远端列表。|
|`-f <patch_name>`|上传 Patch 文件或更新 Patch 列表|会修改远端 Patch 文件或列表。|
|`-u` / `--is_update`|执行服务器 Patch 热更|会触发远端热更。|

## 高风险模式

以下模式应在文档中明确目的、影响范围和恢复方式后，才考虑登记到机器人执行资料：

```text
-m
-r bak / -r re
-d <patch_file>
-f <patch_name>
-u
```

其中 `-r re`、`-d`、`-f` 与 `-u` 可能直接改变服务器状态或内容。当前机器人应仅允许 `-s <server_id> -ck` 的检查模式。

## 前置条件

- 脚本需要同目录的 `config.ini` 解析服务器地址；
- 执行环境需要具备远程 SSH / SCP 能力；
- 检查模式依赖已有的 Patch 检查脚本；
- 真实执行必须使用 Linux 已部署的脚本版本。

## 风险等级

- `-ck`：查询 / 检查；会向远端传输并运行检查辅助脚本，不应误认为零写入的本地查询；
- 其他参数组合：变更操作，需单独评审和确认。

## 资料来源

- 脚本阅读副本：[../scripts/cc_patch.py](../scripts/cc_patch.py)
