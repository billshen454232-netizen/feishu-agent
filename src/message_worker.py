from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .config import AppConfig
from .conversation_log import ConversationLogEntry
from .conversation import ConversationStore
from .message_jobs import MessageJobStore
from .muliu_executor import MuliuExecutionError, MuliuExecutionResult, MuliuSubmittedError
from .muliu_firewall import MuliuFirewallError
from .muliu_plan import MuliuPlan, MuliuPlanError, MuliuPlanKind, parse_plan
from .muliu_planner import MuliuPlanGeneration, MuliuPlanGenerationError
from .muliu_routing import MuliuPlanRouteResolver, MuliuRouteSnapshot, MuliuRoutingError
from .operation_message import (
    build_execution_status_card,
    build_operation_status_card,
    build_plan_confirmation_card,
    build_plan_display_card,
    clean_muliu_execution_log,
    format_execution_result,
    format_rejection,
    mask_sensitive_operation_data,
)
from .server_environments import ServerEnvironments, ServerEnvironmentsError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueueItem:
    kind: str
    message_id: str
    chat_id: str = ""
    confirmation_token: str = ""


class MessageJobQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()

    async def enqueue(self, message_id: str) -> None:
        await self._queue.put(QueueItem(kind="request", message_id=message_id))

    async def enqueue_confirmation(
        self,
        message_id: str,
        chat_id: str,
        confirmation_token: str,
    ) -> None:
        await self._queue.put(
            QueueItem(
                kind="confirmation",
                message_id=message_id,
                chat_id=chat_id,
                confirmation_token=confirmation_token,
            )
        )

    async def get(self) -> QueueItem:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()


