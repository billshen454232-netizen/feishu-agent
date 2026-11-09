import asyncio
import base64
import json
import re
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.baye_check import (
    BayeAccount,
    BayeCheckClient,
    BayeCheckCoordinator,
    BayeCheckError,
    BayeCheckSubmitUnknown,
    build_followup_card,
    decide,
    load_accounts,
    ocr_texts,
    parse_followup,
    parse_judgement,
    should_judge,
)
from src.message_jobs import MessageJobStore
from src.message_worker import _plan_to_json
from src.muliu_executor import MuliuExecutionResult, MuliuStepResult
from src.muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
from src.muliu_planner import convert_time_arguments
from src.muliu_routing import MuliuPlanRouteResolver, MuliuRouteSnapshot
from src.muliu_script_catalog import MuliuScriptCatalog, parse_call_contracts
from src.muliu_worker import ClaimedWorkerJob, MuliuWorker
from src.utils.time_text import parse_time_text
from src.worker_protocol import serialize_execution_result

from tests.test_muliu_worker import AcceptAllFirewall, RecordingGatewayClient, make_config

BAYE_PATH = "/home/serverGeneralScript/_bot/baye_open_time_check"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
CATALOG = (
    "# Muliu\n\n<!-- MULIU_CALL_CONTRACTS\n"
    + json.dumps({
        "max_plan_steps": 3,
        "contracts": [
            {"name": "basic", "path": "/home/serverGeneralScript/basic_info.sh", "args": ["{server_id}"],
             "variables": {"server_id": "[0-9]{3,8}"}, "runner": "bash", "risk": "read"},
            {"name": "baye-open-time-check", "path": BAYE_PATH, "args": ["{check_server}"],
             "variables": {"check_server": "[0-9]{3,8}"}, "runner": "bash", "risk": "write", "backend": "baye_check"},
            {"name": "baye-open-time-check-with-time", "path": BAYE_PATH, "args": ["{check_server}", "{timestamp}"],
             "variables": {"check_server": "[0-9]{3,8}", "timestamp": "[0-9]{9,12}"}, "runner": "bash", "risk": "write",
             "backend": "baye_check"},
        ],
    })
    + "\nMULIU_CALL_CONTRACTS -->"
)


def baye_plan(server="1800"):
    return MuliuPlan(
        summary="检查 {} 服开服配置".format(server),
        kind=MuliuPlanKind.OPERATION,
        steps=[MuliuStep(path=BAYE_PATH, args=[server], description="检查 {} 服开服配置".format(server))],
    )


OPEN_TS = parse_time_text("2026-11-09T12:00").timestamp


def baye_plan_with_time(server="1800"):
    plan = baye_plan(server)
    plan.steps[0] = MuliuStep(path=BAYE_PATH, args=[server, str(OPEN_TS)], description=plan.steps[0].description)
    return plan


def write_accounts(path, rows):
    lines = ["服号,完整服务器名,账号,密码,设备"] + [",".join(row) for row in rows]
    path.write_text("\n".join(lines), encoding="utf-8")


def settings(tmp_path, **overrides):
    values = dict(base_url="http://executor:8899", default_serial="DEV1", accounts_path=str(tmp_path / "accounts.csv"),
                  state_path=str(tmp_path / "pending.json"), poll_interval_seconds=0, timeout_seconds=60,
                  request_timeout_seconds=5)
    values.update(overrides)
    return SimpleNamespace(**values)


# ---- account table -------------------------------------------------------

def test_load_accounts_matches_header_aliases_and_hides_password(tmp_path):
    path = tmp_path / "accounts.csv"
    write_accounts(path, [("1800", "1800服 鼎业千秋", "qa01", "p@ss,", ""), ("", "skip", "x", "y", "")])
    path.write_text(path.read_text(encoding="utf-8").replace("p@ss,", "p@ss"), encoding="utf-8")

    accounts = load_accounts(path)

    assert list(accounts) == ["1800"]
    assert accounts["1800"].server_name == "1800服 鼎业千秋"
    assert accounts["1800"].password == "p@ss"
    assert "p@ss" not in repr(accounts["1800"]) and "qa01" not in repr(accounts["1800"])


