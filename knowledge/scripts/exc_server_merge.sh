#!/bin/bash
new_server_name=${@:1:1}
# 生成配置
python3.7 /home/serverGeneralScript/pys_col/gen_combine_config.py $@
# 本地配置生成的地址
local_file_path="/home/serverGeneralScript/pys_col/combine_config/combine_demo_new.lua"
#使用awk从eee所对应的目录中的config.ini中获取IP地址相关信息
valueip=$(awk -F '=' -v key="$new_server_name"_game '$1 == key {print $2}' config.ini)
# 远端合服配置文件所在的地址
target_file_path="/data/app/game-server/etc/combine_tool_cluster_config.lua"

#使用scp将本地文件覆盖到目标服务器对应文件
scp $local_file_path root@$valueip:$target_file_path
# 远端执行合服脚本
ssh root@$valueip "cd /data/app/game-server/bin;bash start_combine_tool.sh "
