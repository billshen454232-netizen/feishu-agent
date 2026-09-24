import argparse
import configparser
import os
import subprocess
import json
from collections import defaultdict


def get_remote_ip_from_ini(ini_file_path: str, server_id: str) -> str:
    """
    从INI文件解析指定server_id对应的远程IP
    :param ini_file_path: INI文件路径（如./config.ini）
    :param server_id: 输入的服务器ID（如2001）
    :return: 匹配的IP地址
    """
    # 初始化配置解析器
    config = configparser.ConfigParser()
    # 读取INI文件（解决键名大小写问题）
    config.optionxform = str  # 保留key的原始大小写
    if not os.path.exists(ini_file_path):
        raise FileNotFoundError(f"INI文件不存在：{ini_file_path}")
    
    # 读取[serverName]段
    if "serverName" not in config.sections():
        config.read(ini_file_path, encoding="utf-8")
        if "serverName" not in config.sections():
            raise ValueError("INI文件中未找到[serverName]段")
    
    # 拼接key（如2001 → 2001_game）
    target_key = f"{server_id}_game"
    # 获取对应IP
    try:
        remote_ip = config.get("serverName", target_key)
        if not remote_ip:
            raise ValueError(f"server_id {server_id} 对应IP为空")
        return remote_ip
    except configparser.NoOptionError:
        raise ValueError(f"INI文件中未找到 {target_key} 对应的IP")


def excute_remote_cmd(cmd):
    try:
        # 执行命令，捕获输出
        result = subprocess.run(
            cmd,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8"
        )
        print(f"命令执行成功：{result.stdout}")
    except subprocess.CalledProcessError as e:
        print(f"命令执行失败：{e.stderr}")
        raise


def compare_patch_lists(json_file1: str, json_file2: str, server1_id: str, server2_id: str):
    """
    对比两个服务器的 patch_order_list.json 文件

    :param json_file1: 服务器1的 JSON 文件路径
    :param json_file2: 服务器2的 JSON 文件路径
    :param server1_id: 服务器1的ID（用于显示）
    :param server2_id: 服务器2的ID（用于显示）
    """
    # 读取两个 JSON 文件
    with open(json_file1, 'r', encoding='utf-8') as f:
        data1 = json.load(f)
    with open(json_file2, 'r', encoding='utf-8') as f:
        data2 = json.load(f)

    list1 = data1.get("list", {})
    list2 = data2.get("list", {})

    # 创建文件名到patch信息的映射
    patches1 = {info.get("file"): {"id": pid, **info} for pid, info in list1.items()}
    patches2 = {info.get("file"): {"id": pid, **info} for pid, info in list2.items()}

    # 获取文件名集合
    files1 = set(patches1.keys())
    files2 = set(patches2.keys())

    # 只在服务器1有的
    only_in_server1 = files1 - files2
    # 只在服务器2有的
    only_in_server2 = files2 - files1
    # 两个服务器都有的
    common_files = files1 & files2

    # 检查共同文件的差异
    differences = []
    for file in common_files:
        patch1 = patches1[file]
        patch2 = patches2[file]

        diff = {}
        if patch1.get("order") != patch2.get("order"):
            diff["order"] = (patch1.get("order"), patch2.get("order"))
        if patch1.get("patch_type") != patch2.get("patch_type"):
            diff["patch_type"] = (patch1.get("patch_type"), patch2.get("patch_type"))
        if patch1.get("id") != patch2.get("id"):
            diff["id"] = (patch1.get("id"), patch2.get("id"))

        if diff:
            differences.append({"file": file, "diff": diff})

    # 打印对比结果
    print("\n" + "="*80)
    print(f"对比服务器 {server1_id} 和 {server2_id} 的 patch_order_list.json")
    print("="*80)

    print(f"\n服务器 {server1_id} patch 总数: {len(list1)}")
    print(f"服务器 {server2_id} patch 总数: {len(list2)}")

    if only_in_server1:
        print(f"\n【只在服务器 {server1_id} 存在的 patch】({len(only_in_server1)} 个):")
        for file in sorted(only_in_server1):
            patch = patches1[file]
            print(f"  - {file} (type: {patch.get('patch_type')}, order: {patch.get('order')})")

    if only_in_server2:
        print(f"\n【只在服务器 {server2_id} 存在的 patch】({len(only_in_server2)} 个):")
        for file in sorted(only_in_server2):
            patch = patches2[file]
            print(f"  - {file} (type: {patch.get('patch_type')}, order: {patch.get('order')})")

    if differences:
        print(f"\n【两个服务器都有但存在差异的 patch】({len(differences)} 个):")
        for item in differences:
            file = item["file"]
            diff = item["diff"]
            print(f"  - {file}:")
            for key, (val1, val2) in diff.items():
                print(f"      {key}: {server1_id}={val1}, {server2_id}={val2}")

    if not only_in_server1 and not only_in_server2 and not differences:
        print("\n✓ 两个服务器的 patch_order_list.json 完全一致！")

    print("="*80 + "\n")