def test_load_accounts_reads_excel_gbk_csv_and_indexes_by_server_number(tmp_path):
    path = tmp_path / "accounts.csv"
    path.write_bytes("服号,完整服务器名,账号,密码,服号,设备\nS11800,1800服 鼎业千秋,qa01,secret,S11800,\n".encode("gbk"))

    accounts = load_accounts(path)

    assert accounts["1800"] is accounts["S11800"]
    assert accounts["1800"].server_name == "1800服 鼎业千秋"


def test_load_accounts_reports_missing_columns(tmp_path):
    path = tmp_path / "accounts.csv"
    path.write_text("服号,账号\n1800,qa01\n", encoding="utf-8")
    with pytest.raises(BayeCheckError, match="服务器名"):
        load_accounts(path)


# ---- executor client -----------------------------------------------------

def _client(handler):
    return BayeCheckClient("http://executor:8899", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_client_maps_busy_device_to_error_and_transport_failure_to_unknown():
    account = BayeAccount("1800", "1800服 鼎业千秋", "qa01", "secret")

    busy = _client(lambda request: httpx.Response(409, json={"detail": "device busy"}))
    with pytest.raises(BayeCheckError, match="409") as excinfo:
        await busy.submit(account, "DEV1")
    assert not isinstance(excinfo.value, BayeCheckSubmitUnknown)

    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(BayeCheckSubmitUnknown):
        await _client(boom).submit(account, "DEV1")


@pytest.mark.asyncio
async def test_client_sends_account_from_table():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(202, json={"id": "c1", "status": "queued"})

    await _client(handler).submit(BayeAccount("1800", "1800服 鼎业千秋", "qa01", "secret"), "DEV1")
    assert seen == {"serial": "DEV1", "account": "qa01", "password": "secret", "server": "1800服 鼎业千秋"}


# ---- coordinator ---------------------------------------------------------

class FakeExecutor:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.submitted = []

    async def submit(self, account, serial):
        self.submitted.append((account.server_id, serial))
        return {"id": "c1", "status": "queued"}

    async def get(self, check_id):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        job = {"id": check_id, "status": status, "step": "done", "created_at": "t0", "finished_at": "t1"}
        if status == "pass":
            job["screenshots"] = [{"name": "server_list", "label": "选服列表", "url": "/a.png"},
                                  {"name": "baye", "label": "霸业页面", "url": "/b.png"}]
        return job

    async def image(self, url):
        return PNG


async def _no_sleep(_seconds):
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_coordinator_submits_watches_and_sends_screenshots(tmp_path):
    write_accounts(tmp_path / "accounts.csv", [("1800", "1800服 鼎业千秋", "qa01", "secret", "")])
    sent = []

    async def send(message_id, payload):
        sent.append((message_id, payload))
        return True

    executor = FakeExecutor(["running", "running", "pass"])
    coordinator = BayeCheckCoordinator(settings(tmp_path), send, client=executor, sleep=_no_sleep)

    check_id = await coordinator.submit("om_1", "1800")
    assert check_id == "c1"
    assert json.loads((tmp_path / "pending.json").read_text(encoding="utf-8"))[0]["check_id"] == "c1"
    for _ in range(50):
        if sent:
            break
        await asyncio.sleep(0)
    await coordinator.stop()

    assert executor.submitted == [("1800", "DEV1")]
    message_id, payload = sent[0]
    assert message_id == "om_1" and payload["status"] == "pass"
    assert [image["label"] for image in payload["images"]] == ["选服列表", "霸业页面"]
    assert "secret" not in json.dumps(payload) and "qa01" not in json.dumps(payload)
    assert json.loads((tmp_path / "pending.json").read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_coordinator_rejects_unknown_server_without_submitting(tmp_path):
    write_accounts(tmp_path / "accounts.csv", [("1800", "1800服 鼎业千秋", "qa01", "secret", "")])
    executor = FakeExecutor(["pass"])
    coordinator = BayeCheckCoordinator(settings(tmp_path), lambda *_: None, client=executor)

    with pytest.raises(BayeCheckError, match="1900"):
        await coordinator.submit("om_1", "1900")
    assert executor.submitted == []


@pytest.mark.asyncio
async def test_coordinator_resumes_pending_checks_after_restart(tmp_path):
    (tmp_path / "pending.json").write_text(json.dumps([
        {"message_id": "om_9", "check_id": "c1", "server_id": "1800", "server_name": "1800服", "serial": "DEV1",
         "submitted_at": 0}
    ]), encoding="utf-8")
    sent = []

    async def send(message_id, payload):
        sent.append(message_id)
        return True

    coordinator = BayeCheckCoordinator(settings(tmp_path), send, client=FakeExecutor(["fail"]), sleep=_no_sleep,
                                       clock=lambda: 1)
    assert coordinator.resume() == 1
    for _ in range(50):
        if sent:
            break
        await asyncio.sleep(0)
    await coordinator.stop()
    assert sent == ["om_9"]


# ---- follow-up card --------------------------------------------------------

def test_followup_card_shows_images_and_review_note():
    followup = parse_followup({
        "kind": "baye_check", "check_id": "c1", "server_id": "1800", "server_name": "1800服 鼎业千秋", "status": "pass",
        "images": [{"name": "baye", "label": "霸业页面", "png_base64": base64.b64encode(PNG).decode()}],
    })
    card = build_followup_card(followup, [("霸业页面", "img_v2_1")])

    assert card["header"]["template"] == "green"
    assert {"tag": "img", "img_key": "img_v2_1", "alt": {"tag": "plain_text", "content": "霸业页面"}} in card["elements"]
    assert "开服时间 + 4 小时" in json.dumps(card, ensure_ascii=False)


def test_parse_followup_rejects_bad_payloads():
    with pytest.raises(ValueError):
        parse_followup({"kind": "other", "check_id": "c1"})
    with pytest.raises(ValueError):
        parse_followup({"kind": "baye_check", "check_id": "c1", "images": [{"png_base64": "!!"}]})
    with pytest.raises(ValueError):
        parse_followup({"kind": "baye_check", "check_id": "c1", "images": [{"png_base64": "AA=="}] * 3})


# ---- AI judging --------------------------------------------------------------

def test_ocr_texts_accepts_text_lines_and_per_screenshot_shapes():
    job = {"ocr": {"server": {"lines": ["本服开启时间:", {"text": "2026年11月9日12:00"}]}, "baye": ""},
           "screenshots": [{"name": "baye", "ocr_text": "已于2026年11月9日 16:00:00 完成"}]}
    assert ocr_texts(job) == {"server": "本服开启时间:\n2026年11月9日12:00", "baye": "已于2026年11月9日 16:00:00 完成"}


def _parsed(**extra):
    return parse_followup({"kind": "baye_check", "check_id": "c1", "server_id": "1800", "status": "pass", **extra})


def test_judging_needs_both_message_time_and_ocr_text():
    assert not should_judge(_parsed(ocr={"server": "x"}))
    assert not should_judge(_parsed(expected_open_time=OPEN_TS))
    assert should_judge(_parsed(expected_open_time=OPEN_TS, ocr={"server": "x"}))


@pytest.mark.parametrize("seen_open, seen_end, verdict", [
    ("2026-11-09T12:00", "2026-11-09T16:03", "正确"),
    ("2026-11-09T12:00", "2026-11-09T17:00", "不正确"),
    ("2026-11-10T12:00", "", "不正确"),
    ("2026-11-09T12:00", "", "无法判定"),
    ("看不清", "2026-11-09T16:00", "无法判定"),
])
def test_verdict_is_computed_by_code_from_the_values_ai_read(seen_open, seen_end, verdict):
    followup = _parsed(expected_open_time=OPEN_TS)
    assert decide(followup, {"open_time_seen": seen_open, "baye_end_time": seen_end})[0] == verdict


def test_parse_judgement_extracts_json_from_chatty_reply():
    assert parse_judgement('结果如下 {"open_time_seen": "2026-11-09T12:00"} 以上')["open_time_seen"] == "2026-11-09T12:00"
    with pytest.raises(ValueError):
        parse_judgement("无法读取")


def test_card_without_ocr_says_no_judgement_was_made():
    card = build_followup_card(_parsed(expected_open_time=OPEN_TS), [])
    text = json.dumps(card, ensure_ascii=False)
    assert "期望霸业第一阶段结束：2026-11-09 16:00" in text and "没有返回截图识别文字" in text


def test_planner_time_text_becomes_a_timestamp_for_the_timed_contract():
    plan = baye_plan()
    plan.steps[0] = MuliuStep(path=BAYE_PATH, args=["1800", "2026-11-09T12:00"], description="检查 1800 服开服配置")
    converted = convert_time_arguments(plan, parse_call_contracts(CATALOG))
    assert converted.steps[0].args == ["1800", str(OPEN_TS)]
    assert "2026-11-09 12:00" in converted.steps[0].description


# ---- catalog / routing -----------------------------------------------------

def test_backend_contract_is_excluded_from_gs1_runner_manifest():
    contracts = parse_call_contracts(CATALOG)
    assert contracts.backend_for(baye_plan().steps[0]) == "baye_check"
    manifest_paths = [contract.path for contract in contracts.runner_manifest().contracts]
    assert manifest_paths == ["/home/serverGeneralScript/basic_info.sh"]


def test_repository_catalog_registers_baye_check():
    contracts = MuliuScriptCatalog("config/muliu_script_catalog.md").read_call_contracts()
    assert contracts.backend_for(baye_plan().steps[0]) == "baye_check"
    assert contracts.backend_for(baye_plan_with_time().steps[0]) == "baye_check"


def _resolver(tmp_path):
    env = tmp_path / "server_environments.json"
    env.write_text(json.dumps({"regions": {"cn": {"name": "国服", "environments": {"qa": ["2003"]}}}, "groups": {}}),
                   encoding="utf-8")
    return MuliuPlanRouteResolver(str(env), parse_call_contracts(CATALOG))


def test_baye_plan_routes_to_cn_and_must_be_alone(tmp_path):
    resolver = _resolver(tmp_path)
    assert resolver.resolve(baye_plan()) == MuliuRouteSnapshot.from_region("cn")

    mixed = baye_plan()
    mixed.steps.append(MuliuStep(path="/home/serverGeneralScript/basic_info.sh", args=["2003"], description="查询"))
    with pytest.raises(ValueError, match="单独发起"):
        resolver.resolve(mixed)


# ---- worker dispatch -------------------------------------------------------

class FakeCoordinator:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def submit(self, message_id, server_id, expected_open_time=0):
        self.calls.append((message_id, server_id, expected_open_time))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _claimed(plan):
    return ClaimedWorkerJob("om_1", json.dumps({
        "kind": "operation", "summary": plan.summary,
        "steps": [{"path": step.path, "args": step.args, "description": step.description} for step in plan.steps],
    }), "cn")


def _worker(tmp_path, coordinator):
    return MuliuWorker(make_config(), AcceptAllFirewall(), RecordingGatewayClient(), _resolver(tmp_path),
                       call_contracts=parse_call_contracts(CATALOG), baye_checks=coordinator)


@pytest.mark.asyncio
async def test_worker_submits_baye_check_without_muliu(tmp_path, monkeypatch):
    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", lambda *_: pytest.fail("Muliu must not run"))
    coordinator = FakeCoordinator("c1")
    worker = _worker(tmp_path, coordinator)

    await worker._execute_claimed_job(_claimed(baye_plan()))

    assert coordinator.calls == [("om_1", "1800", 0)]
    (message_id, result), = worker._gateway_client.results
    assert result.succeeded and "c1" in result.step_results[0].log


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, unknown", [(BayeCheckError("账号表里没有"), False), (BayeCheckSubmitUnknown("无响应"), True)])
async def test_worker_reports_baye_submit_failures(tmp_path, outcome, unknown):
    worker = _worker(tmp_path, FakeCoordinator(outcome))

    await worker._execute_claimed_job(_claimed(baye_plan()))

    (_, result), = worker._gateway_client.results
    assert not result.succeeded
    assert result.result_unknown is unknown


@pytest.mark.asyncio
async def test_worker_without_baye_config_fails_cleanly(tmp_path):
    worker = _worker(tmp_path, None)
    await worker._execute_claimed_job(_claimed(baye_plan()))
    (_, result), = worker._gateway_client.results
    assert not result.succeeded and not result.result_unknown
    assert "未启用" in result.failure_reason


@pytest.mark.asyncio
async def test_worker_passes_the_expected_open_time(tmp_path):
    coordinator = FakeCoordinator("c1")
    worker = _worker(tmp_path, coordinator)
    await worker._execute_claimed_job(_claimed(baye_plan_with_time()))
    assert coordinator.calls == [("om_1", "1800", OPEN_TS)]


# ---- gateway follow-up endpoint -------------------------------------------

def _gateway(monkeypatch, tmp_path, plan, ai=None):
    """A Gateway app with one finished 开服配置检查 job ``om_req`` leased to jenkins-worker-1."""
    catalog_path = tmp_path / "catalog.md"
    catalog_path.write_text(CATALOG, encoding="utf-8")
    env_path = tmp_path / "server_environments.json"
    env_path.write_text(json.dumps({"regions": {"cn": {"name": "国服", "environments": {}}}, "groups": {}}),
                        encoding="utf-8")
    config_path = tmp_path / "gateway-config.json"
    config_path.write_text(json.dumps({
        "runtime": {"role": "gateway", "worker_token": "secret-token", "lease_timeout_seconds": 60},
        "feishu": {"app_id": "app", "app_secret": "secret", "verification_token": "verify"},
        "ai": {"provider": "openai_compatible", "base_url": "https://api.example.com/v1", "api_key": "key", "model": "m"},
        "job_queue": {"database_path": str(tmp_path / "jobs.sqlite3"), "recovery_window_seconds": 600},
        "muliu": {"script_catalog_path": str(catalog_path)},
        "execution_interpretation": {"server_environments_path": str(env_path), "background_timeout_seconds": 5},
    }), encoding="utf-8")
    feishu_clients = []

    class FakeFeishu:
        def __init__(self, _cfg):
            self.uploads, self.cards, self.card_updates = [], [], []
            feishu_clients.append(self)

        async def reply_text(self, message_id, text):
            pass

        async def update_card(self, message_id, card):
            self.card_updates.append((message_id, card))

        async def upload_image(self, image, filename="image.png"):
            self.uploads.append(filename)
            return "img_{}".format(len(self.uploads))

        async def reply_card(self, message_id, card):
            self.cards.append((message_id, card))
            return "om_reply"

    monkeypatch.setattr("src.app.create_ai_client", lambda _cfg: ai if ai is not None else object())
    monkeypatch.setattr("src.app.FeishuClient", FakeFeishu)
    app = create_app(config_path)

    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_req", "evt_1", "oc_1", "ou_1", "检查 1800 服开服配置")
    store.mark_awaiting_confirmation("om_req", plan_json=_plan_to_json(plan), confirmation_message_id="card_1",
                                     confirmation_token="token_1", confirmation_method="card", route_region="cn")
    store.claim_card_confirmation(request_message_id="om_req", confirmation_message_id="card_1", chat_id="oc_1",
                                  user_id="ou_1", confirmation_token="token_1", max_age_seconds=600)
    store.queue_for_worker("om_req")
    return app, feishu_clients


HEADERS = {"Authorization": "Bearer secret-token", "X-Feishu-Worker-Id": "jenkins-worker-1"}


def _finish_job(client, plan):
    assert client.post("/internal/worker/jobs/claim", headers=HEADERS).status_code == 200
    submitted = MuliuExecutionResult(summary=plan.summary, step_results=[MuliuStepResult(
        step_number=1, description=plan.steps[0].description, path=BAYE_PATH, args=list(plan.steps[0].args),
        succeeded=True, log="已提交\nAI_OP_END status=success")])
    response = client.post("/internal/worker/jobs/om_req/result", headers=HEADERS,
                           json={"result": serialize_execution_result(submitted)})
    assert response.status_code == 200


def _followup(**extra):
    return {
        "kind": "baye_check", "check_id": "c1", "server_id": "1800", "server_name": "1800服 鼎业千秋", "status": "pass",
        "finished_at": "2026-11-09 17:21:16",
        "images": [{"name": "server_list", "label": "选服列表", "png_base64": base64.b64encode(PNG).decode()},
                   {"name": "baye", "label": "霸业页面", "png_base64": base64.b64encode(PNG).decode()}],
        **extra,
    }


def test_gateway_followup_uploads_images_and_replies_in_thread(monkeypatch, tmp_path):
    plan = baye_plan()
    app, feishu_clients = _gateway(monkeypatch, tmp_path, plan)

    with TestClient(app) as client:
        _finish_job(client, plan)
        other = client.post("/internal/worker/jobs/om_req/followup",
                            headers={**HEADERS, "X-Feishu-Worker-Id": "other-worker"}, json=_followup())
        assert other.status_code == 404
        first = client.post("/internal/worker/jobs/om_req/followup", headers=HEADERS, json=_followup())
        again = client.post("/internal/worker/jobs/om_req/followup", headers=HEADERS, json=_followup())

    assert first.json() == {"status": "accepted"}
    assert again.json() == {"status": "duplicate"}
    feishu = feishu_clients[0]
    assert feishu.uploads == ["server_list.png", "baye.png"]
    (target, card), = feishu.cards
    assert target == "om_req"
    assert [e["img_key"] for e in card["elements"] if e["tag"] == "img"] == ["img_1", "img_2"]
    assert "未在消息里给出开服时间" in json.dumps(card, ensure_ascii=False)


def test_gateway_judges_ocr_text_against_the_open_time_from_the_message(monkeypatch, tmp_path):
    class FakeAI:
        def __init__(self):
            self.prompts = []

        async def generate(self, history, prompt):
            self.prompts.append(prompt)
            return '好的：{"open_time_seen": "2026-11-09T12:00", "baye_end_time": "2026-11-09T16:00", ' \
                   '"baye_text": "已于2026年11月9日 16:00:00 完成", "explanation": "选服列表与霸业页面一致"}'

    ai = FakeAI()
    plan = baye_plan_with_time()
    app, feishu_clients = _gateway(monkeypatch, tmp_path, plan, ai=ai)
    followup = _followup(expected_open_time=OPEN_TS, ocr={"server": "本服开启时间: 2026年11月9日12:00",
                                                         "baye": "已于2026年11月9日 16:00:00 完成"})

    with TestClient(app) as client:
        _finish_job(client, plan)
        response = client.post("/internal/worker/jobs/om_req/followup", headers=HEADERS, json=followup)
        assert response.json() == {"status": "accepted"}
        feishu = feishu_clients[0]
        deadline = time.monotonic() + 5
        while not feishu.cards and time.monotonic() < deadline:
            time.sleep(0.02)

    (_, card), = feishu.cards
    assert card["header"]["title"]["content"] == "开服配置检查：正确"
    text = json.dumps(card, ensure_ascii=False)
    assert "期望开服时间：2026-11-09 12:00" in text and "2026-11-09 16:00 ✓" in text
    assert "本服开启时间" in ai.prompts[0] and "secret" not in ai.prompts[0]
