import hashlib
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from src.config import RuntimeConfig
from src.conversation import ConversationStore
from src.message_jobs import MessageJobStore
from src.gvg_connection import (
    GVG_CONFIG_PATH, GVG_QUERY_PATH, GvgChangeStore, GvgChangeUnknown, GvgConfigFile, GvgQueryError,
    WeeklyGvgChange, WeeklyGvgScope, change_id, configured_zone, list_weekly_gvg,
)
from src.muliu_firewall import MuliuFirewall
from src.gvg_topology import GVG_TOPOLOGY_PATH
from src.muliu_plan import MuliuPlan, MuliuStep
from src.muliu_routing import MuliuPlanRouteResolver
from src.muliu_script_catalog import MuliuScriptCatalog
from src.muliu_worker import ClaimedWorkerJob, MuliuWorker
from src.message_worker import MessageJobProcessor, _plan_to_json
from src.server_manager import ServerManagerClient
from tests.test_message_worker import FakeAIClient, FakeFeishuClient
from tests.test_muliu_worker import RecordingExecutor, RecordingGatewayClient, make_config

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.mark.parametrize(("content", "expected"), [
    ("NSLG_GVGZONENUM = 4 -- Weekly\n", "4"),
    ("NSLG_GVGZONENUM = nil\r\n", "nil"),
    ("-- NSLG_GVGZONENUM = 4\n", "已注释"),
    ("--[=[\nNSLG_GVGZONENUM = 9\n]=]\nNSLG_GVGZONENUM = 4\n", "4"),
    ("local example = [[\nNSLG_GVGZONENUM = 4\n]]\n", "未配置"),
    ("NSLG_GVGZONENUM = \"4\"\n", "未知"),
    ("NSLG_GVGZONENUM = 4\nNSLG_GVGZONENUM = nil\n", "未知"),
    ("NSLG_GVGZONENUM = compute_zone()\n", "未知"),
    ("NSLG_ZONENUM = 4\n", "未配置"),
])
def test_configured_zone_only_reports_an_unambiguous_literal(content, expected):
    assert configured_zone(content) == expected


def _response(target, value, **overrides):
    item = {
        "game_id": target, "primary": True, "role": "game", "status": "succeeded",
        "result": {"files": [{"path": GVG_CONFIG_PATH, "content": f"NSLG_GVGZONENUM = {value}\n"}]},
    }
    return {"items": [{**item, **overrides}]}


