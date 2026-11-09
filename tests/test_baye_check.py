import asyncio
import base64
import json
import re
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
    load_accounts,
    parse_followup,
)
from src.message_jobs import MessageJobStore
from src.message_worker import _plan_to_json
from src.muliu_executor import MuliuExecutionResult, MuliuStepResult
from src.muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
from src.muliu_routing import MuliuPlanRouteResolver, MuliuRouteSnapshot
from src.muliu_script_catalog import MuliuScriptCatalog, parse_call_contracts
from src.muliu_worker import ClaimedWorkerJob, MuliuWorker
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


# ---- catalog / routing -----------------------------------------------------

def test_backend_contract_is_excluded_from_gs1_runner_manifest():
    contracts = parse_call_contracts(CATALOG)
    assert contracts.backend_for(baye_plan().steps[0]) == "baye_check"
    manifest_paths = [contract.path for contract in contracts.runner_manifest().contracts]
    assert manifest_paths == ["/home/serverGeneralScript/basic_info.sh"]


def test_repository_catalog_registers_baye_check():
    contracts = MuliuScriptCatalog("config/muliu_script_catalog.md").read_call_contracts()
    assert contracts.backend_for(baye_plan().steps[0]) == "baye_check"


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

    async def submit(self, message_id, server_id):
        self.calls.append((message_id, server_id))
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

    assert coordinator.calls == [("om_1", "1800")]
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


# ---- gateway follow-up endpoint -------------------------------------------

def test_gateway_followup_uploads_images_and_replies_in_thread(monkeypatch, tmp_path):
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
        "execution_interpretation": {"server_environments_path": str(env_path)},
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

    monkeypatch.setattr("src.app.create_ai_client", lambda _cfg: object())
    monkeypatch.setattr("src.app.FeishuClient", FakeFeishu)
    app = create_app(config_path)

    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    plan = baye_plan()
    store.create_if_new("om_req", "evt_1", "oc_1", "ou_1", "检查 1800 服开服配置")
    store.mark_awaiting_confirmation("om_req", plan_json=_plan_to_json(plan), confirmation_message_id="card_1",
                                     confirmation_token="token_1", confirmation_method="card", route_region="cn")
    store.claim_card_confirmation(request_message_id="om_req", confirmation_message_id="card_1", chat_id="oc_1",
                                  user_id="ou_1", confirmation_token="token_1", max_age_seconds=600)
    store.queue_for_worker("om_req")
    headers = {"Authorization": "Bearer secret-token", "X-Feishu-Worker-Id": "jenkins-worker-1"}
    followup = {
        "kind": "baye_check", "check_id": "c1", "server_id": "1800", "server_name": "1800服 鼎业千秋", "status": "pass",
        "images": [{"name": "server_list", "label": "选服列表", "png_base64": base64.b64encode(PNG).decode()},
                   {"name": "baye", "label": "霸业页面", "png_base64": base64.b64encode(PNG).decode()}],
    }

    with TestClient(app) as client:
        assert client.post("/internal/worker/jobs/claim", headers=headers).status_code == 200
        submitted = MuliuExecutionResult(summary=plan.summary, step_results=[MuliuStepResult(
            step_number=1, description=plan.steps[0].description, path=BAYE_PATH, args=["1800"], succeeded=True,
            log="已提交\nAI_OP_END status=success")])
        response = client.post("/internal/worker/jobs/om_req/result", headers=headers,
                               json={"result": serialize_execution_result(submitted)})
        assert response.status_code == 200

        other = client.post("/internal/worker/jobs/om_req/followup",
                            headers={**headers, "X-Feishu-Worker-Id": "other-worker"}, json=followup)
        assert other.status_code == 404
        first = client.post("/internal/worker/jobs/om_req/followup", headers=headers, json=followup)
        again = client.post("/internal/worker/jobs/om_req/followup", headers=headers, json=followup)

    assert first.json() == {"status": "accepted"}
    assert again.json() == {"status": "duplicate"}
    feishu = feishu_clients[0]
    assert feishu.uploads == ["server_list.png", "baye.png"]
    (target, card), = feishu.cards
    assert target == "om_req"
    assert [e["img_key"] for e in card["elements"] if e["tag"] == "img"] == ["img_1", "img_2"]
