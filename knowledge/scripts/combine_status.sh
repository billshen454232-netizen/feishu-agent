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

# 完成标记只写在 nohup.log；它可能累积多次合服，判定时只取最后一个「开始流程【1/…】」之后的部分
# （exc_server_merge.sh 启动前也会把旧 nohup.log 改名为 nohup.log.prev）
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
没有对应的serverInfo
PATTERNS
)

failed=""; succeeded=""
for f in $files; do
    if [[ "$f" == nohup.log ]] && start=$(grep -n '开始流程【1/' nohup.log | tail -n 1 | cut -d: -f1) && [[ -n "$start" ]]; then
        # nohup.log 可能累积多次合服（未经本脚本启动时不会归档）：只看最后一次合服那一段
        tail_text=$(tail -n +"$start" nohup.log | tail -c 524288)
    else
        tail_text=$(tail -c 524288 "$f")
    fi
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

# 摘要放最前：卡在哪一步、本次合服的失败记录（工具遇到失败常常不退出，只会一直等，日志末尾全是心跳）
if [[ -f nohup.log ]]; then
    seg_start=$(grep -n '开始流程【1/' nohup.log | tail -n 1 | cut -d: -f1)
    segment=$(tail -n +"${seg_start:-1}" nohup.log)
    last_begin=$(grep -o '开始流程【[0-9]*/[0-9]*】' <<<"$segment" | tail -n 1)
    last_end=$(grep -o '结束流程【[0-9]*/[0-9]*】' <<<"$segment" | tail -n 1)
    echo "---- 合服进度摘要 ----"
    if [[ -n "$last_begin" && "${last_begin#开始}" != "${last_end#结束}" ]]; then
        step_title=$(grep -F -A1 -- "$last_begin" <<<"$segment" | tail -n 1 | sed -E 's/^<<[^>]*>>[- ]*//; s/[- ]*$//')
        echo "当前停在：${last_begin#开始} ${step_title}（该流程尚未结束）"
    elif [[ -n "$last_end" ]]; then
        echo "最后完成：${last_end}"
    fi
    progress=$(grep -E '剩余未修改完成角色数量' <<<"$segment" | tail -n 1)
    [[ -n "$progress" ]] && echo "最新进度：${progress}"
    blocking=$(grep -E '失败|错误|ERROR|没有对应' <<<"$segment" | grep -v '不阻塞' | tail -n 20)
    if [[ -n "$blocking" ]]; then
        echo "本次合服的失败记录（不含「不阻塞流程」的，最多 20 行）："
        printf '%s\n' "$blocking"
    fi
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
# 主控日志行很长（会带整张 serverList），放最后并截断每行，避免把上面的关键信息挤出机器人保留的日志长度
if [[ -n "$latest" ]]; then
    echo "---- 主控日志最后 20 行（已过滤心跳，每行最多 300 字符）----"
    grep -v -E 'RpcHeartBeat|STATICTIMER|RPCUpdateDBQueueSize|DispatchTransMessage|RPCTransferMessage' "$latest" | tail -n 20 | cut -c1-300
fi
echo "COMBINE_STATE=${state}"
REMOTE
