local config = {}

config = {}
NSLG_GROUP = "combine"
config['common'] = {
    hostnum = 4,
    log_level = "DEBUG", -- INFO, DEBUG
    -- common_ip = "192.168.50.109"
    src_server_id = {1023}, --这个战区所包含的服务器
    des_server_id = {11023}, --这个战区匹配到下赛季的目标服
    all_des_server_id = {11023,31023}, -- 同批次下赛季的所有目标服
    src_zone_id = 102303,   -- 从哪个战区合，src_server_id需要和ServerInfo中的sub_server_list一致
    IS_COMBINE = true,

    des_back_player_server_id_list = nil,  -- 回流服指定要在这个列表中，如果为nil，则表示不限制。默认不限制了，如果不想进回流服，那就置为{}

    common_router = "router_1",
    -- is_ignore_zone = true,  -- 当这个为true的时候，不会根据zone_id来合服，而是根据src_server_id和des_server_id来合服
    -- force_target_server_id = 11023, -- 强制合到某目标服
    -- combine_is_revert_account = true, -- 是否是回滚Account表
    -- revert_db_col = "Account20240824_1040", -- 回滚Account表的来源数据库
    -- combine_is_for_pre_test = true, -- 是否为先锋测试服合服

    season_scenario_info = {  scenario = 1, season = 1,}, -- 合服来源的赛季、剧本信息
}

config['gamemanager'] = {
    ip = "127.0.0.1",
    port = 11000,
    game_type = 'gamemanager',
    console_port = 11001,
}

config['game_1'] = {
    ip = "127.0.0.1",
    port = 11010,
    game_type = 'combine_tool',
    game_type_order = 1, --该类型服务器的第几个进程
    logic_service_num = 2,
    need_load_tool = true,
    need_load_map = true,
    dbmanager = "dbmanager_1",
}

config['game_2'] = {
    ip = "127.0.0.1",
    port = 11020,
    game_type = 'combine_tool',
    game_type_order = 2, --该类型服务器的第几个进程
    logic_service_num = 4,
    need_load_tool = true,
    need_load_map = true,
    dbmanager = "dbmanager_1",
}

config['game_3'] = {
    ip = "127.0.0.1",
    port = 11030,
    game_type = 'combine_tool',
    game_type_order = 3, --该类型服务器的第几个进程
    logic_service_num = 4,
    need_load_tool = true,
    need_load_map = true,
    dbmanager = "dbmanager_1",
}

config['router_1'] = {
    ip = "127.0.0.1",
    port = 11040,
    game_type = 'router',
    router_type = "in",
}

config['mongo'] = {
    nslg_game_common = {
        host = "login-server.zjhuayu.top",
        port = 27017,
        authdb = 'nslg_game_common',
        username = 'nslgGame',
        password = 'nslgGamePwd',
    },
    nslg_common = {
        host = "login-server.zjhuayu.top",
        port = 27017,
        authdb = 'nslg_common',
        username = 'nslgCommon',
        password = 'nslgCommonPwd',
    },
    nslg_combine = {
        host = "127.0.0.1",
        port = 27017,
        authdb = 'nslg_combine',
        username = 'nslgGame',
        password = 'nslgGamePwd',
    },
    [1023] = {
        host = "127.0.0.1",
        port = 27017,
        authdb = 'nslg_game',
        username = 'nslgGame',
        password = 'nslgGamePwd',
    },
    [11023] = {
        host = "127.0.0.1",
        port = 27017,
        authdb = 'nslg_game1',
        username = 'nslgGame',
        password = 'nslgGamePwd',
    },
    [31023] = {
        host = "127.0.0.1",
        port = 27017,
        authdb = 'nslg_game3',
        username = 'nslgGame',
        password = 'nslgGamePwd',
    },
    [102303] = {
        host = "127.0.0.1",
        port = 27017,
        authdb = 'nslg_game',
        username = 'nslgGame',
        password = 'nslgGamePwd',
    },
}

config['dbmanager_1'] = {
    ip = "127.0.0.1",
    port = 11800,
    game_type = 'dbmanager',
    connectDbList = {"nslg_common", "nslg_game_common", "nslg_combine", 1023, 11023, 102303,31023}
}

return config
