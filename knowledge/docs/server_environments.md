# 服务器环境配置

唯一环境事实来源是 [config/server_environments.json](../../config/server_environments.json)。

- 每个服号只在 `regions.<区域>.environments.<环境>` 中登记一次。
- 跨区域环境（例如“海外所有 DEV 环境”）只在 `groups` 中以区域选择器定义，由程序派生服号列表；不得重复手写服号。
- 剧本会随时切换，不属于固定环境属性，因此不在此配置中记录。
- 飞书 Planner 使用的环境服号映射由 `tools/sync_hotfix_planner_catalog.py --write` 生成到 `config/muliu_script_catalog.md`；提交前可用同一脚本的 `--check` 检查是否漂移。

当前“海外所有 DEV 环境”按区域顺序合并港澳台、全球服、韩服、越南服；新增同类测试服时，只需在所属区域的 `dev` 数组中登记一次。
