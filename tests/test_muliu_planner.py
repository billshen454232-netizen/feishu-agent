import json

import pytest

from src.muliu_plan import MuliuPlanError
from src.muliu_planner import MuliuPlanGenerationError, MuliuPlanner
from src.muliu_script_catalog import parse_call_contracts


CONTRACT_CATALOG = """
# Muliu 测试服执行调用合同

<!-- MULIU_CALL_CONTRACTS
{
  "max_plan_steps": 10,
  "contracts": [
    {
      "name": "basic-server-info",
      "path": "/home/serverGeneralScript/basic_info.sh",
      "args": ["{server_id}"],
      "variables": {"server_id": "[0-9]{3,8}"},
      "runner": "bash",
      "risk": "read"
    },
    {
      "name": "single-server-patch-list-check",
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": ["-s", "{server_id}", "-ck"],
      "variables": {"server_id": "[0-9]{3,8}"},
      "runner": "python3.7",
      "risk": "read"
    }
  ]
}
MULIU_CALL_CONTRACTS -->
"""


class FakeCatalog:
    def read(self):
        return CONTRACT_CATALOG

    def read_call_contracts(self):
        return parse_call_contracts(CONTRACT_CATALOG)


class FakeAIClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    async def generate(self, history, prompt):
        self.prompts.append((history, prompt))
        if self.responses:
            return self.responses.pop(0)
        marker = "候选决策：\n---\n"
        candidate_start = prompt.index(marker) + len(marker)
        candidate_end = prompt.index("\n---", candidate_start)
        return prompt[candidate_start:candidate_end]


VALID_PATCH_PLAN = """
{
  "kind": "operation",
  "summary": "检查 6001 服补丁状态",
  "steps": [
    {
      "path": "/home/serverGeneralScript/cc_patch.py",
      "args": ["-s", "6001", "-ck"],
      "description": "检查 6001 服补丁状态"
    }
  ]
}
"""


@pytest.mark.asyncio
async def test_normalizes_single_step_object_without_another_model_call():
    ai = FakeAIClient([
        """
        {
          "kind": "operation",
  "summary": "检查 6001 服补丁状态",
          "steps": {
            "path": "/home/serverGeneralScript/cc_patch.py",
            "args": ["-s", "6001", "-ck"],
            "description": "检查 6001 服补丁状态"
          }
        }
        """
    ])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=10)

    plan = await planner.create_plan("查询 6001 服 Patch 列表")

    assert len(ai.prompts) == 2
    assert "独立语义复核器" in ai.prompts[1][1]
    assert plan.steps[0].path == "/home/serverGeneralScript/cc_patch.py"
    assert plan.steps[0].args == ["-s", "6001", "-ck"]
    assert plan.summary == "检查 6001 服补丁状态"
    assert plan.kind.value == "operation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_text",
    [
        "查询6001服务器的patch列表",
        "查看6001服补丁列表",
        "检查6001服Patch状态",
    ],
)
async def test_catalog_prompt_covers_every_registered_patch_query_phrase(user_text):
    ai = FakeAIClient([VALID_PATCH_PLAN])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=10)

    plan = await planner.create_plan(user_text)

    assert plan.steps[0].path == "/home/serverGeneralScript/cc_patch.py"
    assert plan.steps[0].args == ["-s", "6001", "-ck"]
    prompt = ai.prompts[0][1]
    assert "闭合白名单" in prompt
    assert "不能误判为“资料没有查询能力”" in prompt
    assert user_text in prompt
    assert len(ai.prompts) == 2
    assert "独立语义复核器" in ai.prompts[1][1]


