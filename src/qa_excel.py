"""Excel round trip for unsatisfied answers (team-kb design §5.9-2).

Export the 「不满意」 Q&A as .xlsx; the reviewer hands it to the askers, who fill in 「正确回答」; importing the
file puts each filled answer on the review page as 「待确认」 until the reviewer archives it.
openpyxl is a Gateway-only dependency (requirements-gateway.txt) and is imported lazily.
"""

from __future__ import annotations

import io
import time
from typing import Any

HEADERS = ("编号", "时间", "提问人", "类型", "问题", "机器人回答", "引用来源", "正确回答", "填写人")
WIDTHS = (36, 17, 10, 11, 36, 60, 30, 60, 10)
MAX_ROWS = 1000
MAX_ANSWER_CHARS = 4000


class QaExcelError(ValueError):
    """The workbook cannot be produced or read."""


def _openpyxl():
    try:
        import openpyxl  # noqa: PLC0415 - optional Gateway dependency
    except ImportError as exc:
        raise QaExcelError("Gateway 未安装 openpyxl：在 Gateway 执行 .venv/bin/pip install -r requirements-gateway.txt") from exc
    return openpyxl


def _final_answer(record: dict[str, Any]) -> str:
    revisions = record.get("revisions") or []
    return revisions[-1]["answer"] if revisions else record.get("answer", "")


def _sources(record: dict[str, Any]) -> str:
    sources = record.get("sources") or {}
    names = ["{}（{}）".format(s.get("title", ""), s.get("owner") or "—") for s in sources.get("team_kb", [])]
    names += [str(path).replace("knowledge/", "") for path in sources.get("docs", [])]
    names += ["已审核问答 #{}".format(archive_id) for archive_id in sources.get("archives", [])]
    return "，".join(names)


def export_records(records: list[dict[str, Any]]) -> bytes:
    openpyxl = _openpyxl()
    from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "不满意问答"
    sheet.append(HEADERS)
    for record in records[:MAX_ROWS]:
        sheet.append([
            record["message_id"],
            time.strftime("%Y-%m-%d %H:%M", time.localtime(int(record.get("created_at") or 0))),
            record.get("user_name") or record.get("user_id") or "",
            "组内知识库" if record.get("domain") == "team_kb" else "脚本资料",
            record.get("question", ""),
            _final_answer(record),
            _sources(record),
            "",
            "",
        ])
    header_fill = PatternFill("solid", fgColor="DDEBF7")
    fill_fill = PatternFill("solid", fgColor="FFF2CC")  # the two columns the askers fill in
    for index, width in enumerate(WIDTHS, start=1):
        letter = sheet.cell(row=1, column=index).column_letter
        sheet.column_dimensions[letter].width = width
        sheet.cell(row=1, column=index).font = Font(bold=True)
        sheet.cell(row=1, column=index).fill = fill_fill if HEADERS[index - 1] in ("正确回答", "填写人") else header_fill
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def parse_import(data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """``(filled rows, problems)``; a row counts when it has 编号 and a non-empty 正确回答."""
    openpyxl = _openpyxl()
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any unreadable upload
        raise QaExcelError("无法读取这个文件，请上传从运维页导出、填写后保存的 .xlsx") from exc
    sheet = workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = [str(value or "").strip() for value in next(rows, ())]
    if "编号" not in header or "正确回答" not in header:
        raise QaExcelError("第一行缺少「编号」或「正确回答」列，请使用运维页导出的表格")
    id_col, answer_col = header.index("编号"), header.index("正确回答")
    filler_col = header.index("填写人") if "填写人" in header else None
    filled, problems = [], []
    for number, values in enumerate(rows, start=2):
        if number > MAX_ROWS + 1:
            problems.append("超过 {} 行，后面的没有导入".format(MAX_ROWS))
            break
        values = list(values) + [None] * len(header)
        message_id = str(values[id_col] or "").strip()
        answer = str(values[answer_col] or "").strip()
        if not answer:
            continue
        if not message_id:
            problems.append("第 {} 行没有编号".format(number))
            continue
        if len(answer) > MAX_ANSWER_CHARS:
            problems.append("第 {} 行正确回答超过 {} 字".format(number, MAX_ANSWER_CHARS))
            continue
        filler = str(values[filler_col] or "").strip()[:40] if filler_col is not None else ""
        filled.append({"row": number, "message_id": message_id, "answer": answer, "filler": filler})
    workbook.close()
    return filled, problems
