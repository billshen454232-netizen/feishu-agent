"""FastAPI evaluates route annotations at import time; the Gateway runs Python 3.9.

``X | None`` and similar PEP 604 unions only evaluate on 3.10+, so a route written
that way passes locally (newer Python) but crashes the Gateway on startup.
"""

import inspect

from fastapi.routing import APIRoute

from src.admin_app import create_admin_app
from src.execution_report import ExecutionReportStore
from src.plan_board import PlanStore
from src.qa_library import QaService, QaStore


def test_route_annotations_avoid_pep604_unions(tmp_path):
    db = tmp_path / "db.sqlite3"
    report = ExecutionReportStore(db); report.initialize()
    qa_store = QaStore(db); qa_store.initialize()
    plans = PlanStore(db); plans.initialize(None)
    app = create_admin_app(report, qa_service=QaService(answer_client=object(), store=qa_store), plan_store=plans)

    offenders = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for name, parameter in inspect.signature(route.endpoint).parameters.items():
            annotation = parameter.annotation
            if isinstance(annotation, str) and "|" in annotation:
                offenders.append("{} {}: {}".format(route.path, name, annotation))
    assert offenders == [], "use Optional[...] / Union[...] in route signatures (Gateway is Python 3.9): {}".format(offenders)