@pytest.mark.asyncio
async def test_catalog_supports_registered_basic_info_shape():
    ai = FakeAIClient([
        """
        {
          "kind": "operation",
          "summary": "查询 6001 服基础信息",
          "steps": [
            {
              "path": "/home/serverGeneralScript/basic_info.sh",
              "args": ["6001"],
              "description": "查询 6001 服基础信息"
            }
          ]
        }
        """
    ])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=10)

    plan = await planner.create_plan("查询 6001 服基础信息")

    assert plan.steps[0].path == "/home/serverGeneralScript/basic_info.sh"
    assert plan.steps[0].args == ["6001"]
    assert len(ai.prompts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_text", "path", "args"),
    [
        ("正常关闭 5000 服", "/home/serverGeneralScript/shutdown", ["5000"]),
        ("强制关闭 5000 服", "/home/serverGeneralScript/shutdown", ["5000", "kill"]),
        ("启动 5000 服", "/home/serverGeneralScript/start", ["5000"]),
        ("只启动 5000 服游戏服务", "/home/serverGeneralScript/start", ["5000", "noz"]),
        ("清档 5000 服", "/home/serverGeneralScript/clear", ["5000"]),
        ("把 5000 服改为赛季 3、剧本 2003", "/home/serverGeneralScript/modify_game_config.sh", ["5000", "3", "2003"]),
        ("设置 5000 服开服时间戳 1790820000", "/home/serverGeneralScript/starttime", ["5000", "1790820000", "--backup", "yes"]),
        ("检查 5000 服起服失败原因", "/home/serverGeneralScript/server_log_query.py", ["--server-id", "5000", "--profile", "startup_errors", "--max-lines", "50"]),
        ("热更 5000 服 Patch", "/home/serverGeneralScript/cc_patch.py", ["-s", "5000", "-u"]),
    ],
)
async def test_production_catalog_accepts_registered_operation_plans(user_text, path, args):
    from pathlib import Path

    catalog_path = Path(__file__).parents[1] / "config" / "muliu_script_catalog.md"

    class ProductionCatalog:
        def read(self):
            return catalog_path.read_text(encoding="utf-8")

        def read_call_contracts(self):
            return parse_call_contracts(self.read())

    response = json.dumps({
        "kind": "operation",
        "summary": user_text,
        "steps": [{"path": path, "args": args, "description": user_text}],
    }, ensure_ascii=False)
    ai = FakeAIClient([response])
    planner = MuliuPlanner(ai, ProductionCatalog(), max_steps=1)

    plan = await planner.create_plan(user_text)

    assert plan.steps[0].path == path
    assert plan.steps[0].args == args
    assert user_text in ai.prompts[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_text",
    [
        "6000服现在是什么剧本？",
        "6000服当前剧本是什么？",
        "查看 6000 服当前剧本",
        "查询 6000 服剧本配置",
    ],
)
async def test_targeted_live_scenario_query_uses_basic_info_contract(user_text):
    ai = FakeAIClient([
        """
        {
          "kind": "operation",
          "summary": "查询 6000 服当前剧本",
          "steps": [
            {
              "path": "/home/serverGeneralScript/basic_info.sh",
              "args": ["6000"],
              "description": "查询 6000 服当前剧本配置"
            }
          ]
        }
        """
    ])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=10)

    plan = await planner.create_plan(user_text)

    assert plan.steps[0].path == "/home/serverGeneralScript/basic_info.sh"
    assert plan.steps[0].args == ["6000"]
    assert user_text in ai.prompts[0][1]


