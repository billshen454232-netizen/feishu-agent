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
import json
import logging
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site  # noqa: E402
import normalize  # noqa: E402

logger = logging.getLogger("team-kb")

GROUPS = ("系统组", "赛季组", "国服", "其他")
NAME = re.compile(r"^[0-9A-Za-z_·一-龥]{1,20}$")
FILE_NAME = re.compile(r'^[^/\\:*?"<>|]{1,80}\.md$')
MAX_FILE_BYTES = 3 * 1024 * 1024
MAX_FILES = 20
MAX_BODY_BYTES = 45 * 1024 * 1024
MAX_QUERY = 50


class SearchIndex:
    """In-memory rows of every case table plus one entry per feature; rebuilt after each upload."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.features: List[Dict] = []
        self.rows: List[Dict] = []

    def load(self) -> None:
        features, rows = [], []
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
            rows.extend(self._rows(rel, title, meta.get("owner", ""), body))
        self.features, self.rows = features, rows
        logger.warning("search index loaded: %s features, %s rows", len(features), len(rows))

    @staticmethod
    def _rows(rel: str, title: str, owner: str, body: str) -> List[Dict]:
        rows, header = [], None
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if not line.lstrip().startswith("|"):
                header = None
                continue
            if header is None:
                if i + 1 < len(lines) and normalize.SEP.match(lines[i + 1]):
                    header = normalize.cells(line)
                continue
            if normalize.SEP.match(line):
                continue
            if "预期结果" not in header:
                continue
            record = dict(zip(header, normalize.cells(line)))
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
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index = SearchIndex(root)
        self.lock = threading.Lock()

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
        hits = normalize.sensitive_hits(text)
        if hits:
            result["reasons"].append("包含敏感信息，请屏蔽后重新上传：" + "；".join(hits[:10]) + ("…" if len(hits) > 10 else ""))
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
        target.parent.mkdir(parents=True, exist_ok=True)
        build_site.write(target, stored)
        written.append(rel.as_posix())
        warnings = list(summary["warnings"])
        if (self.root / "cards" / rel).is_file():
            if existed:
                warnings.append("摘要卡片仍是旧版本，用例已更新")
        else:
            warnings.append("暂无摘要卡片")
        result.update(status="updated" if existed else "added", path=rel.as_posix(), cases=summary["cases"],
                      unrated=summary["unrated"], title=summary["title"], warnings=warnings)
        return result

    def _git_commit(self, uploader: str, written: List[str]) -> None:
        author = "{} <{}@team-kb>".format(uploader, "upload")
        message = "upload by {}: {}".format(uploader, ", ".join(written[:5]) + (" …" if len(written) > 5 else ""))
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    kb = KnowledgeBase(Path(args.root))
    kb.index.load()
    Handler.kb = kb
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    logger.warning("team-kb service listening on 127.0.0.1:%s", args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
