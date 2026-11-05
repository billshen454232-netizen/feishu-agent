#!/bin/bash
# 合服：检查起始服机器 → 生成合服配置 → 上传 → 后台启动合服工具 → 按日志关键字等待结果（≤90 秒）
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE="$SCRIPT_DIR/config.ini"
PY=python3
MERGE_WAIT_SECONDS=90
MERGE_POLL_SECONDS=5
new_server_name=${@:1:1}

valueip=$(awk -F '=' -v key="$new_server_name"_game '$1 == key {print $2}' "$CONFIG_FILE" | tr -d '\r ')
if [[ -z "$valueip" ]]; then
    echo "找不到 ${new_server_name}_game 配置，已停止"
    exit 1
fi

# 合服前检查：起始服机器上是否已有合服工具进程（GROUP=<编号>combine）
running=$(ssh root@$valueip "ps -ef | grep 'skynet/skynet' | grep -E 'GROUP=[0-9]*combine' | grep -v grep; true")
if [[ $? -ne 0 ]]; then
    echo "无法连接起始服 ${new_server_name} 所在机器检查合服进程，已停止"
    exit 1
fi
if [[ -n "$running" ]]; then
    echo "起始服 ${new_server_name} 所在机器已有合服工具进程在运行，已停止本次合服（未生成配置、未覆盖配置、未启动合服工具）："
    echo "$running" | awk '{print "  PID " $2}'
    echo "请先确认上一次合服已结束；需要停止时可执行「关闭 ${new_server_name} 服的合服工具」"
    exit 2
fi

$PY "$SCRIPT_DIR/pys_col/gen_combine_config.py" "$@" || { echo "生成合服配置失败，已停止：未覆盖配置、未启动合服工具"; exit 1; }

local_file_path="$SCRIPT_DIR/pys_col/combine_config/combine_demo_new.lua"
target_file_path="/data/app/game-server/etc/combine_tool_cluster_config.lua"
scp "$local_file_path" root@$valueip:$target_file_path || { echo "上传合服配置失败，已停止：未启动合服工具"; exit 1; }

# 旧 nohup.log 改名为 nohup.log.prev（start_combine_tool.sh 只会追加），让判定只看当次合服的输出
ssh root@$valueip 'cd "$(readlink -f /data/app/game-server/../log)" 2>/dev/null && [ -f nohup.log ] && mv -f nohup.log nohup.log.prev; true'

# -b 1：后台模式，启动后立即返回（否则 start_combine_tool.sh 末尾 tail -f 会一直挂住 ssh）
ssh root@$valueip "cd /data/app/game-server/bin;bash start_combine_tool.sh -b 1" >/dev/null || { echo "启动合服工具失败"; exit 1; }
echo "合服工具已启动，等待合服结果（最多 ${MERGE_WAIT_SECONDS} 秒）"

# 测试服合服通常 20–30 秒；按 combine_status.sh（与 Server Manager 相同的日志关键字）判定结果
waited=0
while true; do
    sleep "$MERGE_POLL_SECONDS"
    waited=$((waited + MERGE_POLL_SECONDS))
    status_output=$(bash "$SCRIPT_DIR/combine_status.sh" "$new_server_name" 2>&1)
    state=$(sed -n 's/^COMBINE_STATE=//p' <<<"$status_output" | tail -n 1)
    if [[ "$state" == "succeeded" ]]; then
        echo "合服完成（约 ${waited} 秒）"
        echo "$status_output"
        echo "注意：合服工具合完不会自动退出，确认数据后可执行「关闭 ${new_server_name} 服的合服工具」"
        exit 0
    fi
    if [[ "$state" == "failed" ]]; then
        echo "合服失败（约 ${waited} 秒），日志如下："
        echo "$status_output"
        exit 1
    fi
    if (( waited >= MERGE_WAIT_SECONDS )); then
        echo "合服超过 ${MERGE_WAIT_SECONDS} 秒仍未结束，按异常处理（测试服正常 20–30 秒），日志如下："
        echo "$status_output"
        exit 3
    fi
done
