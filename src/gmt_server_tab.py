"""Client for the test GMT selection-tab API."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

GMT_TAB_PATH = "/home/serverGeneralScript/_bot/gmt_server_tab"

PAGE = "/server_version/ServerListHome"
QUERY = "/server_version/query_one_server"
UPDATE = "/server_version/modify_mul_server"


class GmtError(ValueError):
    pass


class GmtResultUnknown(GmtError):
    pass


def parse_preview(log: str, requested: list[str]) -> dict:
    try:
        preview = json.loads(log)
        if {"host_num", "season", "scenario", "new_season", "new_scenario", "signature"} != preview.keys():
            raise ValueError
        if not all(type(preview[key]) is int and 0 <= preview[key] <= 99999999 for key in preview if key != "signature"):
            raise ValueError
        if [preview[key] for key in ("host_num", "new_season", "new_scenario")] != list(map(int, requested)):
            raise ValueError
        if not re.fullmatch(r"[a-f0-9]{64}", preview["signature"]):
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise GmtError("GMT 预检结果与请求不一致。") from exc
    return preview


@dataclass(frozen=True)
class ServerTab:
    host_num: int
    server_id: int
    season: int
    scenario: int
    name: str


class ListPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.csrf = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name") == "csrf-token":
            self.csrf = attributes.get("content") or ""


def gmt_origin(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("请填写 GMT 的 HTTPS 站点地址，不带路径或凭据")
    return value.rstrip("/")


class GmtClient:
    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        self._csrf = ""

    def login(self, username: str, password: str) -> None:
        self.http.get("/login").raise_for_status()
        response = self.http.post(
            "/api/login",
            json={"username": username, "password": password},
            headers={"Referer": str(self.http.base_url.join("/login"))},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("code") != 0:
            raise GmtError("GMT 登录未成功。")

    def load_csrf(self) -> str:
        response = self.http.get(PAGE)
        response.raise_for_status()
        page = ListPage()
        page.feed(response.text)
        if not page.csrf:
            raise GmtError("GMT 选服列表页缺少提交校验字段。")
        self._csrf = page.csrf
        return page.csrf

    def read_tab(self, host_num: int) -> ServerTab:
        response = self.http.get(
            QUERY, params={"HostNum": host_num, "ServerId": ""}, headers={"X-CSRFToken": self._csrf},
        )
        response.raise_for_status()
        try:
            body = response.json()
            if body["code"] != 0:
                raise ValueError
            row = body["data"]["server"]
            if any(not re.fullmatch(r"[0-9]+", str(row[key])) for key in ("HostNum", "ServerId", "season", "scenario")):
                raise ValueError
            name = row["Name"]
            if row.get("ServerType") not in (1, "1", "LOGIC") or not isinstance(name, str) or not name.strip():
                raise ValueError
            tab = ServerTab(*(int(row[key]) for key in ("HostNum", "ServerId", "season", "scenario")), name=name)
            if tab.host_num != host_num or tab.server_id < 1:
                raise ValueError
            return tab
        except (ValueError, KeyError, TypeError) as exc:
            raise GmtError(f"无法读取 {host_num} 服的 GMT 页签记录。") from exc

    def ensure_session(self, username: str, password: str) -> None:
        if self._csrf:
            try:
                self.load_csrf()
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {302, 303} or exc.response.headers.get("location") not in {"/", "/login"}:
                    raise
            self.http.cookies.clear()
            self._csrf = ""
        self.login(username, password)
        self.load_csrf()

    def signature(self, snapshot: ServerTab, season: int, scenario: int) -> str:
        values = [str(self.http.base_url), asdict(snapshot), season, scenario]
        return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()

    def preview(self, snapshot: ServerTab, season: int, scenario: int) -> str:
        return json.dumps({
            "host_num": snapshot.host_num, "season": snapshot.season, "scenario": snapshot.scenario,
            "new_season": season, "new_scenario": scenario, "signature": self.signature(snapshot, season, scenario),
        })

    def update_tab(self, snapshot: ServerTab, season: int, scenario: int) -> ServerTab:
        csrf = self.load_csrf()
        if self.read_tab(snapshot.host_num) != snapshot:
            raise GmtError("预览后的 GMT 记录已变化，请重新预览。")
        if ";" in snapshot.name or "\n" in snapshot.name or "\r" in snapshot.name:
            raise GmtError("服名含有批量请求分隔符，无法生成单服请求。")
        # GMT's batch lookup emits HostNum_ServerId_Name for each selected logic server.
        fields = {
            "HostNumList": str(snapshot.host_num),
            "ServerNameList": f"{snapshot.host_num}_{snapshot.server_id}_{snapshot.name}",
            "season": str(season),
            "scenario": str(scenario),
        }
        try:
            response = self.http.post(UPDATE, data=fields, headers={"X-CSRFToken": csrf})
            response.raise_for_status()
            outcome = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GmtResultUnknown("GMT 未返回修改成功，请核对提交结果。") from exc
        if not isinstance(outcome, dict) or outcome.get("ret") != "SUCCESS":
            raise GmtResultUnknown("GMT 未返回修改成功，请核对提交结果。")
        try:
            tab = self.read_tab(snapshot.host_num)
        except (httpx.HTTPError, GmtError) as exc:
            raise GmtResultUnknown("GMT 返回成功，回读记录失败。") from exc
        if tab != ServerTab(snapshot.host_num, snapshot.server_id, season, scenario, snapshot.name):
            raise GmtResultUnknown("GMT 回读结果与目标赛季和剧本不符，请核对本次提交。")
        return tab
