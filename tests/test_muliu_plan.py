import json

import pytest

from src.muliu_plan import MuliuPlanError, MuliuPlanKind, parse_plan


VALID_OPERATION = '''
{
  "kind": "operation",
  "summary": "查询 6001 服信息",
  "steps": [
    {
      "path": "/home/serverGeneralScript/basic_info.sh",
      "args": ["6001"],
      "description": "查询服务器信息"
    }
  ]
}
'''


def test_parses_valid_operation_plan():
    plan = parse_plan(VALID_OPERATION, allow_legacy_kind=False)

    assert plan.kind is MuliuPlanKind.OPERATION
    assert plan.summary == "查询 6001 服信息"
    assert plan.steps[0].path == "/home/serverGeneralScript/basic_info.sh"
    assert plan.steps[0].args == ["6001"]


@pytest.mark.parametrize("kind", ["knowledge", "clarify", "reject"])
def test_parses_non_operation_empty_step_decisions(kind):
    plan = parse_plan(
        '{{"kind":"{}","summary":"当前不能执行","steps":[]}}'.format(kind),
        allow_legacy_kind=False,
    )

    assert plan.kind is MuliuPlanKind(kind)
    assert plan.steps == []


def test_legacy_operation_plan_remains_parseable_for_persisted_confirmation():
    plan = parse_plan(
        '''
        {
          "summary": "查询 6001 服信息",
          "steps": [
            {
              "path": "/home/serverGeneralScript/basic_info.sh",
              "args": ["6001"],
              "description": "查询服务器信息"
            }
          ]
        }
        '''
    )

    assert plan.kind is MuliuPlanKind.OPERATION


def test_legacy_empty_plan_remains_rejection_for_persisted_data():
    plan = parse_plan('{"summary":"无法执行：需求不明确","steps":[]}')

    assert plan.kind is MuliuPlanKind.REJECT
    assert plan.steps == []


def test_new_model_decision_requires_explicit_kind():
    with pytest.raises(MuliuPlanError, match="kind 必须是字符串"):
        parse_plan(
            '{"summary":"查询 6001 服信息","steps":[]}',
            allow_legacy_kind=False,
        )


@pytest.mark.parametrize(
    ("kind", "steps", "message"),
    [
        ("operation", [], "kind=operation 时 steps 必须至少包含一个步骤"),
        (
            "knowledge",
            [
                {
                    "path": "/home/serverGeneralScript/basic_info.sh",
                    "args": ["6001"],
                    "description": "查询",
                }
            ],
            "kind=knowledge 时 steps 必须为空数组",
        ),
        (
            "clarify",
            [
                {
                    "path": "/home/serverGeneralScript/basic_info.sh",
                    "args": ["6001"],
                    "description": "查询",
                }
            ],
            "kind=clarify 时 steps 必须为空数组",
        ),
        (
            "reject",
            [
                {
                    "path": "/home/serverGeneralScript/basic_info.sh",
                    "args": ["6001"],
                    "description": "查询",
                }
            ],
            "kind=reject 时 steps 必须为空数组",
        ),
    ],
)
def test_kind_enforces_step_shape(kind, steps, message):
    with pytest.raises(MuliuPlanError, match=message):
        parse_plan(
            json.dumps({"kind": kind, "summary": "测试", "steps": steps}),
            allow_legacy_kind=False,
        )


def test_rejects_markdown_wrapped_json():
    with pytest.raises(MuliuPlanError, match="合法 JSON"):
        parse_plan('```json\n{"kind":"reject","summary":"x","steps":[]}\n```')


def test_rejects_unknown_step_fields():
    with pytest.raises(MuliuPlanError, match="不允许的字段"):
        parse_plan(
            '''
            {
              "kind": "operation",
              "summary": "x",
              "steps": [
                {
                  "path": "/home/serverGeneralScript/basic_info.sh",
                  "args": ["6001"],
                  "description": "x",
                  "command": "rm -rf /"
                }
              ]
            }
            ''',
            allow_legacy_kind=False,
        )