@pytest.mark.asyncio
async def test_production_catalog_accepts_ordered_clear_then_config_plan():
    from pathlib import Path

    catalog_path = Path(__file__).parents[1] / "config" / "muliu_script_catalog.md"

    class ProductionCatalog:
        def read(self):
            return catalog_path.read_text(encoding="utf-8")

        def read_call_contracts(self):
            return parse_call_contracts(self.read())

    plan_json = json.dumps({
        "kind": "operation",
        "summary": "清档 5000 服并改为赛季 10、剧本 5",
        "steps": [
            {
                "path": "/home/serverGeneralScript/clear",
                "args": ["5000"],
                "description": "清档 5000 服",
            },
            {
                "path": "/home/serverGeneralScript/modify_game_config.sh",
                "args": ["5000", "10", "5"],
                "description": "将 5000 服改为赛季 10、剧本 5",
            },
        ],
    }, ensure_ascii=False)
    ai = FakeAIClient([plan_json])
    planner = MuliuPlanner(ai, ProductionCatalog(), max_steps=10)

    plan = await planner.create_plan("5000 服清档，然后改为赛季 10、剧本 5")

    assert [(step.path, step.args) for step in plan.steps] == [
        ("/home/serverGeneralScript/clear", ["5000"]),
        ("/home/serverGeneralScript/modify_game_config.sh", ["5000", "10", "5"]),
    ]
    assert "可拆成多个顺序步骤" in ai.prompts[0][1]
    assert "可以把用户明确要求的多个已登记动作拆成按顺序执行的多个步骤" in ai.prompts[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_plan",
    [
        """{
          "kind":"operation",
          "summary":"查询时间",
          "steps":[{
            "path":"/home/serverGeneralScript/basic_info.sh",
            "args":["6001","ctime"],
            "description":"查询当前时间"
          }]
        }""",
        """{
          "kind":"operation",
          "summary":"对比补丁",
          "steps":[{
            "path":"/home/serverGeneralScript/cc_patch.py",
            "args":["-s","6001","6002","-ck"],
            "description":"对比 Patch"
          }]
        }""",
        """{
          "kind":"operation",
          "summary":"修改补丁",
          "steps":[{
            "path":"/home/serverGeneralScript/cc_patch.py",
            "args":["-s","6001","-ck","-m"],
            "description":"删除缺失条目"
          }]
        }""",
    ],
)
async def test_unregistered_branches_remain_rejected_after_one_repair_attempt(invalid_plan):
    ai = FakeAIClient([invalid_plan, invalid_plan])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=10)

    with pytest.raises(MuliuPlanGenerationError, match="不符合任何已登记调用合同") as captured:
        await planner.create_plan_with_trace("未登记请求")

    assert captured.value.repair_attempts == 1
    assert len(ai.prompts) == 2
    assert "受限修复" in ai.prompts[1][1]


@pytest.mark.asyncio
async def test_semantic_repair_separates_environment_label_from_server_id():
    invalid_plan = json.dumps({
        "kind": "operation",
        "summary": "关闭 HMT5000 服",
        "steps": [{
            "path": "/home/serverGeneralScript/shutdown",
            "args": ["HMT5000"],
            "description": "关闭 HMT5000 服",
        }],
    }, ensure_ascii=False)
    repaired_plan = json.dumps({
        "kind": "operation",
        "summary": "关闭 HMT 环境 5000 服",
        "steps": [{
            "path": "/home/serverGeneralScript/shutdown",
            "args": ["5000"],
            "description": "关闭 HMT 环境 5000 服 game 与 Zone 服务",
        }],
    }, ensure_ascii=False)

    class EnvironmentCatalog:
        def read(self):
            return """<!-- MULIU_CALL_CONTRACTS
            {
              "max_plan_steps": 1,
              "contracts": [{
                "name": "shutdown-normal",
                "path": "/home/serverGeneralScript/shutdown",
                "args": ["{server_id}"],
                "variables": {"server_id": "[0-9]{3,8}"},
                "runner": "bash",
                "risk": "write"
              }]
            }
            MULIU_CALL_CONTRACTS -->"""

        def read_call_contracts(self):
            return parse_call_contracts(self.read())

    ai = FakeAIClient([invalid_plan, repaired_plan])
    planner = MuliuPlanner(ai, EnvironmentCatalog(), max_steps=1)

    generation = await planner.create_plan_with_trace("HMT5000服务器改成赛季9剧本")

    assert generation.plan.steps[0].args == ["5000"]
    assert generation.repair_attempts == 1
    repair_prompt = ai.prompts[1][1]
    assert "HMT5000" in repair_prompt
    assert "HMT` 视为环境描述，将尾部数字 `5000` 作为服务器编号" in repair_prompt
    assert len(ai.prompts) == 3
    assert "独立语义复核器" in ai.prompts[2][1]


