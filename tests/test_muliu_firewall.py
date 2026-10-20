import pytest

from src.config import MuliuConfig
from src.muliu_firewall import MuliuFirewall, MuliuFirewallError
from src.muliu_plan import MuliuPlan, MuliuPlanKind, MuliuStep
from src.muliu_script_catalog import parse_call_contracts


CONTRACT_CATALOG = """
<!-- MULIU_CALL_CONTRACTS
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
      "name": "start-server",
      "path": "/home/serverGeneralScript/start",
      "args": ["{server_id}"],
      "variables": {"server_id": "[0-9]{3,8}"},
      "runner": "bash",
      "risk": "write"
    }
  ]
}
MULIU_CALL_CONTRACTS -->
"""


def make_firewall(contract_catalog=CONTRACT_CATALOG, **overrides):
    config = MuliuConfig(
        script_root="/home/serverGeneralScript",
        blocked_keywords=("dangerous-action",),
        blocked_patterns=(r"--dangerous(?:=|$)",),
        **overrides,
    )
    return MuliuFirewall(config, call_contracts=parse_call_contracts(contract_catalog))


def one_contract_catalog(path, args, variables):
    return """
    <!-- MULIU_CALL_CONTRACTS
    {
      "max_plan_steps": 1,
      "contracts": [{
        "name": "test-contract",
        "path": %s,
        "args": %s,
        "variables": %s,
        "runner": "bash",
        "risk": "read"
      }]
    }
    MULIU_CALL_CONTRACTS -->
    """ % (__import__("json").dumps(path), __import__("json").dumps(args), __import__("json").dumps(variables))


def make_plan(
    path="/home/serverGeneralScript/basic_info.sh",
    args=None,
    *,
    kind=MuliuPlanKind.OPERATION,
):
    return MuliuPlan(
        summary="查询服务器信息",
        steps=[MuliuStep(path=path, args=args or ["6001"], description="查询 6001 服")],
        kind=kind,
    )


def test_accepts_registered_script_and_exact_argument_list():
    result = make_firewall().validate(make_plan())

    assert result.plan.steps[0].args == ["6001"]


def test_rejects_script_outside_configured_root():
    plan = make_plan(path="/tmp/tool.sh")
    firewall = make_firewall(
        one_contract_catalog("/tmp/tool.sh", ["{server_id}"], {"server_id": "[0-9]{3,8}"})
    )

    with pytest.raises(MuliuFirewallError, match="不在允许目录"):
        firewall.validate(plan)


def test_accepts_registered_extensionless_operation_script_through_contract():
    result = make_firewall().validate(
        make_plan(path="/home/serverGeneralScript/start", args=["6001"])
    )

    assert result.plan.steps[0].path == "/home/serverGeneralScript/start"


def test_rejects_unknown_extensionless_operation_script_through_contract():
    with pytest.raises(MuliuFirewallError, match="不符合任何已登记调用合同"):
        make_firewall().validate(make_plan(path="/home/serverGeneralScript/unregistered"))


def test_rejects_registered_path_with_unregistered_argument_shape():
    with pytest.raises(MuliuFirewallError, match="不符合任何已登记调用合同"):
        make_firewall().validate(make_plan(args=["6001", "ctime"]))


@pytest.mark.parametrize("kind", [MuliuPlanKind.KNOWLEDGE, MuliuPlanKind.CLARIFY, MuliuPlanKind.REJECT])
def test_rejects_non_operation_decision_before_any_execution_checks(kind):
    plan = MuliuPlan(summary="不执行", steps=[], kind=kind)

    with pytest.raises(MuliuFirewallError, match="只有 kind=operation"):
        make_firewall().validate(plan)


def test_rejects_shell_control_characters_in_argument():
    plan = make_plan(args=["6001;whoami"])
    firewall = make_firewall(
        one_contract_catalog(
            "/home/serverGeneralScript/basic_info.sh",
            ["{server_id}"],
            {"server_id": ".+"},
        )
    )

    with pytest.raises(MuliuFirewallError, match="Shell 控制符"):
        firewall.validate(plan)


