"""Preview or update one test server's GMT selection-tab metadata."""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gmt_server_tab import GmtClient, GmtError, PAGE, gmt_origin


def https_origin(value: str) -> str:
    try:
        return gmt_origin(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_number(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,8}", value):
        raise argparse.ArgumentTypeError("请填写数字")
    return int(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", type=https_origin, required=True)
    parser.add_argument("--host-num", type=parse_number, required=True)
    parser.add_argument("--season", type=parse_number, required=True)
    parser.add_argument("--scenario", type=parse_number, required=True)
    parser.add_argument("--apply", action="store_true", help="预览后输入服号确认写入")
    args = parser.parse_args(argv)
    if args.host_num < 1:
        parser.error("服号必须大于零")

    username = input(f"GMT 用户名 ({args.origin})：").strip()
    password = getpass.getpass("GMT 密码：")
    phase = "登录或读取"
    try:
        with httpx.Client(base_url=args.origin, follow_redirects=False, timeout=15, headers={
            "X-Requested-With": "XMLHttpRequest", "Origin": args.origin, "Referer": args.origin + PAGE,
        }) as http:
            gmt = GmtClient(http)
            gmt.login(username, password)
            gmt.load_csrf()
            snapshot = gmt.read_tab(args.host_num)
            print(f"{args.host_num} 服 · 赛季 {snapshot.season} → {args.season}，剧本 {snapshot.scenario} → {args.scenario}")
            if (snapshot.season, snapshot.scenario) == (args.season, args.scenario):
                print("当前值已与目标一致。")
            elif args.apply and input(f"输入服号 {args.host_num} 确认：").strip() == str(args.host_num):
                phase = "提交或回读"
                gmt.update_tab(snapshot, args.season, args.scenario)
                print("GMT 已回读并确认修改成功。")
            return 0
    except GmtError as exc:
        print(exc)
    except httpx.HTTPStatusError as exc:
        print(f"GMT {phase}返回 HTTP {exc.response.status_code}。")
    except (httpx.RequestError, ValueError):
        print(f"GMT {phase}未取得有效结果，请核查当前记录。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
