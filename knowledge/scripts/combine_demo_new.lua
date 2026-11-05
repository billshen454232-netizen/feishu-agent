local config = {}

config = {}
NSLG_GROUP = "combine"
config['common'] = {
    log_level = "DEBUG", -- INFO, DEBUG
    -- common_ip = "<IP>"
    src_server_id = {start_servername}, --这个战区所包含的服务器
    des_server_id = {end_servername,end_tow_servername}, --这个战区匹配到下赛季的目标服
    all_des_server_id = {end_servername,end_tow_servername}, -- 同批次下赛季的所有目标服
    src_zone_id = start_server_zone,   -- 从哪个战区合，src_server_id需要和ServerInfo中的sub_server_list一致
    IS_COMBINE = true,

    is_ignore_zone = false,  -- 当这个为true的时候，不会根据zone_id来合服，而是根据src_server_id和des_server_id来合服
    -- force_target_server_id = 11023, -- 强制合到某目标服
    -- combine_is_revert_account = false, -- 是否是回滚Account表
    -- revert_db_col = "Account20240720_1520", -- 回滚Account表的来源数据库
    season_scenario_info = {  scenario = start_scenario_num, season = start_season_num,}, -- 合服来源的赛季、剧本信息
}

config['gamemanager'] = {
    ip = "<IP>",
    port = 11000,
    game_type = 'gamemanager',
    console_port = 11001,
}

config['game_1'] = {
    ip = "<IP>",
    port = 11010,
    game_type = 'combine_tool',
    game_type_order = 1, --该类型服务器的第几个进程
    logic_service_num = 2,
    need_load_tool = true,
    need_load_map = true,
    dbmanager = "dbmanager_1",
}

config['game_2'] = {
    ip = "<IP>",
    port = 11020,
    game_type = 'combine_tool',
    game_type_order = 2, --该类型服务器的第几个进程
    logic_service_num = 5,
    need_load_tool = true,
    need_load_map = true,
    dbmanager = "dbmanager_1",
}

config['game_3'] = {
    ip = "<IP>",
    port = 11030,
    game_type = 'combine_tool',
    game_type_order = 3, --该类型服务器的第几个进程
    logic_service_num = 5,
    need_load_tool = true,
    need_load_map = true,
    dbmanager = "dbmanager_1",
}

config['mongo'] = {
    nslg_game_common = {
        rs = {
            {
                host = "<MONGO_HOST>",
                port = 3717,
                authdb = 'nslg_game_common',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
            {
                host = "<MONGO_HOST>",
                port = 3717,
                authdb = 'nslg_game_common',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
        },
    },
    nslg_common ={
        rs = {
            {
                host = "<MONGO_HOST>",
                port = 3717,
                authdb = 'nslg_common',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
            {
                host = "<MONGO_HOST>",
                port = 3717,
                authdb = 'nslg_common',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
        },
    },
    nslg_combine = {
    rs = {{
        host = "<MONGO_HOST>",
        port = 3717,
        authdb = 'nslg_combine',
        username = '<REDACTED>',
        password = '<REDACTED>',
    },{
    host = "<MONGO_HOST>",
    port = 3717,
        authdb = 'nslg_combine',
        username = '<REDACTED>',
        password = '<REDACTED>',}
}},
    [start_servername] = {
        rs = {
            {
                host = "dds-start_server_host_one.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_start_servername',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
            {
                host = "dds-start_server_host_two.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_start_servername',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
        },
    },
    [start_server_zone] = {
        rs = {
            {
                host = "dds-start_server_zone_one.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_start_server_zone',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
            {
                host = "dds-start_server_zone_two.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_start_server_zone',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
        },
    },
    [end_servername] = {
        rs = {
            {
                host = "dds-end_server_host_one.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_end_servername',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
            {
                host = "dds-end_server_host_two.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_end_servername',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
        },
    },
    [end_tow_servername] = {
        rs = {
            {
                host = "dds-end_server2_host_one.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_end_tow_servername',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
            {
                host = "dds-end_server2_host_two.mongodb.rds.aliyuncs.com",
                port = 3717,
                authdb = 'nslg_game_end_tow_servername',
                username = '<REDACTED>',
                password = '<REDACTED>',
            },
        },
    },
}

config['dbmanager_1'] = {
    ip = "<IP>",
    port = 11800,
    game_type = 'dbmanager',
    connectDbList = {"nslg_common", "nslg_game_common", "nslg_combine", start_servername, start_server_zone, end_servername,end_tow_servername}
}

return config

