# exc_server_merge.sh

## 用途

在一个明确指定的来源服节点上生成合服配置，覆盖该节点的合服工具配置，并启动合服工具进程。

## 首批受控调用形式

```text
bash exc_server_merge.sh <source_server_id> <target_server_id> <season> <scenario>
```

|参数|含义|
|---|---|
|`source_server_id`|合服来源服；脚本以该服定位执行节点，并将其写入生成配置的来源服字段。|
|`target_server_id`|合服目标服；脚本将其写入生成配置的目标服字段。|
|`season`|合服来源的赛季编号。|
|`scenario`|合服来源的剧本编号。|

首批只登记一个目标服的精确调用形态。脚本虽然支持多个目标服，但多目标合服不在本次合同范围内，不能由机器人自行扩展参数数量。

## 自然语言边界

- “将 5000 服合到 5001 服，赛季 4、剧本 9”对应 `source_server_id=5000`、`target_server_id=5001`、`season=4`、`scenario=9`；
- 必须同时提供来源服、目标服、赛季和剧本；缺任一项时应要求补充，不得猜测；
- 两个服必须归属同一受控路由区域；国服与海外服不能混合在同一合服请求中；
- 不向 AI、飞书计划或调用参数暴露执行节点地址、数据库配置、账号或密码。

## 实际影响与验证边界

该脚本会生成配置、覆盖来源服节点的 `combine_tool_cluster_config.lua`，然后启动合服工具。启动脚本可能停止已有合服工具进程并整理旧日志。

脚本返回成功只代表生成、上传和启动命令已返回；不代表合服已完成，也不代表数据结果已经验证。合服完成情况必须通过后续专用状态或日志核验确认。

这是一项高影响写操作，必须在群内展示完整的来源服、目标服、赛季和剧本，并由原请求人确认。

## 资料来源

- 脚本阅读副本：[../scripts/exc_server_merge.sh](../scripts/exc_server_merge.sh)
- 配置生成阅读副本：[../scripts/gen_combine_config.py](../scripts/gen_combine_config.py)
- 合服工具启动阅读副本：[../scripts/start_combine_tool.sh](../scripts/start_combine_tool.sh)
