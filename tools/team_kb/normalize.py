"""Normalize one uploaded test-case Markdown file and check it before it enters the team knowledge base.

Rules (agreed with the owner, 2026-11-10):
  * case tables (have 预期结果 and one of 执行步骤/前置条件/测试项) get the standard header; 模块 / 二级模块 empty
    cells are forward-filled within the table (Excel merged cells); empty 分级 becomes 「未分级」 (never guessed);
  * every other table and all prose are copied verbatim;
  * sensitive values (IP, password, token, phone, database host) reject the file; nothing is stored.
Standard library only, Python 3.9+.
"""

from __future__ import annotations

import re
from collections import Counter, OrderedDict
from typing import Dict, List, Tuple

SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
PIPE = re.compile(r"(?<!\\)\|")
STANDARD = ["用例ID", "模块", "二级模块", "分级", "测试项", "前置条件", "执行步骤", "预期结果", "实际结果", "备注"]
ALIASES = {
    "用例ID": "用例ID", "模块": "模块", "二级模块": "二级模块", "二级": "二级模块", "分类": "二级模块",
    "用例分级": "分级", "分级": "分级", "测试项": "测试项", "前置条件": "前置条件", "执行步骤": "执行步骤",
    "预期结果": "预期结果", "实际结果": "实际结果", "测试结果": "实际结果", "备注": "备注",
}
FILL = ("模块", "二级模块")
LEVELS = {"P0", "P1", "P2", "P3"}
SENSITIVE = {
    "IP 地址": re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    "密码": re.compile(r"(?i)(password|passwd|pwd|密码)\s*[:=：]\s*[^\s|*]{3,}"),
    "密钥 / token": re.compile(r"(?i)(token|secret|access[_-]?key|appsecret|api[_-]?key)\s*[:=：]\s*[^\s|*]{6,}"),
    "手机号": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "数据库地址": re.compile(r"(?i)dds-[a-z0-9]{6,}|\.rds\.aliyuncs\.com|mongodb://"),
}
SPILL = re.compile(r"^\s*(\d+[.、]|（|\()")  # a 「2.停起服」 in 模块: multi-line cell split into rows on export


def cells(line: str) -> List[str]:
    """Split on unescaped pipes only; cells keep their ``\\|`` escapes (still valid Markdown)."""
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.strip() for c in PIPE.split(body)]


def sensitive_hits(text: str) -> List[str]:
    """``类型：第 N 行 xx…`` for every sensitive value (version numbers like 1.2.3.4 are ignored)."""
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in SENSITIVE.items():
            for match in pattern.finditer(line):
                value = match.group(0)
                if kind == "IP 地址" and all(int(part) < 10 for part in value.split(".")):
                    continue
                hits.append("{}：第 {} 行 {}…".format(kind, number, value[:3]))
    return hits


def _is_case_header(header: List[str]) -> bool:
    names = {ALIASES.get(h) for h in header}
    return "预期结果" in names and bool(names & {"执行步骤", "前置条件", "测试项"})


def _normalize_table(header: List[str], rows: List[str], stats: Counter) -> Tuple[List[str], List[Dict[str, str]]]:
    mapped, extra = [], []
    for h in header:
        std = ALIASES.get(h)
        if std is None or std in mapped:
            extra.append(h)
            mapped.append(None)
        else:
            mapped.append(std)
    cols = [c for c in STANDARD if c in mapped] + ["附加:{}".format(h) for h in extra if h]
    records, last = [], {}
    for raw in rows:
        vals = cells(raw)
        if len(vals) > len(header):
            vals = vals[: len(header) - 1] + [" | ".join(vals[len(header) - 1:])]
        elif len(vals) < len(header):
            vals += [""] * (len(header) - len(vals))
        rec: Dict[str, str] = {}
        for std, h, v in zip(mapped, header, vals):
            if std:
                rec[std] = v
            elif h:
                rec["附加:{}".format(h)] = v
        if not any(rec.values()):
            continue
        if rec.get("模块") and SPILL.match(rec["模块"]):
            stats["spill"] += 1
        for key in FILL:
            if key in rec:
                if rec[key]:
                    last[key] = rec[key]
                elif key in last:
                    rec[key] = last[key]
        if "分级" in rec:
            upper = rec["分级"].upper()
            if not rec["分级"]:
                rec["分级"] = "未分级"
                stats["unrated"] += 1
            elif upper in LEVELS:
                rec["分级"] = upper
            else:
                stats["odd_level"] += 1
        records.append(rec)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for rec in records:
        lines.append("| " + " | ".join(rec.get(c, "").replace("\n", " ") for c in cols) + " |")
    return lines, records


def normalize(text: str, *, title_fallback: str, group: str, owner: str, extra_meta: Dict[str, str]) -> Tuple[str, Dict]:
    """Return (stored file text with frontmatter, summary) for one uploaded Markdown file."""
    lines = text.lstrip(chr(0xFEFF)).splitlines()
    properties = []
    if lines and lines[0].strip() == "---":  # Obsidian properties: ours replace the block, values stay readable
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            properties = [l.strip() for l in lines[1:end] if l.strip()]
            lines = lines[end + 1:]
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), title_fallback)
    stats: Counter = Counter()
    body, cases = [], []
    if properties:
        body += ["> 原文档属性：" + "；".join(properties), ""]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("|") and i + 1 < len(lines) and SEP.match(lines[i + 1]):
            header = cells(line)
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            if _is_case_header(header):
                table, records = _normalize_table(header, lines[i + 2:j], stats)
                body.extend(table)
                cases.extend(records)
            else:
                body.extend(lines[i:j])
            i = j
            continue
        body.append(line)
        i += 1
    levels = Counter(r.get("分级", "无分级列") for r in cases)
    modules: "OrderedDict[str, None]" = OrderedDict()
    for r in cases:
        modules[r.get("模块") or title] = None
    quote = lambda value: '"' + value.replace('"', "'") + '"'  # noqa: E731
    front = ["---", "title: {}".format(quote(title)), "group: {}".format(group), "owner: {}".format(quote(owner)),
             "case_count: {}".format(len(cases)),
             "levels: {" + ", ".join("{}: {}".format(k, v) for k, v in sorted(levels.items())) + "}",
             "modules: [" + ", ".join(quote(m) for m in list(modules)[:40]) + "]"]
    front += ["{}: {}".format(k, quote(v)) for k, v in extra_meta.items()]
    front += ["---", ""]
    warnings = []
    if not cases:
        warnings.append("没有识别到用例表格（表头需包含「预期结果」和「执行步骤 / 前置条件 / 测试项」之一），按普通文档收录")
    if stats["spill"]:
        warnings.append("{} 行的「模块」像是步骤文字（如「2.xxx」），多半是单元格内换行导出时被拆成了多行；建议上传 Excel 原件或在 Excel 里重新导出".format(stats["spill"]))
    if stats["odd_level"]:
        warnings.append("{} 行的分级列不是 P0–P3（已原样保留）".format(stats["odd_level"]))
    summary = {"title": title, "cases": len(cases), "unrated": stats["unrated"], "warnings": warnings}
    return "\n".join(front + body).rstrip() + "\n", summary