@pytest.mark.asyncio
async def test_semantic_review_replaces_legal_but_inadequate_basic_info_plan():
    candidate_plan = json.dumps({
        "kind": "operation",
        "summary": "检查 5000 服起服失败原因",
        "steps": [{
            "path": "/home/serverGeneralScript/basic_info.sh",
            "args": ["5000"],
            "description": "查询 5000 服基础信息",
        }],
    }, ensure_ascii=False)
    reviewed_plan = json.dumps({
        "kind": "operation",
        "summary": "检查 5000 服起服失败原因",
        "steps": [{
            "path": "/home/serverGeneralScript/server_log_query.py",
            "args": ["--server-id", "5000", "--profile", "startup_errors", "--max-lines", "50"],
            "description": "读取 5000 服固定启动日志中的近期异常信号",
        }],
    }, ensure_ascii=False)

    class DiagnosticCatalog:
        def read(self):
            return """<!-- MULIU_CALL_CONTRACTS
            {
              "max_plan_steps": 1,
              "contracts": [
                {
                  "name": "basic-server-info",
                  "path": "/home/serverGeneralScript/basic_info.sh",
                  "args": ["{server_id}"],
                  "variables": {"server_id": "[0-9]{3,8}"},
                  "runner": "bash",
                  "risk": "read"
                },
                {
                  "name": "startup-failure-diagnosis",
                  "path": "/home/serverGeneralScript/server_log_query.py",
                  "args": ["--server-id", "{server_id}", "--profile", "startup_errors", "--max-lines", "50"],
                  "variables": {"server_id": "[0-9]{3,8}"},
                  "runner": "python3.7",
                  "risk": "read"
                }
              ]
            }
            MULIU_CALL_CONTRACTS -->"""

        def read_call_contracts(self):
            return parse_call_contracts(self.read())

    ai = FakeAIClient([candidate_plan, reviewed_plan])
    planner = MuliuPlanner(ai, DiagnosticCatalog(), max_steps=1)

    plan = await planner.create_plan("检查 5000 服起服失败原因")

    assert plan.steps[0].path == "/home/serverGeneralScript/server_log_query.py"
    assert plan.steps[0].args == [
        "--server-id", "5000", "--profile", "startup_errors", "--max-lines", "50",
    ]
    assert len(ai.prompts) == 2
    assert "候选决策已经通过 JSON、路径与参数合同校验；这不代表它在业务语义上正确" in ai.prompts[1][1]
    assert "/home/serverGeneralScript/basic_info.sh" in ai.prompts[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_text", "kind", "summary"),
    [
        ("Patch 查询支持什么参数？", "knowledge", "当前已登记 Patch 查询参数。"),
        ("启动测试服", "clarify", "需要补充合法服务器编号。"),
        ("重启 5000 服", "reject", "当前未登记重启组合操作。"),
    ],
)
async def test_accepts_non_operation_registry_decisions(user_text, kind, summary):
    response = json.dumps({"kind": kind, "summary": summary, "steps": []}, ensure_ascii=False)
    ai = FakeAIClient([response])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=1)

    plan = await planner.create_plan(user_text)

    assert plan.kind.value == kind
    assert plan.summary == summary
    assert plan.steps == []
    assert len(ai.prompts) == 2


