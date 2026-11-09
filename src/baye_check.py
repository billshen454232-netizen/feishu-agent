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
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

from .utils.time_text import BEIJING, TimeTextError, parse_time_text

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
MAX_OCR_CHARS = 4000


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
        account = BayeAccount(server_id, cell("server_name"), cell("account"), cell("password"), cell("serial"))
        accounts[server_id] = account
        # "检查 1800 服" carries only digits; the table may use ids like S11800, so also index "1800服 鼎业千秋" by 1800.
        number = re.match(r"\s*([0-9]{3,8})\s*服", account.server_name)
        if number:
            accounts.setdefault(number.group(1), account)
    return accounts


def _read_rows(path: Path) -> list[list[Any]]:
    if path.suffix.lower() == ".csv":
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030")  # Excel on Chinese Windows saves CSV as GBK
        return [list(row) for row in csv.reader(io.StringIO(text, newline=""))]
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


def ocr_texts(job: dict[str, Any]) -> dict[str, str]:
    """OCR text per screenshot from the executor result, tolerant of a few shapes.

    Accepted: ``job["ocr"] = {name: "text" | {"text": ...} | {"lines": [...]}}`` or ``screenshot["ocr_text"]``.
    """
    texts: dict[str, str] = {}

    def text_of(value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("text") if value.get("text") is not None else value.get("lines")
        if isinstance(value, list):
            value = "\n".join(str(item.get("text") if isinstance(item, dict) else item) for item in value)
        return str(value or "").strip()

    raw = job.get("ocr")
    if isinstance(raw, dict):
        for name, value in raw.items():
            if text_of(value):
                texts[str(name)[:40]] = text_of(value)[:MAX_OCR_CHARS]
    for shot in job.get("screenshots") or []:
        if isinstance(shot, dict) and text_of(shot.get("ocr_text")) and str(shot.get("name")) not in texts:
            texts[str(shot.get("name"))[:40]] = text_of(shot.get("ocr_text"))[:MAX_OCR_CHARS]
    return texts


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

    async def submit(self, message_id: str, server_id: str, expected_open_time: int = 0) -> str:
        """Submit and start watching; returns the check id. Raises BayeCheckError when nothing was started.

        ``expected_open_time`` (Unix seconds, 0 = not given) comes from the user's message and is only
        carried along to the result so the Gateway can judge the screenshots' OCR text against it.
        """
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
                 "server_name": account.server_name, "serial": serial, "submitted_at": int(self._clock()),
                 "expected_open_time": int(expected_open_time or 0)}
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
            "expected_open_time": int(entry.get("expected_open_time") or 0), "ocr": ocr_texts(job),
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
FIRST_STAGE_SECONDS = 4 * 3600  # 第一个霸业阶段持续 4 小时
TOLERANCE_SECONDS = 5 * 60
REVIEW_NOTE = "未在消息里给出开服时间，未做自动判定，请人工核对：① 选服列表里的开服时间；② 霸业第一阶段结束时间应为 开服时间 + 4 小时。"
VERDICT_OK, VERDICT_WRONG, VERDICT_UNKNOWN = "正确", "不正确", "无法判定"


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
    raw_ocr = payload.get("ocr") or {}
    if not isinstance(raw_ocr, dict) or len(raw_ocr) > MAX_FOLLOWUP_IMAGES:
        raise ValueError("invalid ocr")
    ocr = {str(name)[:40]: str(text or "")[:MAX_OCR_CHARS] for name, text in raw_ocr.items() if str(text or "").strip()}
    try:
        expected_open_time = int(payload.get("expected_open_time") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid expected_open_time") from exc
    if expected_open_time < 0:
        raise ValueError("invalid expected_open_time")
    fields = {key: str(payload.get(key) or "")[:300] for key in
              ("server_id", "server_name", "status", "step", "error", "note", "created_at", "finished_at")}
    return {**fields, "check_id": check_id, "images": images, "ocr": ocr, "expected_open_time": expected_open_time}


def should_judge(followup: dict[str, Any]) -> bool:
    """AI reads the OCR text only when the user gave an open time and the executor returned text."""
    return followup["status"] == "pass" and followup["expected_open_time"] > 0 and bool(followup["ocr"])


def _beijing(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, BEIJING).strftime("%Y-%m-%d %H:%M")


def build_judge_prompt(followup: dict[str, Any]) -> str:
    """Ask the AI to *read* values from OCR text; the verdict is computed by :func:`decide`."""
    ocr_blocks = "\n\n".join("【{}】\n{}".format(name, text) for name, text in followup["ocr"].items())
    return (
        "你在核对游戏新服的开服配置。下面是真机截图的 OCR 识别文字（可能有错字、断行、顺序错乱）。\n"
        "只做读取，不要判断对错。\n\n"
        "目标服务器：{server}\n截图时间（北京时间，检查结束时间，可作为“现在”）：{shot}\n\n"
        "{ocr}\n\n"
        "请读出：\n"
        "1. open_time_seen：选服列表里目标服务器的开服时间（通常在“本服开启时间”之后）。\n"
        "2. baye_end_time：霸业页面第一个阶段的结束时间。若显示“已于 X 完成”，就是 X；"
        "若显示剩余时间 / 倒计时 R，则为 截图时间 + R（这一步可以由你计算）。\n"
        "3. baye_text：霸业页面中你依据的原文片段。\n"
        "时间一律写成 YYYY-MM-DDTHH:MM（缺年份用截图时间的年份）；读不出来就写空字符串，不要猜。\n"
        "只输出一个 JSON 对象："
        '{{"open_time_seen": "...", "baye_end_time": "...", "baye_text": "...", "explanation": "一两句说明依据"}}'
    ).format(server=followup["server_name"] or followup["server_id"],
             shot=followup["finished_at"] or "未知", ocr=ocr_blocks)


def parse_judgement(text: str) -> dict[str, str]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("AI 没有返回 JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("AI 返回的不是 JSON 对象")
    return {key: str(data.get(key) or "").strip()[:300] for key in ("open_time_seen", "baye_end_time", "baye_text", "explanation")}


def decide(followup: dict[str, Any], judgement: dict[str, str]) -> tuple[str, list[str]]:
    """Deterministic verdict from the values the AI read; returns (verdict, reasons)."""
    expected_open = followup["expected_open_time"]
    expected_end = expected_open + FIRST_STAGE_SECONDS
    reasons: list[str] = []
    verdicts: list[str] = []
    for label, key, expected in (("开服时间", "open_time_seen", expected_open), ("霸业第一阶段结束", "baye_end_time", expected_end)):
        seen_text = judgement.get(key, "")
        try:
            seen = parse_time_text(seen_text).timestamp if seen_text else None
        except TimeTextError:
            seen = None
        if seen is None:
            verdicts.append(VERDICT_UNKNOWN)
            reasons.append("{}：未能从截图文字读出（期望 {}）".format(label, _beijing(expected)))
        elif abs(seen - expected) <= TOLERANCE_SECONDS:
            verdicts.append(VERDICT_OK)
            reasons.append("{}：{} ✓".format(label, _beijing(seen)))
        else:
            verdicts.append(VERDICT_WRONG)
            reasons.append("{}：截图为 {}，期望 {} ✗".format(label, _beijing(seen), _beijing(expected)))
    if VERDICT_WRONG in verdicts:
        return VERDICT_WRONG, reasons
    if VERDICT_UNKNOWN in verdicts:
        return VERDICT_UNKNOWN, reasons
    return VERDICT_OK, reasons


_VERDICT_TEMPLATES = {VERDICT_OK: "green", VERDICT_WRONG: "red", VERDICT_UNKNOWN: "yellow"}


def build_followup_card(followup: dict[str, Any], image_keys: list[tuple[str, str]], *,
                        judgement: Optional[dict[str, str]] = None, judge_error: str = "") -> dict[str, object]:
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
    expected_open = followup["expected_open_time"]
    if expected_open:
        lines.append("期望开服时间：{}；期望霸业第一阶段结束：{}".format(
            _beijing(expected_open), _beijing(expected_open + FIRST_STAGE_SECONDS)))
    elements: list[dict[str, object]] = [{"tag": "div", "text": {"tag": "plain_text", "content": "\n".join(lines)}}]
    if judgement is not None:
        verdict, reasons = decide(followup, judgement)
        title, template = "开服配置检查：{}".format(verdict), _VERDICT_TEMPLATES[verdict]
        detail = reasons + ([("AI 说明：" + judgement["explanation"])] if judgement.get("explanation") else [])
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": "\n".join(detail)}})
    for label, key in image_keys:
        elements.append({"tag": "img", "img_key": key, "alt": {"tag": "plain_text", "content": label or "截图"}})
    if followup["images"] and not image_keys:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": "截图上传飞书失败，请在执行机控制台查看该任务。"}})
    elif not followup["images"]:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": "没有取得截图。"}})
    if followup["status"] == "pass" and judgement is None:
        if judge_error:
            note = "自动判定未完成（{}），请人工核对截图。".format(judge_error)
        elif expected_open and not followup["ocr"]:
            note = "执行机没有返回截图识别文字，未做自动判定，请人工核对截图。"
        else:
            note = REVIEW_NOTE
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
    elif judgement is not None:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content":
                         "AI 只负责从截图文字中读取时间，对错由程序比较（误差 ±5 分钟）；如有疑问以截图为准。"}]})
    return {"config": {"wide_screen_mode": True}, "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
            "elements": elements}
