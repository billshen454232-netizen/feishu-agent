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
