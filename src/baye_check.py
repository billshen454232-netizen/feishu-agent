"""新服开服配置检查 on the intranet Worker (design: docs/superpowers/specs/2026-11-06-baye-open-time-check.md).

The UI-automation API (``POST /api/baye-checks``) logs a test account into the target server on
a real phone and returns two screenshots. A check takes minutes, so the Worker only submits it,
reports "submitted" for the claimed job right away, and keeps polling in the background; the
finished screenshots are then posted to the Gateway as a follow-up to the same Feishu thread.

Account passwords live only in the Worker-local account table and in the submit request body;
they never reach the Gateway, the AI, Feishu cards or logs.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

TERMINAL = {"pass", "fail", "stopped", "interrupted"}
BACKEND_KIND = "baye_check"
_HEADERS = {
    "server_id": ("服号", "服务器编号", "server_id"),
    "server_name": ("服务器名", "完整服务器名", "选服列表里的完整服务器名", "server"),
    "account": ("账号", "account"),
    "password": ("密码", "password"),
    "serial": ("设备", "设备serial", "设备 serial", "serial"),
}
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


class BayeCheckError(RuntimeError):
    """The check could not be submitted (nothing ran on the phone)."""


@dataclass(frozen=True)
class BayeAccount:
    server_id: str
    server_name: str
    account: str
    password: str
    serial: str = ""

    def __repr__(self) -> str:  # never leak credentials through logs / tracebacks
        return "BayeAccount(server_id={!r}, server_name={!r}, serial={!r})".format(self.server_id, self.server_name, self.serial)


def load_accounts(path: str | Path) -> dict[str, BayeAccount]:
    """Read the account table (.xlsx or .csv). The first row is the header; column names are matched loosely."""
    path = Path(path)
    if not path.is_file():
        raise BayeCheckError("找不到开服检查账号表 {}".format(path.name))
    rows = _read_rows(path)
    if not rows:
        return {}
    header = [str(cell or "").strip().replace(" ", "") for cell in rows[0]]
    index: dict[str, int] = {}
    for field, names in _HEADERS.items():
        for position, name in enumerate(header):
            if name in {item.replace(" ", "") for item in names}:
                index[field] = position
                break
    missing = [field for field in ("server_id", "server_name", "account", "password") if field not in index]
    if missing:
        raise BayeCheckError("开服检查账号表缺少列：{}".format("、".join(_HEADERS[field][0] for field in missing)))
    accounts: dict[str, BayeAccount] = {}
    for row in rows[1:]:
        def cell(field: str) -> str:
            position = index.get(field)
            if position is None or position >= len(row) or row[position] is None:
                return ""
            value = row[position]
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            return str(value).strip() if field != "password" else str(value)
        server_id = cell("server_id")
        if not server_id:
            continue
        accounts[server_id] = BayeAccount(server_id, cell("server_name"), cell("account"), cell("password"), cell("serial"))
    return accounts


def _read_rows(path: Path) -> list[list[Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [list(row) for row in csv.reader(handle)]
    import openpyxl  # Worker-only dependency

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return [list(row) for row in workbook.worksheets[0].iter_rows(values_only=True)]
    finally:
        workbook.close()


class BayeCheckClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0, http_client: Optional[httpx.AsyncClient] = None) -> None:
        self._base = base_url.rstrip("/")
        self._http = http_client or httpx.AsyncClient(timeout=timeout)

    async def submit(self, account: BayeAccount, serial: str) -> dict[str, Any]:
        body = {"serial": serial, "account": account.account, "password": account.password, "server": account.server_name}
        try:
            response = await self._http.post(self._base + "/api/baye-checks", json=body)
        except httpx.HTTPError as exc:
            raise BayeCheckSubmitUnknown("提交检查请求无响应（{}）".format(type(exc).__name__)) from exc
        finally:
            body.clear()
        if response.status_code == 409:
            raise BayeCheckError("检查手机正在执行其他任务（409），请稍后再试：{}".format(_detail(response)))
        if response.status_code == 400:
            raise BayeCheckError("检查参数或设备状态不对（400）：{}".format(_detail(response)))
        if response.status_code >= 500:
            raise BayeCheckSubmitUnknown("执行机返回 HTTP {}".format(response.status_code))
        response.raise_for_status()
        return response.json()

    async def get(self, check_id: str) -> dict[str, Any]:
        response = await self._http.get(self._base + "/api/baye-checks/" + check_id)
        response.raise_for_status()
        return response.json()

    async def image(self, url: str) -> bytes:
        response = await self._http.get(self._base + url)
        response.raise_for_status()
        if len(response.content) > _MAX_IMAGE_BYTES:
            raise BayeCheckError("截图过大")
        return response.content


class BayeCheckSubmitUnknown(BayeCheckError):
    """The submit request may have reached the executor; the API has no idempotency key, so never resend."""


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail") or "")[:200]
    except ValueError:
        return response.text[:200]


FollowupSender = Callable[[str, dict[str, Any]], Awaitable[bool]]


class BayeCheckCoordinator:
    """Submits checks for claimed jobs and watches them in the background (state survives restarts)."""

    def __init__(self, settings: Any, send_followup: FollowupSender, *, client: Optional[BayeCheckClient] = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, clock: Callable[[], float] = time.time) -> None:
        self._settings = settings
        self._send = send_followup
        self._client = client or BayeCheckClient(settings.base_url, timeout=settings.request_timeout_seconds)
        self._sleep = sleep
        self._clock = clock
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def submit(self, message_id: str, server_id: str) -> str:
        """Submit and start watching; returns the check id. Raises BayeCheckError when nothing was started."""
        accounts = load_accounts(self._settings.accounts_path)
        account = accounts.get(server_id)
        if account is None:
            raise BayeCheckError("开服检查账号表里没有 {} 服，请先在账号表登记".format(server_id))
        if not account.server_name or not account.account or not account.password:
            raise BayeCheckError("开服检查账号表中 {} 服的服务器名、账号或密码为空".format(server_id))
        serial = account.serial or self._settings.default_serial
        if not serial:
            raise BayeCheckError("没有可用的检查手机：账号表未指定设备，Worker 配置也没有 default_serial")
        job = await self._client.submit(account, serial)
        check_id = str(job.get("id") or "")
        if not check_id:
            raise BayeCheckSubmitUnknown("执行机没有返回任务 ID")
        entry = {"message_id": message_id, "check_id": check_id, "server_id": server_id,
                 "server_name": account.server_name, "serial": serial, "submitted_at": int(self._clock())}
        self._save(self._load() + [entry])
        self._start(entry)
        logger.warning("baye check submitted message_id=%s check_id=%s server=%s", message_id, check_id, server_id)
        return check_id

    def resume(self) -> int:
        """Restart watchers for checks submitted before a Worker restart."""
        pending = self._load()
        for entry in pending:
            self._start(entry)
        return len(pending)

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def _start(self, entry: dict[str, Any]) -> None:
        if entry["check_id"] not in self._tasks:
            self._tasks[entry["check_id"]] = asyncio.create_task(self._watch(entry))

    async def _watch(self, entry: dict[str, Any]) -> None:
        check_id = entry["check_id"]
        deadline = entry["submitted_at"] + self._settings.timeout_seconds + 300
        job: dict[str, Any] = {}
        while True:
            try:
                job = await self._client.get(check_id)
                if job.get("status") in TERMINAL:
                    break
            except Exception:  # noqa: BLE001 - keep polling the same id; the check itself is unaffected
                logger.warning("baye check poll failed check_id=%s", check_id, exc_info=True)
            if self._clock() >= deadline:
                job = {**job, "status": job.get("status") or "unknown", "error": "超过等待时限仍未结束，请在执行机控制台查看该任务"}
                break
            await self._sleep(self._settings.poll_interval_seconds)
        images = []
        for shot in job.get("screenshots") or []:
            try:
                data = await self._client.image(str(shot.get("url") or ""))
                images.append({"name": shot.get("name"), "label": shot.get("label"), "png_base64": base64.b64encode(data).decode("ascii")})
            except Exception:  # noqa: BLE001 - report what we have
                logger.warning("baye check image download failed check_id=%s name=%s", check_id, shot.get("name"), exc_info=True)
        payload = {
            "kind": BACKEND_KIND, "check_id": check_id, "server_id": entry["server_id"], "server_name": entry["server_name"],
            "status": str(job.get("status") or "unknown"), "step": str(job.get("step") or ""), "error": str(job.get("error") or ""),
            "note": str(job.get("note") or ""), "created_at": str(job.get("created_at") or ""),
            "finished_at": str(job.get("finished_at") or ""), "images": images,
        }
        for attempt in range(1, 61):  # Gateway restarts / network blips: retry for ~30 minutes
            try:
                if await self._send(entry["message_id"], payload):
                    break
            except Exception:  # noqa: BLE001
                logger.warning("baye check follow-up delivery failed check_id=%s attempt=%s", check_id, attempt, exc_info=True)
            await self._sleep(30)
        self._save([item for item in self._load() if item["check_id"] != check_id])
        self._tasks.pop(check_id, None)
        logger.warning("baye check finished check_id=%s status=%s images=%s", check_id, payload["status"], len(images))

    def _load(self) -> list[dict[str, Any]]:
        path = Path(self._settings.state_path)
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            logger.error("baye check state file unreadable: %s", path)
            return []
        return data if isinstance(data, list) else []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        path = Path(self._settings.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


_STATUS_TITLES = {
    "pass": ("开服配置检查完成：截图已取得", "green"),
    "fail": ("开服配置检查失败", "red"),
    "stopped": ("开服配置检查已被停止", "grey"),
    "interrupted": ("开服配置检查被中断", "orange"),
}
MAX_FOLLOWUP_IMAGES = 2
REVIEW_NOTE = "自动判定尚未接入，请人工核对：① 选服列表里的开服时间；② 霸业剩余时间应约为 开服时间 + 4 小时 − 截图时间。"


def parse_followup(payload: Any) -> dict[str, Any]:
    """Validate a Worker follow-up; returns the payload with decoded PNG bytes under ``images[i]["data"]``."""
    if not isinstance(payload, dict) or payload.get("kind") != BACKEND_KIND:
        raise ValueError("unsupported follow-up kind")
    check_id = str(payload.get("check_id") or "")
    if not check_id or len(check_id) > 64:
        raise ValueError("invalid check_id")
    raw_images = payload.get("images") or []
    if not isinstance(raw_images, list) or len(raw_images) > MAX_FOLLOWUP_IMAGES:
        raise ValueError("invalid images")
    images = []
    for item in raw_images:
        if not isinstance(item, dict):
            raise ValueError("invalid image")
        try:
            data = base64.b64decode(str(item.get("png_base64") or ""), validate=True)
        except ValueError as exc:
            raise ValueError("invalid image encoding") from exc
        if not data or len(data) > _MAX_IMAGE_BYTES:
            raise ValueError("invalid image size")
        images.append({"name": str(item.get("name") or "")[:40], "label": str(item.get("label") or "")[:60], "data": data})
    fields = {key: str(payload.get(key) or "")[:300] for key in
              ("server_id", "server_name", "status", "step", "error", "note", "created_at", "finished_at")}
    return {**fields, "check_id": check_id, "images": images}


def build_followup_card(followup: dict[str, Any], image_keys: list[tuple[str, str]]) -> dict[str, object]:
    """Result card for a finished check; ``image_keys`` is [(label, img_key)]. Text is plain_text (no markdown injection)."""
    title, template = _STATUS_TITLES.get(followup["status"], ("开服配置检查结果未知", "yellow"))
    lines = ["服务器：{}".format(followup["server_name"] or followup["server_id"]), "任务 ID：{}".format(followup["check_id"])]
    if followup["step"]:
        lines.append("步骤：{}".format(followup["step"]))
    if followup["error"]:
        lines.append("错误：{}".format(followup["error"]))
    if followup["note"]:
        lines.append("备注：{}".format(followup["note"]))
    if followup["created_at"] or followup["finished_at"]:
        lines.append("开始 / 结束：{} / {}".format(followup["created_at"] or "-", followup["finished_at"] or "-"))
    elements: list[dict[str, object]] = [{"tag": "div", "text": {"tag": "plain_text", "content": "\n".join(lines)}}]
    for label, key in image_keys:
        elements.append({"tag": "img", "img_key": key, "alt": {"tag": "plain_text", "content": label or "截图"}})
    if followup["images"] and not image_keys:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": "截图上传飞书失败，请在执行机控制台查看该任务。"}})
    elif not followup["images"]:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": "没有取得截图。"}})
    if followup["status"] == "pass":
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": REVIEW_NOTE}]})
    return {"config": {"wide_screen_mode": True}, "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
            "elements": elements}
