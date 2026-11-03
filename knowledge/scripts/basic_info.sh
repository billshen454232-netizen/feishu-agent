#!/usr/bin/env bash

# 兼容两种用法：
#   basic_server_info.sh 6000 BL_111111
#   basic_server_info.sh -s 6000 -u BL_111111 [-m ctime|ssinfo|ssnum]

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE="$SCRIPT_DIR/config.ini"
REMOTE_TARGET_FILE="/data/app/game-server/server_common/Debug_ServerStartTime.lua"
REMOTE_CONSOLE_SCRIPT="$SCRIPT_DIR/remote_console/qa_main_console_game.sh"
SCORE_SCRIPT="$SCRIPT_DIR/expect_script/season_score.sh"
DB_QUERY_SCRIPT="$SCRIPT_DIR/py_help/db_query.py"
SSH_OPTIONS=(-o BatchMode=yes -o ConnectTimeout=10)
SUPERVISOR_LOG="/data/log/supervisor/supervisor_game_6.log"
START_MARKER="SEVER START WORK"
SUPERVISOR_EVIDENCE_LINES=20      # START WORK 之后最多输出的非轮转行
LOG_SCAN_FILES=30                 # 每个 bglog 目录取最新的文件数（覆盖最近一小时各进程）
LOG_SCAN_LINES=1000               # 每个文件只看最后多少行
LOG_EVIDENCE_LINES=60             # 异常证据最多输出行数
LOG_EVIDENCE_LINE_CHARS=300       # 单行最大字符数
LOG_EVIDENCE_PATTERN='error|traceback|fail|exception|panic'

server_id=""
user_id=""
method=""
positionals=()

usage() {
    cat <<'EOF'
用法：
  basic_server_info.sh [选项]
  basic_server_info.sh <服务器ID> [玩家ID|ctime]
  basic_server_info.sh ssinfo <剧本ID>
  basic_server_info.sh ssnum <玩家ID>

选项：
  -s, --server_id, --server-id ID    服务器 ID，例如 5010
  -u, --user_id, --user-id ID        玩家 ID 或账号名，例如 123456、BL_xxx
  -m, --method METHOD                查询方法：ctime、ssinfo、ssnum
  -h, --help                         查看帮助

示例：
  basic_server_info.sh -s 6000                    # 查询服务器基本信息
  basic_server_info.sh -s 6000 -u BL_111111       # 查询玩家在该服务器的分数
  basic_server_info.sh -s 6000 -m ctime           # 查询服务器时间
  basic_server_info.sh -u 123 -m ssinfo            # 查询剧本 123 的所有服务器
  basic_server_info.sh -u BL_111111 -m ssnum      # 查询角色经历过的赛季
EOF
}

die() {
    printf '错误：%s\n' "$*" >&2
    exit 2
}

require_option_value() {
    [[ $# -ge 2 && -n $2 && $2 != -* ]] || die "选项 $1 缺少参数"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -s|--server_id|--server-id)
            require_option_value "$1" "${2:-}"
            server_id=$2
            shift 2
            ;;
        -u|--user_id|--user-id)
            require_option_value "$1" "${2:-}"
            user_id=$2
            shift 2
            ;;
        -m|--method)
            require_option_value "$1" "${2:-}"
            method=$2
            shift 2
            ;;
        -s=*|--server_id=*|--server-id=*)
            server_id=${1#*=}
            shift
            ;;
        -u=*|--user_id=*|--user-id=*)
            user_id=${1#*=}
            shift
            ;;
        -m=*|--method=*)
            method=${1#*=}
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            positionals+=("$@")
            break
            ;;
        -*)
            die "不支持的选项：$1（使用 -h 查看帮助）"
            ;;
        *)
            positionals+=("$1")
            shift
            ;;
    esac
done