@pytest.mark.asyncio
async def test_new_model_response_without_kind_is_repaired_before_semantic_review():
    legacy_response = json.dumps({
        "summary": "查询 6001 服基础信息",
        "steps": [{
            "path": "/home/serverGeneralScript/basic_info.sh",
            "args": ["6001"],
            "description": "查询 6001 服基础信息",
        }],
    }, ensure_ascii=False)
    repaired_response = json.dumps({
        "kind": "operation",
        "summary": "查询 6001 服基础信息",
        "steps": [{
            "path": "/home/serverGeneralScript/basic_info.sh",
            "args": ["6001"],
            "description": "查询 6001 服基础信息",
        }],
    }, ensure_ascii=False)
    ai = FakeAIClient([legacy_response, repaired_response, repaired_response])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=1)

    generation = await planner.create_plan_with_trace("查询 6001 服基础信息")

    assert generation.plan.kind.value == "operation"
    assert generation.response_stages == ("initial", "initial_repair", "semantic_review")
    assert generation.validation_errors == ("kind 必须是字符串", None, None)
    assert len(ai.prompts) == 3


@pytest.mark.asyncio
async def test_retries_once_with_schema_error_then_accepts_repaired_plan():
    ai = FakeAIClient([
        '{"kind":"operation","summary":"检查 6001 服补丁状态","steps":"cc_patch.py -s 6001 -ck"}',
        VALID_PATCH_PLAN,
        VALID_PATCH_PLAN,
    ])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=10)

    generation = await planner.create_plan_with_trace("查询 6001 服 Patch 列表")

    assert generation.plan.summary == "检查 6001 服补丁状态"
    assert generation.validation_errors == ("steps 必须是数组", None, None)
    assert generation.response_stages == ("initial", "initial_repair", "semantic_review")
    assert generation.repair_attempts == 1
    assert len(ai.prompts) == 3
    assert "这次校验错误：\nsteps 必须是数组" in ai.prompts[1][1]
    assert "不合规响应" in ai.prompts[1][1]


@pytest.mark.asyncio
async def test_repair_failure_remains_rejected_without_a_plan():
    ai = FakeAIClient([
        '{"kind":"operation","summary":"检查 6001 服补丁状态","steps":"not-an-array"}',
        '{"kind":"operation","summary":"仍然错误","steps":"still-not-an-array"}',
    ])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=10)

    with pytest.raises(MuliuPlanGenerationError, match="已自动纠正 1 次仍失败") as captured:
        await planner.create_plan_with_trace("查询 6001 服 Patch 列表")

    assert captured.value.raw_responses == (
        '{"kind":"operation","summary":"检查 6001 服补丁状态","steps":"not-an-array"}',
        '{"kind":"operation","summary":"仍然错误","steps":"still-not-an-array"}',
    )
    assert captured.value.validation_errors == ("steps 必须是数组", "steps 必须是数组")
    assert captured.value.repair_attempts == 1
    assert len(ai.prompts) == 2


@pytest.mark.asyncio
async def test_semantic_review_repair_records_distinct_stage():
    invalid_review = '{"kind":"operation","summary":"检查","steps":"bad"}'
    ai = FakeAIClient([
        VALID_PATCH_PLAN,
        invalid_review,
        VALID_PATCH_PLAN,
    ])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=1)

    generation = await planner.create_plan_with_trace("查询 6001 服 Patch 列表")

    assert generation.response_stages == (
        "initial",
        "semantic_review",
        "semantic_review_repair",
    )
    assert generation.validation_errors == (None, "steps 必须是数组", None)
    assert generation.repair_attempts == 1
    assert len(ai.prompts) == 3


@pytest.mark.asyncio
async def test_repair_does_not_accept_unknown_step_fields():
    invalid_with_unknown_field = """
    {
      "kind": "operation",
      "summary": "检查",
      "steps": [
        {
          "path": "/home/serverGeneralScript/cc_patch.py",
          "args": ["-s", "6001", "-ck"],
          "description": "检查",
          "command": "ignored"
        }
      ]
    }
    """
    ai = FakeAIClient([
        '{"kind":"operation","summary":"检查","steps":"bad"}',
        invalid_with_unknown_field,
        invalid_with_unknown_field,
    ])
    planner = MuliuPlanner(ai, FakeCatalog(), max_steps=10)

    with pytest.raises(MuliuPlanError, match="不允许的字段"):
        await planner.create_plan("查询 6001 服 Patch 列表")
