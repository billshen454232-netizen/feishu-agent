"""Wire format shared by the public gateway and the intranet Muliu worker."""

from __future__ import annotations

from typing import Any

from .muliu_executor import (
    BASIC_INFO_SCRIPT_PATH,
    START_SCRIPT_PATH,
    MuliuExecutionResult,
    MuliuStepResult,
)
from .muliu_plan import MuliuPlan


class WorkerProtocolError(ValueError):
    """Raised when a worker request or result has an invalid bounded shape."""


_MAX_RESULT_LOG_CHARS = 100_000
_MAX_ERROR_CHARS = 4_000


def serialize_execution_result(result: MuliuExecutionResult) -> dict[str, object]:
    """Convert an execution outcome into the worker's fixed JSON response shape."""
    return {
        "summary": result.summary,
        "step_results": [
            {
                "step_number": step.step_number,
                "description": step.description,
                "path": step.path,
                "args": step.args,
                "succeeded": step.succeeded,
                "log": step.log,
            }
            for step in result.step_results
        ],
        "failed_step_number": result.failed_step_number,
        "failure_reason": result.failure_reason,
        "result_unknown_reason": result.result_unknown_reason,
    }


def parse_execution_result(value: Any, *, max_steps: int) -> MuliuExecutionResult:
    """Parse a bounded worker outcome before matching it to the stored plan.

    Network-supplied paths and arguments are not executable: the worker has already
    re-parsed and firewall-validated the leased plan before it can call Muliu.
    """
    if not isinstance(value, dict):
        raise WorkerProtocolError("worker result 必须是 JSON 对象")
    allowed = {
        "summary",
        "step_results",
        "failed_step_number",
        "failure_reason",
        "result_unknown_reason",
    }
    _reject_unknown_fields(value, allowed, "worker result")
    summary = _require_text(value.get("summary"), "summary", 120)
    raw_steps = value.get("step_results")
    if not isinstance(raw_steps, list) or len(raw_steps) > max_steps + 1:
        raise WorkerProtocolError("step_results 格式无效或超出上限")
    step_results = [_parse_step_result(item, index) for index, item in enumerate(raw_steps, start=1)]

    failed_step_number = value.get("failed_step_number")
    if failed_step_number is not None and (
        isinstance(failed_step_number, bool) or not isinstance(failed_step_number, int) or failed_step_number < 1
    ):
        raise WorkerProtocolError("failed_step_number 必须是正整数或 null")
    failure_reason = _require_optional_text(value.get("failure_reason", ""), "failure_reason", _MAX_ERROR_CHARS)
    result_unknown_reason = _require_optional_text(
        value.get("result_unknown_reason", ""),
        "result_unknown_reason",
        _MAX_ERROR_CHARS,
    )
    if failure_reason and result_unknown_reason:
        raise WorkerProtocolError("执行结果不能同时包含 failure_reason 和 result_unknown_reason")
    if failed_step_number is None and failure_reason:
        raise WorkerProtocolError("失败结果必须包含 failed_step_number")
    if failed_step_number is not None and not failure_reason:
        raise WorkerProtocolError("failed_step_number 必须配套 failure_reason")

    return MuliuExecutionResult(
        summary=summary,
        step_results=step_results,
        failed_step_number=failed_step_number,
        failure_reason=failure_reason,
        result_unknown_reason=result_unknown_reason,
    )


def validate_execution_result_matches_plan(
    result: MuliuExecutionResult,
    plan: MuliuPlan,
) -> None:
    """Reject an audit result that does not describe the leased plan.

    The executor reports each requested step in order. A successful ``start`` step
    is immediately followed by its fixed ``basic_info.sh`` verification result, so
    the worker may only return a contiguous prefix of that deterministic sequence.
    """
    if result.summary != plan.summary:
        raise WorkerProtocolError("worker result.summary 与已确认计划不一致")

    expected_steps = _expected_result_steps(plan)
    result_steps = result.step_results
    if len(result_steps) > len(expected_steps):
        raise WorkerProtocolError("worker result 的步骤数量超出已确认计划")

    for result_index, step_result in enumerate(result_steps):
        expected = expected_steps[result_index]
        if (
            step_result.step_number != expected.step_number
            or step_result.description != expected.description
            or step_result.path != expected.path
            or step_result.args != expected.args
        ):
            raise WorkerProtocolError(
                "worker result 第 {} 项与已确认计划不一致".format(result_index + 1)
            )

    _validate_result_terminal_shape(result, expected_steps)