class MessageJobProcessor:
    """Process one Feishu message at a time.

    Operation mode is intentionally confirmation-based: a natural-language request
    creates a stored plan and replies with a per-request confirmation token. Muliu
    is contacted only when a later group message supplies that token.
    """

    def __init__(
        self,
        config: AppConfig,
        store: MessageJobStore,
        conversations: ConversationStore,
        ai_client: Any,
        feishu_client: Any,
        knowledge_base: Any | None = None,
        queue: MessageJobQueue | None = None,
        operation_planner: Any | None = None,
        muliu_firewall: Any | None = None,
        muliu_executor: Any | None = None,
        route_resolver: MuliuPlanRouteResolver | None = None,
        script_knowledge_base: Any | None = None,
        script_catalog: Any | None = None,
        intent_router: Any | None = None,
        conversation_log: Any | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._conversations = conversations
        self._ai_client = ai_client
        self._feishu_client = feishu_client
        self._knowledge_base = knowledge_base
        self._queue = queue
        self._operation_planner = operation_planner
        self._muliu_firewall = muliu_firewall
        self._muliu_executor = muliu_executor
        self._route_resolver = route_resolver
        self._script_knowledge_base = script_knowledge_base
        self._script_catalog = script_catalog
        # Retained as an optional observability hint. It must never choose the
        # knowledge path before the registry-aware planner sees a request.
        self._intent_router = intent_router
        self._conversation_log = conversation_log
        self._task: asyncio.Task[None] | None = None
        self._operation_tasks: set[asyncio.Task[None]] = set()
        self._group_status_tasks: dict[str, asyncio.Task[None]] = {}
        self._stopping = False

    def start(self) -> None:
        if self._queue is None:
            raise RuntimeError("Cannot start MessageJobProcessor without a queue")
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        operation_tasks = tuple(self._operation_tasks)
        group_status_tasks = tuple(self._group_status_tasks.values())
        for task in (*operation_tasks, *group_status_tasks):
            task.cancel()
        if operation_tasks or group_status_tasks:
            await asyncio.gather(*operation_tasks, *group_status_tasks, return_exceptions=True)

    async def process_one(self, message_id: str) -> None:
        job = self._store.get(message_id)
        if job is None:
            logger.warning("job missing message_id=%s", message_id)
            return
        if job.status in {"done", "failed", "expired", "awaiting_confirmation"}:
            logger.warning("job skipped message_id=%s status=%s", message_id, job.status)
            return

        total_started = time.perf_counter()
        ai_ms = 0
        feishu_reply_ms = 0
        logger.warning("job processing message_id=%s", message_id)
        self._store.mark_processing(message_id)

        try:
            if self._operation_planner is not None:
                # Every script-related request reaches the same registry-aware AI
                # decision. Keyword routing used to prevent registered calls such as
                # “关 5000 服” from ever reaching the planner.
                ai_started = time.perf_counter()
                generation: MuliuPlanGeneration | None = None
                try:
                    generation = await self._create_operation_plan(job.text)
                    plan = generation.plan
                    ai_ms = self._elapsed_ms(ai_started)
                    if plan.kind is MuliuPlanKind.KNOWLEDGE:
                        (
                            knowledge_ms,
                            knowledge_ai_ms,
                            feishu_reply_ms,
                            local_knowledge_hit,
                            reply,
                            source_paths,
                        ) = await self._process_registry_knowledge_decision(
                            message_id,
                            job,
                            plan,
                        )
                        ai_ms += knowledge_ai_ms
                        logger.warning(
                            "registry knowledge timings message_id=%s knowledge_ms=%s ai_ms=%s feishu_reply_ms=%s total_ms=%s local_knowledge_hit=%s",
                            message_id,
                            knowledge_ms,
                            ai_ms,
                            feishu_reply_ms,
                            self._elapsed_ms(total_started),
                            local_knowledge_hit,
                        )
                        self._append_conversation_log(
                            job,
                            "script_knowledge",
                            reply,
                            source_paths=source_paths,
                            generation=generation,
                        )
                        return
                    if plan.kind is not MuliuPlanKind.OPERATION:
                        reply = format_rejection(plan.summary)
                        await self._reply_and_finish(message_id, reply)
                        self._append_conversation_log(job, "operation_rejected", reply, generation=generation)
                    elif job.chat_type != "group":
                        if job.chat_type == "p2p":
                            reason = "测试服操作只能在群内发起并确认；请到指定运维群 @机器人重新发送该请求。"
                        else:
                            reason = "无法确认该请求来自可执行的群聊；请到指定运维群 @机器人重新发送该请求。"
                        reply = format_rejection(reason)
                        await self._reply_and_finish(message_id, reply)
                        self._append_conversation_log(job, "operation_rejected", reply, generation=generation)
                    elif self._muliu_firewall is None:
                        reply = format_rejection("Muliu 防火墙尚未初始化")
                        await self._reply_and_finish(message_id, reply)
                        self._append_conversation_log(job, "operation_rejected", reply, generation=generation)
                    else:
                        self._muliu_firewall.validate(plan)
                        route_snapshot = self._resolve_route_snapshot(plan)
                        if not job.user_id.startswith("ou_"):
                            reply = format_rejection(
                                "未获取到可私发确认卡的请求人 open_id；请在指定运维群 @机器人重新发送该请求。"
                            )
                            await self._reply_and_finish(message_id, reply)
                            self._append_conversation_log(job, "operation_rejected", reply, generation=generation)
                            return
                        confirmation_token = self._new_confirmation_token()
                        group_card = build_plan_display_card(
                            plan,
                            route_label=route_snapshot.label,
                            route_identifier=route_snapshot.identifier,
                        )
                        confirmation_card = build_plan_confirmation_card(
                            plan,
                            request_message_id=message_id,
                            confirmation_token=confirmation_token,
                            route_label=route_snapshot.label,
                            route_identifier=route_snapshot.identifier,
                        )
                        reply_started = time.perf_counter()
                        group_status_message_id = await self._feishu_client.reply_card(message_id, group_card)
                        confirmation_message = await self._feishu_client.send_card_to_open_id(
                            job.user_id,
                            confirmation_card,
                        )
                        feishu_reply_ms = self._elapsed_ms(reply_started)
                        self._store.mark_awaiting_confirmation(
                            message_id,
                            plan_json=_plan_to_json(plan),
                            confirmation_message_id=confirmation_message.message_id,
                            confirmation_chat_id=confirmation_message.chat_id,
                            group_status_message_id=group_status_message_id,
                            confirmation_token=confirmation_token,
                            confirmation_method="card",
                            route_region=route_snapshot.region,
                        )
                        self._append_conversation_log(
                            job,
                            "operation_plan",
                            "已在群内展示只读计划，并私发原请求人确认卡：{}".format(plan.summary),
                            generation=generation,
                        )
                except MuliuPlanGenerationError as exc:
                    reply = format_rejection(str(exc))
                    await self._reply_and_finish(message_id, reply)
                    self._append_conversation_log(job, "operation_rejected", reply, generation=exc)
                except (MuliuPlanError, MuliuFirewallError, MuliuRoutingError) as exc:
                    reply = format_rejection(str(exc))
                    await self._reply_and_finish(message_id, reply)
                    self._append_conversation_log(job, "operation_rejected", reply, generation=generation)
            else:
                knowledge_ms, ai_ms, feishu_reply_ms, local_knowledge_hit = await self._process_legacy_chat(message_id, job)
                logger.warning(
                    "job timings message_id=%s knowledge_ms=%s ai_ms=%s feishu_reply_ms=%s total_ms=%s local_knowledge_hit=%s fallback=%s",
                    message_id,
                    knowledge_ms,
                    ai_ms,
                    feishu_reply_ms,
                    self._elapsed_ms(total_started),
                    local_knowledge_hit,
                    self._config.knowledge_base.fallback_when_miss,
                )
                return

            logger.warning(
                "job completed message_id=%s ai_ms=%s feishu_reply_ms=%s total_ms=%s",
                message_id,
                ai_ms,
                feishu_reply_ms,
                self._elapsed_ms(total_started),
            )
        except Exception as exc:  # noqa: BLE001 - persist unexpected operational failures
            error = str(exc)
            self._store.mark_failed(message_id, error)
            if 'group_status_message_id' in locals():
                await self._mark_group_confirmation_unavailable(group_status_message_id, job.text, error)
            logger.exception("job failed message_id=%s error=%s", message_id, exc)

    async def process_confirmation(self, chat_id: str, confirmation_token: str, message_id: str) -> bool:
        """Accept a text-confirmed plan and run it outside the shared message queue."""
        pending_job = self._store.claim_confirmation(
            chat_id,
            confirmation_token,
            max_age_seconds=self._config.muliu.confirmation_timeout_seconds,
        )
        if pending_job is None:
            return False
        await self._start_claimed_confirmation(pending_job, message_id)
        if self._config.runtime.role == "gateway":
            queued_job = self._store.get(pending_job.message_id)
            if queued_job is not None and queued_job.status == "queued_for_worker":
                reply = (
                    "计划已排入内网 Muliu Worker 队列，等待受控 Worker 领取并回传可信终态。"
                    "请勿重复确认或重试。"
                )
                try:
                    await self._feishu_client.reply_text(message_id, reply)
                except Exception:  # noqa: BLE001 - queueing remains durable if acknowledgement fails
                    logger.exception(
                        "gateway queue acknowledgement failed message_id=%s",
                        pending_job.message_id,
                    )
                self._append_conversation_log(queued_job, "operation_queued_for_worker", reply)
        return True

    async def process_card_action(
        self,
        *,
        action: str,
        request_message_id: str,
        confirmation_message_id: str,
        chat_id: str,
        user_id: str,
        confirmation_token: str,
    ) -> dict[str, object] | None:
        """Handle a CardKit action without trusting its executable contents.

        Returning a card lets the HTTP callback replace the original card within
        Feishu's three-second deadline. Muliu execution starts only in a detached
        task after the exact persisted plan has been atomically claimed.
        """
        max_age_seconds = self._config.muliu.confirmation_timeout_seconds
        if action == "cancel_operation":
            pending_job = self._store.cancel_card_confirmation(
                request_message_id,
                confirmation_message_id,
                chat_id,
                user_id,
                confirmation_token,
                max_age_seconds=max_age_seconds,
            )
            if pending_job is None:
                return None
            reply = "已取消待确认操作：{}".format(pending_job.text)
            status_card = build_operation_status_card(
                "测试服操作已取消",
                pending_job.text,
                "未调用 Muliu，也未执行任何目标脚本。",
                template="grey",
            )
            self._schedule_group_status_update(pending_job, status_card)
            self._append_conversation_log(pending_job, "operation_cancelled", reply)
            return status_card

        if action != "confirm_operation":
            return None
        pending_job = self._store.claim_card_confirmation(
            request_message_id,
            confirmation_message_id,
            chat_id,
            user_id,
            confirmation_token,
            max_age_seconds=max_age_seconds,
        )
        if pending_job is None:
            return None
        accepted = await self._start_claimed_confirmation(
            pending_job,
            pending_job.confirmation_message_id,
        )
        if not accepted:
            status_card = build_operation_status_card(
                "测试服操作未提交",
                pending_job.text,
                "已拒绝该已确认计划，未进入内网 Worker 队列。",
                template="red",
            )
            self._schedule_group_status_update(pending_job, status_card)
            return status_card
        status_card = build_operation_status_card(
            "测试服操作已提交",
            pending_job.text,
            (
                "计划已排入内网 Muliu Worker 队列，等待受控 Worker 领取并回传可信终态。请勿重复确认或重试。"
                if self._config.runtime.role == "gateway"
                else "正在等待受控 Muliu 任务返回可信终态。请勿重复确认或重试。"
            ),
            template="blue",
        )
        self._schedule_group_status_update(pending_job, status_card)
        return status_card

    async def _start_claimed_confirmation(self, pending_job: Any, reply_message_id: str) -> bool:
        """Parse the stored plan and launch it after an atomic confirmation claim."""
        try:
            plan = parse_plan(pending_job.plan_json, max_steps=self._config.muliu.max_steps)
        except MuliuPlanError as exc:
            error = str(exc)
            result = MuliuExecutionResult(
                summary=pending_job.text,
                step_results=[],
                failed_step_number=1,
                failure_reason=error,
            )
            self._store.mark_failed(pending_job.message_id, error)
            reply = format_rejection(error)
            await self.deliver_execution_result(
                pending_job,
                reply_message_id,
                reply,
                status_card=build_execution_status_card(result),
            )
            return False

        if self._config.runtime.role == "gateway":
            if pending_job.route_region not in {"cn", "overseas"}:
                error = "已确认计划缺少受控路由分类，不能进入内网 Worker 队列"
                result = MuliuExecutionResult(
                    summary=plan.summary,
                    step_results=[],
                    failed_step_number=1,
                    failure_reason=error,
                )
                self._store.mark_failed(pending_job.message_id, error)
                await self.deliver_execution_result(
                    pending_job,
                    reply_message_id,
                    format_rejection(error),
                    status_card=build_execution_status_card(result),
                )
                return False
            if not self._store.queue_for_worker(pending_job.message_id):
                error = "确认计划无法进入内网 Worker 队列"
                result = MuliuExecutionResult(
                    summary=plan.summary,
                    step_results=[],
                    failed_step_number=1,
                    failure_reason=error,
                )
                self._store.mark_failed(pending_job.message_id, error)
                await self.deliver_execution_result(
                    pending_job,
                    reply_message_id,
                    format_rejection(error),
                    status_card=build_execution_status_card(result),
                )
                return False
            return True

        if self._muliu_executor is None:
            error = "Muliu 执行器尚未初始化"
            result = MuliuExecutionResult(
                summary=plan.summary,
                step_results=[],
                failed_step_number=1,
                failure_reason=error,
            )
            self._store.mark_failed(pending_job.message_id, error)
            await self.deliver_execution_result(
                pending_job,
                reply_message_id,
                format_rejection(error),
                status_card=build_execution_status_card(result),
            )
            return False

        self._store.mark_submission_in_progress(pending_job.message_id)
        task = asyncio.create_task(self._execute_confirmed_plan(pending_job, plan, reply_message_id))
        self._operation_tasks.add(task)
        task.add_done_callback(self._operation_tasks.discard)
        return True

    async def _execute_confirmed_plan(self, pending_job: Any, plan: MuliuPlan, reply_message_id: str) -> None:
        """Wait for one Muliu operation without blocking unrelated Feishu messages."""
        try:
            if self._muliu_executor is None:
                raise MuliuExecutionError("Muliu 执行器尚未初始化")
            execution_result = await self._muliu_executor.execute_plan(plan)
            fallback_reply = format_execution_result(execution_result)
            if execution_result.succeeded:
                self._store.mark_done(pending_job.message_id)
            elif execution_result.result_unknown:
                self._store.mark_submitted_result_unknown(
                    pending_job.message_id,
                    execution_result.result_unknown_reason,
                )
            else:
                self._store.mark_failed(pending_job.message_id, execution_result.failure_reason)
            reply = await self.interpret_execution_result(
                pending_job,
                execution_result,
                fallback_reply,
            )
            await self.deliver_execution_result(
                pending_job,
                reply_message_id,
                reply,
                status_card=build_execution_status_card(execution_result),
            )
        except asyncio.CancelledError:
            self._store.mark_submitted_result_unknown(
                pending_job.message_id,
                "机器人停止时无法确认已提交操作的最终结果；请勿重复确认或重试",
            )
            raise
        except MuliuSubmittedError as exc:
            error = str(exc)
            if exc.submitted:
                result = MuliuExecutionResult(
                    summary=plan.summary,
                    step_results=[],
                    result_unknown_reason=error,
                )
                reply = format_execution_result(result)
                self._store.mark_submitted_result_unknown(pending_job.message_id, error)
            else:
                result = MuliuExecutionResult(
                    summary=plan.summary,
                    step_results=[],
                    failed_step_number=1,
                    failure_reason=error,
                )
                reply = format_rejection(error)
                self._store.mark_submission_failed(pending_job.message_id, error)
            await self.deliver_execution_result(
                pending_job,
                reply_message_id,
                reply,
                status_card=build_execution_status_card(result),
            )
        except MuliuExecutionError as exc:
            error = str(exc)
            result = MuliuExecutionResult(
                summary=plan.summary,
                step_results=[],
                failed_step_number=1,
                failure_reason=error,
            )
            self._store.mark_submission_failed(pending_job.message_id, error)
            reply = format_rejection(error)
            await self.deliver_execution_result(
                pending_job,
                reply_message_id,
                reply,
                status_card=build_execution_status_card(result),
            )
        except Exception as exc:  # noqa: BLE001 - preserve an uncertain remote operation
            error = "机器人在等待结果时发生异常：{}；请勿重复确认或重试".format(exc)
            result = MuliuExecutionResult(
                summary=plan.summary,
                step_results=[],
                result_unknown_reason=error,
            )
            self._store.mark_submitted_result_unknown(pending_job.message_id, error)
            reply = format_execution_result(result)
            await self.deliver_execution_result(
                pending_job,
                reply_message_id,
                reply,
                status_card=build_execution_status_card(result),
            )

    async def interpret_execution_result(
        self,
        pending_job: Any,
        result: MuliuExecutionResult,
        fallback_reply: str,
    ) -> str:
        """Add a bounded AI reading of a trustworthy successful worker result.

        The worker result has already been bound to the persisted plan before this
        method is called. Interpretation is presentation-only: it never changes
        terminal job state and every unavailable/unsafe AI outcome falls back to
        the normal, auditable result text.
        """
        fallback_reply = mask_sensitive_operation_data(fallback_reply)
        if (
            not self._config.execution_interpretation.enabled
            or not should_interpret_with_ai(pending_job.text, result)
            or self._ai_client is None
        ):
            return fallback_reply

        execution_data = _build_clean_execution_data(
            result,
            max_chars=self._config.execution_interpretation.max_execution_data_chars,
        )
        if not execution_data:
            return fallback_reply

        environment_context = _load_environment_context(
            self._config.execution_interpretation.server_environments_path
        )
        prompt = _build_execution_interpretation_prompt(
            user_text=pending_job.text,
            environment_context=environment_context,
            execution_data=execution_data,
        )
        try:
            interpretation = await asyncio.wait_for(
                self._ai_client.generate([], prompt),
                timeout=self._config.execution_interpretation.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("execution result interpretation timed out message_id=%s", pending_job.message_id)
            return fallback_reply
        except Exception:  # noqa: BLE001 - presentation must never hide a trusted result
            logger.warning(
                "execution result interpretation unavailable message_id=%s",
                pending_job.message_id,
                exc_info=True,
            )
            return fallback_reply

        interpretation = mask_sensitive_operation_data(str(interpretation).strip())
        if not interpretation:
            return fallback_reply
        return mask_sensitive_operation_data(
            "{}\n\n---\n\n执行明细：\n{}".format(
                interpretation,
                fallback_reply,
            )
        )

    def _schedule_group_status_update(self, pending_job: Any, status_card: dict[str, object]) -> None:
        """Mirror a private-card transition to the group without delaying its callback."""
        group_status_message_id = getattr(pending_job, "group_status_message_id", "")
        update_card = getattr(self._feishu_client, "update_card", None)
        if not group_status_message_id or not callable(update_card):
            return
        previous_task = self._group_status_tasks.get(group_status_message_id)
        if previous_task is not None and not previous_task.done():
            previous_task.cancel()
        task = asyncio.create_task(update_card(group_status_message_id, status_card))
        self._group_status_tasks[group_status_message_id] = task
        task.add_done_callback(
            lambda completed: self._finish_group_status_update(group_status_message_id, completed)
        )

    def _finish_group_status_update(self, group_status_message_id: str, task: asyncio.Task[None]) -> None:
        if self._group_status_tasks.get(group_status_message_id) is task:
            self._group_status_tasks.pop(group_status_message_id, None)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:  # noqa: BLE001 - the private action outcome remains durable
            logger.exception("group status card update failed message_id=%s", group_status_message_id)

    async def _mark_group_confirmation_unavailable(
        self,
        group_status_message_id: str,
        summary: str,
        error: str,
    ) -> None:
        """Make a failed private-card delivery explicit in the group audit card."""
        update_card = getattr(self._feishu_client, "update_card", None)
        if not callable(update_card):
            return
        try:
            await update_card(
                group_status_message_id,
                build_operation_status_card(
                    "测试服操作未提交",
                    summary,
                    "未能向原请求人发送私聊确认卡，未提交给 Muliu。原因：{}".format(error),
                    template="red",
                ),
            )
        except Exception:  # noqa: BLE001 - the original send failure remains the durable outcome
            logger.exception("group confirmation failure status update failed message_id=%s", group_status_message_id)

    async def deliver_execution_result(
        self,
        pending_job: Any,
        reply_message_id: str,
        reply: str,
        *,
        status_card: dict[str, object] | None = None,
    ) -> None:
        """Deliver a terminal outcome to the requester and preserve group audit state."""
        try:
            update_card = getattr(self._feishu_client, "update_card", None)
            if status_card is not None and pending_job.confirmation_method == "card" and callable(update_card):
                if pending_job.confirmation_message_id:
                    await update_card(pending_job.confirmation_message_id, status_card)
                if pending_job.group_status_message_id:
                    self._schedule_group_status_update(pending_job, status_card)
            await self._feishu_client.reply_text(reply_message_id, reply)
        except Exception:  # noqa: BLE001 - delivery failure does not change remote outcome
            logger.exception("execution result delivery failed message_id=%s", pending_job.message_id)
        finally:
            self._append_conversation_log(pending_job, "execution_result", reply)

    async def _process_registry_knowledge_decision(
        self,
        message_id: str,
        job: Any,
        decision: MuliuPlan,
    ) -> tuple[int, int, int, bool, str, tuple[str, ...]]:
        """Answer a planner-classified knowledge request with current registry context."""
        knowledge_started = time.perf_counter()
        result = self._script_knowledge_base.search(job.text) if self._script_knowledge_base is not None else None
        knowledge_ms = self._elapsed_ms(knowledge_started)
        source_paths = [source.path for source in result.sources] if result is not None and result.hit else []
        sources = "\n".join("- {}".format(path) for path in source_paths)
        document_context = result.context if result is not None and result.hit else "（未命中补充脚本资料）"
        capability_context = self._capability_context()
        prompt = (
            "你是测试服脚本资料问答助手。只依据下列当前执行注册表和本地资料回答用户的问题。"
            "回答应简洁、准确，用中文说明当前可执行能力、用途、参数形状、风险和前置检查。"
            "当前执行注册表优先于旧资料中的‘未登记’表述；若资料与注册表冲突，以注册表为准。"
            "不得生成执行计划、不得请求确认执行、不得输出自由 Shell 命令、不得补充资料中没有的服务器地址、账号、凭据、内部环境信息或操作细节。"
            "如果注册表没有对应能力，直接说明尚未登记，不要猜测。\n\n"
            "规划器的受限决策摘要：{}\n\n当前执行注册表：\n{}\n\n本地资料：\n{}\n\n来源列表：\n{}\n\n用户问题：{}"
        ).format(decision.summary, capability_context, document_context, sources or "（无）", job.text)
        ai_started = time.perf_counter()
        reply = await self._ai_client.generate([], prompt)
        ai_ms = self._elapsed_ms(ai_started)
        if source_paths:
            reply = "📚 以下回答基于当前执行注册表和本地脚本资料生成。\n\n{}\n\n来源：\n{}".format(reply, sources)
        else:
            reply = "📚 以下回答基于当前执行注册表生成。\n\n{}".format(reply)

        self._conversations.append(job.chat_id, "user", job.text)
        self._conversations.append(job.chat_id, "assistant", reply)
        reply_started = time.perf_counter()
        await self._reply_and_finish(message_id, reply)
        return (
            knowledge_ms,
            ai_ms,
            self._elapsed_ms(reply_started),
            bool(source_paths),
            reply,
            tuple(source_paths),
        )

    def _resolve_route_snapshot(self, plan: MuliuPlan) -> MuliuRouteSnapshot:
        if self._route_resolver is None:
            raise MuliuRoutingError("受控路由解析器尚未初始化")
        return self._route_resolver.resolve(plan)

    def _capability_context(self) -> str:
        read_capability_context = getattr(self._script_catalog, "read_capability_context", None)
        if callable(read_capability_context):
            return read_capability_context()
        return "当前执行注册表未提供可展示摘要。"

    async def _process_legacy_chat(self, message_id: str, job: Any) -> tuple[int, int, int, bool]:
        knowledge_started = time.perf_counter()
        ai_user_text, source_paths, local_knowledge_hit = self._build_ai_user_text(job.text)
        knowledge_ms = self._elapsed_ms(knowledge_started)
        ai_ms = 0
        if ai_user_text is None:
            reply = "📚 本地知识库没有找到与该问题相关的内容。当前已关闭外部/通用知识回答。"
        else:
            ai_started = time.perf_counter()
            history = self._conversations.get(job.chat_id)
            reply = await self._ai_client.generate(history, ai_user_text)
            ai_ms = self._elapsed_ms(ai_started)
            if local_knowledge_hit:
                reply = self._format_local_knowledge_reply(reply, source_paths)
        self._conversations.append(job.chat_id, "user", job.text)
        self._conversations.append(job.chat_id, "assistant", reply)
        feishu_reply_started = time.perf_counter()
        await self._reply_and_finish(message_id, reply)
        self._append_conversation_log(
            job,
            "knowledge_base" if local_knowledge_hit else "general_question",
            reply,
            source_paths=tuple(source_paths),
        )
        return knowledge_ms, ai_ms, self._elapsed_ms(feishu_reply_started), local_knowledge_hit

    async def _create_operation_plan(self, user_text: str) -> MuliuPlanGeneration:
        """Use trace-aware planners while keeping simple test doubles compatible."""
        if self._operation_planner is None:
            raise RuntimeError("Muliu 计划生成器尚未初始化")
        create_with_trace = getattr(self._operation_planner, "create_plan_with_trace", None)
        if callable(create_with_trace):
            return await create_with_trace(user_text)
        plan = await self._operation_planner.create_plan(user_text)
        return MuliuPlanGeneration(plan=plan, raw_responses=())

    def _append_conversation_log(
        self,
        job: Any,
        kind: str,
        reply: str,
        *,
        source_paths: tuple[str, ...] = (),
        generation: MuliuPlanGeneration | MuliuPlanGenerationError | None = None,
    ) -> None:
        """Log locally without turning an otherwise completed Feishu reply into a failure."""
        if self._conversation_log is None:
            return
        raw_responses: tuple[str, ...] = ()
        validation_errors: tuple[str | None, ...] = ()
        normalized_single_step = False
        response_stages: tuple[str, ...] = ()
        repair_attempts = 0
        if isinstance(generation, MuliuPlanGenerationError):
            raw_responses = generation.raw_responses
            validation_errors = generation.validation_errors
            response_stages = generation.response_stages
            normalized_single_step = generation.normalized_single_step
            repair_attempts = generation.repair_attempts
        elif isinstance(generation, MuliuPlanGeneration):
            raw_responses = generation.raw_responses
            validation_errors = generation.validation_errors
            response_stages = generation.response_stages
            normalized_single_step = generation.normalized_single_step
            repair_attempts = generation.repair_attempts
        try:
            self._conversation_log.append(
                ConversationLogEntry(
                    occurred_at=datetime.now().astimezone(),
                    chat_id=job.chat_id,
                    message_id=job.message_id,
                    kind=kind,
                    user_text=job.text,
                    reply_text=reply,
                    source_paths=source_paths,
                    plan_raw_responses=raw_responses,
                    plan_validation_errors=validation_errors,
                    plan_response_stages=response_stages,
                    plan_repair_attempts=repair_attempts,
                    normalized_single_step=normalized_single_step,
                )
            )
        except Exception:  # noqa: BLE001 - local audit logging must not change bot outcomes
            logger.exception(
                "local conversation log write failed message_id=%s kind=%s",
                job.message_id,
                kind,
            )

    def _build_ai_user_text(self, user_text: str) -> tuple[str | None, list[str], bool]:
        if self._knowledge_base is None:
            return user_text, [], False
        result = self._knowledge_base.search(user_text)
        if result.hit:
            source_paths = [source.path for source in result.sources]
            sources = "\n".join("- {}".format(path) for path in source_paths)
            return (
                "请优先依据以下本地知识库资料回答；如果资料不足，请明确说明不足之处，再结合通用知识补充。\n\n"
                "{}\n\n来源列表：\n{}\n\n用户问题：{}".format(result.context, sources, user_text),
                source_paths,
                True,
            )
        if self._config.knowledge_base.fallback_when_miss == "local_only":
            return None, [], False
        return (
            "本地知识库没有找到足够相关的资料。请先在回答开头说明这一点，然后再基于通用知识或可用外部能力进行推理回答。\n\n用户问题：{}".format(user_text),
            [],
            False,
        )

    @staticmethod
    def _format_local_knowledge_reply(reply: str, source_paths: list[str]) -> str:
        sources = "\n".join("- {}".format(path) for path in source_paths)
        return "📚 以下回答基于本地知识库资料生成。\n\n{}\n\n来源：\n{}".format(reply, sources)

    async def _reply_and_finish(self, message_id: str, reply: str) -> None:
        await self._feishu_client.reply_text(message_id, reply)
        self._store.mark_done(message_id)

    def _confirmation_text(self, confirmation_token: str) -> str:
        return "{} {}".format(self._config.muliu.confirmation_prefix, confirmation_token)

    @staticmethod
    def _new_confirmation_token() -> str:
        return secrets.token_hex(3).upper()

    async def _run(self) -> None:
        if self._queue is None:
            return
        while not self._stopping:
            item = await self._queue.get()
            try:
                if item.kind == "request":
                    await self.process_one(item.message_id)
                elif item.kind == "confirmation":
                    await self._process_confirmation_message(item)
                else:
                    logger.warning("ignored unknown queue item kind=%s", item.kind)
            finally:
                self._queue.task_done()

    async def _process_confirmation_message(self, item: QueueItem) -> None:
        await self.process_confirmation(item.chat_id, item.confirmation_token, item.message_id)

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)


_INTERPRETATION_INTENT_KEYWORDS = (
    "是否存在",
    "有没有",
    "是否有",
    "是否包含",
    "对比",
    "分析",
    "有哪些",
    "哪个是",
    "什么版本",
)
_INTERPRETATION_SCRIPT_NAMES = (
    "batch_server_query.py",
    "cc_patch.py",
    "server_log_query.py",
)


def should_interpret_with_ai(job_text: str, result: MuliuExecutionResult) -> bool:
    """Return whether a successful result benefits from a concise AI conclusion."""
    if not result.succeeded or not result.step_results:
        return False
    if any(keyword in job_text for keyword in _INTERPRETATION_INTENT_KEYWORDS):
        return True
    return any(
        step.path.rsplit("/", 1)[-1] in _INTERPRETATION_SCRIPT_NAMES
        for step in result.step_results
    )


def _build_clean_execution_data(result: MuliuExecutionResult, *, max_chars: int) -> str:
    """Prepare bounded, wrapper-free and sensitive-data-redacted execution facts."""
    lines: list[str] = []
    for index, step in enumerate(result.step_results, start=1):
        log = mask_sensitive_operation_data(clean_muliu_execution_log(step.log))
        if not log or log == "（无额外输出日志）":
            continue
        lines.extend([
            "步骤 {}/{}：{}".format(
                index,
                len(result.step_results),
                mask_sensitive_operation_data(step.description),
            ),
            "脚本：{}".format(step.path.rsplit("/", 1)[-1]),
            "实际输出：",
            log,
            "",
        ])
    return "\n".join(lines).strip()[:max_chars]


def _load_environment_context(path: str) -> str:
    """Load only validated environment ownership facts, never connection data."""
    try:
        environments = ServerEnvironments.load(path)
    except ServerEnvironmentsError:
        logger.warning("execution interpretation environment context unavailable", exc_info=True)
        return "（环境资产配置当前不可用；不要据此推断环境归属。）"

    lines = list(environments.region_context_lines())
    lines.extend(
        "{}：{}".format(mapping.name, "、".join(mapping.server_ids))
        for mapping in environments.group_mappings()
    )
    return mask_sensitive_operation_data("\n".join(lines) or "（未加载到环境资产。）")


def _build_execution_interpretation_prompt(
    *,
    user_text: str,
    environment_context: str,
    execution_data: str,
) -> str:
    return (
        "你是飞书测试服运维 AI 助手。用户发起的操作已执行完成；你只能依据下列脱敏后的真实结果生成结论。"
        "\n\n【用户原始提问】\n{}"
        "\n\n【环境资产参考】\n{}"
        "\n\n【实际执行结果】\n{}"
        "\n\n【输出约束】\n"
        "1. 第一行直接回答用户问题的核心结论，例如【存在】、【不存在】、【版本完全一致】或【存在差异】。\n"
        "2. 只陈述实际执行结果明确支持的事实；数据不足时明确说明无法确认，绝不猜测。\n"
        "3. 用简洁的中文 Markdown 列出关键证据；不要复述整段原始日志。\n"
        "4. 严禁输出私网 IP、主机地址、账号、凭据、Cookie、数据库连接、文件系统工作目录或执行命令。\n"
        "5. 不要建议、触发或描述新的执行操作。\n"
        "6. 严禁添加任何多余前缀、说明性标题或客套话（如‘AI 研读结论：’、‘分析结果：’等），直接输出结论。"
    ).format(mask_sensitive_operation_data(user_text), environment_context, execution_data)


def _plan_to_json(plan: MuliuPlan) -> str:
    return json.dumps(
        {
            "kind": plan.kind.value,
            "summary": plan.summary,
            "steps": [
                {
                    "path": step.path,
                    "args": step.args,
                    "description": step.description,
                }
                for step in plan.steps
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
