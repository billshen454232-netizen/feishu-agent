#!/usr/bin/env python3
"""Check an uploaded 开服配置检查 account table and install it for the Worker.

Run by the Jenkins job ``feishu_baye_accounts_upload`` as the ``jenkins`` user with the system
python3 (stdlib only, Python 3.9+), so it cannot import the Worker package. The column rules mirror
``src/baye_check.py``. Output never contains accounts or passwords.

    install_baye_accounts.py <uploaded.csv> <target.csv>
"""

import csv
import io
import os
import re
import sys
import tempfile

HEADERS = {
    "server_id": ("服号", "服务器编号", "server_id"),
    "server_name": ("服务器名", "完整服务器名", "选服列表里的完整服务器名", "server"),
    "account": ("账号", "account"),
    "password": ("密码", "password"),
}
MAX_BYTES = 1024 * 1024


def check(raw):
    """Return the server numbers in the table; raise ValueError with a readable reason."""
    if not raw:
        raise ValueError("文件为空")
    if len(raw) > MAX_BYTES:
        raise ValueError("文件超过 1 MB，确认上传的是账号表 CSV")
    if raw.startswith(b"PK"):
        raise ValueError("这是 xlsx 文件；请在 Excel 里「另存为 CSV」后再上传")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows:
        raise ValueError("没有表头")
    header = [cell.strip().replace(" ", "") for cell in rows[0]]
    index = {}
    for field, names in HEADERS.items():
        for position, name in enumerate(header):
            if name in names:
                index[field] = position
                break
    missing = [HEADERS[field][0] for field in HEADERS if field not in index]
    if missing:
        raise ValueError("缺少列：{}".format("、".join(missing)))
    numbers = []
    for line_number, row in enumerate(rows[1:], start=2):
        def cell(field):
            position = index[field]
            return row[position].strip() if position < len(row) else ""
        if not cell("server_id"):
            continue
        empty = [HEADERS[field][0] for field in ("server_name", "account", "password") if not cell(field)]
        if empty:
            raise ValueError("第 {} 行缺少：{}".format(line_number, "、".join(empty)))
        match = re.match(r"\s*([0-9]{3,8})\s*服", cell("server_name"))
        numbers.append(match.group(1) if match else cell("server_id"))
    if not numbers:
        raise ValueError("没有任何账号行")
    return numbers


def install(raw, target):
    directory = os.path.dirname(os.path.abspath(target))
    fd, tmp = tempfile.mkstemp(prefix=".baye_accounts.", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    source, target = argv[1], argv[2]
    try:
        with open(source, "rb") as handle:
            raw = handle.read()
    except OSError:
        print("没有收到上传文件：请在「Build with Parameters」里选择账号表 CSV")
        return 1
    try:
        numbers = check(raw)
        install(raw, target)
    except ValueError as exc:
        print("账号表未更新：{}".format(exc))
        return 1
    finally:
        os.unlink(source)
    print("账号表已更新：{} 个服 —— {}".format(len(numbers), "、".join(numbers)))
    print("下一次「检查 X 服开服配置」即生效，无需重启 Worker")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
