from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .ai_client import generate_with_provenance
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
    build_execution_warning_card,
    build_job_failure_card,
    build_operation_status_card,
    build_plan_confirmation_card,
    clean_muliu_execution_log,
    format_execution_result,
    format_rejection,
    mask_sensitive_operation_data,
)
from .execution_report import (
    INTERPRETATION_FAILURE,
    INTERPRETATION_READ_SUCCESS,
    OUTCOME_WARNING,
    PATTERN_VERIFIED,
    READ_SUCCESS_MODE_AI,
    ExecutionReportStore,
    compute_error_signature,
    is_read_only_result,
    result_scope,
)
from .qa_library import DOMAIN_TEAM_KB, QA_PENDING, QA_REUSED, QaService
from .server_environments import ServerEnvironments, ServerEnvironmentsError
from .team_kb_questions import strip_prefix as strip_team_kb_prefix
from .worker_protocol import parse_execution_result, serialize_execution_result

logger = logging.getLogger(__name__)

# How long a "continue remaining steps" offer stays valid after a failed result.
RESUME_OFFER_SECONDS = 1800


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
        report_store: ExecutionReportStore | None = None,
        interpretation_ai_client: Any | None = None,
        clear_notifier: Any | None = None,
        qa_service: QaService | None = None,
        team_kb_questions: Any | None = None,
    ) -> None:
        self._config = config
        # Questions for the intranet team knowledge base wait here until the team-kb service answers (design §5).
        self._team_kb_questions = team_kb_questions
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
        self._report_store = report_store
        # Result readings may use a stronger pool than chat (ai.routing.interpretation).
        self._interpretation_ai_client = interpretation_ai_client or ai_client
        self._interpretation_tasks: set[asyncio.Task[None]] = set()
        self._interpretation_semaphore: asyncio.Semaphore | None = None
        # Requester display names (open_id -> name), filled from chat member lists.
        self._member_names: dict[str, str] = {}
        self._member_lookup_at: dict[str, float] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._clear_notifier = clear_notifier
        self._qa_service = qa_service or QaService(
            answer_client=ai_client,
            knowledge_base=script_knowledge_base,
            capability_context=self._capability_context,
        )
        self._stopping = False

    def start(self) -> None:
        if self._queue is None:
            raise RuntimeError("Cannot start MessageJobProcessor without a queue")
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run())
            self.recover_interpretations()

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        operation_tasks = (*self._operation_tasks, *self._interpretation_tasks, *self._background_tasks)
        for task in operation_tasks:
            task.cancel()
        if operation_tasks:
            await asyncio.gather(*operation_tasks, return_exceptions=True)

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
        self._schedule_background(self._remember_requester_name(job))

        try:
            forced_question = strip_team_kb_prefix(job.text)
            if forced_question is not None:  # 「问题 …」 always goes to the team knowledge base
                await self._ask_team_kb(message_id, job, forced_question)
                return
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
                    self._record_planning(message_id, generation)
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
                    if plan.kind is MuliuPlanKind.TEAM_KB:
                        await self._ask_team_kb(message_id, job, job.text, generation=generation)
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
                        confirmation_token = self._new_confirmation_token()
                        card = build_plan_confirmation_card(
                            plan,
                            request_message_id=message_id,
                            confirmation_token=confirmation_token,
                            route_label=route_snapshot.label,
                            route_identifier=route_snapshot.identifier,
                        )
                        reply_started = time.perf_counter()
                        confirmation_message_id = await self._feishu_client.reply_card(message_id, card)
                        feishu_reply_ms = self._elapsed_ms(reply_started)
                        self._store.mark_awaiting_confirmation(
                            message_id,
                            plan_json=_plan_to_json(plan),
                            confirmation_message_id=confirmation_message_id,
                            confirmation_token=confirmation_token,
                            confirmation_method="card",
                            route_region=route_snapshot.region,
                        )
                        self._notify_clear("clear_prepared", job, plan, route_snapshot.region, operator_id=job.user_id)
                        self._append_conversation_log(
                            job,
                            "operation_plan",
                            "已返回待确认交互卡片：{}".format(plan.summary),
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
            self._store.mark_failed(message_id, str(exc))
            logger.exception("job failed message_id=%s error=%s", message_id, exc)
            await self._notify_job_failure(message_id, exc)

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
        include_execution_logs: bool = False,
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
            self._append_conversation_log(pending_job, "operation_cancelled", reply)
            return build_operation_status_card(
                "测试服操作已取消",
                pending_job.text,
                "未调用 Muliu，也未执行任何目标脚本。",
                template="grey",
            )

        if action == "resume_operation":
            return await self._resume_remaining_steps(
                request_message_id=request_message_id,
                confirmation_message_id=confirmation_message_id,
                chat_id=chat_id,
                user_id=user_id,
                token=confirmation_token,
            )

        if action != "confirm_operation":
            return None
        pending_job = self._store.claim_card_confirmation(
            request_message_id,
            confirmation_message_id,
            chat_id,
            user_id,
            confirmation_token,
            max_age_seconds=max_age_seconds,
            include_execution_logs=include_execution_logs,
        )
        if pending_job is None:
            return None
        accepted = await self._start_claimed_confirmation(
            pending_job,
            pending_job.confirmation_message_id,
            confirmer_id=user_id,
        )
        if not accepted:
            return build_operation_status_card(
                "测试服操作未提交",
                pending_job.text,
                "已拒绝该已确认计划，未进入内网 Worker 队列。",
                template="red",
            )
        return build_operation_status_card(
            "测试服操作已提交",
            pending_job.text,
            (
                "计划已排入内网 Muliu Worker 队列，等待受控 Worker 领取并回传可信终态。请勿重复确认或重试。"
                if self._config.runtime.role == "gateway"
                else "正在等待受控 Muliu 任务返回可信终态。请勿重复确认或重试。"
            ),
            template="blue",
        )

    async def _start_claimed_confirmation(
        self,
        pending_job: Any,
        reply_message_id: str,
        *,
        confirmer_id: str = "",
    ) -> bool:
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
                status_card=build_execution_status_card(result, detail=result_card_detail(reply, result)),
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
                reply = format_rejection(error)
                await self.deliver_execution_result(
                    pending_job,
                    reply_message_id,
                    reply,
                    status_card=build_execution_status_card(result, detail=result_card_detail(reply, result)),
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
                reply = format_rejection(error)
                await self.deliver_execution_result(
                    pending_job,
                    reply_message_id,
                    reply,
                    status_card=build_execution_status_card(result, detail=result_card_detail(reply, result)),
                )
                return False
            self._notify_clear(
                "clear_executing",
                pending_job,
                plan,
                pending_job.route_region,
                operator_id=confirmer_id or pending_job.user_id,
            )
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
            reply = format_rejection(error)
            await self.deliver_execution_result(
                pending_job,
                reply_message_id,
                reply,
                status_card=build_execution_status_card(result, detail=result_card_detail(reply, result)),
            )
            return False

        self._store.mark_submission_in_progress(pending_job.message_id)
        self._notify_clear(
            "clear_executing",
            pending_job,
            plan,
            pending_job.route_region,
            operator_id=confirmer_id or pending_job.user_id,
        )
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
            if execution_result.succeeded:
                self._store.mark_done(pending_job.message_id)
            elif execution_result.result_unknown:
                self._store.mark_submitted_result_unknown(
                    pending_job.message_id,
                    execution_result.result_unknown_reason,
                )
            else:
                self._store.mark_failed(pending_job.message_id, execution_result.failure_reason)
            await self.present_execution_result(pending_job, execution_result, reply_message_id)
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
                status_card=build_execution_status_card(result, detail=result_card_detail(reply, result)),
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
                status_card=build_execution_status_card(result, detail=result_card_detail(reply, result)),
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
                status_card=build_execution_status_card(result, detail=result_card_detail(reply, result)),
            )

    async def present_execution_result(
        self,
        pending_job: Any,
        result: MuliuExecutionResult,
        reply_message_id: str,
    ) -> None:
        """Deliver a persisted terminal result first; explanation never blocks it.

        done / submitted_result_unknown use deterministic templates. failed first
        looks up the template library; only a miss (or a read-only success when
        the review page enables it) is read by AI, in the background, after the
        trusted card has already been delivered. Nothing here changes job state.
        """
        fallback_reply = mask_sensitive_operation_data(format_execution_result(result))
        try:
            presentation = self._plan_presentation(pending_job, result)
        except Exception:  # noqa: BLE001 - template lookup must never hide a trusted result
            logger.exception("execution report lookup failed message_id=%s", pending_job.message_id)
            presentation = _Presentation(detail=result_card_detail(fallback_reply, result), source="template")
        if presentation.warning:
            fallback_reply = "⚠️ 按已审核模板判定：查询完成，服务器状态存疑（机器终态仍为失败）\n\n{}".format(fallback_reply)
            status_card = build_execution_warning_card(result, detail=presentation.detail)
        else:
            status_card = build_execution_status_card(
                result,
                detail=presentation.detail,
                resume=self._offer_resume(pending_job, result),
            )
        await self.deliver_execution_result(
            pending_job,
            reply_message_id,
            fallback_reply,
            status_card=status_card,
        )
        if self._report_store is None:
            return
        try:
            self._report_store.record_artifact(
                pending_job.message_id,
                _terminal_status(result),
                presentation.source,
                fallback_reply,
                error_fingerprint=presentation.fingerprint,
            )
            if presentation.background_kind and self._report_store.enqueue_interpretation(
                pending_job.message_id,
                presentation.background_kind,
                serialize_execution_result(result),
                error_fingerprint=presentation.fingerprint,
            ):
                self._schedule_interpretation(pending_job.message_id)
        except Exception:  # noqa: BLE001 - bookkeeping must never hide a delivered result
            logger.exception("execution report bookkeeping failed message_id=%s", pending_job.message_id)

    def _plan_presentation(self, pending_job: Any, result: MuliuExecutionResult) -> "_Presentation":
        store = self._report_store
        ai_available = (
            store is not None
            and self._interpretation_ai_client is not None
            and self._config.execution_interpretation.enabled
        )
        if result.succeeded:
            detail = _success_detail(result)
            if (
                ai_available
                and store.read_success_mode() == READ_SUCCESS_MODE_AI
                and is_read_only_result(result, self._call_contracts())
            ):
                return _Presentation(
                    detail="{}\n\nAI 正在后台读取查询结果，完成后更新此卡片。".format(detail),
                    source="ai_pending",
                    background_kind=INTERPRETATION_READ_SUCCESS,
                )
            return _Presentation(detail=detail, source="template")
        if result.result_unknown:
            return _Presentation(detail=_unknown_detail(result), source="template")

        signature = compute_error_signature(result)
        base = _failure_detail(result)
        pattern = store.find_pattern(signature.fingerprint) if store is not None else None
        if pattern is not None:
            hit = store.record_pattern_hit(
                pattern.pattern_id,
                pending_job.message_id,
                result_scope(result, pending_job.route_region),
            ) or pattern
            logger.info(
                "execution failure matched template message_id=%s pattern_id=%s status=%s",
                pending_job.message_id,
                hit.pattern_id,
                hit.status,
            )
            if (
                hit.status == PATTERN_VERIFIED
                and hit.outcome == OUTCOME_WARNING
                and is_read_only_result(result, self._call_contracts())
            ):
                return _Presentation(
                    detail="【执行完成 · 服务器状态存疑】{}\n机器判定：{}\n\n说明来源：已审核典型状况（命中 {} 次）\n{}".format(
                        mask_sensitive_operation_data(result.summary),
                        mask_sensitive_operation_data(result.failure_reason),
                        hit.hit_count,
                        hit.diagnosis,
                    ),
                    source="verified_warning",
                    fingerprint=signature.fingerprint,
                    warning=True,
                )
            return _Presentation(
                detail="{}\n\n{}\n{}".format(base, _pattern_label(hit), hit.diagnosis),
                source="verified_template" if hit.status == PATTERN_VERIFIED else "candidate_template",
                fingerprint=signature.fingerprint,
            )
        if ai_available:
            return _Presentation(
                detail="{}\n\n典型故障库未命中，AI 正在后台分析，完成后更新此卡片。".format(base),
                source="ai_pending",
                background_kind=INTERPRETATION_FAILURE,
                fingerprint=signature.fingerprint,
            )
        return _Presentation(detail=base, source="template", fingerprint=signature.fingerprint)

    def _call_contracts(self) -> Any | None:
        read_call_contracts = getattr(self._script_catalog, "read_call_contracts", None)
        if not callable(read_call_contracts):
            return None
        try:
            return read_call_contracts()
        except Exception:  # noqa: BLE001 - unknown risk is treated as a write
            logger.warning("call contracts unavailable for result classification", exc_info=True)
            return None

    def recover_interpretations(self) -> None:
        """Resume background readings interrupted by a restart (never re-executes)."""
        if self._report_store is None:
            return
        try:
            self._report_store.prune_artifacts(self._config.execution_interpretation.artifact_retention_days)
            pending = self._report_store.recover_interpretations()
        except Exception:  # noqa: BLE001
            logger.exception("execution interpretation recovery failed")
            return
        for message_id in pending:
            self._schedule_interpretation(message_id)

    async def wait_for_interpretations(self) -> None:
        """Test/shutdown helper: wait for scheduled background readings."""
        while self._interpretation_tasks:
            await asyncio.gather(*tuple(self._interpretation_tasks), return_exceptions=True)

    def _schedule_interpretation(self, message_id: str) -> None:
        task = asyncio.create_task(self._run_interpretation(message_id))
        self._interpretation_tasks.add(task)
        task.add_done_callback(self._interpretation_tasks.discard)

    async def _run_interpretation(self, message_id: str) -> None:
        if self._interpretation_semaphore is None:
            self._interpretation_semaphore = asyncio.Semaphore(self._config.execution_interpretation.max_concurrent)
        async with self._interpretation_semaphore:
            try:
                await self._interpret_in_background(message_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("execution interpretation crashed message_id=%s", message_id)

    async def _interpret_in_background(self, message_id: str) -> None:
        store = self._report_store
        if store is None:
            return
        task = store.claim_interpretation(message_id)
        if task is None:
            return
        job = self._store.get(message_id)
        if job is None:
            store.finish_interpretation(message_id, "failed", error_kind="job_missing")
            return
        try:
            result = parse_execution_result(json.loads(task.payload_json), max_steps=self._config.muliu.max_steps)
        except Exception:  # noqa: BLE001
            logger.warning("execution interpretation payload invalid message_id=%s", message_id, exc_info=True)
            store.finish_interpretation(message_id, "failed", error_kind="bad_payload")
            return

        is_failure = task.kind == INTERPRETATION_FAILURE
        execution_data = _build_clean_execution_data(
            result,
            max_chars=self._config.execution_interpretation.max_execution_data_chars,
        )
        environment_context = _load_environment_context(
            self._config.execution_interpretation.server_environments_path
        )
        if is_failure:
            prompt = _build_failure_diagnosis_prompt(
                user_text=job.text,
                result=result,
                environment_context=environment_context,
                execution_data=execution_data,
            )
            base_detail = _failure_detail(result)
        else:
            prompt = _build_execution_interpretation_prompt(
                user_text=job.text,
                environment_context=environment_context,
                execution_data=execution_data,
            )
            base_detail = _success_detail(result)

        budget = self._config.execution_interpretation.background_timeout_seconds
        started = time.perf_counter()
        try:
            generation = await asyncio.wait_for(generate_with_provenance(self._interpretation_ai_client, [], prompt), timeout=budget)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - presentation must never hide a trusted result
            elapsed_ms = self._elapsed_ms(started)
            timed_out = isinstance(exc, asyncio.TimeoutError)
            logger.warning(
                "execution interpretation %s message_id=%s kind=%s elapsed_ms=%s input_chars=%s",
                "timed out" if timed_out else "unavailable",
                message_id,
                task.kind,
                elapsed_ms,
                len(prompt),
                exc_info=not timed_out,
            )
            store.finish_interpretation(
                message_id,
                "timed_out" if timed_out else "failed",
                error_kind="timeout" if timed_out else type(exc).__name__,
                input_chars=len(prompt),
                elapsed_ms=elapsed_ms,
            )
            store.set_artifact_source(message_id, "template")
            await self._update_result_card(
                job,
                result,
                "{}\n\nAI 分析未完成，请按上方{}继续核验。".format(base_detail, "失败原因" if is_failure else "执行结果"),
            )
            return

        elapsed_ms = self._elapsed_ms(started)
        text = mask_sensitive_operation_data(str(generation.text).strip())
        logger.info(
            "execution interpretation done message_id=%s kind=%s model=%s elapsed_ms=%s input_chars=%s output_chars=%s",
            message_id,
            task.kind,
            generation.model,
            elapsed_ms,
            len(prompt),
            len(text),
        )
        if not text:
            store.finish_interpretation(message_id, "failed", error_kind="empty_output", elapsed_ms=elapsed_ms)
            store.set_artifact_source(message_id, "template")
            await self._update_result_card(job, result, base_detail)
            return

        candidate_id = None
        if is_failure:
            pattern = store.add_candidate(
                compute_error_signature(result),
                text,
                message_id,
                result_scope(result, job.route_region),
            )
            candidate_id = pattern.pattern_id
            detail = "{}\n\n说明来源：AI 新分析（待人工确认）\n可信终态仍为：执行失败\n{}".format(base_detail, text)
            source = "ai_new"
        else:
            detail = text
            source = "ai_read"
        store.finish_interpretation(
            message_id,
            "done",
            summary=text,
            model_name=str(generation.model or ""),
            input_chars=len(prompt),
            elapsed_ms=elapsed_ms,
            candidate_pattern_id=candidate_id,
        )
        store.set_artifact_source(message_id, source)
        await self._update_result_card(job, result, detail, reply_text=text)

    async def _update_result_card(
        self,
        job: Any,
        result: MuliuExecutionResult,
        detail: str,
        *,
        reply_text: str = "",
    ) -> None:
        """Refresh only the explanation of an already-delivered result."""
        consumed = getattr(self._store, "resume_consumed", None)
        if callable(consumed) and consumed(job.message_id):
            # The requester already continued the remaining steps; that card now tracks the new job.
            return
        try:
            update_card = getattr(self._feishu_client, "update_card", None)
            if job.confirmation_method == "card" and job.confirmation_message_id and callable(update_card):
                await update_card(
                    job.confirmation_message_id,
                    build_execution_status_card(result, detail=detail, resume=self._resume_card_info(job)),
                )
            elif reply_text:
                await self._feishu_client.reply_text(job.message_id, reply_text)
        except Exception:  # noqa: BLE001
            logger.exception("execution interpretation card update failed message_id=%s", job.message_id)

    async def deliver_execution_result(
        self,
        pending_job: Any,
        reply_message_id: str,
        reply: str,
        *,
        status_card: dict[str, object] | None = None,
    ) -> None:
        """Deliver an already-persisted terminal outcome without changing it."""
        try:
            update_card = getattr(self._feishu_client, "update_card", None)
            if (
                status_card is not None
                and pending_job.confirmation_method == "card"
                and callable(update_card)
                and pending_job.confirmation_message_id
            ):
                await update_card(pending_job.confirmation_message_id, status_card)
            if pending_job.confirmation_method != "card" or pending_job.include_execution_logs:
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
        """Answer a planner-classified knowledge request (catalog > archived Q&A > MD docs)."""
        knowledge_started = time.perf_counter()
        answer = await self._qa_service.answer(job.text, decision.summary)
        knowledge_ms = self._elapsed_ms(knowledge_started)
        source_paths = list(answer.doc_paths)
        archive_refs = "、".join("#{}".format(archive_id) for archive_id in answer.archive_ids)
        if answer.reused_archive_id is not None:
            reply = "📚 以下为已审核答案（问答 #{}）。\n\n{}".format(answer.reused_archive_id, answer.text)
        else:
            lines = ["- {}".format(path) for path in source_paths]
            if archive_refs:
                lines.insert(0, "- 已审核问答 {}".format(archive_refs))
            if source_paths:
                reply = "📚 以下回答基于当前执行注册表和本地脚本资料生成。\n\n{}\n\n来源：\n{}".format(
                    answer.text, "\n".join(lines)
                )
            elif archive_refs:
                reply = "📚 以下回答基于当前执行注册表和已审核问答生成。\n\n{}\n\n来源：\n{}".format(
                    answer.text, "\n".join(lines)
                )
            else:
                reply = "📚 以下回答基于当前执行注册表生成。\n\n{}".format(answer.text)

        self._conversations.append(job.chat_id, "user", job.text)
        self._conversations.append(job.chat_id, "assistant", reply)
        reply_started = time.perf_counter()
        await self._reply_and_finish(message_id, reply)
        self._record_qa(job, answer)
        return (
            knowledge_ms,
            answer.elapsed_ms,
            self._elapsed_ms(reply_started),
            bool(source_paths),
            reply,
            tuple(source_paths),
        )

    async def _ask_team_kb(
        self,
        message_id: str,
        job: Any,
        question: str,
        *,
        generation: MuliuPlanGeneration | None = None,
    ) -> None:
        """Hand the question to the team-kb service; the answer arrives later through deliver_team_kb_answer."""
        if self._team_kb_questions is None or not question.strip():
            reply = "📒 请在「问题」后面写上要问的内容，例如：问题 浮桥之前是谁测的" if self._team_kb_questions is not None else "📒 组内知识库问答未启用。"
            await self._reply_and_finish(message_id, reply)
            self._append_conversation_log(job, "team_kb_unavailable", reply, generation=generation)
            return
        question = question.strip()
        exact, similar = self._qa_service.team_kb_lookup(question)
        if exact is not None:  # the reviewer's archived answer for exactly this question: no AI, no Worker round trip
            reply = "📒 组内知识库（已审核答案，问答 #{}）\n\n{}".format(exact["archive_id"], exact["answer"])
            await self._reply_and_finish(message_id, reply)
            self._conversations.append(job.chat_id, "user", job.text)
            self._conversations.append(job.chat_id, "assistant", reply)
            self._record_team_kb_qa(job.message_id, question, exact["answer"], sources={"archives": [exact["archive_id"]]},
                                    model="", elapsed_ms=0, status=QA_REUSED)
            self._append_conversation_log(job, "team_kb", reply, generation=generation)
            return
        references = [{"id": item["archive_id"], "question": item["canonical_question"], "answer": item["answer"]}
                      for item in similar]
        self._team_kb_questions.ask(message_id, job.chat_id, question, references=references)
        # The Gateway queue's part is done; a restart must not ask again (the answer is delivered by message id).
        self._store.mark_done(message_id)
        logger.warning("team kb question queued message_id=%s chars=%s references=%s",
                       message_id, len(question), [r["id"] for r in references])

    async def deliver_team_kb_answer(
        self,
        question: dict[str, Any],
        reply: str,
        *,
        kind: str = "team_kb",
        answer: dict[str, Any] | None = None,
    ) -> None:
        """Reply under the asker's message with the team-kb answer (or the timeout notice), log it and queue it for review."""
        message_id = question["question_id"]
        await self._feishu_client.reply_text(message_id, reply)
        if answer is not None and answer.get("ok"):
            asked, answered = int(question.get("asked_at") or 0), int(question.get("answered_at") or 0)
            self._record_team_kb_qa(
                message_id, question["text"], answer["text"],
                sources={"team_kb": answer["sources"], "archives": [r["id"] for r in question.get("references") or []]},
                model=answer.get("model", ""), elapsed_ms=max(0, answered - asked) * 1000 if asked else 0,
            )
        job = self._store.get(message_id)
        if job is None:
            return
        self._conversations.append(job.chat_id, "user", job.text)
        self._conversations.append(job.chat_id, "assistant", reply)
        self._append_conversation_log(
            job, kind, reply, source_paths=tuple(s.get("path", "") for s in question.get("sources") or []),
        )

    def _record_team_kb_qa(self, message_id: str, question: str, answer: str, *, sources: dict[str, Any], model: str,
                           elapsed_ms: int, status: str = QA_PENDING) -> None:
        """Put a team-kb answer on the review page (问答) so a wrong one can be corrected and archived."""
        store = self._qa_service.store
        if store is None:
            return
        try:
            store.record(message_id, question, answer, sources=sources, model=model, elapsed_ms=elapsed_ms,
                         status=status, domain=DOMAIN_TEAM_KB)
        except Exception:  # noqa: BLE001 - review bookkeeping must never affect the reply
            logger.warning("team kb qa record not stored message_id=%s", message_id, exc_info=True)

    def _record_qa(self, job: Any, answer: Any) -> None:
        store = self._qa_service.store
        if store is None:
            return
        try:
            store.record(
                job.message_id,
                job.text,
                answer.text,
                sources={"docs": list(answer.doc_paths), "archives": list(answer.archive_ids)},
                model=answer.model,
                elapsed_ms=answer.elapsed_ms,
                status=QA_REUSED if answer.reused_archive_id is not None else QA_PENDING,
            )
        except Exception:  # noqa: BLE001 - review bookkeeping must never affect the reply
            logger.warning("qa record not stored message_id=%s", job.message_id, exc_info=True)

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
            generation = await generate_with_provenance(self._ai_client, history, ai_user_text)
            ai_ms = self._elapsed_ms(ai_started)
            reply = generation.text
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

    def _offer_resume(self, job: Any, result: MuliuExecutionResult) -> dict[str, object] | None:
        """Record a one-time continue offer when the failed step was proven never submitted."""
        from_step = result.resumable_from_step
        offer = getattr(self._store, "offer_resume", None)
        if not from_step or job.confirmation_method != "card" or not callable(offer):
            return None
        try:
            remaining = parse_plan(job.plan_json, max_steps=self._config.muliu.max_steps).steps[from_step - 1:]
        except MuliuPlanError:
            return None
        if not remaining:
            return None
        token = secrets.token_hex(8)
        offer(job.message_id, from_step, token)
        return {
            "request_message_id": job.message_id,
            "confirmation_token": token,
            "from_step": from_step,
            "steps": [step.description for step in remaining],
        }

    def _resume_card_info(self, job: Any) -> dict[str, object] | None:
        getter = getattr(self._store, "get_resume_offer", None)
        offer = getter(job.message_id) if callable(getter) else None
        if offer is None:
            return None
        from_step, token = offer
        try:
            remaining = parse_plan(job.plan_json, max_steps=self._config.muliu.max_steps).steps[from_step - 1:]
        except MuliuPlanError:
            return None
        return {
            "request_message_id": job.message_id,
            "confirmation_token": token,
            "from_step": from_step,
            "steps": [step.description for step in remaining],
        }

    async def _resume_remaining_steps(
        self,
        *,
        request_message_id: str,
        confirmation_message_id: str,
        chat_id: str,
        user_id: str,
        token: str,
    ) -> dict[str, object] | None:
        """Continue the never-submitted steps of a failed plan, cut deterministically from the stored plan."""
        original = self._store.get(request_message_id)
        if original is None:
            return None
        from_step = self._store.claim_resume(
            request_message_id,
            confirmation_message_id=confirmation_message_id,
            chat_id=chat_id,
            user_id=user_id,
            token=token,
            max_age_seconds=RESUME_OFFER_SECONDS,
        )
        if from_step is None:
            return None
        try:
            plan = parse_plan(original.plan_json, max_steps=self._config.muliu.max_steps)
            steps = plan.steps[from_step - 1:]
            if not steps:
                raise MuliuPlanError("没有可继续执行的步骤")
            remaining = MuliuPlan(
                summary="{}（继续：第 {}–{} 步）".format(plan.summary, from_step, len(plan.steps)),
                steps=list(steps),
                kind=plan.kind,
                route_scope=plan.route_scope,
            )
            if self._muliu_firewall is not None:
                self._muliu_firewall.validate(remaining)
            if self._route_resolver is not None:
                snapshot = self._resolve_route_snapshot(remaining)
                if snapshot.region != original.route_region:
                    raise MuliuRoutingError("剩余步骤的路由与原计划不一致")
        except (MuliuPlanError, MuliuFirewallError, MuliuRoutingError) as exc:
            logger.warning("resume rejected message_id=%s error=%s", request_message_id, exc)
            return build_operation_status_card(
                "测试服操作未继续",
                original.text,
                "剩余步骤未通过校验，未执行任何操作：{}".format(exc),
                template="red",
            )

        resume_job = self._store.create_resume_job(
            original,
            "{}-r{}".format(original.message_id, from_step),
            text="继续执行第 {}–{} 步：{}".format(from_step, len(plan.steps), original.text),
            plan_json=_plan_to_json(remaining),
        )
        if resume_job is None:
            return None
        self._append_conversation_log(resume_job, "operation_resumed", "继续执行剩余步骤：{}".format(remaining.summary))
        accepted = await self._start_claimed_confirmation(resume_job, resume_job.confirmation_message_id, confirmer_id=user_id)
        if not accepted:
            return build_operation_status_card(
                "测试服操作未继续",
                resume_job.text,
                "剩余步骤未进入执行队列。",
                template="red",
            )
        return build_operation_status_card(
            "测试服操作已提交（继续执行剩余步骤）",
            remaining.summary,
            "\n".join(
                ["将从第 {} 步继续，已完成的步骤不会重复执行：".format(from_step)]
                + ["{}. {}".format(from_step + index, step.description) for index, step in enumerate(steps)]
                + ["请勿重复确认或重试。"]
            ),
            template="blue",
        )

    def _schedule_background(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _member_name(self, chat_id: str, open_id: str) -> str:
        """Display name of a chat member from a cached member list ("" if unknown)."""
        if not open_id:
            return ""
        name = self._member_names.get(open_id)
        if name is not None:
            return name
        lister = getattr(self._feishu_client, "list_chat_member_names", None)
        last_lookup = self._member_lookup_at.get(chat_id, 0.0)
        # Unknown member: refresh that chat's member list at most every 5 minutes.
        if not chat_id or not callable(lister) or time.monotonic() - last_lookup < 300:
            return ""
        self._member_lookup_at[chat_id] = time.monotonic()
        try:
            self._member_names.update(await lister(chat_id))
        except Exception:  # noqa: BLE001 - display data only
            logger.warning("chat member lookup failed chat_id=%s", chat_id, exc_info=True)
            return ""
        return self._member_names.get(open_id, "")

    async def _remember_requester_name(self, job: Any) -> None:
        """Record who asked, for the review page. Never blocks or fails the request."""
        if not job.user_id or job.chat_type != "group" or not callable(getattr(self._store, "set_user_name", None)):
            return
        name = await self._member_name(job.chat_id, job.user_id)
        if not name:
            return
        try:
            self._store.set_user_name(job.message_id, name)
        except Exception:  # noqa: BLE001
            logger.warning("requester name not stored message_id=%s", job.message_id, exc_info=True)

    def _notify_clear(self, event: str, job: Any, plan: MuliuPlan, route_region: str, *, operator_id: str) -> None:
        """Fire-and-forget clear-data event when the plan contains clear steps."""
        notifier = self._clear_notifier
        if notifier is None or not notifier.enabled:
            return
        clear_steps = notifier.clear_steps(plan)
        if not clear_steps:
            return
        self._schedule_background(self._send_clear_event(event, job, plan, clear_steps, route_region, operator_id))

    async def _send_clear_event(
        self,
        event: str,
        job: Any,
        plan: MuliuPlan,
        clear_steps: list[Any],
        route_region: str,
        operator_id: str,
    ) -> None:
        try:
            server_ids: list[str] = []
            contracts = self._call_contracts()
            if contracts is not None:
                try:
                    clear_plan = MuliuPlan(summary=plan.summary, steps=list(clear_steps), kind=plan.kind)
                    server_ids = list(dict.fromkeys(contracts.extract_server_ids(clear_plan)))
                except Exception:  # noqa: BLE001 - server ids are best-effort context
                    server_ids = []
            operator_name = await self._member_name(job.chat_id, operator_id) or operator_id
            groups = self._group_servers_by_region(server_ids)
            cc_webhooks = set(self._clear_notifier.cc_webhooks)
            for region_key, region_name, region_servers in groups:
                webhook_url = self._clear_notifier.webhook_for_region(region_key)
                if webhook_url in cc_webhooks:
                    continue  # that group gets the combined all-region notice below
                if not webhook_url:
                    logger.warning(
                        "clear notification skipped: no webhook for region=%s message_id=%s", region_key, job.message_id
                    )
                    continue
                text = self._clear_notifier.build_text(
                    event,
                    operator_name=operator_name,
                    server_ids=region_servers,
                    plan_summary=plan.summary,
                    region_name=region_name,
                )
                await self._clear_notifier.send(text, webhook_url)
            if cc_webhooks:
                combined = "，".join(
                    "{} {}".format(name, "、".join(servers)) if name else "、".join(servers)
                    for _key, name, servers in groups
                    if servers
                ) or "（未识别服号）"
                text = self._clear_notifier.build_text(
                    event,
                    operator_name=operator_name,
                    server_ids=server_ids,
                    plan_summary=plan.summary,
                    target=combined,
                )
                for webhook_url in self._clear_notifier.cc_webhooks:
                    await self._clear_notifier.send(text, webhook_url)
        except Exception:  # noqa: BLE001 - notification must never affect the operation
            logger.exception("clear notification crashed message_id=%s event=%s", job.message_id, event)

    async def wait_for_background(self) -> None:
        """Test helper: wait for display-only background tasks."""
        while self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

    def _group_servers_by_region(self, server_ids: list[str]) -> list[tuple[str | None, str, list[str]]]:
        """Split servers by owning region (first-seen order); unknown servers go to a ``None`` group."""
        try:
            environments = ServerEnvironments.load(self._config.execution_interpretation.server_environments_path)
        except ServerEnvironmentsError:
            logger.warning("clear notification: environments unavailable, using fallback group", exc_info=True)
            return [(None, "", list(server_ids))]
        groups: dict[str | None, tuple[str, list[str]]] = {}
        for server_id in server_ids:
            owner = environments.region_for_server_id(server_id)
            key, name = owner if owner else (None, "")
            groups.setdefault(key, (name, []))[1].append(server_id)
        if not groups:
            return [(None, "", [])]
        return [(key, name, servers) for key, (name, servers) in groups.items()]

    def _record_planning(self, message_id: str, generation: MuliuPlanGeneration) -> None:
        record = getattr(self._store, "record_planning", None)
        if not callable(record) or not generation.ai_calls:
            return
        try:
            record(message_id, generation.ai_models, generation.ai_ms, len(generation.ai_calls))
        except Exception:  # noqa: BLE001 - observability must not break planning
            logger.warning("planning trace not recorded message_id=%s", message_id, exc_info=True)

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

    async def _notify_job_failure(self, message_id: str, exc: Exception) -> None:
        """Tell the requester a job failed instead of leaving them with silence.

        The failure reply is best-effort: if Feishu itself is unavailable the
        job stays ``failed`` and the notification error is only logged, so a
        broken notification can never mask or overwrite the original failure.
        """
        try:
            await self._feishu_client.reply_card(message_id, build_job_failure_card(str(exc)))
        except Exception:  # noqa: BLE001 - notification must not mask the original failure
            logger.exception("job failure notify failed message_id=%s", message_id)

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


@dataclass(frozen=True)
class _Presentation:
    detail: str
    source: str
    background_kind: str = ""
    fingerprint: str = ""
    warning: bool = False


def _terminal_status(result: MuliuExecutionResult) -> str:
    if result.succeeded:
        return "done"
    if result.result_unknown:
        return "submitted_result_unknown"
    return "failed"


def _success_detail(result: MuliuExecutionResult) -> str:
    lines = ["【执行完成】{}".format(result.summary)]
    total = len(result.step_results)
    for index, step in enumerate(result.step_results, start=1):
        lines.append("{}/{} {} {}".format(index, total, "✅" if step.succeeded else "❌", step.description))
    return mask_sensitive_operation_data("\n".join(lines))


def _unknown_detail(result: MuliuExecutionResult) -> str:
    return mask_sensitive_operation_data(
        "【结果尚未核验】{}\n请勿重复确认或重试同一操作。".format(result.result_unknown_reason)
    )


def _failure_detail(result: MuliuExecutionResult) -> str:
    return mask_sensitive_operation_data("【执行失败】{}".format(result.failure_reason))


def _pattern_label(pattern: Any) -> str:
    if pattern.status == PATTERN_VERIFIED:
        return "说明来源：已审核典型故障（命中 {} 次）".format(pattern.hit_count)
    return "说明来源：AI 分析 · 未人工确认（已复用 {} 次）".format(pattern.hit_count)


def _group_result_summary(reply: str, *, max_chars: int = 1200) -> str:
    """Show a bounded, log-free conclusion in the terminal status card."""
    conclusion = reply.split("\n\n---\n\n执行明细：", 1)[0].strip()
    conclusion = mask_sensitive_operation_data(conclusion)
    if len(conclusion) <= max_chars:
        return conclusion
    return "{}\n…（群内摘要已截断）".format(conclusion[:max_chars])


def result_card_detail(reply: str, result: MuliuExecutionResult) -> str:
    """Prefer an AI conclusion but never put raw execution logs in a status card."""
    summary = _group_result_summary(reply)
    if "执行明细：" in reply:
        return summary
    if result.succeeded:
        return "【执行完成】{}".format(result.summary)
    if result.result_unknown:
        return "【结果尚未核验】{}".format(result.result_unknown_reason)
    return "【执行失败】{}".format(result.failure_reason)


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
        "1. 第一行必须给出本次任务的终态摘要，使用【执行完成】、【执行失败】或【结果尚未核验】之一；不能把脚本或 Muliu 返回成功说成游戏内实际生效。\n"
        "2. 只陈述实际执行结果明确支持的事实；数据不足时明确说明无法确认，绝不猜测。\n"
        "3. 若结果包含多台服务器的 basic_info.sh 查询，必须按服号分为：已开服、已关闭、查询报错/无法确认三组；每台服只能归入一组。没有某类时写“无”。\n"
        "4. 用简洁的中文 Markdown 梳理关键结果和失败点；不要复述整段原始日志。\n"
        "5. 若没有可用脚本输出，也必须基于已知终态和失败原因说明结果；不得虚构执行细节。\n"
        "6. 严禁输出私网 IP、主机地址、账号、凭据、Cookie、数据库连接、文件系统工作目录或执行命令。\n"
        "7. 不要建议、触发或描述新的执行操作。\n"
        "8. 严禁添加任何多余前缀、说明性标题或客套话（如‘AI 研读结论：’、‘分析结果：’等），直接输出结论。"
    ).format(mask_sensitive_operation_data(user_text), environment_context, execution_data)


def _build_failure_diagnosis_prompt(
    *,
    user_text: str,
    result: MuliuExecutionResult,
    environment_context: str,
    execution_data: str,
) -> str:
    return (
        "你是飞书测试服运维 AI 助手。一次操作已由机器人判定为失败；你只能依据下列脱敏后的真实结果做故障诊断。"
        "你的诊断会被收集为【典型故障模板】，以后同类错误（可能是其他服号、其他参数）会直接复用。"
        "\n\n【可信终态】failed（执行失败，不可更改）"
        "\n\n【可信失败原因】\n{}"
        "\n\n【用户原始提问】\n{}"
        "\n\n【环境资产参考】\n{}"
        "\n\n【实际执行结果】\n{}"
        "\n\n【输出约束】\n"
        "1. 不得把失败说成成功；日志中出现“完成”“成功”等文本与失败终态冲突时，明确指出冲突，不裁决成功。\n"
        "2. 输出：可能原因（只写日志与失败原因能支持的）、排查/核验建议、证据缺口。\n"
        "3. 写成适用于同类错误的通用说明：不要写具体服号、赛季、剧本号等本次参数。\n"
        "4. 只给核验与排查建议，不建议自动重试、重放或重复执行写操作。\n"
        "5. 严禁输出私网 IP、主机地址、账号、凭据、Cookie、Token、数据库连接、工作目录或执行命令。\n"
        "6. 简洁中文，不超过 300 字，不要客套话和多余标题。"
    ).format(
        mask_sensitive_operation_data(result.failure_reason or "（无）"),
        mask_sensitive_operation_data(user_text),
        environment_context,
        execution_data or "（无可用脚本输出）",
    )


def _plan_to_json(plan: MuliuPlan) -> str:
    payload = {
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
    }
    if plan.route_scope is not None:
        payload["route_scope"] = plan.route_scope
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