def test_server_manager_writes_with_original_digest_and_polls_without_replay():
    calls = []
    original = "NSLG_GVGZONENUM = 4\n"
    updated = "NSLG_GVGZONENUM = nil\n"
    digest = hashlib.sha256(original.encode()).hexdigest()

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.url.path.endswith("/server-config-file")
            assert json.loads(request.content) == {
                "env": "qa_test", "target": "game_2016", "file": {
                    "machine_id": "fixture", "path": GVG_CONFIG_PATH, "content": updated, "sha256": digest,
                },
            }
            return httpx.Response(202, json={"run_id": "fixture-1"})
        raise httpx.ReadTimeout("fixture timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = ServerManagerClient("https://api.example.invalid", "qa_test", http_client=http)
        with pytest.raises(GvgChangeUnknown, match="结果无法核实"):
            client.write_gvg_config(GvgConfigFile("2016", "fixture", original, digest), updated)
    assert calls == [
        ("POST", "/server_manager/api/server-config-file"),
        ("GET", "/server_manager/api/workflows/runs/fixture-1"),
    ]


def test_read_failure_is_distinct_from_nil():
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json=_response("game_2016", "nil", role="zone"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = ServerManagerClient("https://api.example.invalid", "qa_test", http_client=http)
        with pytest.raises(GvgQueryError, match="2016"):
            client.read_gvg_config("2016")
    scope = WeeklyGvgScope(4, ("2003", "2016"))

    def partial(server):
        if server == "2016":
            raise GvgQueryError("无法读取")
        return "NSLG_GVGZONENUM = nil"

    report = list_weekly_gvg(scope, partial)
    assert "2016 服：未知" in report and "2003 服：nil" in report and "已核实 1 台" in report


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["weeklygvg", "devgvg", "qagvg"])
async def test_worker_dispatches_gvg_list_through_muliu(monkeypatch, scope):
    class Executor(RecordingExecutor):
        instances = []

    monkeypatch.setattr("src.muliu_worker.MuliuExecutor", Executor)
    assets = CONFIG / "server_environments.json"
    contracts = MuliuScriptCatalog(CONFIG / "muliu_script_catalog.md").read_call_contracts()
    config = make_config()
    gateway = RecordingGatewayClient()
    resolver = MuliuPlanRouteResolver(str(assets), contracts)
    worker = MuliuWorker(config, MuliuFirewall(config.muliu, contracts), gateway, resolver, call_contracts=contracts)
    plan = MuliuPlan("列出国服 GVG 依赖", [MuliuStep(GVG_TOPOLOGY_PATH, ["list", scope], "扫描运行拓扑")], route_scope="cn")

    await worker._execute_claimed_job(ClaimedWorkerJob("om_gvg", _plan_to_json(plan), "cn"))

    assert len(Executor.instances) == 1 and Executor.instances[0].plans == [plan]
    message_id, result = gateway.results[0]
    assert message_id == "om_gvg" and result.succeeded


@pytest.mark.asyncio
async def test_gateway_queues_gvg_read_without_an_operation_confirmation(tmp_path):
    class Planner:
        async def create_plan(self, text):
            assert text == "列出国服 Weekly GVG 连接了哪些游戏服"
            return MuliuPlan(
                "核对国服 Weekly GVG 连接配置",
                [MuliuStep(GVG_TOPOLOGY_PATH, ["list", "weeklygvg"], "扫描运行拓扑")],
                route_scope="cn",
            )

    catalog = MuliuScriptCatalog(CONFIG / "muliu_script_catalog.md")
    contracts = catalog.read_call_contracts()
    config = replace(make_config(), runtime=RuntimeConfig(role="gateway"))
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    store.create_if_new("om_query", "event", "oc_group", "ou_requester",
                        "列出国服 Weekly GVG 连接了哪些游戏服", chat_type="group")
    feishu = FakeFeishuClient()
    processor = MessageJobProcessor(
        config, store, ConversationStore(4), FakeAIClient(), feishu,
        operation_planner=Planner(), muliu_firewall=MuliuFirewall(config.muliu, contracts),
        route_resolver=MuliuPlanRouteResolver(str(CONFIG / "server_environments.json"), contracts),
        script_catalog=catalog,
    )
    await processor.process_one("om_query")

    job = store.claim_next_worker_job("test-worker")
    assert job is not None and job.message_id == "om_query"
    assert job.route_region == "cn" and not job.confirmation_message_id and not feishu.replies


class FakeGvgManager:
    def __init__(self):
        self.values = {server: "4" for server in ("2005", "2010", "2015", "2016", "2020")}
        self.values["2003"] = "nil"
        self.running = set()
        self.edited = set()
        self.writes = []
        self.unknown_on = None

    def gvg_config_file(self, server_id):
        content = f"NSLG_HOSTNUM = {server_id}\nNSLG_GVGZONENUM = {self.values[server_id]} -- keep\n"
        if server_id in self.edited:
            content += "NSLG_SEASON = 2\n"
        return GvgConfigFile(server_id, f"machine-{server_id}", content, hashlib.sha256(content.encode()).hexdigest())

    def gvg_game_stopped(self, server_id, message_id, phase):
        return server_id not in self.running

    def write_gvg_config(self, config, content):
        server = config.server_id
        assert self.gvg_config_file(server).sha256 == config.sha256
        assert server not in self.running
        self.writes.append(server)
        if self.unknown_on == server:
            raise GvgChangeUnknown("结果未知")
        self.values[server] = configured_zone(content)
        assert content == self.gvg_config_file(server).content


def _change_fixture(tmp_path):
    manager = FakeGvgManager()
    store = GvgChangeStore(tmp_path / "gateway-worker.sqlite3")
    scope = WeeklyGvgScope(4, tuple(sorted(manager.values)))
    return manager, store, scope


@pytest.mark.parametrize("drift", ["file", "running", "membership"])
def test_confirmed_gvg_change_refuses_drift_before_any_write(tmp_path, drift):
    manager, store, scope = _change_fixture(tmp_path)
    change = WeeklyGvgChange(scope, store, manager, "om_keep")
    preview = json.loads(change.preview("preview-keep", "2016"))
    assert preview["targets"] == ["2005", "2010", "2015", "2020"]
    if drift == "file":
        manager.edited.add("2010")
    elif drift == "running":
        manager.running.add("2010")
    else:
        manager.values["2003"] = "4"
    with pytest.raises(GvgQueryError):
        change.apply(preview["id"], preview["targets"])
    assert manager.writes == []


def test_gvg_change_restores_only_successful_writes(tmp_path):
    manager, store, scope = _change_fixture(tmp_path)
    change = WeeklyGvgChange(scope, store, manager, "om_keep")
    preview = json.loads(change.preview("preview-keep", "2016"))
    assert not manager.writes
    with pytest.raises(GvgQueryError, match="预检范围不一致"):
        change.apply(preview["id"], preview["targets"] + ["2016"])
    report, reason, unknown = change.apply(preview["id"], preview["targets"])
    assert not reason and not unknown and "2020 服 4 → nil" in report
    assert manager.values["2016"] == "4" and manager.values["2003"] == "nil"
    assert manager.writes == preview["targets"]
    with pytest.raises(GvgQueryError):
        change.apply(preview["id"], preview["targets"])

    restore = WeeklyGvgChange(scope, store, manager, "om_restore")
    recovery = json.loads(restore.preview("preview-restore", preview["id"]))
    assert recovery["targets"] == preview["targets"]
    _, reason, unknown = restore.apply(recovery["id"], recovery["targets"])
    assert not reason and not unknown and manager.values["2003"] == "nil"
    assert manager.writes == preview["targets"] * 2
    assert all(item["state"] == "restored" for item in store.get(preview["id"])["items"].values())


def test_gvg_change_stops_after_one_unknown_submission(tmp_path):
    manager, store, scope = _change_fixture(tmp_path)
    manager.unknown_on = "2010"
    change = WeeklyGvgChange(scope, store, manager, "om_keep")
    preview = json.loads(change.preview("preview-keep", "2016"))
    report, _, unknown = change.apply(preview["id"], preview["targets"])
    assert unknown and "2005 服 4 → nil" in report and "待处理服号：2015、2020" in report
    assert manager.writes == ["2005", "2010"]
    assert store.get(preview["id"])["items"]["2010"]["state"] == "unknown"
    with pytest.raises(GvgQueryError, match="结果待核对"):
        WeeklyGvgChange(scope, store, manager, "om_restore").preview("preview-restore", preview["id"])


@pytest.mark.asyncio
async def test_gateway_gvg_preview_freezes_targets_in_confirmation(tmp_path):
    class Feishu(FakeFeishuClient):
        async def reply_card(self, message_id, card):
            self.replies.append((message_id, card))
            return "om_confirmation"

    catalog = MuliuScriptCatalog(CONFIG / "muliu_script_catalog.md")
    contracts = catalog.read_call_contracts()
    config = replace(make_config(), runtime=RuntimeConfig(role="gateway"), execution_interpretation=replace(
        make_config().execution_interpretation, server_environments_path=str(CONFIG / "server_environments.json")))
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    message_id = "om_keep"
    store.create_if_new(message_id, "event", "oc_group", "ou_requester",
                        "国服 Weekly GVG 只保留 2016 的连接配置", chat_type="group")
    feishu = Feishu()
    processor = MessageJobProcessor(
        config, store, ConversationStore(4), FakeAIClient(), feishu,
        muliu_firewall=MuliuFirewall(config.muliu, contracts),
        route_resolver=MuliuPlanRouteResolver(str(CONFIG / "server_environments.json"), contracts),
        script_catalog=catalog,
    )
    await processor.process_one(message_id)
    job = store.claim_next_worker_job("test-worker")
    assert job and not feishu.replies
    from src.muliu_plan import parse_plan

    plan = parse_plan(job.plan_json)
    assert plan.steps[0].args == ["preview-keep", "weeklygvg", "2016"]
    targets = ["2005", "2010", "2015", "2020"]
    done = store.complete_worker_job(message_id, "test-worker", status="done")
    await processor.present_gvg_preview(done, plan, json.dumps({"id": change_id(message_id), "cluster": 4, "targets": targets}))
    pending = store.get(message_id)
    assert pending.status == "awaiting_confirmation" and pending.user_id == "ou_requester"
    assert parse_plan(pending.plan_json).steps[0].args == ["apply", "weeklygvg", change_id(message_id), ",".join(targets)]
    assert "2005、2010、2015、2020" in json.dumps(feishu.replies[0][1], ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "scope", "cluster"), [
    ("Weekly", "weeklygvg", "4"), ("DEV", "devgvg", "260005"), ("QA", "qagvg", "260006"),
])
async def test_gateway_topology_stop_requires_original_requester_confirmation(tmp_path, label, scope, cluster):
    from src.muliu_plan import parse_plan

    class Feishu(FakeFeishuClient):
        async def reply_card(self, message_id, card):
            self.replies.append((message_id, card))
            return "om_card"

    catalog = MuliuScriptCatalog(CONFIG / "muliu_script_catalog.md")
    contracts = catalog.read_call_contracts()
    config = replace(make_config(), runtime=RuntimeConfig(role="gateway"))
    store = MessageJobStore(tmp_path / "jobs.sqlite3")
    store.initialize()
    message_id = "om_gvg_stop"
    store.create_if_new(message_id, "event", "oc_group", "ou_requester", f"国服 {label} GVG 停服", chat_type="group")
    feishu = Feishu()
    processor = MessageJobProcessor(
        config, store, ConversationStore(4), FakeAIClient(), feishu,
        muliu_firewall=MuliuFirewall(config.muliu, contracts),
        route_resolver=MuliuPlanRouteResolver(str(CONFIG / "server_environments.json"), contracts),
        script_catalog=catalog,
    )
    await processor.process_one(message_id)
    job = store.claim_next_worker_job("test-worker")
    assert job and not feishu.replies
    plan = parse_plan(job.plan_json)
    assert plan.steps[0].args == ["preview-stop", scope]

    done = store.complete_worker_job(message_id, "test-worker", status="done")
    log = "GVG_TOPOLOGY_PREVIEW " + json.dumps({
        "scope": scope, "operation": "stop", "signature": "a" * 64,
        "actions": [[0, "2005", "game", 0], [2, cluster, "gvg_zone", 0]],
    })
    await processor.present_topology_preview(done, plan, log)
    pending = store.get(message_id)
    assert pending.status == "awaiting_confirmation" and pending.user_id == "ou_requester"
    assert parse_plan(pending.plan_json).steps[0].args == ["stop", scope, "a" * 64]
    assert "game 2005" in json.dumps(feishu.replies[0][1], ensure_ascii=False)
    assert store.claim_next_worker_job("test-worker") is None
