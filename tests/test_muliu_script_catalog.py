import json
from pathlib import Path

import pytest

from src.muliu_plan import MuliuPlan, MuliuStep
from src.muliu_script_catalog import MuliuCallContractError, MuliuScriptCatalog, parse_call_contracts


CATALOG = """
# Muliu 测试服执行调用合同

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


def make_plan(path, args):
    return MuliuPlan(
        summary="查询测试服信息",
        steps=[MuliuStep(path=path, args=args, description="查询")],
    )


def test_contract_accepts_registered_basic_info_shape():
    contracts = parse_call_contracts(CATALOG)

    contracts.validate(make_plan("/home/serverGeneralScript/basic_info.sh", ["6001"]))


def test_contract_accepts_registered_patch_query_shape():
    contracts = parse_call_contracts(CATALOG)

    contracts.validate(
        make_plan("/home/serverGeneralScript/cc_patch.py", ["-s", "6001", "-ck"])
    )


@pytest.mark.parametrize(
    "args",
    [
        ["6001", "ctime"],
        ["ssinfo", "100"],
        ["ssnum", "account"],
        ["6001", "123456"],
    ],
)
def test_contract_rejects_unregistered_basic_info_branches(args):
    contracts = parse_call_contracts(CATALOG)

    with pytest.raises(MuliuCallContractError, match="不符合任何已登记调用合同"):
        contracts.validate(make_plan("/home/serverGeneralScript/basic_info.sh", args))


@pytest.mark.parametrize(
    "args",
    [
        ["-s", "6001", "6002", "-ck"],
        ["-s", "6001", "-ck", "-m"],
        ["-s", "6001", "-ck", "-r", "bak"],
        ["-s", "6001", "-ck", "-d", "test.lua"],
        ["-s", "6001", "-ck", "-f", "uf_x_p1"],
        ["-s", "6001", "-ck", "-u"],
        ["--server", "6001", "--check"],
    ],
)
def test_contract_rejects_unregistered_patch_forms(args):
    contracts = parse_call_contracts(CATALOG)

    with pytest.raises(MuliuCallContractError, match="不符合任何已登记调用合同"):
        contracts.validate(make_plan("/home/serverGeneralScript/cc_patch.py", args))


@pytest.mark.parametrize("server_id", ["12", "6001a", "6001;whoami", "600100001"])
def test_contract_rejects_invalid_server_id(server_id):
    contracts = parse_call_contracts(CATALOG)

    with pytest.raises(MuliuCallContractError, match="不符合任何已登记调用合同"):
        contracts.validate(make_plan("/home/serverGeneralScript/basic_info.sh", [server_id]))


def test_contract_rejects_multiple_steps_over_catalog_limit():
    contracts = parse_call_contracts(CATALOG)
    plan = MuliuPlan(
        summary="同时查询两个项目",
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/basic_info.sh",
                args=["6001"],
                description="查询基础信息",
            ),
            MuliuStep(
                path="/home/serverGeneralScript/cc_patch.py",
                args=["-s", "6001", "-ck"],
                description="查询 Patch",
            ),
        ],
    )

    with pytest.raises(MuliuCallContractError, match="最多允许 1 个已登记步骤"):
        contracts.validate(plan)


def test_production_catalog_accepts_ordered_registered_steps():
    catalog_path = Path(__file__).parents[1] / "config" / "muliu_script_catalog.md"
    contracts = parse_call_contracts(catalog_path.read_text(encoding="utf-8"))
    plan = MuliuPlan(
        summary="清档 5000 服并改为赛季 10、剧本 5",
        steps=[
            MuliuStep(
                path="/home/serverGeneralScript/clear",
                args=["5000"],
                description="清档 5000 服",
            ),
            MuliuStep(
                path="/home/serverGeneralScript/modify_game_config.sh",
                args=["5000", "10", "5"],
                description="将 5000 服改为赛季 10、剧本 5",
            ),
        ],
    )

    contracts.validate(plan)


@pytest.mark.parametrize(
    ("path", "args"),
    [
        ("/home/serverGeneralScript/shutdown", ["5000"]),
        ("/home/serverGeneralScript/shutdown", ["5000", "autokill"]),
        ("/home/serverGeneralScript/start", ["5000"]),
        ("/home/serverGeneralScript/start", ["5000", "noz"]),
        ("/home/serverGeneralScript/clear", ["5000"]),
        ("/home/serverGeneralScript/clear", ["5000", "by"]),
        ("/home/serverGeneralScript/modify_game_config.sh", ["5000", "3", "2003"]),
        ("/home/serverGeneralScript/starttime", ["5000"]),
        ("/home/serverGeneralScript/starttime", ["5000", "ctime", "--backup", "yes"]),
        ("/home/serverGeneralScript/starttime", ["5000", "1790820000", "--backup", "yes"]),
        (
            "/home/serverGeneralScript/server_log_query.py",
            ["--server-id", "5000", "--profile", "startup_errors", "--max-lines", "50"],
        ),
        ("/home/serverGeneralScript/cc_patch.py", ["-s", "5000", "5001", "-ck"]),
        ("/home/serverGeneralScript/cc_patch.py", ["-s", "5000", "-r", "bak"]),
        ("/home/serverGeneralScript/cc_patch.py", ["-s", "5000", "-r", "re"]),
        ("/home/serverGeneralScript/cc_patch.py", ["-s", "5000", "-ck", "-m"]),
        ("/home/serverGeneralScript/cc_patch.py", ["-s", "5000", "-u"]),
        ("/home/serverGeneralScript/cc_patch.py", ["-s", "5000", "-d", "avatar_patch_60952.lua"]),
        ("/home/serverGeneralScript/cc_patch.py", ["-s", "5000", "-f", "uf_hotfix"]),
        ("/home/serverGeneralScript/basic_info.sh", ["5000", "ctime"]),
        ("/home/serverGeneralScript/basic_info.sh", ["ssinfo", "2001"]),
        ("/home/serverGeneralScript/basic_info.sh", ["ssnum", "player_account"]),
        ("/home/serverGeneralScript/basic_info.sh", ["5000", "123456"]),
        ("/home/serverGeneralScript/clear_logic_game.py", ["5000"]),
        ("/home/serverGeneralScript/clear_zone.py", ["5000"]),
        ("/home/serverGeneralScript/cleardb.py", ["5000"]),
        (
            "/home/serverGeneralScript/batch_server_query.py",
            ["--servers", "5000,5001,6000,6001", "--label", "海外所有DEV环境"],
        ),
    ],
)
def test_production_catalog_accepts_every_registered_operation_shape(path, args):
    catalog_path = Path(__file__).parents[1] / "config" / "muliu_script_catalog.md"
    contracts = parse_call_contracts(catalog_path.read_text(encoding="utf-8"))

    contracts.validate(make_plan(path, args))


def test_production_catalog_exposes_database_scripts_as_registered_contracts():
    catalog_path = Path(__file__).parents[1] / "config" / "muliu_script_catalog.md"
    contracts = parse_call_contracts(catalog_path.read_text(encoding="utf-8"))

    for path in (
        "/home/serverGeneralScript/clear_logic_game.py",
        "/home/serverGeneralScript/clear_zone.py",
        "/home/serverGeneralScript/cleardb.py",
    ):
        assert contracts.validate(make_plan(path, ["5000"])) is None


def test_runner_manifest_includes_exact_contracts_and_fixed_runners():
    manifest = parse_call_contracts(CATALOG).runner_manifest().as_jsonable()

    assert manifest == {
        "version": 1,
        "contracts": [
            {
                "name": "basic-server-info",
                "path": "/home/serverGeneralScript/basic_info.sh",
                "args": ["{server_id}"],
                "variables": {"server_id": "[0-9]{3,8}"},
                "runner": ["bash"],
                "risk": "read",
            },
            {
                "name": "single-server-patch-list-check",
                "path": "/home/serverGeneralScript/cc_patch.py",
                "args": ["-s", "{server_id}", "-ck"],
                "variables": {"server_id": "[0-9]{3,8}"},
                "runner": ["python3.7"],
                "risk": "read",
            },
        ],
    }


def test_write_runner_manifest_writes_contract_artifact(tmp_path):
    catalog_path = tmp_path / "catalog.md"
    output_path = tmp_path / "deployment" / "muliu_runner_manifest.json"
    catalog_path.write_text(CATALOG, encoding="utf-8")

    written = MuliuScriptCatalog(catalog_path).write_runner_manifest(output_path)

    assert written == output_path
    assert output_path.read_text(encoding="utf-8") == (
        json.dumps(
            parse_call_contracts(CATALOG).runner_manifest().as_jsonable(),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def test_contract_rejects_unknown_risk_value():
    invalid_catalog = CATALOG.replace('"risk": "read"', '"risk": "unsafe"', 1)

    with pytest.raises(MuliuCallContractError, match="risk 必须是以下值之一"):
        parse_call_contracts(invalid_catalog)


def test_checked_in_runner_manifest_matches_production_catalog():
    repository_root = Path(__file__).parents[1]
    catalog = MuliuScriptCatalog(repository_root / "config" / "muliu_script_catalog.md")
    checked_in = (repository_root / "deployment" / "gs1" / "muliu_runner_manifest.json").read_text(
        encoding="utf-8"
    )

    assert checked_in == json.dumps(
        catalog.read_runner_manifest().as_jsonable(),
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def test_contract_rejects_missing_contract_block():
    with pytest.raises(MuliuCallContractError, match="MULIU_CALL_CONTRACTS"):
        parse_call_contracts("# 只有说明，没有机器合同")
