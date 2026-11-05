#!/bin/bash
# 只读：查询起始服所在机器的合服状态（判定规则与 Server Manager core/merge/agent.py 的 _scan_logs 一致）
# 用法：combine_status.sh <起始服服号>
# 输出最后的 COMBINE_STATE=running|succeeded|failed|unknown；合服工具合完不会自动退出，进程数不作为完成依据。
set -u
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE="$SCRIPT_DIR/config.ini"
server_id=${1:-}

if [[ $# -ne 1 || ! "$server_id" =~ ^[0-9]{3,8}$ ]]; then
    echo "用法: $0 <起始服服号>" >&2
    exit 1
fi
host=$(awk -F '=' -v key="${server_id}_game" '$1 == key {print $2}' "$CONFIG_FILE" | tr -d '\r ')
if [[ -z "$host" ]]; then
    echo "找不到 ${server_id}_game 配置" >&2
    exit 1
fi

ssh "root@$host" 'bash -s' <<'REMOTE'
log_dir=$(readlink -f /data/app/game-server/../log)
echo "合服日志目录：${log_dir}"
procs=$(ps -ef | grep 'skynet/skynet' | grep -E 'GROUP=[0-9]*combine' | grep -v grep | wc -l)
echo "合服工具进程：${procs} 个（合完不会自动退出，不作为完成依据）"
cd "$log_dir" 2>/dev/null || { echo "日志目录不存在"; echo "COMBINE_STATE=unknown"; exit 0; }

# 完成标记只写在 nohup.log；exc_server_merge.sh 每次启动前把旧 nohup.log 改名为 nohup.log.prev，保证只含当次合服
files=$(ls -t combine_* nohup.log 2>/dev/null | head -n 21)
if [[ -z "$files" ]]; then
    echo "没有合服日志"
    echo "COMBINE_STATE=unknown"
    exit 0
fi
latest=$(ls -t combine_server_gamemanager_*.log 2>/dev/null | head -n 1)
[[ -n "$latest" ]] && echo "主控日志：${latest}（最后更新 $(date -r "$latest" '+%F %T')）"

success_patterns=$(cat <<'PATTERNS'
---------- 此次合服操作流程结束，感谢使用 ----------
---------- 结束流程【22/22】 ----------
PATTERNS
)
failure_patterns=$(cat <<'PATTERNS'
线上合服环境无法
未配置src_zone_id
未找到src_zone_id对应的服ServerInfo
未找到src_zone_id对应的子服列表
src_zone_id对应的服不是zone类型
src_zone_id对应的服未配置name
src_zone_id对应的服未配置season
src_zone_id对应的子服列表和配置的src_server_id不一致
src_zone_id对应的子服列表中有重复的src_server_id
未找到CrossMatchService的nextServerInfo
未找到CrossMatchService的backPlayerWriteList
CrossMatchService的nextServerIdList和配置的des_server_id不一致
合服数据校验发现错误
matchUnitGroup has no matchRegionGroup
matchUnitGroup matchRegionGroup not match
combine fail
auth failed
ENVGAME is not exist
init service failed
FAILED
Traceback
PATTERNS
)

failed=""; succeeded=""
for f in $files; do
    tail_text=$(tail -c 524288 "$f")
    while IFS= read -r p; do
        [[ -n "$p" ]] && grep -qF -- "$p" <<<"$tail_text" && failed+="  ${f}: ${p}"$'\n'
    done <<<"$failure_patterns"
    while IFS= read -r p; do
        [[ -n "$p" ]] && grep -qF -- "$p" <<<"$tail_text" && succeeded+="  ${f}: ${p}"$'\n'
    done <<<"$success_patterns"
done

if [[ -n "$failed" ]]; then
    state=failed; echo "命中失败关键字："; printf '%s' "$failed"
elif [[ -n "$succeeded" ]]; then
    state=succeeded; echo "命中完成关键字："; printf '%s' "$succeeded"
else
    state=running; echo "未命中完成或失败关键字（合服进行中，或已中断）"
fi

if [[ -n "$latest" ]]; then
    echo "---- 主控日志最后 40 行 ----"
    tail -n 40 "$latest"
fi
trace=$(ls -t combine_trace_server_*.log 2>/dev/null | head -n 1)
if [[ -n "$trace" && -s "$trace" ]]; then
    echo "---- trace 日志中的报错行（最多 20 行）----"
    grep -n -iE 'error|traceback|fail|exception' "$trace" | tail -n 20
fi
if [[ -f nohup.log ]]; then
    echo "---- nohup.log 最后 15 行（合服流程输出）----"
    tail -n 15 nohup.log
fi
echo "COMBINE_STATE=${state}"
REMOTE
