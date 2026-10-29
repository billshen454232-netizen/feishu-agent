#!/bin/bash
set -u

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE="$SCRIPT_DIR/config.ini"
target_server_id=${1:-}

if [[ $# -ne 1 || ! "$target_server_id" =~ ^[0-9]{3,8}$ ]]; then
    echo "用法: $0 <target_server_id>" >&2
    exit 1
fi

get_game_host() {
    local id=$1
    awk -F '=' -v key="${id}_game" '$1 == key {print $2}' "$CONFIG_FILE"
}

host=$(get_game_host "$target_server_id")
if [[ -z "$host" ]]; then
    echo "找不到目标服 ${target_server_id} 对应的 ${target_server_id}_game 配置" >&2
    exit 1
fi

echo "停止目标服 ${target_server_id} 的合服工具进程"
ssh "root@$host" "cd /data/app/game-server && bash bin/kill_combine.sh"

echo "目标服 ${target_server_id} 的关闭脚本已执行"
