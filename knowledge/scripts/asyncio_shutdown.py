import asyncio
from py_help.db_query import db_query
import sys


async def query_server_info(server):
    # 使用asyncio的异步子进程API，替代同步的subprocess.run()
    process = await asyncio.create_subprocess_exec(
        "./shutdown",server,"autokill",
        stdout=asyncio.subprocess.PIPE,  # 捕获 stdout（可选）
        stderr=asyncio.subprocess.PIPE   # 捕获 stderr（可选）
    )
    # 等待子进程完成，并获取输出（如果需要）
    stdout, stderr = await process.communicate()
    # 可选：打印脚本输出或错误信息
    if stdout:
        # print(f"服务器 {server} 输出:\n{stdout.decode().strip()}")
        pass
    if stderr:
        print(f"服务器 {server} 错误:\n{stderr.decode().strip()}")

async def main(list_server):
    tasks = [query_server_info(server) for server in list_server]
    await asyncio.gather(*tasks)


args = sys.argv[1:]
global scenario_filter
try:
    scenario_filter = args[1]
except:
    pass

server_name_list = []


if args and args[0] == "weekly":
    server_name_list = ["2003", "2005", "2010", "2015", "2016", "2017", "2018", "2019", "2213","2020"]
elif args and any("dev" in arg for arg in args):
    server_name_list = ["2002", "2004", "2009", "2110","2021" ,"2214","2030","2031"]
elif args and any("test" in arg or "qa" in arg for arg in args):
    server_name_list = ["2006", "2007", "2008", "2011", "2212","2013"]
else:
    server_name_list.append(args)
#print(server_name_list)
# 获取scenario为1000的回归备战服务器id
#server_collections = db_query(query={"scenario": 1000}, collection_name="ServerInfo", db_name="nslg_game_common")
#for collection_name in server_collections:
#    server_name_list.append(str(collection_name["hostNum"]))

#server_name_list = list(set(server_name_list))
print(server_name_list)
asyncio.run(main(server_name_list))