def delete_patch_and_reorder(json_file_path: str, patch_file_name: str):
    """
    删除指定的 patch 并重新排序 id 和 order

    :param json_file_path: patch_order_list.json 文件路径
    :param patch_file_name: 要删除的 patch 文件名（如 "avatar_patch_60952.lua"）
    """
    # 读取 JSON 文件
    with open(json_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 获取原始列表
    original_list = data.get("list", {})

    # 先查找原始数据中每个 patch_type 的起始 order（必须在删除之前）
    all_patches_by_type = defaultdict(list)
    for patch_info in original_list.values():
        patch_type = patch_info.get("patch_type")
        all_patches_by_type[patch_type].append(patch_info.get("order", 0))

    # 确定每个类型的起始 order（通常是该类型中最小的 order）
    type_start_order = {}
    for patch_type, orders in all_patches_by_type.items():
        type_start_order[patch_type] = min(orders)

    # 找到要删除的 patch
    deleted_patch_id = None
    deleted_patch_type = None

    for patch_id, patch_info in original_list.items():
        if patch_info.get("file") == patch_file_name:
            deleted_patch_id = patch_id
            deleted_patch_type = patch_info.get("patch_type")
            break

    if deleted_patch_id is None:
        raise ValueError(f"未找到文件名为 '{patch_file_name}' 的 patch")

    print(f"找到要删除的 patch: ID={deleted_patch_id}, Type={deleted_patch_type}, File={patch_file_name}")

    # 删除指定的 patch
    del original_list[deleted_patch_id]

    # 按照原有的 id 顺序排序（转换为整数后排序）
    sorted_patches = sorted(original_list.items(), key=lambda x: int(x[0]))

    # 重新构建 list，使用新的 id 和 order
    new_list = {}
    new_id = 0
    type_order_counter = {}

    for _, patch_info in sorted_patches:
        patch_type = patch_info.get("patch_type")

        # 初始化该类型的计数器（首次遇到时使用起始 order）
        if patch_type not in type_order_counter:
            type_order_counter[patch_type] = type_start_order[patch_type]

        # 创建新的 patch 条目
        new_list[str(new_id)] = {
            "patch_type": patch_type,
            "order": type_order_counter[patch_type],
            "file": patch_info.get("file")
        }

        # 递增计数器
        type_order_counter[patch_type] += 1
        new_id += 1

    # 更新数据并保存
    data["list"] = new_list
    with open(json_file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"成功删除 patch: {patch_file_name}")
    print(f"总计 patch 数量: {len(new_list)}")


def main():
    # 参数解析（支持任意数量的目标服务器）
    parser = argparse.ArgumentParser(description='管理patch 替换list 穿patch文件 执行热更')
    parser.add_argument('-s','--server', nargs='+', help='目标服务器（可以指定多个，如 -s 2001 2002 用于对比）')
    parser.add_argument('-ck','--check', action='store_true', help='查看目标服务器patchlist内容 并且打印是否有缺失文件；如果提供了两个服务器，则对比两个服务器的列表')
    parser.add_argument('-u','--is_update', action='store_true', help='是否执行热更')
    parser.add_argument('-f','--patchname', help='patch文件名字 前缀是uf表示传文件,ul表示传列表')
    parser.add_argument('-r','--recover', help='备份相关 如果备份填bak 恢复备份填re')
    parser.add_argument('-m','--modify-json', action='store_true', help='检查时删除JSON中没有对应文件的条目')
    parser.add_argument('-d','--delete', help='删除指定的patch文件（如 avatar_patch_60952.lua）并重新排序')
    args = parser.parse_args()
    # host的配置文件路径
    ini_file_path="config.ini"
    # 远程文件路径
    remote_patch_list="/data/app/game-server/server_common/hotfix/patch_order_list.json"
    # 远程patch文件路径
    remote_patch_file="/data/app/game-server/server_common/hotfix"
    # 本地patch_order_list文件路径
    local_patch_list= "/home/serverGeneralScript/patch_test/patch_order_list.json"
    # 本地patch文件存放路径
    local_patch_file= "/home/serverGeneralScript/patch_test"
    # 解析和更新list的脚本路径
    update_script = "/home/serverGeneralScript/script_helper/patch_list_update.sh"
    patchname=args.patchname

    # 解析服务器列表
    servers = args.server if args.server else []

    # 根据服务器数量决定模式
    if len(servers) == 0:
        raise ValueError("必须至少指定一个服务器")
    elif len(servers) > 2:
        raise ValueError("最多只能指定两个服务器")

    # ip地址解析（第一个服务器）
    server_host = get_remote_ip_from_ini(ini_file_path, servers[0])

    # 第二个服务器的ip解析（如果提供）
    server_host2 = None
    if len(servers) == 2:
        server_host2 = get_remote_ip_from_ini(ini_file_path, servers[1])

    # 对比两个服务器的patch_list
    if args.check and len(servers) == 2:
        print(f"检测到两个服务器 ({servers[0]} 和 {servers[1]})，执行对比模式...")
        # 本地临时文件路径
        local_patch_list1 = "/home/serverGeneralScript/patch_test/patch_order_list_server1.json"
        local_patch_list2 = "/home/serverGeneralScript/patch_test/patch_order_list_server2.json"

        # 从两个服务器下载 patch_order_list 文件
        print(f"从服务器 {servers[0]} ({server_host}) 下载 patch_order_list.json...")
        excute_remote_cmd(
            f"scp root@{server_host}:{remote_patch_list} {local_patch_list1}"
        )

        print(f"从服务器 {servers[1]} ({server_host2}) 下载 patch_order_list.json...")
        excute_remote_cmd(
            f"scp root@{server_host2}:{remote_patch_list} {local_patch_list2}"
        )

        # 执行对比
        compare_patch_lists(local_patch_list1, local_patch_list2, servers[0], servers[1])

    # 打印目标服务器的patch_list（单服务器模式）
    elif args.check:
        # 传输本地python check脚本到远程执行 获取返回结果
        local_check_script = "/home/serverGeneralScript/patch_test/pyscripts/patch_check.py"
        remote_check_script = "/data/app/game-server/patch_check.py"
        excute_remote_cmd(
            f"scp {local_check_script} root@{server_host}:{remote_check_script}"
        )
        # 执行远端python check脚本
        # 根据 --modify-json 参数决定是否传递 --remove-missing
        check_cmd = f"ssh root@{server_host} python3 {remote_check_script}"
        if args.modify_json:
            check_cmd += " --remove-missing"
        excute_remote_cmd(check_cmd)
    # list备份管理
    if args.recover == "bak":
        excute_remote_cmd(
            f"ssh root@{server_host} cp {remote_patch_list} {remote_patch_list}bak"
        )
    elif args.recover == "re":
        excute_remote_cmd(
            f"ssh root@{server_host} cp {remote_patch_list}bak {remote_patch_list}"
        )

    # 删除指定的 patch 并重新排序
    if args.delete:
        print(f"准备删除 patch: {args.delete}")
        # 从远端获取 patch_order_list 文件
        excute_remote_cmd(
            f"scp root@{server_host}:{remote_patch_list} {local_patch_list}"
        )
        # 执行删除和重新排序
        delete_patch_and_reorder(local_patch_list, args.delete)
        # 覆盖远程文件
        excute_remote_cmd(
            f"scp {local_patch_list} root@{server_host}:{remote_patch_list}"
        )

    if patchname:
        # 解析patch类型
        patch_type= patchname[3:patchname.index("_p")]
        # 获取真实patchname
        patch_name= patchname[3:]
        # patch文件传输
        if patchname.split('_')[0]=="uf":
            excute_remote_cmd(
                f"scp {local_patch_file}/{patch_type}/{patch_name}.lua root@{server_host}:{remote_patch_file}/patch/{patch_type}/"
            )

        # list更新管理
        if patchname.split('_')[0]=="ul":
            # 从远端获取patch_order_list文件
            excute_remote_cmd(
                f"scp root@{server_host}:{remote_patch_list} {local_patch_list}"
            )
            # patch_list 内容写入
            excute_remote_cmd(
                f"bash {update_script} {patch_type} {patch_name}"
            )
            # 覆盖远程
            excute_remote_cmd(
                f"scp {local_patch_list} root@{server_host}:{remote_patch_list}"
            )
    if args.is_update:
        # 执行目标服务器patch脚本
        excute_remote_cmd(
            f"ssh root@{server_host} 'cd /data/app/game-server && bash release_bin/run_patch_game.sh'"
        )


if __name__ == "__main__":
    main()