class _ExpectedResultStep:
    def __init__(
        self,
        *,
        step_number: int,
        description: str,
        path: str,
        args: list[str],
    ) -> None:
        self.step_number = step_number
        self.description = description
        self.path = path
        self.args = args


def _expected_result_steps(plan: MuliuPlan) -> list[_ExpectedResultStep]:
    expected: list[_ExpectedResultStep] = []
    for step_number, step in enumerate(plan.steps, start=1):
        expected.append(
            _ExpectedResultStep(
                step_number=step_number,
                description=step.description,
                path=step.path,
                args=step.args,
            )
        )
        if step.path == START_SCRIPT_PATH and step.args:
            expected.append(
                _ExpectedResultStep(
                    step_number=step_number,
                    description="核验 {} 服是否起服成功".format(step.args[0]),
                    path=BASIC_INFO_SCRIPT_PATH,
                    args=[step.args[0]],
                )
            )
    return expected


def _validate_result_terminal_shape(
    result: MuliuExecutionResult,
    expected_steps: list[_ExpectedResultStep],
) -> None:
    result_steps = result.step_results
    if result.succeeded:
        if len(result_steps) != len(expected_steps):
            raise WorkerProtocolError("成功结果必须包含已确认计划的全部步骤")
        if any(not step.succeeded for step in result_steps):
            raise WorkerProtocolError("成功结果不能包含失败步骤")
        return

    if result.result_unknown:
        if len(result_steps) == len(expected_steps):
            raise WorkerProtocolError("完整成功步骤不能标记为结果未知")
        if any(not step.succeeded for step in result_steps):
            raise WorkerProtocolError("结果未知前的已回传步骤必须成功")
        return

    if result.failed_step_number is None:
        raise WorkerProtocolError("失败结果缺少失败步骤编号")
    if not expected_steps or result.failed_step_number > expected_steps[-1].step_number:
        raise WorkerProtocolError("失败步骤编号超出已确认计划")
    if any(not step.succeeded for step in result_steps[:-1]):
        raise WorkerProtocolError("失败结果只能包含最后一个失败步骤")

    if result_steps and not result_steps[-1].succeeded:
        failed_step = expected_steps[len(result_steps) - 1]
        if failed_step.step_number != result.failed_step_number:
            raise WorkerProtocolError("失败步骤与已确认计划不一致")
        return

    next_result_index = len(result_steps)
    if next_result_index >= len(expected_steps):
        raise WorkerProtocolError("失败结果缺少对应的失败步骤")
    failed_step = expected_steps[next_result_index]
    if failed_step.step_number != result.failed_step_number:
        raise WorkerProtocolError("失败步骤与已确认计划不一致")


def _parse_step_result(value: Any, index: int) -> MuliuStepResult:
    if not isinstance(value, dict):
        raise WorkerProtocolError("step_results[{}] 必须是 JSON 对象".format(index))
    _reject_unknown_fields(
        value,
        {"step_number", "description", "path", "args", "succeeded", "log"},
        "step_results[{}]".format(index),
    )
    step_number = value.get("step_number")
    if isinstance(step_number, bool) or not isinstance(step_number, int) or step_number < 1:
        raise WorkerProtocolError("step_results[{}].step_number 必须是正整数".format(index))
    description = _require_text(value.get("description"), "step_results[{}].description".format(index), 120)
    path = _require_text(value.get("path"), "step_results[{}].path".format(index), 512)
    raw_args = value.get("args")
    if not isinstance(raw_args, list) or len(raw_args) > 32:
        raise WorkerProtocolError("step_results[{}].args 格式无效".format(index))
    args = [_require_text(argument, "step_results[{}].args".format(index), 1024) for argument in raw_args]
    succeeded = value.get("succeeded")
    if not isinstance(succeeded, bool):
        raise WorkerProtocolError("step_results[{}].succeeded 必须是布尔值".format(index))
    log = _require_optional_text(value.get("log", ""), "step_results[{}].log".format(index), _MAX_RESULT_LOG_CHARS)
    return MuliuStepResult(
        step_number=step_number,
        description=description,
        path=path,
        args=args,
        succeeded=succeeded,
        log=log,
    )


def _require_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise WorkerProtocolError("{} 必须是长度不超过 {} 的非空字符串".format(label, maximum))
    return value.strip()


def _require_optional_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise WorkerProtocolError("{} 必须是长度不超过 {} 的字符串".format(label, maximum))
    return value


def _reject_unknown_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise WorkerProtocolError("{} 包含不允许的字段：{}".format(label, ", ".join(sorted(unexpected))))
