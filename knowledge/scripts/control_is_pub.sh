#!/bin/bash

server_id=$1
is_revert=$2

# 配置文件路径
CONFIG_FILE="/data/app/game-etc/server_cluster_config.lua"
CONFIG_FILE_ENV="/data/etc/nslg_env.lua"
MODIFY_SCRIPT="script_helper/modify_remote_config.sh"

# 走外部开服脚本 市场白名单账号也可以进入
if [ "$is_revert" == "op" ]; then
    echo "走开服脚本 外部体验"
    REMOTE_HOST=$(awk -F '=' -v key="$server_id"_game '$1 == key {print $2}' config.ini)
    ssh "root@$REMOTE_HOST" "cd /data/app/game-server && bash release_bin/public_game.sh"
    exit 0
fi

# 使用 modify_remote_config.sh 修改 IS_PUBLISH 的值
if [ "$is_revert" == "k" ]; then
    # k: 设置 IS_PUBLISH = false（关闭发布，开启调试）
    echo "正在关闭发布模式（IS_PUBLISH = false）..."
    bash "$MODIFY_SCRIPT" -s "$server_id" -f "$CONFIG_FILE" -p inside -c common -k IS_PUBLISH -v false
    bash "$MODIFY_SCRIPT" -s "$server_id" -f "$CONFIG_FILE_ENV" -p after -k NSLG_IS_PUBLISH -v false
    echo "调试面板已开启"

elif [ "$is_revert" == "re" ]; then
    # re: 设置 IS_PUBLISH = true（开启发布，关闭调试）
    echo "正在开启发布模式（IS_PUBLISH = true）..."
    bash "$MODIFY_SCRIPT" -s "$server_id" -f "$CONFIG_FILE" -p inside -c common -k IS_PUBLISH -v true
    bash "$MODIFY_SCRIPT" -s "$server_id" -f "$CONFIG_FILE_ENV" -p after -k NSLG_IS_PUBLISH -v true
    echo "外部环境已打开，调试面板已关闭"

else
    echo "错误: 无效的参数"
    echo "用法: $0 <server_id> <k|re|op>"
    echo "  k  - 关闭发布模式（IS_PUBLISH = false）"
    echo "  re - 开启发布模式（IS_PUBLISH = true）"
    echo "  op - 走开服脚本"
    exit 1
fi
