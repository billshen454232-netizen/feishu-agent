#!/usr/bin/env python3
"""Team knowledge base service: server-side search and web upload (design: docs/superpowers/specs/2026-11-10-team-knowledge-base.md).

    server.py --root /opt/team-kb [--port 8091]

Listens on 127.0.0.1 only; nginx serves the static site and forwards /api/ here. Intranet, no login (owner's
decision 2026-11-10); every upload is recorded in the knowledge base's local git with the uploader as author.
Standard library only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import queue
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site  # noqa: E402
import cards  # noqa: E402
import normalize  # noqa: E402
import qa  # noqa: E402

logger = logging.getLogger("team-kb")

GROUPS = ("系统组", "赛季组", "国服", "其他")
NAME = re.compile(r"^[0-9A-Za-z_·一-龥]{1,20}$")
FILE_NAME = re.compile(r'^[^/\\:*?"<>|]{1,80}\.md$')
MAX_FILE_BYTES = 3 * 1024 * 1024
MAX_FILES = 20
MAX_BODY_BYTES = 45 * 1024 * 1024
MAX_QUERY = 50
REVIEW_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}")
SYNC_INTERVAL_SECONDS = 15


class SearchIndex:
    """In-memory rows of every case table plus one entry per feature; rebuilt after each upload."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.features: List[Dict] = []
        self.rows: List[Dict] = []
        self.qa = qa.QAIndex([], [])

    def load(self) -> None:
        features, rows, qa_features = [], [], []
        cases, cards = self._root / "cases", self._root / "cards"
        for path in sorted(cases.rglob("*.md")):
            rel = path.relative_to(cases).as_posix()
            meta, body = build_site.parse_frontmatter(path.read_text(encoding="utf-8"))
            card_path = cards / rel
            card = build_site.parse_card(card_path.read_text(encoding="utf-8")) if card_path.is_file() else {}
            title = meta.get("title") or path.stem
            one = card.get("一句话", "")
            one = "" if build_site.missing(one) else one
            keywords = card.get("关键词", "")
            keywords = "" if build_site.missing(keywords) else keywords
            features.append({
                "path": rel, "title": title, "owner": meta.get("owner", ""), "group": rel.split("/")[0],
                "cases": int(meta.get("case_count") or 0), "summary": one,
                "_text": " ".join([title, keywords, one, " ".join(meta.get("modules") or []), meta.get("owner", "")]).lower(),
                "_title": title.lower(),
            })
            qa_features.append(dict(features[-1], card=card, modules=meta.get("modules") or []))
            rows.extend(self._rows(rel, title, meta.get("owner", ""), body))
        self.features, self.rows, self.qa = features, rows, qa.QAIndex(qa_features, rows)
        logger.warning("search index loaded: %s features, %s rows", len(features), len(rows))

    @staticmethod
    def _rows(rel: str, title: str, owner: str, body: str) -> List[Dict]:
        rows = []
        for record in normalize.case_records(body):
            item = record.get("测试项") or record.get("执行步骤") or ""
            rows.append({
                "path": rel, "title": title, "owner": owner,
                "module": " / ".join(v for v in (record.get("模块", ""), record.get("二级模块", "")) if v),
                "level": record.get("分级", ""), "item": item[:160], "expected": record.get("预期结果", "")[:160],
                "find": item[:40],
                "_text": (title + " " + " ".join(record.values())).lower(),  # feature name counts too
            })
        return rows

    def search(self, query: str, limit: int = 40) -> Dict:
        query = query.strip().lower()[:MAX_QUERY]
        terms = [t for t in query.split() if t]
        if not terms:
            return {"query": query, "features": [], "rows": []}
        grams = []
        for term in terms:
            grams.extend([term] if len(term) <= 2 else [term[i:i + 2] for i in range(len(term) - 1)])

        def score(text: str, title: str = "") -> float:
            if all(t in text for t in terms):
                return 100.0 + (50 if query in text else 0) + (30 if any(t in title for t in terms) else 0)
            hit = sum(1 for g in grams if g in text)
            coverage = hit / len(grams)
            return coverage * 60 if coverage >= 0.6 else 0.0

        features = []
        for feature in self.features:
            value = score(feature["_text"], feature["_title"])
            if value:
                features.append((value, feature))
        rows = []
        for row in self.rows:
            value = score(row["_text"], row["title"].lower())
            if value:
                rows.append((value + {"P0": 3, "P1": 2, "P2": 1}.get(row["level"], 0), row))
        features.sort(key=lambda item: -item[0])
        rows.sort(key=lambda item: -item[0])
        public = lambda item: {k: v for k, v in item.items() if not k.startswith("_")}  # noqa: E731
        return {
            "query": query, "terms": terms,
            "features": [public(f) for _, f in features[:10]],
            "rows": [public(r) for _, r in rows[:limit]],
            "row_total": len(rows),
        }


