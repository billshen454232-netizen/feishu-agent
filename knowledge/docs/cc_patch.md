# cc_patch.py

## 用途

管理测试服的 `patch_order_list.json` 与 Patch 文件。脚本可用于查询、对比、备份 / 恢复、移除无效条目、删除 / 上传 Patch，以及触发 Patch 热更。

机器人是否可执行任一精确参数形态，只以 [../../config/muliu_script_catalog.md](../../config/muliu_script_catalog.md) 的当前合同为准。本文不因为脚本原生支持某参数就授予许可，也不把仓库合同写成 GS-1 已部署证据。

## 常见调用形态与影响

|调用形式|用途|影响与验证边界|
|---|---|---|
|`cc_patch.py -s <server_id> -ck`|查询单服 Patch 列表和缺失文件。|会向目标服传输并运行检查辅助脚本，不是零接触本地读取。|
|`cc_patch.py -s <server_id_1> <server_id_2> -ck`|对比两个服务器的 Patch 列表。|读取并比较两边列表；不能据此推断 Patch 已在游戏内生效。|
|`cc_patch.py -s <server_id> -ck -m`|清理列表中缺失文件的条目。|会修改远端 Patch 列表。|
|`cc_patch.py -s <server_id> -r bak`|备份远端 Patch 列表。|会创建远端备份；备份存在不等于恢复流程已验证。|
|`cc_patch.py -s <server_id> -r re`|恢复 Patch 列表备份。|会覆盖当前远端列表。|
|`cc_patch.py -s <server_id> -d <patch_file>`|删除指定 Patch 并重新排序。|会改变远端列表。|
|`cc_patch.py -s <server_id> -f <patch_name>`|上传 Patch 文件或更新 Patch 列表。|会写入远端文件或列表。|
|`cc_patch.py -s <server_id> -u`|触发服务器 Patch 热更。|脚本终态成功不等于 Patch 已被进程加载或游戏内效果已生效。|

具体是否已登记、参数限制和风险级别以 catalog 当前合同为准。不得从上述示例外推其他 flag、多个目标、文件名、路径或自由参数。

## 自然语言边界

- “查询 / 查看 / 检查 2003 服 Patch 列表”只应映射到合同明确允许的检查形态；
- “比较 2001 与 2003 的 Patch”必须同时给出两个明确服号，并仅能匹配合同的双服对比形态；
- 备份、恢复、删除、上传和热更都是高影响变更，不能被“查看 Patch”之类请求推断出来；
- 不向 AI、飞书计划或参数暴露内部主机、Patch 目录、SCP / SSH 选项或 Shell 片段。

## 前置条件与结果表述

- 已部署脚本依赖同目录受控 `config.ini`、SSH / SCP 能力和 Patch 辅助脚本；内部路由不能进入知识资料或群消息；
- 必须以实际 Linux 部署版本为准，不以本仓库阅读副本断言远端能力；
- 脚本返回成功最多证明对应脚本命令的终态。是否上传正确、列表是否已生效、服务是否重载或游戏内效果是否改变，均需要独立证据。

## 风险等级

- 检查与对比：查询 / 检查，但仍会触达远端；
- 备份、恢复、清理、删除、上传和热更：写操作，必须经精确合同、防火墙和原请求人确认；
- 所有模式的真实 GS-1 部署状态均需环境维护者独立核验。

## 资料来源

- 脚本阅读副本：[../scripts/cc_patch.py](../scripts/cc_patch.py)
