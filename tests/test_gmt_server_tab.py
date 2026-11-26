import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from src.config import RuntimeConfig
from src.conversation import ConversationStore
from src.gmt_server_tab import GMT_TAB_PATH, GmtClient, GmtError, PAGE, QUERY, UPDATE, ServerTab
from src.message_jobs import MessageJobStore
from src.message_worker import MessageJobProcessor
from src.muliu_firewall import MuliuFirewall
from src.muliu_plan import MuliuPlan, MuliuStep
from src.muliu_routing import MuliuPlanRouteResolver, MuliuRoutingError
from src.muliu_script_catalog import MuliuScriptCatalog
from src.muliu_worker import ClaimedWorkerJob, MuliuWorker
from tests.test_message_worker import FakeAIClient, FakeFeishuClient
from tests.test_muliu_worker import RecordingGatewayClient, make_config

CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_update_posts_only_target_and_tab_fields_and_reads_result():
    requests = []
    server = {"HostNum": 5202, "ServerId": 46, "ServerType": 1, "season": "9", "scenario": "17", "Name": "fixture"}

    def handle(request):
        requests.append((request.method, request.url.path))
        if request.url.path == PAGE:
            return httpx.Response(200, text='<meta name="csrf-token" content="fixture-csrf">')
        if request.url.path == QUERY:
            assert request.url.params["HostNum"] == "5202"
            return httpx.Response(200, json={"code": 0, "data": {"server": server}})
        assert request.url.path == UPDATE
        assert request.headers["X-CSRFToken"] == "fixture-csrf"
        assert parse_qs(request.content.decode(), keep_blank_values=True) == {
            "HostNumList": ["5202"], "ServerNameList": ["5202_46_fixture"], "season": ["10"], "scenario": ["16"],
        }
        server.update(season="10", scenario="16")
        return httpx.Response(200, json={"ret": "SUCCESS"})

    with httpx.Client(base_url="https://gmt.example.test", transport=httpx.MockTransport(handle)) as http:
        result = GmtClient(http).update_tab(ServerTab(5202, 46, 9, 17, "fixture"), 10, 16)
    assert result == ServerTab(5202, 46, 10, 16, "fixture")
    assert requests == [("GET", PAGE), ("GET", QUERY), ("POST", UPDATE), ("GET", QUERY)]


@pytest.mark.parametrize("has_csrf", [False, True])
def test_write_is_blocked_by_missing_csrf_or_changed_snapshot(has_csrf):
    def handle(request):
        assert request.method == "GET"
        if request.url.path == PAGE:
            meta = '<meta name="csrf-token" content="fixture-csrf">' if has_csrf else ""
            return httpx.Response(200, text=meta)
        return httpx.Response(200, json={"code": 0, "data": {"server": {
            "HostNum": 5202, "ServerId": 46, "ServerType": 1, "Name": "fixture", "season": "8", "scenario": "17",
        }}})

    with httpx.Client(base_url="https://gmt.example.test", transport=httpx.MockTransport(handle)) as http:
        error = "记录已变化" if has_csrf else "缺少提交校验字段"
        with pytest.raises(GmtError, match=error):
            GmtClient(http).update_tab(ServerTab(5202, 46, 9, 17, "fixture"), 10, 16)