# 未使用选项时，保留旧的位置参数调用方式。
if [[ ${#positionals[@]} -gt 0 ]]; then
    [[ -z $server_id && -z $user_id && -z $method ]] || die "选项参数不能与位置参数混用"
    [[ ${#positionals[@]} -le 2 ]] || die "位置参数过多（使用 -h 查看帮助）"
    server_id=${positionals[0]}
    user_id=${positionals[1]:-}
fi

validate_server_id() {
    [[ $server_id =~ ^[A-Za-z0-9_-]+$ ]] || die "服务器 ID 格式无效：$server_id"
}

validate_user_id() {
    [[ $user_id =~ ^[A-Za-z0-9_-]+$ ]] || die "玩家 ID 格式无效：$user_id"
}

get_remote_host() {
    [[ -f $CONFIG_FILE ]] || die "找不到服务器配置文件：$CONFIG_FILE"

    local remote_host
    remote_host=$(awk -F '=' -v key="${server_id}_game" '
        $1 == key {
            value = $2
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            print value
            exit
        }
    ' "$CONFIG_FILE")
    [[ -n $remote_host ]] || die "配置文件中不存在服务器 ${server_id}_game"
    printf '%s\n' "$remote_host"
}

ssh_remote() {
    local remote_host=$1
    shift
    ssh "${SSH_OPTIONS[@]}" "root@$remote_host" "$@"
}

run_ctime() {
    validate_server_id
    [[ -x $REMOTE_CONSOLE_SCRIPT || -f $REMOTE_CONSOLE_SCRIPT ]] || die "找不到控制台脚本：$REMOTE_CONSOLE_SCRIPT"

    local output
    if ! output=$(bash "$REMOTE_CONSOLE_SCRIPT" "$server_id" | grep -E '^[0-9]{2,}$'); then
        die "未能取得服务器 ${server_id} 的时间戳"
    fi

    printf '服务器当前时间\n%s -> %s\n' "$output" "$(date -d "@$output" '+%Y-%m-%d %H:%M:%S')"
}

run_ssinfo() {
    validate_user_id
    [[ $user_id =~ ^[0-9]+$ ]] || die "ssinfo 的剧本 ID 必须为数字：$user_id"
    [[ -f $DB_QUERY_SCRIPT ]] || die "找不到数据库查询脚本：$DB_QUERY_SCRIPT"

    printf '查询目标剧本所有服务器信息\n'
    python3 "$DB_QUERY_SCRIPT" -d nslg_game_common -c ServerInfo -q "{\"scenario\": $user_id}"
}

run_ssnum() {
    validate_user_id
    [[ -f $DB_QUERY_SCRIPT ]] || die "找不到数据库查询脚本：$DB_QUERY_SCRIPT"

    printf '查询角色经历过的赛季\n'
    python3 "$DB_QUERY_SCRIPT" -d nslg_game_common -c Combine -q "{\"avatar.account_name\":\"$user_id\"},{\"soul.accSeasonNum\":1}"
}

run_score() {
    validate_server_id
    validate_user_id
    [[ -f $SCORE_SCRIPT ]] || die "找不到赛季分数查询脚本：$SCORE_SCRIPT"

    local output score
    if ! output=$(expect "$SCORE_SCRIPT" "$server_id" "$user_id"); then
        die "查询玩家分数失败；请检查服务器连接、玩家 ID 和控制台状态"
    fi

    score=$(printf '%s\n' "$output" | grep -E '\+[0-9]+[[:space:]]+\[[0-9]+(\.[0-9]+)?\]' || true)
    [[ -n $score ]] || die "未查询到玩家 ${user_id} 的赛季分数"
    printf '%s\n' "$score"
}

run_basic_info() {
    validate_server_id

    local remote_host original_content process_count process_name
    remote_host=$(get_remote_host)

    ssh_remote "$remote_host" 'cat /data/app/etc/nslg_game.lua'
    printf '************************\n当前代码版本:\n'
    ssh_remote "$remote_host" 'cat /data/app/game-server/release_etc/version.txt'
    printf '************************\n'

    # Debug_ServerStartTime.lua 在部分服务器或版本中不存在；缺失时继续检查服务状态。
    original_content=$(ssh_remote "$remote_host" "awk 'NR == 1 { print \$2; exit }' '$REMOTE_TARGET_FILE'" 2>/dev/null || true)

    if [[ -n $original_content ]]; then
        printf '************************\n开服时间 %s 时间戳/时间如下：\n%s -> %s\n************************\n' \
            "$REMOTE_TARGET_FILE" "$original_content" "$(date -d "@$original_content" '+%Y-%m-%d %H:%M:%S')"
    else
        printf '************************\n开服时间文件 %s 不存在或为空。\n************************\n' "$REMOTE_TARGET_FILE"
    fi

    if ! process_count=$(ssh_remote "$remote_host" "cd /data/app/game-server && ps -aux | grep '[s]kynet' | wc -l"); then
        die "查询 skynet 进程失败"
    fi
    [[ $process_count =~ ^[0-9]+$ ]] || die "获取到无效的进程数：$process_count"

    if [[ $process_count -eq 0 ]]; then
        printf '************************\n服务器已正常关闭\n************************\n'
        return 0
    fi

    if ! process_name=$(ssh_remote "$remote_host" "ps -aux | grep '[s]kynet' | awk '{print \$13}' | awk -F '=' '{print \$2}' | sort -u"); then
        die "查询 skynet 进程名称失败"
    fi
    printf '*************************\n开启中的 skynet 服务器进程组为：%s\n*************************\n' "$process_name"

    # supervisor_game_6.log 每小时追加一行 "logger file name:"（bglog 按小时轮转），
    # 开服标记很快会被挤出末尾，不能只看最后几行。规则：找最近一次 START WORK，
    # 它之后除了轮转行与空行没有其他输出 → 开服成功；否则输出这些行作为证据。
    local start_report last_start after_start
    if ! start_report=$(ssh_remote "$remote_host" "f='$SUPERVISOR_LOG'; [ -f \"\$f\" ] || { echo 'LAST_START=missing'; exit 0; }; n=\$(grep -n '$START_MARKER' \"\$f\" | tail -n 1 | cut -d: -f1); echo \"LAST_START=\${n:-0}\"; [ -n \"\$n\" ] && tail -n +\$((n + 1)) \"\$f\" | grep -v '^logger file name:' | grep -v '^[[:space:]]*\$' | tail -n $SUPERVISOR_EVIDENCE_LINES; true"); then
        die "读取 supervisor 日志失败"
    fi
    last_start=$(sed -n '1s/^LAST_START=//p' <<<"$start_report")
    after_start=$(sed '1d' <<<"$start_report")

    if [[ $last_start =~ ^[1-9][0-9]*$ && -z $after_start ]]; then
        printf '************************\n开服成功\n************************\n'
        return 0
    fi

    printf '未检测到开服成功信息\n'
    if [[ $last_start == missing ]]; then
        printf '************************\nsupervisor 日志 %s 不存在\n************************\n' "$SUPERVISOR_LOG"
    elif [[ $last_start == 0 ]]; then
        printf '************************\nsupervisor 日志中没有开服标记 %s\n************************\n' "$START_MARKER"
    else
        printf '************************\n最近一次开服标记（第 %s 行）之后的异常输出（已排除日志轮转行，最多 %s 行）：\n%s\n************************\n' \
            "$last_start" "$SUPERVISOR_EVIDENCE_LINES" "$after_start"
    fi

    # 证据：bglog/game 与 bglog/zone 最新的若干日志文件，每个只看最后 N 行，
    # 只保留异常关键词所在行，限制总行数与单行长度，供人工 / AI 诊断。
    local evidence
    evidence=$(ssh_remote "$remote_host" "cd /data/log/bglog 2>/dev/null || exit 0; for dir in game zone; do [ -d \"\$dir\" ] || continue; ls -t \"\$dir\" | grep '\\.log\$' | head -n $LOG_SCAN_FILES | while read -r f; do tail -n $LOG_SCAN_LINES \"\$dir/\$f\" | grep -iE '$LOG_EVIDENCE_PATTERN' | sed \"s|^|\$dir/\$f: |\"; done; done | cut -c1-$LOG_EVIDENCE_LINE_CHARS | tail -n $LOG_EVIDENCE_LINES" || true)

    if [[ -z $evidence ]]; then
        printf '************************\nbglog 最新 %s 个日志文件的最后 %s 行内未发现异常关键词\n************************\n' \
            "$LOG_SCAN_FILES" "$LOG_SCAN_LINES"
        return 1
    fi

    if grep -q 'run_patch error' <<<"$evidence"; then
        printf '************************\npatch 文件报错：\n%s\n************************\n' "$(grep 'patch' <<<"$evidence")"
    elif grep -q 'Listen error' <<<"$evidence"; then
        printf '************************\n服务器未正确关闭，端口占用报错\n>>> 服务器开关服太快，建议使用 KILL 杀进程重启\n************************\n'
    elif grep -q 'game_6 start fail' <<<"$evidence"; then
        printf '************************\n服务器启动失败，剧本沙盒场景拉起报错\n>>> game_6 start fail\n************************\n'
    fi
    printf '************************\nbglog 异常行（每个文件最后 %s 行内匹配，最多 %s 行）：\n%s\n************************\n' \
        "$LOG_SCAN_LINES" "$LOG_EVIDENCE_LINES" "$evidence"
    return 1
}

case $method in
    '')
        if [[ $server_id == 'ssinfo' ]]; then
            run_ssinfo
        elif [[ $server_id == 'ssnum' ]]; then
            run_ssnum
        elif [[ -n $user_id ]]; then
            run_score
        else
            run_basic_info
        fi
        ;;
    ctime)
        [[ -n $server_id ]] || die 'ctime 需要指定服务器 ID（-s）'
        [[ -z $user_id ]] || die 'ctime 不接受玩家 ID'
        run_ctime
        ;;
    ssinfo)
        [[ -z $server_id || $server_id =~ ^[A-Za-z0-9_-]+$ ]] || die "服务器 ID 格式无效：$server_id"
        [[ -n $user_id ]] || die 'ssinfo 需要通过 -u 指定剧本 ID'
        run_ssinfo
        ;;
    ssnum)
        [[ -z $server_id || $server_id =~ ^[A-Za-z0-9_-]+$ ]] || die "服务器 ID 格式无效：$server_id"
        [[ -n $user_id ]] || die 'ssnum 需要通过 -u 指定玩家 ID'
        run_ssnum
        ;;
    *)
        die "不支持的查询方法：$method（支持：ctime、ssinfo、ssnum）"
        ;;
esac