def test_rejects_dynamic_code_execution_option():
    plan = make_plan(args=["-c", "print(1)"])
    firewall = make_firewall(
        one_contract_catalog(
            "/home/serverGeneralScript/basic_info.sh",
            ["{first}", "{second}"],
            {"first": ".+", "second": ".+"},
        )
    )

    with pytest.raises(MuliuFirewallError, match="动态代码执行"):
        firewall.validate(plan)


def test_rejects_configured_keyword():
    plan = make_plan(args=["dangerous-action"])
    firewall = make_firewall(
        one_contract_catalog(
            "/home/serverGeneralScript/basic_info.sh",
            ["{argument}"],
            {"argument": ".+"},
        )
    )

    with pytest.raises(MuliuFirewallError, match="拦截关键词"):
        firewall.validate(plan)


def test_validates_hotfix_server_threshold_contract():
    from pathlib import Path
    from src.muliu_script_catalog import MuliuScriptCatalog
    catalog = MuliuScriptCatalog(Path(__file__).parents[1] / "config" / "muliu_script_catalog.md")
    firewall = MuliuFirewall(MuliuConfig(script_root="/home/serverGeneralScript"), call_contracts=catalog.read_call_contracts())

    valid_plan = MuliuPlan(
        summary="热更 5010 服弹劾发起同盟人数为 1",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/auto_hotfix_tool.py",
                args=["-s", "5010", "-k", "union_impeach_min", "-v", "1"],
                description="热更 5010 服弹劾发起同盟人数阈值为 1",
            )
        ],
    )
    firewall.validate(valid_plan)

    typed_valid_plan = MuliuPlan(
        summary="以 common 类型热更 5010 服弹劾发起同盟人数为 1",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/auto_hotfix_tool.py",
                args=[
                    "-s", "5010", "-k", "union_impeach_min", "-v", "1",
                    "--patch-type", "common",
                ],
                description="以 common 类型热更 5010 服弹劾发起同盟人数阈值为 1",
            )
        ],
    )
    firewall.validate(typed_valid_plan)

    cleanup_valid_plan = MuliuPlan(
        summary="清理 5010 服霸业名额的旧版自动补丁",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/auto_hotfix_tool.py",
                args=["-s", "5010", "--cleanup-legacy", "capital_city_extra_quota"],
                description="清理 5010 服霸业名额的旧版自动补丁",
            )
        ],
    )
    firewall.validate(cleanup_valid_plan)

    restore_valid_plan = MuliuPlan(
        summary="还原 5010 服霸业名额的自动热更",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/auto_hotfix_tool.py",
                args=["-s", "5010", "--restore-auto", "capital_city_extra_quota"],
                description="还原 5010 服霸业名额的自动热更",
            )
        ],
    )
    firewall.validate(restore_valid_plan)

    invalid_plan = MuliuPlan(
        summary="非法调用",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/auto_hotfix_tool.py",
                args=["-s", "5010; rm -rf /", "-k", "union_impeach_min", "-v", "1"],
                description="注入攻击",
            )
        ],
    )
    with pytest.raises(MuliuFirewallError):
        firewall.validate(invalid_plan)

    unknown_key_plan = MuliuPlan(
        summary="未登记热更 key",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/auto_hotfix_tool.py",
                args=["-s", "5010", "-k", "unknown_threshold", "-v", "1"],
                description="尝试使用格式合法但未登记的 key",
            )
        ],
    )
    with pytest.raises(MuliuFirewallError, match="不符合任何已登记调用合同"):
        firewall.validate(unknown_key_plan)

    invalid_patch_type_plan = MuliuPlan(
        summary="非法补丁类型",
        kind=MuliuPlanKind.OPERATION,
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/auto_hotfix_tool.py",
                args=[
                    "-s", "5010", "-k", "union_impeach_min", "-v", "1",
                    "--patch-type", "../../etc",
                ],
                description="尝试写入非白名单目录",
            )
        ],
    )
    with pytest.raises(MuliuFirewallError):
        firewall.validate(invalid_patch_type_plan)