def test_http_200_can_contain_a_gmt_rejection():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == PAGE:
            return httpx.Response(200, text='<meta name="csrf-token" content="fixture-csrf">渠道:DEV')
        if request.url.path == QUERY:
            return httpx.Response(200, json={"code": 0, "data": {"server": {
                "HostNum": 5202, "ServerId": 46, "ServerType": 1, "Name": "fixture", "season": "9", "scenario": "17",
            }}})
        assert request.url.path == UPDATE
        return httpx.Response(200, text="ERROR not same ServerNameList:")

    with httpx.Client(base_url="https://gmt.example.test", transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(GmtError, match="未返回修改成功"):
            GmtClient(http).update_tab(ServerTab(5202, 46, 9, 17, "fixture"), 10, 16)
    assert paths == [PAGE, QUERY, UPDATE]


@pytest.mark.asyncio
@pytest.mark.parametrize(("region", "host_num"), [("cn", 2003), ("overseas", 5202)])
@pytest.mark.parametrize("outcome", ["verified", "changed", "unknown"])
async def test_gmt_bot_previews_requires_requester_confirmation_and_verifies_write(tmp_path, outcome, region, host_num):
    class Feishu(FakeFeishuClient):
        async def reply_card(self, message_id, card):
            self.replies.append((message_id, card))
            return "om_card"

    class Planner:
        async def create_plan(self, text):
            return MuliuPlan(f"修改 {host_num} GMT 选服页签", [
                MuliuStep(GMT_TAB_PATH, ["preview", str(host_num), "10", "16"], "读取 GMT 当前值"),
            ])

    paths = []
    server = {"HostNum": host_num, "ServerId": 46, "ServerType": 1, "Name": "fixture", "season": "9", "scenario": "17"}

    def handle(request):
        assert request.url.host == f"{region}.gmt.example.test"
        paths.append(request.url.path)
        if request.url.path == "/login":
            return httpx.Response(200)
        if request.url.path == "/api/login":
            assert json.loads(request.content)["username"] == region
            return httpx.Response(200, json={"code": 0})
        if request.url.path == PAGE:
            return httpx.Response(200, text='<meta name="csrf-token" content="fixture">')
        if request.url.path == QUERY:
            return httpx.Response(200, json={"code": 0, "data": {"server": server}})
        assert request.url.path == UPDATE
        if outcome == "unknown":
            raise httpx.ReadTimeout("no receipt", request=request)
        server.update(season="10", scenario="16")
        return httpx.Response(200, json={"ret": "SUCCESS"})

    catalog = MuliuScriptCatalog(CONFIG / "muliu_script_catalog.md")
    contracts = catalog.read_call_contracts()
    config = replace(make_config(), gmt_origins={
        "cn": "https://cn.gmt.example.test", "overseas": "https://overseas.gmt.example.test",
    })
    firewall = MuliuFirewall(config.muliu, contracts)
    resolver = MuliuPlanRouteResolver(str(CONFIG / "server_environments.json"), contracts)
    gateway = RecordingGatewayClient()
    worker = MuliuWorker(config, firewall, gateway, resolver, call_contracts=contracts)
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_req", "event", "oc_group", "ou_requester", f"{host_num} 选服页签改到赛季 10 剧本 16", chat_type="group")
    feishu = Feishu()
    processor = MessageJobProcessor(
        replace(config, runtime=RuntimeConfig(role="gateway")), store, ConversationStore(4), FakeAIClient(), feishu,
        operation_planner=Planner(), muliu_firewall=firewall, route_resolver=resolver, script_catalog=catalog,
    )
    with httpx.Client(base_url=config.gmt_origins[region], transport=httpx.MockTransport(handle)) as http:
        worker._gmt_clients[region] = GmtClient(http)
        await processor.process_one("om_req")
        job = store.claim_next_worker_job("test-worker")
        assert job and job.route_region == region and not feishu.replies
        await worker._execute_claimed_job(ClaimedWorkerJob(job.message_id, job.plan_json, job.route_region))
        preview_result = gateway.results[-1][1]
        assert preview_result.succeeded and UPDATE not in paths
        done = store.complete_worker_job("om_req", "test-worker", status="done")
        from src.muliu_plan import parse_plan
        await processor.present_gmt_preview(done, parse_plan(done.plan_json), preview_result.step_results[0].log)
        pending = store.get("om_req")
        assert pending.status == "awaiting_confirmation"
        card = json.dumps(feishu.replies[0][1], ensure_ascii=False)
        assert "赛季 9 → 10" in card and "剧本 17 → 16" in card
        assert store.claim_next_worker_job("test-worker") is None
        assert store.claim_card_confirmation("om_req", "om_card", "oc_group", "ou_other", pending.confirmation_token, 600) is None
        assert store.claim_card_confirmation("om_req", "om_card", "oc_group", "ou_requester", pending.confirmation_token, 600)
        assert store.queue_for_worker("om_req")
        confirmed = store.claim_next_worker_job("test-worker")
        if outcome == "changed":
            server["scenario"] = "15"
        await worker._execute_claimed_job(ClaimedWorkerJob(confirmed.message_id, confirmed.plan_json, confirmed.route_region))
    result = gateway.results[-1][1]
    assert paths.count("/api/login") == 1
    assert paths.count(UPDATE) == (0 if outcome == "changed" else 1)
    assert result.succeeded == (outcome == "verified")
    assert result.result_unknown == (outcome == "unknown")
    if outcome == "verified":
        assert "赛季 10，剧本 16" in result.step_results[0].log


def test_gmt_tab_routes_only_to_registered_test_servers():
    contracts = MuliuScriptCatalog(CONFIG / "muliu_script_catalog.md").read_call_contracts()
    resolver = MuliuPlanRouteResolver(str(CONFIG / "server_environments.json"), contracts)
    overseas = MuliuStep(GMT_TAB_PATH, ["preview", "5202", "10", "16"], "预检")
    cn = replace(overseas, args=["preview", "2003", "10", "16"])
    assert resolver.resolve(MuliuPlan("GMT", [overseas])).region == "overseas"
    assert resolver.resolve(MuliuPlan("GMT", [cn])).region == "cn"
    for steps in ([replace(cn, args=["preview", "99999999", "10", "16"])], [cn, overseas]):
        with pytest.raises(MuliuRoutingError):
            resolver.resolve(MuliuPlan("GMT", steps))