class KnowledgeBase:
    def __init__(self, root: Path, public_url: str = "") -> None:
        self.root = root
        self.public_url = public_url.rstrip("/")
        self.index = SearchIndex(root)
        self.lock = threading.Lock()
        self.cards: Optional["CardWorker"] = None  # set when an AI is configured
        self.ai: Optional["cards.CardAI"] = None  # answers Feishu questions; None = list the best features only

    def upload(self, payload: Dict) -> Dict:
        uploader = str(payload.get("uploader") or "").strip()
        group = str(payload.get("group") or "").strip()
        owner = str(payload.get("owner") or "").strip() or uploader
        files = payload.get("files") or []
        problems = []
        if not NAME.match(uploader):
            problems.append("上传人必填：中文、字母、数字或下划线，20 字以内")
        if group not in GROUPS:
            problems.append("组别必须是：" + "、".join(GROUPS))
        if not NAME.match(owner):
            problems.append("测试（作者）格式不对：中文、字母、数字或下划线，20 字以内")
        if not isinstance(files, list) or not files:
            problems.append("没有选择文件")
        elif len(files) > MAX_FILES:
            problems.append("一次最多上传 {} 个文件".format(MAX_FILES))
        if problems:
            return {"ok": False, "errors": problems, "files": []}

        with self.lock:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            inbox = self.root / "inbox" / "{}-{}".format(stamp, uploader)
            inbox.mkdir(parents=True, exist_ok=True)
            results, written = [], []
            for item in files:
                results.append(self._one_file(item, uploader, group, owner, stamp, inbox, written))
            if written:
                build_site.build(self.root)
                self._git_commit(uploader, written)
                self.index.load()
        return {"ok": True, "errors": [], "files": results}

    def _one_file(self, item: Dict, uploader: str, group: str, owner: str, stamp: str, inbox: Path, written: List[str]) -> Dict:
        name = str((item or {}).get("name") or "")
        result = {"name": name, "status": "rejected", "reasons": [], "warnings": []}
        if not FILE_NAME.match(name) or Path(name).name != name:
            result["reasons"].append("文件名不合法（只接受 .md，不能包含 / \\ : * ? \" < > |，80 字以内）")
            return result
        try:
            raw = base64.b64decode(str(item.get("content") or ""), validate=True)
        except ValueError:
            result["reasons"].append("文件内容读取失败")
            return result
        if not raw or len(raw) > MAX_FILE_BYTES:
            result["reasons"].append("文件为空或超过 {} MB".format(MAX_FILE_BYTES // 1024 // 1024))
            return result
        (inbox / name).write_bytes(raw)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gb18030")
            except UnicodeDecodeError:
                result["reasons"].append("无法识别文件编码（请另存为 UTF-8）")
                return result
        rel = Path(group, owner, name) if group in ("系统组", "赛季组") or owner != uploader else Path(group, name)
        stored, summary = normalize.normalize(
            text, title_fallback=Path(name).stem, group=group, owner=owner,
            extra_meta={"uploaded_by": uploader, "uploaded_at": stamp},
        )
        target = self.root / "cases" / rel
        existed = target.is_file()
        if existed and _same_content(target.read_text(encoding="utf-8"), stored):
            result.update(status="unchanged", path=rel.as_posix(), cases=summary["cases"], title=summary["title"])
            return result
        hits = normalize.masked_hits(text)
        if hits:  # not a blanket rejection: the admin decides on the ops page (design §4.1)
            review_id = self._hold(raw, name, rel, stored, summary, hits, uploader, group, owner)
            where = "；".join("{} 第 {} 行".format(h["kind"], h["line"]) for h in hits[:5]) + ("…" if len(hits) > 5 else "")
            result.update(status="held", path=rel.as_posix(), cases=summary["cases"], title=summary["title"], review_id=review_id,
                          warnings=["命中 IP / 密码等规则（{}），已提交管理员审核，通过后自动入库".format(where)])
            return result
        result.update(path=rel.as_posix(), cases=summary["cases"], unrated=summary["unrated"], title=summary["title"],
                      **self._write_case(rel, stored, summary))
        written.append(rel.as_posix())
        return result

    def _write_case(self, rel: Path, stored: str, summary: Dict) -> Dict:
        target = self.root / "cases" / rel
        existed = target.is_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        build_site.write(target, stored)
        warnings = list(summary.get("warnings") or [])
        if self.cards is not None and cards.card_input(stored) is not None:
            self.cards.enqueue(rel.as_posix())
            warnings.append("摘要卡片由 AI 在后台生成，约 1 分钟后刷新页面可见")
        elif (self.root / "cards" / rel).is_file():
            if existed:
                warnings.append("摘要卡片仍是旧版本，用例已更新")
        else:
            warnings.append("暂无摘要卡片")
        return {"status": "updated" if existed else "added", "warnings": warnings}

    # ---- admin review of held files (design §4.1) -------------------------------------------------
    def _hold(self, raw: bytes, name: str, rel: Path, stored: str, summary: Dict, hits: List[Dict],
              uploader: str, group: str, owner: str) -> str:
        review_id = "{}-{}".format(time.strftime("%Y%m%d-%H%M%S"), hashlib.sha1(raw).hexdigest()[:8])
        box = self.root / "pending" / review_id
        box.mkdir(parents=True, exist_ok=True)
        (box / "raw.md").write_bytes(raw)
        build_site.write(box / "stored.md", stored)
        meta = {
            "id": review_id, "uploader": uploader, "group": group, "owner": owner, "name": name, "path": rel.as_posix(),
            "title": summary["title"], "cases": summary["cases"], "hits": hits, "uploaded_at": int(time.time()),
            "view_url": "{}/api/pending/{}".format(self.public_url, review_id) if self.public_url else "",
            "summary": summary, "reported": False,
        }
        build_site.write(box / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
        logger.warning("upload held for review id=%s uploader=%s path=%s hits=%s", review_id, uploader, rel, len(hits))
        return review_id

    def unreported(self) -> List[Dict]:
        items = []
        for meta_path in sorted((self.root / "pending").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not meta.get("reported"):
                items.append(meta)
        return items

    def mark_reported(self, review_ids: List[str]) -> None:
        with self.lock:
            for review_id in review_ids:
                meta_path = self.root / "pending" / review_id / "meta.json"
                if meta_path.is_file():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    meta["reported"] = True
                    build_site.write(meta_path, json.dumps(meta, ensure_ascii=False, indent=1))

    def apply_decision(self, decision: Dict) -> Tuple[bool, str]:
        review_id = str(decision.get("id") or "")
        if not REVIEW_ID.fullmatch(review_id):
            return False, "无效的审核编号"
        pending = self.root / "pending"
        with self.lock:
            if any((pending / done / review_id).is_dir() for done in ("_done", "_rejected")):
                return True, "此前已处理"
            box = pending / review_id
            if not box.is_dir():
                return False, "内网找不到这个待审核文件"
            meta = json.loads((box / "meta.json").read_text(encoding="utf-8"))
            if decision.get("action") == "approve":
                rel = Path(meta["path"])
                outcome = self._write_case(rel, (box / "stored.md").read_text(encoding="utf-8"), meta.get("summary") or {})
                build_site.build(self.root)
                self._git_commit(meta["uploader"], [meta["path"]], approved_by=str(decision.get("operator") or "管理员"))
                self.index.load()
                destination, message = "_done", "已入库（{}）：{}".format("更新" if outcome["status"] == "updated" else "新增", meta["path"])
            else:
                destination, message = "_rejected", "已拒绝，未入库"
            (pending / destination).mkdir(exist_ok=True)
            box.rename(pending / destination / review_id)
        logger.warning("review applied id=%s action=%s", review_id, decision.get("action"))
        return True, message

    def pending_text(self, review_id: str) -> Optional[str]:
        """Full text of a held file, for the admin's intranet-only link."""
        if not REVIEW_ID.fullmatch(review_id):
            return None
        for folder in ("", "_done", "_rejected"):
            raw_path = self.root / "pending" / folder / review_id / "raw.md"
            if raw_path.is_file():
                raw = raw_path.read_bytes()
                try:
                    return raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    return raw.decode("gb18030", errors="replace")
        return None

    def _git_commit(self, uploader: str, written: List[str], approved_by: str = "") -> None:
        author = "{} <{}@team-kb>".format(uploader, "upload")
        message = "upload by {}{}: {}".format(uploader, " (approved by {})".format(approved_by) if approved_by else "",
                                             ", ".join(written[:5]) + (" …" if len(written) > 5 else ""))
        try:
            subprocess.run(["git", "add", "-A", "cases", "index.md"], cwd=self.root, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=team-kb", "-c", "user.email=team-kb@localhost", "commit", "-q",
                            "--author", author, "-m", message], cwd=self.root, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            logger.error("git commit failed: %s", getattr(exc, "stderr", exc))


def _same_content(old: str, new: str) -> bool:
    strip = lambda text: re.sub(r"^uploaded_(by|at): .*\n", "", text, flags=re.M)  # noqa: E731
    return strip(old) == strip(new)


class Handler(BaseHTTPRequestHandler):
    kb: Optional[KnowledgeBase] = None

    def log_message(self, fmt: str, *args) -> None:  # one line per request in journald
        logger.info("%s %s", self.address_string(), fmt % args)

    def _json(self, status: int, payload: Dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/api/health":
            return self._json(200, {"ok": True, "features": len(self.kb.index.features), "rows": len(self.kb.index.rows)})
        if url.path.startswith("/api/pending/"):
            text = self.kb.pending_text(url.path[len("/api/pending/"):])
            if text is None:
                return self._json(404, {"error": "not found"})
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return None
        if url.path == "/api/search":
            query = (parse_qs(url.query).get("q") or [""])[0]
            started = time.perf_counter()
            result = self.kb.index.search(query)
            result["ms"] = round((time.perf_counter() - started) * 1000, 1)
            return self._json(200, result)
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/upload":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            return self._json(413, {"ok": False, "errors": ["上传内容为空或过大"]})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"ok": False, "errors": ["请求格式不对"]})
        try:
            result = self.kb.upload(payload if isinstance(payload, dict) else {})
        except Exception:  # noqa: BLE001
            logger.exception("upload failed")
            return self._json(500, {"ok": False, "errors": ["服务器处理失败，请联系管理员"]})
        logger.warning("upload uploader=%s files=%s", payload.get("uploader"),
                       [(f["name"], f["status"]) for f in result.get("files", [])])
        return self._json(200, result)


class GatewaySync(threading.Thread):
    """Report held files to the Gateway admin page and apply the admin's decisions (Worker pulls; Gateway never calls in)."""

    def __init__(self, kb: KnowledgeBase, base_url: str, token: str, interval: float = SYNC_INTERVAL_SECONDS) -> None:
        super().__init__(name="team-kb-gateway-sync", daemon=True)
        self.kb, self.base_url, self.token, self.interval = kb, base_url.rstrip("/"), token, interval
        self._last_error = ""

    def run(self) -> None:
        while True:
            try:
                self.sync_once()
                if self._last_error:
                    logger.warning("gateway sync recovered")
                self._last_error = ""
            except Exception as exc:  # noqa: BLE001 - keep retrying; log each new kind of failure once
                message = "{}: {}".format(type(exc).__name__, exc)
                if message != self._last_error:
                    logger.error("gateway sync failed: %s", message)
                self._last_error = message
            time.sleep(self.interval)

    def sync_once(self) -> None:
        unreported = self.kb.unreported()
        if unreported:
            fields = ("id", "uploader", "group", "owner", "name", "path", "title", "cases", "hits", "uploaded_at", "view_url")
            self._call("POST", "/internal/team-kb/reviews", {"items": [{k: m.get(k) for k in fields} for m in unreported[:100]]})
            self.kb.mark_reported([m["id"] for m in unreported[:100]])
        for decision in self._call("GET", "/internal/team-kb/decisions").get("items", []):
            ok, message = self.kb.apply_decision(decision)
            self._call("POST", "/internal/team-kb/reviews/{}/applied".format(decision.get("id")), {"ok": ok, "message": message})

    def _call(self, method: str, path: str, payload: Optional[Dict] = None) -> Dict:
        return gateway_request(self.base_url, self.token, method, path, payload)


class QuestionPoller(threading.Thread):
    """Long-polls the Gateway for Feishu questions (design §5.1) and answers them in a small thread pool."""

    WAIT_SECONDS = 25

    def __init__(self, kb: KnowledgeBase, base_url: str, token: str, workers: int = 3) -> None:
        super().__init__(name="team-kb-questions", daemon=True)
        self.kb, self.base_url, self.token = kb, base_url.rstrip("/"), token
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="team-kb-answer")
        self._last_error = ""

    def run(self) -> None:
        while True:
            try:
                for item in self.poll_once():
                    self._pool.submit(self.answer_one, item)
                if self._last_error:
                    logger.warning("question polling recovered")
                self._last_error = ""
            except Exception as exc:  # noqa: BLE001 - keep polling; log each new kind of failure once
                message = "{}: {}".format(type(exc).__name__, exc)
                if message != self._last_error:
                    logger.error("question polling failed: %s", message)
                self._last_error = message
                time.sleep(15 if "HTTP 404" in message else 5)

    def poll_once(self) -> List[Dict]:
        path = "/internal/team-kb/questions?wait={}".format(self.WAIT_SECONDS)
        return list(self._call("GET", path, timeout=self.WAIT_SECONDS + 15).get("items") or [])

    def answer_one(self, item: Dict) -> Dict:
        question_id = str(item.get("id") or "")
        try:
            result = qa.answer(str(item.get("text") or ""), self.kb.index.qa, self.kb.ai,
                               references=item.get("references") or [])
        except Exception as exc:  # noqa: BLE001 - the asker still gets a reply from the Gateway
            logger.exception("question failed id=%s", question_id)
            result = {"ok": False, "reason": type(exc).__name__, "text": "", "sources": []}
        logger.warning("question answered id=%s ok=%s sources=%s model=%s ms=%s error=%s", question_id, result.get("ok"),
                       [s.get("title") for s in result.get("sources") or []], result.get("model"),
                       result.get("elapsed_ms"), result.get("error", ""))
        try:
            self._call("POST", "/internal/team-kb/questions/{}/answer".format(quote(question_id, safe="")), result)
        except Exception as exc:  # noqa: BLE001
            logger.error("answer delivery failed id=%s: %s", question_id, exc)
        return result

    def _call(self, method: str, path: str, payload: Optional[Dict] = None, timeout: float = 20) -> Dict:
        return gateway_request(self.base_url, self.token, method, path, payload, timeout=timeout)


def gateway_request(base_url: str, token: str, method: str, path: str, payload: Optional[Dict] = None,
                    timeout: float = 20) -> Dict:
    request = Request(
        base_url + path, method=method,
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        # Own User-Agent: Cloudflare rejects the default "Python-urllib/x.y" (error 1010).
        headers={"Authorization": "Bearer " + token, "X-Feishu-Worker-Id": "team-kb",
                 "Content-Type": "application/json", "User-Agent": "team-kb/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        raise RuntimeError("{} {} -> HTTP {}".format(method, path.split("?")[0], exc.code)) from None
    except URLError as exc:
        raise RuntimeError("{} {} -> {}".format(method, path.split("?")[0], exc.reason)) from None


class CardWorker(threading.Thread):
    """Writes the feature card of each stored upload in the background (one AI call takes ~10–60 s)."""

    RETRY_SECONDS = 60

    def __init__(self, kb: KnowledgeBase, ai: "cards.CardAI") -> None:
        super().__init__(name="team-kb-cards", daemon=True)
        self.kb, self.ai = kb, ai
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._queued: set = set()
        self._guard = threading.Lock()

    def enqueue(self, rel: str) -> None:
        with self._guard:
            if rel in self._queued:
                return
            self._queued.add(rel)
        self._queue.put(rel)

    def run(self) -> None:
        while True:
            rel = self._queue.get()
            with self._guard:
                self._queued.discard(rel)
            for attempt in (1, 2):
                try:
                    logger.warning("card %s: %s", rel, self.process(rel))
                    break
                except Exception as exc:  # noqa: BLE001 - one retry, then the page keeps showing no card
                    logger.error("card failed (attempt %s) path=%s: %s", attempt, rel, exc)
                    if attempt == 1:
                        time.sleep(self.RETRY_SECONDS)

    def process(self, rel: str) -> str:
        case_path = self.kb.root / "cases" / rel
        if not case_path.is_file():
            return "skipped: case file gone"
        stored = case_path.read_text(encoding="utf-8")
        source = cards.card_input(stored)
        if source is None:
            return "skipped: no case table"
        card, model = self.ai.generate(*source)  # slow; outside the lock
        with self.kb.lock:
            if not case_path.is_file() or case_path.read_text(encoding="utf-8") != stored:
                return "skipped: case changed meanwhile (the newer upload queued its own card)"
            target = self.kb.root / "cards" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            build_site.write(target, card)
            build_site.build(self.kb.root)
            try:
                cards.commit(self.kb.root, [rel], model)
            except (OSError, subprocess.CalledProcessError) as exc:
                logger.error("git commit (card) failed: %s", getattr(exc, "stderr", exc))
            self.kb.index.load()
        return "written by " + model


def _gateway_settings(agent_config: str) -> Optional[Tuple[str, str]]:
    if not agent_config or not Path(agent_config).is_file():
        return None
    runtime = json.loads(Path(agent_config).read_text(encoding="utf-8")).get("runtime") or {}
    base_url, token = str(runtime.get("gateway_base_url") or ""), str(runtime.get("worker_token") or "")
    return (base_url, token) if base_url.startswith(("http://", "https://")) and token else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--public-url", default="", help="intranet base URL of the site, for the admin's full-text links")
    parser.add_argument("--agent-config", default="", help="feishu-agent Worker config.json (gateway_base_url, worker_token)")
    parser.add_argument("--ai-config", default="", help="ai.json with the card models (see cards.py); absent = no cards")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    kb = KnowledgeBase(Path(args.root), public_url=args.public_url)
    kb.index.load()
    Handler.kb = kb
    gateway = _gateway_settings(args.agent_config)
    ai = cards.CardAI.from_file(args.ai_config)
    kb.ai = ai
    if gateway:
        GatewaySync(kb, *gateway).start()
        QuestionPoller(kb, *gateway).start()
    else:
        logger.warning("gateway sync and Feishu questions disabled: no --agent-config with runtime.gateway_base_url / worker_token")
    if ai:
        kb.cards = CardWorker(kb, ai)
        kb.cards.start()
        logger.warning("card generation enabled: %s", ", ".join(m["model"] for m in ai.models))
    else:
        logger.warning("card generation disabled: no usable --ai-config")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    logger.warning("team-kb service listening on 127.0.0.1:%s", args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
