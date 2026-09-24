#!/bin/bash
server_id=$1
# 定义玩家ID
userId=$2

# 获取目标服务器ip
REMOTE_HOST=$(awk -F '=' -v key="$server_id"_game '$1 == key {print $2}' config.ini)
REMOTE_TARGET_FILE="/data/app/game-server/server_common/Debug_ServerStartTime.lua"

if [[ "$userId" == "ctime" ]]; then
    echo "服务器当前时间"
    # 调用 expect 脚本并获取输出
    output=$(bash /home/serverGeneralScript/remote_console/qa_main_console_game.sh "$server_id"|grep -E "^[0-9]{2,}$")
    echo "$output" "->" $(date -d @"$output" +"%Y-%m-%d %H:%M:%S")
    exit 0
fi

if [[ "$server_id" == "ssinfo" ]]; then
    echo "查询目标剧本所有服务器信息"
    python3 /home/serverGeneralScript/py_help/db_query.py -d nslg_game_common -c ServerInfo -q '{"scenario": '$userId'}'
    exit 0
fi
if [[ "$server_id" == "ssnum" ]]; then
    echo "查询角色经历过的赛季"
    python3 /home/serverGeneralScript/py_help/db_query.py -d nslg_game_common -c Combine -q '{"avatar.account_name":"'$userId'"},{"soul.accSeasonNum":1}'
    exit 0
fi

if [[ -n "$userId" ]]; then
    # 调用 expect 脚本并获取输出
    output=`expect expect_script/season_score.sh $server_id $userId`
    echo "$output" | grep -E '\+[0-9]+\s+\[[0-9]+(\.[0-9]+)?\]'
    exit 0
fi

# 输出目标服的赛季赛季剧本配置
ssh "root@$REMOTE_HOST" "cat /data/app/etc/nslg_game.lua"
echo "************************"
echo "当前代码版本:"
ssh "root@$REMOTE_HOST" "cat /data/app/game-server/release_etc/version.txt"
echo "************************"
# 输出开服的时间戳，并转化为日期输出
original_content=$(ssh "root@$REMOTE_HOST" 'cat '"$REMOTE_TARGET_FILE"' | awk -F " " "{print \$2}"' 2>/dev/null)
if [ -n "$original_content" ]; then
    echo "************************"
    echo "开服时间 $REMOTE_TARGET_FILE 时间戳/时间如下："
    echo "$original_content" "->" $(date -d @"$original_content" +"%Y-%m-%d %H:%M:%S")
    echo "************************"
else
    echo "************************"
    echo "开服时间文件 $REMOTE_TARGET_FILE 不存在或为空。"
    echo "************************"
fi

process_count=$(ssh root@$REMOTE_HOST "cd /data/app/game-server && ps -aux | grep skynet | grep -v grep | wc -l")
# 根据进程数量进行判断
if [ "$process_count" -eq 0 ]; then
    echo "************************"
    echo "服务器已正常关闭"
    echo "************************"
    exit 0  # 退出脚本或函数，返回成功状态码
fi
# 检查服务器skynet进程的名字，判断进程类型输出
process_name=$(ssh root@$REMOTE_HOST "ps -aux | grep skynet | grep -v grep |awk -F ' ' '{print \$13}'|awk -F '=' '{print \$2}'|sort -u")
echo "*************************"
echo "开启中的skynet服务器进程组为:$process_name"
echo "*************************"
# 执行远程命令并获取输出
output=$(ssh root@$REMOTE_HOST "tac /data/log/supervisor/supervisor_game_6.log|head -6")

# 检查输出中是否包含 "SEVER START WORK"
if echo "$output" | grep -q "SEVER START WORK"; then
    echo "************************"
    echo "开服成功"
    echo "************************"
else
    echo "未检测到开服成功信息"
    ssh root@$REMOTE_HOST 'dir_path="/data/log/bglog/game"; if [ -z "$(ls -A "$dir_path")" ]; then echo "bglog/game文件夹为空";exit 1; else echo "文件夹不为空"; fi'
    exit_code=$?
    if [[ $exit_code -eq 1 ]]; then
        exit 1
    fi
    trace_info=$(ssh -o ConnectTimeout=5 root@$REMOTE_HOST "cd /data/log/bglog/game && grep -i 'trace' \$(ls *.log | tail -100)")
    # 简单的检查一下日志中是否包含对应字符 打印可能匹配的问题
    if echo "$trace_info" | grep -q "run_patch error"; then
        echo "************************"
        echo "patch文件报错:"
        echo "$trace_info" | grep "patch"
        echo "************************"
    # 服务器开关服太快，建议使用KILL杀进程重启
    elif echo "$trace_info" | grep -q "Listen error"; then
        echo "************************"
        echo "服务器未正确关闭，端口占用报错文件报错"
        echo ">>>服务器开关服太快，建议使用KILL杀进程重启"
        echo "************************"
    elif echo "$trace_info" | grep -q "game_6 start fail"; then
        echo "************************"
        echo "服务器启动失败,剧本沙盒场景拉起报错"
        echo ">>> game_6 start fail"
        echo "************************"
    fi

    exit 1
fi
