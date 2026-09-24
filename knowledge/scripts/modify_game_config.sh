#!/bin/bash
server_id=$1
NEW_SEASON=$2
NEW_SCENARIO=$3

# 获取目标服务器ip
REMOTE_HOST=$(awk -F '=' -v key="$server_id"_game '$1 == key {print $2}' config.ini)
CONFIG_FILE="/data/app/etc/nslg_game.lua"

# 替换赛季值
ssh root@$REMOTE_HOST "sed -i 's/^\(NSLG_SEASON\s*=\s*\)[0-9]\+/\1$NEW_SEASON/' $CONFIG_FILE"

# 替换剧本值
ssh root@$REMOTE_HOST "sed -i 's/^\(NSLG_SCENARIO\s*=\s*\)[0-9]\+/\1$NEW_SCENARIO/' $CONFIG_FILE"

# echo "配置更新完成:"
# echo "新赛季值: $NEW_SEASON"
# echo "新剧本值: $NEW_SCENARIO"

# 输出更新后目标服的赛季赛季剧本配置
echo "更新后配置如下:"
ssh "root@$REMOTE_HOST" "cat /data/app/etc/nslg_game.lua"
