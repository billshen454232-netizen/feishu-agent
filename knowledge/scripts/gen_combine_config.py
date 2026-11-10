import os
import subprocess
import sys
import argparse
import re

def read_config_value(config_file, key):
    """从config_host.ini读取配置值"""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('=', 1)
                if len(parts) == 2 and parts[0].strip() == key:
                    return parts[1].strip()
        return ""
    except FileNotFoundError:
        print(f"错误：配置文件 {config_file} 不存在")
        sys.exit(1)
    except Exception as e:
        print(f"读取配置文件失败：{str(e)}")
        sys.exit(1)

def get_db_host(server_name, db_name=""):
    """通过shell脚本获取数据库host配置

    Args:
        server_name: 服务器名称/ID，如 '5010'

    Returns:
        dict: {'host1': '...', 'host2': '...'}
        例如: {'host1': '3ns56e409f0c49441', 'host2': '3ns56e409f0c49442'}
    """
    get_db_host_script=os.path.join(os.path.dirname(os.path.dirname(__file__)),"script_helper","get_db_host.py")
    # 不用 capture_output / text（3.7+）：港服 GS-1 经木流执行时 python3 是 pyenv 3.6.7
    result = subprocess.run(
        ["bash", get_db_host_script, str(server_name), str(db_name)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )
    lines = result.stdout.strip().split('\n')
    if len(lines) >= 2:
        return {
            'host1': lines[0].strip(),
            'host2': lines[1].strip()
        }
    else:
        print(f"错误：无法获取服务器 {server_name} 的host配置")
        print(f"Shell脚本输出：{result.stdout}")
        print(f"Shell脚本错误：{result.stderr}")
        sys.exit(1)


SERVER_GAME_AREAS = {
    "HMT": {"5000", "5001", "5010", "5011","5012","5013","5020", "5021"},
    "KR": {"6000", "6001", "6010", "6011", "6020", "6021"},
}


def get_game_area(server_id):
    """根据服务器 ID 获取对应游戏地区。"""
    server_id = str(server_id)
    for game_area, server_ids in SERVER_GAME_AREAS.items():
        if server_id in server_ids:
            return game_area

    raise ValueError(f"未配置服务器 {server_id} 对应的游戏地区")


COMBINE_TOOL_PROCESSES = 3  # game_1 / game_2 / game_3


def season_scenario_lines(season_scenarios):
    """赛季剧本写在哪里：common 始终写第一个起始服的（与原单起始服配置一致，**不能注释**）；
    起始服赛季剧本不同时，再按出现顺序写到 game_1、game_2…（服务端确认：各起始服的赛季剧本被覆盖即可，顺序无要求）。
    没单独写的进程沿用 common；common 被注释时这些进程会用默认 赛季1_剧本1 找服，卡在流程【3/23】
    （「slave对应的特征字符串(赛季_剧本_地区)1_1_HMT没有对应的serverInfo」）。

    Returns: (common 行, {进程序号: 行})
    """
    distinct = []
    for pair in season_scenarios:
        if pair not in distinct:
            distinct.append(pair)
    if len(distinct) > COMBINE_TOOL_PROCESSES:
        raise ValueError(f"起始服共有 {len(distinct)} 种赛季剧本，超过合服工具进程数 {COMBINE_TOOL_PROCESSES}")
    season, scenario = distinct[0]
    common = f"    season_scenario_info = {{  scenario = {scenario}, season = {season},}}, -- 合服来源的赛季、剧本信息"
    if len(distinct) == 1:
        return common, {}
    per_process = {
        order: f"    season_scenario_info = {{  scenario = {scenario}, season = {season},}}"
        for order, (season, scenario) in enumerate(distinct, 1)
    }
    return common, per_process


def generate_lua_config(new_server, end_servers, season_num, scenario_num, extra_sources=()):
    """动态生成Lua配置内容

    extra_sources: 同战区的其他起始服 [(服号, 赛季, 剧本)]；战区仍按第一个起始服（new_server + 01）。
    """
    game_area = get_game_area(new_server)
    extra_source_ids = [str(server) for server, _, _ in extra_sources]
    all_sources = [str(new_server)] + extra_source_ids
    if len(set(all_sources)) != len(all_sources) or set(all_sources) & {str(s) for s in end_servers}:
        raise ValueError("起始服不能重复，也不能同时是目标服")
    common_season_line, process_season_lines = season_scenario_lines(
        [(str(season_num), str(scenario_num))] + [(str(season), str(scenario)) for _, season, scenario in extra_sources]
    )

    def process_season(order):
        line = process_season_lines.get(order)
        return f"\n{line}" if line else ""

    # 读取新服务器的配置
    start_hosts_game = get_db_host(new_server)
    start_host1 = start_hosts_game['host1']
    start_host2 = start_hosts_game['host2']
    start_hosts_zone = get_db_host(new_server, "zone")
    start_zone1 = start_hosts_zone['host1']
    start_zone2 = start_hosts_zone['host2']
    start_server_zone = f"{new_server}01"  # 战区ID规则：服务器名+01
    config_ini_path=os.path.join(os.path.dirname(os.path.dirname(__file__)),"config.ini")
    local_addr=read_config_value(config_ini_path, f"{new_server}_game")

    # 读取所有目标服务器的配置（存储为字典，key=服务器名，value=host1和host2）
    # 其他起始服与目标服一样只需要自己的 game 库（战区库只有一个：第一个起始服 + 01）
    end_servers_config = {}
    for end_server in extra_source_ids + list(end_servers):
        end_servers_config[end_server] = get_db_host(end_server)

    # 生成MongoDB中目标服务器的配置块（动态追加所有目标服务器）
    mongo_end_servers = ""
    for i, end_server in enumerate(extra_source_ids + list(end_servers), 1):
        cfg = end_servers_config[end_server]
        # 第一个目标服务器用end_server_host_one，第二个及以后用end_serverX_host_one
        mongo_end_servers += f'''
    [{end_server}] = {{
        rs = {{
            {{
                host = "dds-{cfg['host1']}.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_{end_server}',
                username = 'nslg_game_{end_server}',
                password = '数据库密码',
            }},
            {{
                host = "dds-{cfg['host2']}.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_{end_server}',
                username = 'nslg_game_{end_server}',
                password = '数据库密码',
            }},
        }},
    }},'''

    # 生成dbmanager的connectDbList（包含所有目标服务器）
    connect_db_list = ["nslg_common", "nslg_game_common", "nslg_combine", new_server, start_server_zone] + extra_source_ids + list(end_servers)
    # connect_db_str = ", ".join([f'"{db}"' for db in connect_db_list])
    # 遍历列表，根据元素类型决定是否添加引号
    formatted_items = []
    for idx,item in enumerate(connect_db_list):
        if idx>=3:
            formatted_items.append(str(item))
        else:
            # 其他类型（如字符串）添加引号
            formatted_items.append(f'"{item}"')

    # 用逗号连接所有元素
    connect_db_str = ", ".join(formatted_items)
    # 拼接完整的Lua配置内容
    lua_content = f'''local config = {{}}

config = {{}}
NSLG_GROUP = "combine"
config['common'] = {{
    log_level = "DEBUG", -- INFO, DEBUG
    -- common_ip = ""
    src_server_id = {{{", ".join(all_sources)}}}, --这个战区所包含的服务器
    des_server_id = {{{", ".join(end_servers)}}}, --这个战区匹配到下赛季的目标服
    all_des_server_id = {{{", ".join(end_servers)}}}, -- 同批次下赛季的所有目标服
    src_zone_id = {start_server_zone},   -- 从哪个战区合，src_server_id需要和ServerInfo中的sub_server_list一致
    IS_COMBINE = true,

    is_ignore_zone = false,  -- 当这个为true的时候，不会根据zone_id来合服，而是根据src_server_id和des_server_id来合服
    des_back_player_server_id_list={{}}, --默认不进入回归备战服。把这个列表设为空集合表示不允许选中任何回流服
    -- force_target_server_id = 11023, -- 强制合到某目标服
    -- combine_is_revert_account = false, -- 是否是回滚Account表
    -- revert_db_col = "Account20240720_1520", -- 回滚Account表的来源数据库
{common_season_line}
}}

config['gamemanager'] = {{
    ip = "127.0.0.1",
    port = 11000,
    game_type = 'gamemanager',
    console_port = 11001,
}}

config['game_1'] = {{
    ip = "127.0.0.1",
    port = 11010,
    game_type = 'combine_tool',
    game_type_order = 1, --该类型服务器的第几个进程
    logic_service_num = 2,
    need_load_tool = true,
    need_load_map = true,
    dbmanager = "dbmanager_1",{process_season(1)}
}}

config['game_2'] = {{
    ip = "127.0.0.1",
    port = 11020,
    game_type = 'combine_tool',
    game_type_order = 2, --该类型服务器的第几个进程
    logic_service_num = 5,
    need_load_tool = true,
    need_load_map = true,
    dbmanager = "dbmanager_1",{process_season(2)}
}}

config['game_3'] = {{
    ip = "127.0.0.1",
    port = 11030,
    game_type = 'combine_tool',
    game_type_order = 3, --该类型服务器的第几个进程
    logic_service_num = 5,
    need_load_tool = true,
    need_load_map = true,
    dbmanager = "dbmanager_1",{process_season(3)}
}}

config['mongo'] = {{
    nslg_game_common = {{
        rs = {{
            {{
                host = "dds-3ns56e409f0c49441.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_common',
                username = 'nslg_game_common',
                password = '数据库密码',
            }},
            {{
                host = "dds-3ns56e409f0c49442.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_common',
                username = 'nslg_game_common',
                password = '数据库密码',
            }},
        }},
    }},
    nslg_common ={{
        rs = {{
            {{
                host = "dds-3ns56e409f0c49441.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_common',
                username = 'nslg_common',
                password = '数据库密码',
            }},
            {{
                host = "dds-3ns56e409f0c49442.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_common',
                username = 'nslg_common',
                password = '数据库密码',
            }},
        }},
    }},
    nslg_combine = {{
        rs = {{
            {{
                host = "dds-3ns56e409f0c49441.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_combine',
                username = 'nslg_combine',
                password = '数据库密码',
            }},
            {{
                host = "dds-3ns56e409f0c49442.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_combine',
                username = 'nslg_combine',
                password = '数据库密码',
            }}
        }}
    }},
    [{new_server}] = {{
        rs = {{
            {{
                host = "dds-{start_host1}.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_{new_server}',
                username = 'nslg_game_{new_server}',
                password = '数据库密码',
            }},
            {{
                host = "dds-{start_host2}.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_{new_server}',
                username = 'nslg_game_{new_server}',
                password = '数据库密码',
            }},
        }},
    }},
    [{start_server_zone}] = {{
        rs = {{
            {{
                host = "dds-{start_zone1}.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_{start_server_zone}',
                username = 'nslg_game_{start_server_zone}',
                password = '数据库密码',
            }},
            {{
                host = "dds-{start_zone2}.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_{start_server_zone}',
                username = 'nslg_game_{start_server_zone}',
                password = '数据库密码',
            }},
        }},
    }},{mongo_end_servers}
}}

config['dbmanager_1'] = {{
    ip = "127.0.0.1",
    port = 11800,
    game_type = 'dbmanager',
    connectDbList = {{{connect_db_str}}}
}}
config['router_1'] = {{
    ip = "127.0.0.1",
    port = 11040,
    game_type = 'router',
    router_type = "in",
}}
config.common.common_router = "router_1"
config.common.router_out = {{ hostnum = 1, address = "ip:port", open = true }}
config.common.cross_server = {{ cross = 2 }}
config.common.common_ip = "{str(local_addr)}"
config.common.hostnum = {new_server}04
config.common.game_area = "{game_area}"
return config'''

    return lua_content

def main():
    # 参数解析（支持任意数量的目标服务器）
    parser = argparse.ArgumentParser(description='动态生成合服配置文件')
    parser.add_argument('new_server', help='新服务器名称')
    parser.add_argument('end_servers', nargs='+', help='一个或多个目标服务器名称（支持无限追加）')
    parser.add_argument('season_num', help='赛季编号')
    parser.add_argument('scenario_num', help='场景编号')
    parser.add_argument('--also', nargs=3, action='append', default=[], metavar=('服号', '赛季', '剧本'),
                        help='同战区的其他起始服，可重复，如 --also 5011 5 10')
    args = parser.parse_args()

    # 生成Lua配置内容
    try:
        lua_content = generate_lua_config(
            new_server=args.new_server,
            end_servers=args.end_servers,
            season_num=args.season_num,
            scenario_num=args.scenario_num,
            extra_sources=[tuple(item) for item in args.also],
        )
    except ValueError as exc:
        print(f"错误：{exc}")
        sys.exit(1)

    # 写入文件
    output_file = os.path.join(os.path.dirname(__file__),"combine_config","combine_demo_new.lua")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(lua_content)
    print(f"配置文件已生成：{output_file}")

if __name__ == "__main__":
    main()
