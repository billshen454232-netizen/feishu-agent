#!/bin/bash
source init_args.sh
group_name=${group_num}"combine"
num=`ps -ef | grep 'skynet/skynet' | grep 'cross_server' | grep 'lua' | grep -v 'grep' | awk '{print $2}'|wc -l`
if [[ $num -gt 0 ]];
then
	bash kill_server.sh
fi
cd ../
time2=$(date "+%Y%m%d%H%M%S")
echo $pwd
echo $time2
if [ ! -d "../log" ]; then
	mkdir -p ../log
fi
if [ ! -d "../log/bak" ]; then
	mkdir -p ../log/bak
fi
cur_dir=$(pwd)
echo $cur_dir
cd ../log
rm -rf ../log/bak/*
ls | grep combine | awk '{print $1}' | xargs -n1 -I {} mv {}  bak/{}.$time2

cd $cur_dir

echo $cur_dir

# nohup ./skynet/skynet etc/app.lua GROUP=$group_name WORKER=gamemanager CLUSTER=router_server_cluster_config > ../log/nohup.log 2>&1 &
# nohup ./skynet/skynet etc/gamemanager/combine_tool_gamemanager.lua > ../log/nohup.log 2>&1 &
nohup ./skynet/skynet etc/app.lua GROUP=$group_name WORKER=gamemanager CLUSTER=combine_tool_cluster_config >> ../log/nohup.log 2>&1 &
sleep 2
# nohup ./skynet/skynet etc/dbmanager/combine_tool_dbmanager_1.lua >> ../log/nohup.log 2>&1 &
nohup ./skynet/skynet etc/app.lua GROUP=$group_name WORKER=dbmanager NUM=1 CLUSTER=combine_tool_cluster_config >> ../log/nohup.log 2>&1 &

# nohup ./skynet/skynet etc/game/combine_tool_game_1.lua >> ../log/nohup.log 2>&1 &
nohup ./skynet/skynet etc/app.lua GROUP=$group_name WORKER=game NUM=1 CLUSTER=combine_tool_cluster_config >> ../log/nohup.log 2>&1 &

# nohup ./skynet/skynet etc/game/combine_tool_game_2.lua >> ../log/nohup.log 2>&1 &
nohup ./skynet/skynet etc/app.lua GROUP=$group_name WORKER=game NUM=2 CLUSTER=combine_tool_cluster_config >> ../log/nohup.log 2>&1 &

# nohup ./skynet/skynet etc/game/combine_tool_game_3.lua >> ../log/nohup.log 2>&1 &
nohup ./skynet/skynet etc/app.lua GROUP=$group_name WORKER=game NUM=3 CLUSTER=combine_tool_cluster_config >> ../log/nohup.log 2>&1 &

nohup ./skynet/skynet etc/app.lua GROUP=$group_name WORKER=routerserver CLUSTER=combine_tool_cluster_config NAME=router_1 LOG=router >> ../log/nohup.log 2>&1 &

if [ $is_backgroup -eq 0 ]; then
    tail ---disable-inotify -f ../log/nohup.log
fi
