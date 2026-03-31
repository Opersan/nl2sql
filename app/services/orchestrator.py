"""Orchestrator – Sprint 1 + Sprint 2.

Sprint 1 – ``Orchestrator``
============================
Chains validation → compilation → execution without any LLM involvement.
Returns a structured ``OrchestrationResult`` with clear error-phase
separation.

Sprint 2 – ``ChatOrchestrator``
================================
Upper-level orchestrator that adds LLM planning and narration:

  User message → Planner → QueryPlan → Orchestrator.run_plan → Narrator → ChatResult

Error-source contract
=====================
* **Validation errors** → ``result.validation.errors``
* **Compilation errors** → ``result.compilation_error``
* **Execution errors**  → ``result.execution_result.error_message``

The ``failed_phase`` field (``ErrorPhase`` enum) on ``OrchestrationResult``
tells callers *which* stage failed so they can branch on a single
discriminator.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.core.exceptions import CompilationError, PlannerError
from app.core.logging import get_logger
from app.domain.execution_models import (
    ErrorPhase,
    ExecutionResult,
    ExecutionStatus,
    OrchestrationResult,
)
from app.domain.models import ChatResult
from app.domain.query_plan import QueryPlan
from app.providers.executor.base import ExecutorProvider
from app.services.narrator_service import NarratorService
from app.services.planner_service import PlannerService
from app.services.session_service import SessionService
from app.services.clarification_state_manager import ClarificationStateManager
from app.services.sql_compiler import SQLCompiler
from app.services.validation_repair_service import ValidationRepairService
from app.services.execution_risk import assess_pre_execution_risk, bind_summary, sql_fingerprint
from app.services.followup_context_merge import FollowupContextMergeService
from app.services.validation_service import ValidationService

if TYPE_CHECKING:
    from app.domain.trace_models import TraceCollector

logger = get_logger(__name__)


_PRECHECK_REASON_TO_SUBTYPE: dict[str, str] = {
    "precheck_date_literal_invalid": "oracle_date_type_error",
    "precheck_timeout_prone_shape": "timeout",
    "precheck_timeout_prone_large_join": "timeout",
    "precheck_timeout_prone_simple_listing": "timeout",
    "precheck_invalid_filter_value": "unknown_execution_error",
}


class Orchestrator:
    """Sprint 1 pipeline orchestrator (no LLM).

    The orchestrator no longer resolves the target table itself – the
    ``ValidationService`` resolves it and attaches it to
    ``ValidationResult.resolved_table``.  This eliminates the redundant
    catalog round-trip and the associated ``type: ignore`` escape hatch.
    """

    def __init__(
        self,
        validation_service: ValidationService,
        compiler: SQLCompiler,
        executor: ExecutorProvider,
        validation_repair_service: ValidationRepairService | None = None,
    ) -> None:
        self._validator = validation_service
        self._compiler = compiler
        self._executor = executor
        catalog = getattr(validation_service, "_catalog", None)
        self._validation_repair = validation_repair_service or (
            ValidationRepairService(catalog) if catalog is not None else None
        )
        self._last_trace: dict[str, Any] | None = None
        self._last_trace_by_task: dict[int, dict[str, Any] | None] = {}

    def _set_last_trace(self, trace: dict[str, Any] | None) -> None:
        self._last_trace = trace
        task = asyncio.current_task()
        if task is not None:
            self._last_trace_by_task[id(task)] = trace
            if len(self._last_trace_by_task) > 2048:
                self._last_trace_by_task.clear()

    @property
    def last_trace(self) -> dict[str, Any] | None:
        """Return deterministic pipeline trace data from the most recent run."""
        task = asyncio.current_task()
        if task is not None and id(task) in self._last_trace_by_task:
            return self._last_trace_by_task[id(task)]
        return self._last_trace

    async def run_plan(
        self,
        plan: QueryPlan,
        *,
        trace_collector: TraceCollector | None = None,
    ) -> OrchestrationResult:
        """Execute the full deterministic pipeline for *plan*.

        Steps
        -----
        1. Validate the plan against the catalog.
        2. If validation fails → return immediately (``failed_phase = VALIDATION``).
        3. Compile the plan into Oracle SQL using the resolved table from
           validation (no second catalog lookup).
        4. Execute via the configured executor.
        5. Return the aggregated result with the appropriate ``failed_phase``.
        """
        self._set_last_trace({
            "input_plan": plan.model_dump(mode="json"),
            "last_completed_stage": None,
            "current_stage_at_failure": None,
            "root_cause_stage": None,
            "validation": None,
            "validation_repair": None,
            "compile": None,
            "execute": None,
        })

        # 1 – Validate
        if trace_collector:
            _tc_val_mono = trace_collector.stage_started(
                "validation",
                summary="Validating QueryPlan against catalog…",
                metadata={"plan_table": plan.table, "plan_intent": plan.intent},
            )
        validation_started = time.perf_counter()
        validation = await self._validator.validate(plan)
        validation_ms = int((time.perf_counter() - validation_started) * 1000)

        self._last_trace["validation"] = {
            "ok": validation.ok,
            "errors": [issue.model_dump(mode="json") for issue in validation.errors],
            "warnings": [issue.model_dump(mode="json") for issue in validation.warnings],
            "resolved_table": validation.resolved_table.name if validation.resolved_table else None,
            "resolved_tables": sorted(validation.resolved_tables.keys()),
            "latency_ms": validation_ms,
        }

        if not validation.ok and self._validation_repair is not None:
            repaired_plan, repair_result, repair_trace = await self._validation_repair.repair(plan, validation)
            self._last_trace["validation_repair"] = {
                "attempted": repair_trace.get("attempted", False),
                "repair_applied": repair_trace.get("repaired", False),
                "repaired": repair_trace.get("repaired", False),
                "reason_codes": list(dict.fromkeys(repair_trace.get("reasons", []))),
                "skipped_reason_codes": list(dict.fromkeys(repair_trace.get("skipped_reason_codes", []))),
                "repair_actions": [
                    {
                        "repair_type": action.repair_type,
                        "description": action.description,
                        "field_path": action.field_path,
                        "original_value": action.original_value,
                        "repaired_value": action.repaired_value,
                    }
                    for action in repair_result.actions
                ],
                "before": plan.model_dump(mode="json"),
                "after": repaired_plan.model_dump(mode="json"),
            }
            if repair_result.repair_applied:
                revalidation_started = time.perf_counter()
                revalidation = await self._validator.validate(repaired_plan)
                self._last_trace["validation_repair"].update({
                    "revalidated": True,
                    "revalidate_ok": revalidation.ok,
                    "revalidate_latency_ms": int((time.perf_counter() - revalidation_started) * 1000),
                    "revalidate_errors": [issue.model_dump(mode="json") for issue in revalidation.errors],
                })
                if revalidation.ok:
                    plan = repaired_plan
                    validation = revalidation
                    self._last_trace["input_plan"] = plan.model_dump(mode="json")
                    self._last_trace["validation"] = {
                        "ok": validation.ok,
                        "errors": [issue.model_dump(mode="json") for issue in validation.errors],
                        "warnings": [issue.model_dump(mode="json") for issue in validation.warnings],
                        "resolved_table": validation.resolved_table.name if validation.resolved_table else None,
                        "resolved_tables": sorted(validation.resolved_tables.keys()),
                        "latency_ms": validation_ms,
                    }
                else:
                    reason_codes = list(self._last_trace["validation_repair"].get("reason_codes", []))
                    if "revalidate_failed_after_repair" not in reason_codes:
                        reason_codes.append("revalidate_failed_after_repair")
                    self._last_trace["validation_repair"]["reason_codes"] = reason_codes

        if validation.ok:
            self._last_trace["last_completed_stage"] = "validation"
        else:
            self._last_trace["current_stage_at_failure"] = "validation"
            self._last_trace["root_cause_stage"] = "validation"

        if trace_collector:
            from app.services.trace_serializer import build_validation_payload, safe_payload

            validation_payload = build_validation_payload(self._last_trace.get("validation") or {})
            if self._last_trace.get("validation_repair"):
                validation_payload["validation_repair"] = safe_payload(
                    self._last_trace["validation_repair"]
                )
            validation_summary = (
                f"Validation OK — table={self._last_trace['validation'].get('resolved_table')}"
                if validation.ok
                else "Validation FAILED: " + ", ".join(e.message for e in validation.errors[:2])
            )
            if validation.ok:
                trace_collector.stage_completed(
                    "validation",
                    started_at_mono=_tc_val_mono,
                    summary=validation_summary,
                    payload=validation_payload,
                    metadata={"resolved_table": self._last_trace["validation"].get("resolved_table")},
                )
            else:
                trace_collector.stage_failed(
                    "validation",
                    started_at_mono=_tc_val_mono,
                    summary=validation_summary,
                    payload=validation_payload,
                )

        if not validation.ok:
            logger.info("Plan validation failed: %s", validation.errors)
            return OrchestrationResult(
                validation=validation,
                failed_phase=ErrorPhase.VALIDATION,
            )

        # 2 – Table is already resolved by validation; no re-lookup needed.
        table = validation.resolved_table
        assert table is not None, (
            "validation.ok is True but resolved_table is None – "
            "this indicates a bug in ValidationService."
        )

        # 3 – Compile
        if trace_collector:
            _tc_cmp_mono = trace_collector.stage_started(
                "compile",
                summary="Compiling QueryPlan → Oracle SQL…",
                metadata={"table": validation.resolved_table.name if validation.resolved_table else None},
            )
        compile_started = time.perf_counter()
        try:
            compiled = self._compiler.compile(
                plan,
                table,
                extra_tables=validation.resolved_tables or None,
            )
        except CompilationError as exc:
            self._last_trace["compile"] = {
                "ok": False,
                "error": str(exc),
                "latency_ms": int((time.perf_counter() - compile_started) * 1000),
            }
            self._last_trace["current_stage_at_failure"] = "compile"
            self._last_trace["root_cause_stage"] = "compile"
            logger.error("Compilation error: %s", exc)
            if trace_collector:
                trace_collector.stage_failed(
                    "compile",
                    started_at_mono=_tc_cmp_mono,
                    summary=f"Compilation error: {exc}",
                    payload={"ok": False, "error": str(exc)},
                )
            return OrchestrationResult(
                validation=validation,
                failed_phase=ErrorPhase.COMPILATION,
                compilation_error=str(exc),
            )

        self._last_trace["compile"] = {
            "ok": True,
            "sql": compiled.sql,
            "params": compiled.params,
            "table": compiled.table,
            "selected_columns": list(compiled.selected_columns),
            "executed_sql_fingerprint": sql_fingerprint(compiled.sql),
            "bind_summary": bind_summary(compiled),
            "latency_ms": int((time.perf_counter() - compile_started) * 1000),
        }
        self._last_trace["last_completed_stage"] = "compile"

        logger.info("Compiled SQL:\n%s", compiled.sql)
        logger.info("Params: %s", compiled.params)

        precheck = assess_pre_execution_risk(plan, table)
        execution_plan = plan
        execution_compiled = compiled
        if precheck.safe_mode_applied and precheck.effective_limit is not None and precheck.effective_limit < plan.limit:
            execution_plan = plan.model_copy(update={"limit": precheck.effective_limit})
            logger.info(
                "Safe-mode applied: limit %d -> %d.",
                plan.limit,
                execution_plan.limit,
            )
            safe_compile_started = time.perf_counter()
            try:
                execution_compiled = self._compiler.compile(
                    execution_plan,
                    table,
                    extra_tables=validation.resolved_tables or None,
                )
            except CompilationError as exc:
                self._last_trace["compile"] = {
                    "ok": False,
                    "error": str(exc),
                    "latency_ms": int((time.perf_counter() - safe_compile_started) * 1000),
                }
                self._last_trace["current_stage_at_failure"] = "compile"
                self._last_trace["root_cause_stage"] = "compile"
                logger.error("Safe-mode recompilation error: %s", exc)
                if trace_collector:
                    trace_collector.stage_failed(
                        "compile",
                        started_at_mono=_tc_cmp_mono,
                        summary=f"Safe-mode recompilation error: {exc}",
                        payload={"ok": False, "error": str(exc)},
                    )
                return OrchestrationResult(
                    validation=validation,
                    failed_phase=ErrorPhase.COMPILATION,
                    compilation_error=str(exc),
                )

            self._last_trace["compile"] = {
                "ok": True,
                "sql": execution_compiled.sql,
                "params": execution_compiled.params,
                "table": execution_compiled.table,
                "selected_columns": list(execution_compiled.selected_columns),
                "executed_sql_fingerprint": sql_fingerprint(execution_compiled.sql),
                "bind_summary": bind_summary(execution_compiled),
                "latency_ms": int((time.perf_counter() - compile_started) * 1000),
            }
            logger.info("Safe-mode SQL:\n%s", execution_compiled.sql)
            logger.info("Safe-mode params: %s", execution_compiled.params)

        if trace_collector and self._last_trace.get("compile", {}).get("ok"):
            from app.services.trace_serializer import build_compile_payload

            compile_trace = self._last_trace["compile"]
            trace_collector.stage_completed(
                "compile",
                started_at_mono=_tc_cmp_mono,
                summary=f"SQL compiled — table={compile_trace.get('table')}, cols={len(compile_trace.get('selected_columns', []))}",
                payload=build_compile_payload(compile_trace),
                metadata={"fingerprint": compile_trace.get("executed_sql_fingerprint")},
            )

        self._last_trace["pre_execution"] = {
            "pre_execution_risk_flags": list(precheck.pre_execution_risk_flags),
            "execution_guard_reason": precheck.execution_guard_reason,
            "execution_skipped_reason": precheck.execution_skipped_reason,
            "why_not_executed": precheck.execution_skipped_reason,
            "safe_mode_applied": precheck.safe_mode_applied,
            "safe_mode_reason": precheck.safe_mode_reason,
            "original_limit": plan.limit,
            "effective_limit": execution_plan.limit,
            "should_execute": precheck.should_execute,
            "executed_sql_fingerprint": sql_fingerprint(execution_compiled.sql),
            "bind_summary": bind_summary(execution_compiled),
        }

        if not precheck.should_execute:
            reason = str(precheck.execution_skipped_reason or "pre_execution_blocked")
            subtype = _PRECHECK_REASON_TO_SUBTYPE.get(reason)
            self._last_trace["execute"] = {
                "status": "skipped",
                "row_count": 0,
                "columns": [],
                "error_message": reason,
                "execution_error_subtype": subtype,
                "execution_error_message_normalized": reason,
                "execution_guard_reason": precheck.execution_guard_reason,
                "execution_skipped_reason": reason,
                "pre_execution_risk_flags": list(precheck.pre_execution_risk_flags),
                "safe_mode_applied": precheck.safe_mode_applied,
                "safe_mode_reason": precheck.safe_mode_reason,
                "effective_limit": execution_plan.limit,
                "why_not_executed": reason,
                "executed_sql_fingerprint": sql_fingerprint(execution_compiled.sql),
                "bind_summary": bind_summary(execution_compiled),
                "latency_ms": 0,
            }
            if trace_collector:
                from app.services.trace_serializer import build_execute_payload

                trace_collector.stage_skipped(
                    "execute",
                    summary=f"Execution skipped: {reason}",
                    payload=build_execute_payload(self._last_trace["execute"]),
                )
            return OrchestrationResult(
                validation=validation,
                compiled_query=execution_compiled,
                execution_result=ExecutionResult(
                    status=ExecutionStatus.ERROR,
                    error_message=reason,
                    execution_error_subtype=subtype,
                    execution_error_message_normalized=reason,
                ),
                failed_phase=ErrorPhase.EXECUTION,
            )


        # 4 – Execute
        if trace_collector:
            _tc_exe_mono = trace_collector.stage_started(
                "execute",
                summary="Executing SQL query…",
            )
        execute_started = time.perf_counter()
        try:
            execution = await self._executor.execute(execution_compiled)
        except Exception as exc:
            logger.error("Execution error: %s", exc)
            subtype = getattr(exc, "execution_error_subtype", None) or "unknown_execution_error"
            msg_norm = getattr(exc, "execution_error_message_normalized", None) or str(exc)[:120]
            execution = ExecutionResult(
                status=ExecutionStatus.ERROR,
                error_message=str(exc),
                execution_error_subtype=subtype,
                execution_error_message_normalized=msg_norm,
            )
        self._last_trace["execute"] = {
            "status": execution.status.value,
            "row_count": execution.row_count,
            "columns": list(execution.columns),
            "error_message": execution.error_message,
            "execution_error_subtype": execution.execution_error_subtype,
            "execution_error_message_normalized": execution.execution_error_message_normalized,
            "execution_time_ms": execution.execution_time_ms,
            "executed_sql_fingerprint": sql_fingerprint(execution_compiled.sql),
            "bind_summary": bind_summary(execution_compiled),
            "execution_guard_reason": precheck.execution_guard_reason,
            "execution_skipped_reason": precheck.execution_skipped_reason,
            "pre_execution_risk_flags": list(precheck.pre_execution_risk_flags),
            "safe_mode_applied": precheck.safe_mode_applied,
            "safe_mode_reason": precheck.safe_mode_reason,
            "effective_limit": execution_plan.limit,
            "latency_ms": int((time.perf_counter() - execute_started) * 1000),
        }

        if execution.status == ExecutionStatus.ERROR:
            self._last_trace["current_stage_at_failure"] = "execute"
            self._last_trace["root_cause_stage"] = "execute"
        else:
            self._last_trace["last_completed_stage"] = "execute"

        if trace_collector:
            from app.services.trace_serializer import build_execute_payload

            execute_payload = build_execute_payload(self._last_trace.get("execute") or {})
            execute_summary = (
                f"Executed — {execution.row_count} rows, {len(execution.columns)} cols"
                if execution.status != ExecutionStatus.ERROR
                else f"Execution error: {execution.error_message or 'unknown'}"[:120]
            )
            if execution.status != ExecutionStatus.ERROR:
                trace_collector.stage_completed(
                    "execute",
                    started_at_mono=_tc_exe_mono,
                    summary=execute_summary,
                    payload=execute_payload,
                    metadata={"row_count": execution.row_count},
                )
            else:
                trace_collector.stage_failed(
                    "execute",
                    started_at_mono=_tc_exe_mono,
                    summary=execute_summary,
                    payload=execute_payload,
                )

        # 5 – Determine execution-phase failure and return.
        failed = (
            ErrorPhase.EXECUTION
            if execution.status == ExecutionStatus.ERROR
            else None
        )

        return OrchestrationResult(
            validation=validation,
            compiled_query=execution_compiled,
            execution_result=execution,
            failed_phase=failed,
        )


# ---------------------------------------------------------------------------
# Sprint 2 – ChatOrchestrator (LLM planner + narrator)
# ---------------------------------------------------------------------------


class ChatOrchestrator:
    """Upper-level orchestrator: user message → answer.

    Chains:

    1. ``PlannerService``   – NL → ``QueryPlan``
    2. ``Orchestrator``     – ``QueryPlan`` → ``OrchestrationResult``
    3. ``NarratorService``  – ``OrchestrationResult`` → Turkish text

    Session state is maintained via ``SessionService``.
    """

    def __init__(
        self,
        planner: PlannerService,
        orchestrator: Orchestrator,
        narrator: NarratorService,
        session_service: SessionService,
        clarification_manager: ClarificationStateManager | None = None,
        run_store: Any | None = None,
        followup_merge: FollowupContextMergeService | None = None,
    ) -> None:
        self._planner = planner
        self._orchestrator = orchestrator
        self._narrator = narrator
        self._sessions = session_service
        self._clarification_manager = clarification_manager
        self._run_store = run_store
        self._followup_merge = followup_merge or FollowupContextMergeService(llm=planner._llm)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def has_data_context(self, session_id: str) -> bool:
        """Check if there is an active followup context (snapshot) for this session."""
        return self._followup_merge.get_snapshot(session_id) is not None

    async def _persist_stage_from_event(self, run_id: str, event: Any, order: int) -> None:
        """Persist a StageEvent to the run store.

        Raises on failure so the caller (_finalize_run) can collect warnings.
        """
        if self._run_store is None:
            return
        await self._run_store.persist_stage(
            run_id,
            stage_name=event.stage_name,
            stage_order=order,
            status=event.status.value if hasattr(event.status, "value") else str(event.status),
            started_at=str(event.started_at) if event.started_at else None,
            finished_at=str(event.completed_at) if event.completed_at else None,
            elapsed_ms=event.elapsed_ms,
            summary=event.summary,
            payload=event.payload,
        )

    async def _finalize_run(
        self,
        run_id: str | None,
        conversation_id: str,
        *,
        status: str,
        answer: str,
        tc: "TraceCollector | None" = None,
        clarification_id: str | None = None,
        clarification_options: Any = None,
        clarification_question: str | None = None,
    ) -> None:
        """Persist assistant message, collected stages, and finish run.

        Individual persistence steps are wrapped separately so one failure
        does not prevent the remaining steps.  Failures are logged as
        warnings and emitted as trace events for observability.
        """
        if self._run_store is None:
            return
        _warnings: list[str] = []

        # 1 — assistant message
        try:
            await self._run_store.persist_message(
                conversation_id, "assistant", answer, source="pipeline",
            )
        except Exception as exc:
            _warnings.append(f"persist_message: {exc}")

        if run_id is not None:
            # 2 — stage events
            if tc is not None:
                for idx, event in enumerate(tc.collected_events):
                    try:
                        await self._persist_stage_from_event(run_id, event, idx)
                    except Exception as exc:
                        _warnings.append(f"persist_stage[{idx}]: {exc}")

            # 3 — clarification record
            if status == "clarification" and clarification_id:
                try:
                    await self._run_store.persist_clarification(
                        run_id, conversation_id,
                        clarification_id=clarification_id,
                        question_text=clarification_question,
                        options=clarification_options,
                        status="pending",
                    )
                except Exception as exc:
                    _warnings.append(f"persist_clarification: {exc}")

            # 4 — finish run
            try:
                await self._run_store.finish_run(run_id, status=status)
            except Exception as exc:
                _warnings.append(f"finish_run: {exc}")

        # 5 — conversation status
        try:
            await self._run_store.update_conversation_status(conversation_id, status)
        except Exception as exc:
            _warnings.append(f"update_conversation_status: {exc}")

        # Surface warnings for observability
        if _warnings:
            for w in _warnings:
                logger.warning("[run-store] finalize: %s", w)
            if tc is not None:
                tc.stage_completed(
                    "persistence_warning",
                    summary=f"{len(_warnings)} persistence warning(s) during finalize",
                    payload={"warnings": _warnings, "phase": "finalize"},
                )
            if self._run_store is not None and run_id is not None:
                try:
                    await self._run_store.persist_event(
                        run_id, event_type="persistence_warning",
                        payload={"warnings": _warnings},
                    )
                except Exception:
                    pass  # Cannot persist warning about persistence failure

    async def handle_message(
        self,
        session_id: str,
        message: str,
        *,
        trace_collector: TraceCollector | None = None,
        openwebui_chat_id: str | None = None,
        conversation_history: list[tuple[str, str]] | None = None,
    ) -> ChatResult:
        """Process a single user turn end-to-end.

        Returns a ``ChatResult`` with status, answer, optional plan/SQL and
        rows preview.
        """
        import time as _time

        tc = trace_collector
        _t0_mono = _time.monotonic()

        # Auto-create a lightweight trace collector for stage persistence
        # when run_store is active but caller didn't provide one (e.g. /chat,
        # /v1/chat/completions).  This ensures stages are always persisted.
        if tc is None and self._run_store is not None:
            import uuid as _uuid
            from app.domain.trace_models import TraceCollector as _TC
            tc = _TC(trace_id=_uuid.uuid4().hex)

        # 0 – Session bookkeeping (always, before persistence)
        self._sessions.get_or_create(session_id)
        self._sessions.append_user_message(session_id, message)

        # 0a – Early clarification check: detect BEFORE creating initial run
        #   to avoid duplicate messages and orphan runs.
        _is_clarification_reply = False
        clarification_reply = None
        if self._clarification_manager is not None:
            from app.services.filter_value_profile_provider import FilterValueProfileProvider

            min_auto = FilterValueProfileProvider().policy().min_select_score
            clarification_reply = self._clarification_manager.interpret_reply(
                session_id, message, min_auto_resolve_score=min_auto,
            )
            _is_clarification_reply = clarification_reply is not None

        if _is_clarification_reply:
            # Clarification reply → delegate immediately. _handle_clarification_resume
            # will persist the user message + create its own clarification_resume run.
            return await self._handle_clarification_resume(
                session_id, message, clarification_reply,
                trace_collector=tc, t0_mono=_t0_mono,
                openwebui_chat_id=openwebui_chat_id,
            )

        # 0b – Durable persistence: resolve conversation, persist user message, create run
        _run_id: str | None = None
        _user_msg_id: str | None = None
        _conversation_id: str = session_id
        if self._run_store is not None:
            try:
                _conversation_id = await self._run_store.resolve_conversation(
                    session_id, openwebui_chat_id=openwebui_chat_id,
                )
                _user_msg_id = await self._run_store.persist_message(
                    _conversation_id, "user", message, source="openwebui",
                )
                _run_id = await self._run_store.create_run(
                    _conversation_id,
                    source_message_id=_user_msg_id,
                    trace_id=tc.trace_id if tc else None,
                    run_type="initial",
                )
            except Exception as exc:
                _w = f"[run-store] persistence init failed: {exc}"
                logger.warning(_w)
                if tc:
                    tc.stage_completed(
                        "persistence_warning",
                        summary=_w,
                        payload={"error": str(exc), "phase": "init"},
                    )

        # 1 – Trace: input context stages
        if tc:
            from app.services.trace_serializer import (
                build_catalog_readiness_payload,
                build_question_payload,
                build_runtime_context_payload,
                build_semantic_registry_payload,
                build_settings_snapshot,
            )

            tc.stage_completed(
                "question",
                summary=f"Question: {message[:100]}",
                payload=build_question_payload(message, session_id),
            )
            tc.stage_completed(
                "runtime_context",
                summary="Request runtime context",
                payload=build_runtime_context_payload(
                    session_id=session_id,
                    trace_id=tc.trace_id,
                    settings_snapshot=build_settings_snapshot(),
                ),
            )
            tc.stage_completed(
                "metadata_catalog_load",
                summary="Catalog metadata readiness",
                payload=build_catalog_readiness_payload(),
            )
            tc.stage_completed(
                "semantic_registry_load",
                summary="Semantic registry readiness",
                payload=build_semantic_registry_payload(),
            )

        # 2 – Plan
        _plan_mono = _time.monotonic()
        try:
            plan = await self._planner.plan(message, session_id=session_id)
        except PlannerError as exc:
            if tc:
                tc.stage_failed(
                    "planner_llm_request",
                    started_at_mono=_plan_mono,
                    summary=f"Planner error: {exc}",
                    payload={"error": str(exc)[:300]},
                )
                tc.stage_skipped("validation", summary="Skipped — planner failed")
                tc.stage_skipped("compile", summary="Skipped — planner failed")
                tc.stage_skipped("execute", summary="Skipped — planner failed")
                tc.stage_completed(
                    "final_verdict",
                    summary="Pipeline failed at planner stage",
                    payload={"status": "planner_error", "error_message": str(exc)[:300]},
                )
            answer = f"Plan oluşturulurken hata: {exc}"
            self._sessions.append_assistant_message(session_id, answer)
            await self._finalize_run(
                _run_id, _conversation_id, status="failed", answer=answer, tc=tc,
            )
            return ChatResult(
                session_id=session_id,
                status="execution_error",
                answer=answer,
                error_message=str(exc),
            )

        self._sessions.set_last_plan(session_id, plan)

        if tc:
            self._emit_planner_trace_stages(tc, _plan_mono)

        # 2.5 – Follow-up context merge (single stage; no-op for fresh queries)
        _merge_result = await self._followup_merge.process(
            session_id, message, plan,
        )
        if _merge_result.merge_strategy == "patch" and _merge_result.merged_plan is not None:
            plan = _merge_result.merged_plan
            self._sessions.set_last_plan(session_id, plan)
        if tc:
            tc.stage_completed(
                "followup_context_merge",
                summary=(
                    f"Follow-up merge: {_merge_result.merge_strategy} "
                    f"(confidence={_merge_result.followup_confidence}, "
                    f"preserved={_merge_result.preserved_filters})"
                ),
                payload=_merge_result.to_trace_payload(),
            )

        # 3 – Clarification short-circuit
        if plan.needs_clarification:
            if tc:
                # Emit validation/compile/execute as skipped so the full pipeline
                # shape is always visible in the live view, even when we exit early.
                clarification_reason = plan.clarification_message or "clarification_required"
                tc.stage_skipped(
                    "validation",
                    summary=f"Skipped — pipeline paused for clarification: {clarification_reason[:80]}",
                )
                tc.stage_skipped(
                    "compile",
                    summary="Skipped — pipeline paused for clarification",
                )
                tc.stage_skipped(
                    "execute",
                    summary="Skipped — pipeline paused for clarification",
                )
                _narr_mono = tc.stage_started(
                    "narrator_prompt",
                    summary="Narrating clarification request…",
                )
            answer = await self._narrator.narrate_clarification(plan, user_message=message)
            if tc:
                self._emit_narrator_trace_stages(tc, _narr_mono)
                tc.stage_completed(
                    "final_verdict",
                    summary="Clarification requested",
                    payload={
                        "status": "clarification",
                        "answer_preview": answer[:300],
                        "total_elapsed_ms": int((_time.monotonic() - _t0_mono) * 1000),
                    },
                )
            self._sessions.append_assistant_message(session_id, answer)

            # Build structured clarification payload if a filter-value
            # clarification was persisted in the state manager during planning.
            clar_payload = None
            if self._clarification_manager is not None:
                from app.domain.models import ClarificationOption, ClarificationPayload

                pending = self._clarification_manager.get_pending(session_id)
                if pending is not None:
                    clar_payload = ClarificationPayload(
                        clarification_id=pending.clarification_id,
                        message=answer,
                        options=[
                            ClarificationOption(
                                index=idx + 1,
                                label=candidate.value,
                                value=candidate.value,
                                score=candidate.score,
                            )
                            for idx, candidate in enumerate(pending.candidates)
                        ],
                        target_column=pending.target_column,
                        target_table=pending.target_table,
                        original_filter_value=pending.original_filter_value,
                    )

            _clar_id = clar_payload.clarification_id if clar_payload else None
            _clar_options = (
                [{"index": o.index, "label": o.label, "value": o.value, "score": o.score} for o in clar_payload.options]
                if clar_payload else None
            )

            # Ensure LLM-level clarifications (no filter-value pending) still
            # get a clarification record persisted.
            if _clar_id is None:
                _clar_id = f"llm-clar-{uuid.uuid4().hex[:12]}"
                if plan.clarification_missing_dimensions:
                    _clar_options = [
                        {"index": i + 1, "label": dim, "value": dim, "score": None}
                        for i, dim in enumerate(plan.clarification_missing_dimensions)
                    ]
            await self._finalize_run(
                _run_id, _conversation_id,
                status="clarification", answer=answer, tc=tc,
                clarification_id=_clar_id,
                clarification_options=_clar_options,
                clarification_question=answer,
            )

            return ChatResult(
                session_id=session_id,
                status="clarification",
                answer=answer,
                plan=plan,
                clarification_payload=clar_payload,
            )

        # 4 – Deterministic pipeline (validate → compile → execute)
        result = await self._orchestrator.run_plan(plan, trace_collector=tc)

        # 5a – Validation failure
        if result.failed_phase == ErrorPhase.VALIDATION:
            if tc:
                _narr_mono = tc.stage_started(
                    "narrator_prompt",
                    summary="Narrating validation error…",
                )
            answer = await self._narrator.narrate_validation_error(
                message, result.validation,
            )
            if tc:
                self._emit_narrator_trace_stages(tc, _narr_mono)
                tc.stage_completed(
                    "final_verdict",
                    summary=f"Failed: validation error — {answer[:120]}",
                    payload={
                        "status": "validation_error",
                        "answer_preview": answer[:300],
                        "total_elapsed_ms": int((_time.monotonic() - _t0_mono) * 1000),
                    },
                )
            self._sessions.append_assistant_message(session_id, answer)
            error_codes = [e.code for e in result.validation.errors]
            await self._finalize_run(
                _run_id, _conversation_id, status="failed", answer=answer, tc=tc,
            )
            return ChatResult(
                session_id=session_id,
                status="validation_error",
                answer=answer,
                plan=plan,
                error_code=error_codes[0] if error_codes else None,
                error_message="; ".join(
                    e.message for e in result.validation.errors
                ),
            )

        # 5b – Compilation / execution failure
        if result.failed_phase in (ErrorPhase.COMPILATION, ErrorPhase.EXECUTION):
            if tc:
                _narr_mono = tc.stage_started(
                    "narrator_prompt",
                    summary="Narrating execution/compilation error…",
                )
            answer = await self._narrator.narrate_execution_error(
                message, result,
            )
            if tc:
                self._emit_narrator_trace_stages(tc, _narr_mono)
                _failed_phase_name = result.failed_phase.value if result.failed_phase else "unknown"
                tc.stage_completed(
                    "final_verdict",
                    summary=f"Failed: {_failed_phase_name} — {answer[:120]}",
                    payload={
                        "status": "execution_error",
                        "answer_preview": answer[:300],
                        "total_elapsed_ms": int((_time.monotonic() - _t0_mono) * 1000),
                    },
                )
            self._sessions.append_assistant_message(session_id, answer)
            await self._finalize_run(
                _run_id, _conversation_id, status="failed", answer=answer, tc=tc,
            )
            return ChatResult(
                session_id=session_id,
                status="execution_error",
                answer=answer,
                plan=plan,
                sql=(
                    result.compiled_query.sql
                    if result.compiled_query
                    and settings.enable_sql_in_api_response
                    else None
                ),
                error_message=result.compilation_error
                or (
                    result.execution_result.error_message
                    if result.execution_result
                    else None
                ),
            )

        # 6 – Success
        if tc:
            _narr_mono = tc.stage_started(
                "narrator_prompt",
                summary="Narrating successful result…",
            )
        answer = await self._narrator.narrate_success(message, result)
        if tc:
            self._emit_narrator_trace_stages(tc, _narr_mono)
        self._sessions.append_assistant_message(session_id, answer)

        rows_preview = None
        if result.execution_result and result.execution_result.rows:
            rows_preview = result.execution_result.rows[
                : settings.max_rows_preview
            ]

        if tc:
            from app.services.trace_serializer import build_final_verdict_payload

            narrator_trace = self._narrator.last_trace
            tc.stage_completed(
                "final_verdict",
                summary=f"Success — {answer[:120]}",
                payload=build_final_verdict_payload(
                    status="success",
                    answer=answer,
                    plan_snapshot=plan.model_dump(mode="json"),
                    sql=result.compiled_query.sql if result.compiled_query else None,
                    total_elapsed_ms=int((_time.monotonic() - _t0_mono) * 1000),
                    orchestrator_trace=self._orchestrator.last_trace,
                    narrator_trace=narrator_trace,
                ),
            )

        await self._finalize_run(
            _run_id, _conversation_id, status="success", answer=answer, tc=tc,
        )

        # Record snapshot so the next turn can detect follow-up refinements
        self._followup_merge.record_success(session_id, plan, answer_preview=answer)

        return ChatResult(
            session_id=session_id,
            status="success",
            answer=answer,
            plan=plan,
            sql=(
                result.compiled_query.sql
                if result.compiled_query
                and settings.enable_sql_in_api_response
                else None
            ),
            rows_preview=rows_preview,
        )

    async def _handle_clarification_resume(
        self,
        session_id: str,
        message: str,
        reply: Any,
        *,
        trace_collector: TraceCollector | None = None,
        t0_mono: float = 0.0,
        openwebui_chat_id: str | None = None,
    ) -> ChatResult:
        """Resume the pipeline after the user answers a clarification.

        The ``reply`` is a ``ClarificationReply`` from the state manager.
        We reconstruct the partial plan, apply the resolved filter value,
        then run the downstream deterministic pipeline.
        """
        import time as _time
        from app.services.trace_serializer import safe_payload as _safe_payload

        tc = trace_collector

        # Auto-create trace collector for stage persistence when run_store
        # is active but caller didn't provide one.
        if tc is None and self._run_store is not None:
            import uuid as _uuid
            from app.domain.trace_models import TraceCollector as _TC
            tc = _TC(trace_id=_uuid.uuid4().hex)

        # Persistence: create child run for clarification resume
        _resume_run_id: str | None = None
        _conversation_id = session_id
        if self._run_store is not None:
            try:
                _conversation_id = await self._run_store.resolve_conversation(
                    session_id, openwebui_chat_id=openwebui_chat_id,
                )
                _user_msg_id = await self._run_store.persist_message(
                    _conversation_id, "user", message, source="clarification",
                )
                # Find parent run (most recent run with clarification status)
                runs = await self._run_store.list_runs(_conversation_id)
                parent_run_id = None
                for r in runs:
                    if r["status"] == "clarification":
                        parent_run_id = r["run_id"]
                        break
                _resume_run_id = await self._run_store.create_run(
                    _conversation_id,
                    source_message_id=_user_msg_id,
                    parent_run_id=parent_run_id,
                    trace_id=tc.trace_id if tc else None,
                    run_type="clarification_resume",
                )
                # Resolve the clarification record
                if reply.clarification_id:
                    delegated = reply.resolution_method == "user_deferred_to_system"
                    await self._run_store.resolve_clarification(
                        reply.clarification_id,
                        selected_option=reply.chosen_value,
                        delegated_to_system=delegated,
                        resolved_value=reply.chosen_value,
                        status="delegated" if delegated else "answered",
                    )
            except Exception as exc:
                _w = f"[run-store] clarification resume persist failed: {exc}"
                logger.warning(_w)
                if tc:
                    tc.stage_completed(
                        "persistence_warning",
                        summary=_w,
                        payload={"error": str(exc), "phase": "clarification_resume_init"},
                    )

        if tc:
            tc.stage_completed(
                "clarification_reply_received",
                summary=f"Clarification reply: {reply.resolution_method} → {reply.chosen_value}",
                payload={
                    "clarification_id": reply.clarification_id,
                    "chosen_value": reply.chosen_value,
                    "resolution_method": reply.resolution_method,
                    "target_column": reply.target_column,
                    "original_question": reply.original_question[:200] if reply.original_question else "",
                },
            )

        # Reconstruct the partial plan and apply the resolved value
        plan_json = reply.partial_grounded_plan_json
        try:
            partial_plan = QueryPlan(**plan_json)
        except Exception:
            answer = "Beklemedik bir hata olustu, lutfen sorunuzu tekrar sorun."
            self._sessions.append_assistant_message(session_id, answer)
            await self._finalize_run(
                _resume_run_id, _conversation_id, status="failed", answer=answer, tc=tc,
            )
            return ChatResult(session_id=session_id, status="execution_error", answer=answer)

        # Apply the resolved canonical value to the matching filter
        updated_filters = []
        value_applied = False
        for f in partial_plan.filters:
            if f.column == reply.target_column and not value_applied:
                updated_filters.append(f.model_copy(update={"value": reply.chosen_value}))
                value_applied = True
            else:
                updated_filters.append(f)

        resumed_plan = partial_plan.model_copy(
            update={
                "filters": updated_filters,
                "needs_clarification": False,
                "clarification_message": None,
            }
        )

        if tc:
            stage_name = (
                "user_deferred_to_system"
                if reply.resolution_method == "user_deferred_to_system"
                else "user_selected_candidate"
            )
            tc.stage_completed(
                stage_name,
                summary=f"Resumed with {reply.target_column} = '{reply.chosen_value}'",
                payload={
                    "target_column": reply.target_column,
                    "chosen_value": reply.chosen_value,
                    "resolution_method": reply.resolution_method,
                },
            )
            tc.stage_completed(
                "pipeline_resumed_after_clarification",
                summary="Pipeline resuming from grounding stage",
                payload={"resumed_plan_filters": _safe_payload(
                    [{"column": f.column, "op": f.op.value, "value": f.value} for f in resumed_plan.filters]
                )},
            )

        self._sessions.set_last_plan(session_id, resumed_plan)

        # 4 – Deterministic pipeline (validate → compile → execute)
        result = await self._orchestrator.run_plan(resumed_plan, trace_collector=tc)

        # 5a – Validation failure
        if result.failed_phase == ErrorPhase.VALIDATION:
            if tc:
                _narr_mono = tc.stage_started("narrator_prompt", summary="Narrating validation error…")
            answer = await self._narrator.narrate_validation_error(
                reply.original_question or message, result.validation,
            )
            if tc:
                self._emit_narrator_trace_stages(tc, _narr_mono)
                tc.stage_completed(
                    "final_verdict",
                    summary=f"Failed after clarification: validation — {answer[:120]}",
                    payload={"status": "validation_error", "total_elapsed_ms": int((_time.monotonic() - t0_mono) * 1000)},
                )
            self._sessions.append_assistant_message(session_id, answer)
            await self._finalize_run(
                _resume_run_id, _conversation_id, status="failed", answer=answer, tc=tc,
            )
            return ChatResult(
                session_id=session_id,
                status="validation_error",
                answer=answer,
                plan=resumed_plan,
                error_message="; ".join(e.message for e in result.validation.errors),
            )

        # 5b – Compilation / execution failure
        if result.failed_phase in (ErrorPhase.COMPILATION, ErrorPhase.EXECUTION):
            if tc:
                _narr_mono = tc.stage_started("narrator_prompt", summary="Narrating execution error…")
            answer = await self._narrator.narrate_execution_error(
                reply.original_question or message, result,
            )
            if tc:
                self._emit_narrator_trace_stages(tc, _narr_mono)
                tc.stage_completed(
                    "final_verdict",
                    summary=f"Failed after clarification: execution — {answer[:120]}",
                    payload={"status": "execution_error", "total_elapsed_ms": int((_time.monotonic() - t0_mono) * 1000)},
                )
            self._sessions.append_assistant_message(session_id, answer)
            await self._finalize_run(
                _resume_run_id, _conversation_id, status="failed", answer=answer, tc=tc,
            )
            return ChatResult(
                session_id=session_id,
                status="execution_error",
                answer=answer,
                plan=resumed_plan,
                sql=result.compiled_query.sql if result.compiled_query and settings.enable_sql_in_api_response else None,
                error_message=result.compilation_error or (result.execution_result.error_message if result.execution_result else None),
            )

        # 6 – Success
        if tc:
            _narr_mono = tc.stage_started("narrator_prompt", summary="Narrating result after clarification…")
        answer = await self._narrator.narrate_success(reply.original_question or message, result)
        if tc:
            self._emit_narrator_trace_stages(tc, _narr_mono)
        self._sessions.append_assistant_message(session_id, answer)
        rows_preview = None
        if result.execution_result and result.execution_result.rows:
            rows_preview = result.execution_result.rows[:settings.max_rows_preview]
        if tc:
            from app.services.trace_serializer import build_final_verdict_payload

            tc.stage_completed(
                "final_verdict",
                summary=f"Success after clarification — {answer[:120]}",
                payload=build_final_verdict_payload(
                    status="success",
                    answer=answer,
                    plan_snapshot=resumed_plan.model_dump(mode="json"),
                    sql=result.compiled_query.sql if result.compiled_query else None,
                    total_elapsed_ms=int((_time.monotonic() - t0_mono) * 1000),
                    orchestrator_trace=self._orchestrator.last_trace,
                    narrator_trace=self._narrator.last_trace,
                ),
            )
        await self._finalize_run(
            _resume_run_id, _conversation_id, status="success", answer=answer, tc=tc,
        )

        # Record snapshot so the next turn can detect follow-up refinements
        self._followup_merge.record_success(session_id, resumed_plan, answer_preview=answer)

        return ChatResult(
            session_id=session_id,
            status="success",
            answer=answer,
            plan=resumed_plan,
            sql=result.compiled_query.sql if result.compiled_query and settings.enable_sql_in_api_response else None,
            rows_preview=rows_preview,
        )

    def _emit_planner_trace_stages(
        self,
        tc: TraceCollector,
        plan_request_mono: float,
    ) -> None:
        """Unpack planner trace into stage events for the live view."""
        from app.services.trace_serializer import (
            build_diff_payload,
            build_filter_column_resolution_payload,
            build_filter_value_resolution_payload,
            build_llm_response_payload,
            build_plan_payload,
            build_prompt_payload,
            build_query_understanding_payload,
            build_retrieval_payload,
            safe_payload,
        )

        pt = self._planner.last_trace or {}
        planner_request_started = tc._started_at_mono.get("planner_llm_request", plan_request_mono)

        qu = pt.get("query_understanding")
        if qu is not None:
            tc.stage_completed(
                "query_understanding",
                summary=f"Modules: {qu.get('inferred_modules', [])}  Entities: {qu.get('detected_entities', [])}",
                payload=build_query_understanding_payload(qu),
            )

        retrieval = pt.get("retrieval")
        if retrieval is not None:
            tables = retrieval.get("schema_tables", [])
            tc.stage_completed(
                "schema_retrieval",
                summary=f"Retrieved {len(tables)} table(s): {', '.join(tables[:5])}",
                payload=build_retrieval_payload(retrieval),
            )
            docs = retrieval.get("schema_docs", [])
            examples = retrieval.get("examples", [])
            if docs or examples:
                tc.stage_completed(
                    "document_retrieval",
                    summary=f"{len(docs)} doc(s), {len(examples)} example(s) retrieved",
                    payload={
                        "schema_docs": safe_payload(docs[:10]),
                        "examples": safe_payload(examples[:10]),
                    },
                )
            assessment = retrieval.get("retrieval_assessment") or retrieval.get("noisy")
            if assessment is not None:
                tc.stage_completed(
                    "retrieval_assessment",
                    summary=f"Retrieval assessment: {assessment}",
                    payload={"assessment": assessment, "sufficiency": retrieval.get("sufficiency")},
                )

        prompt_trace = pt.get("prompt")
        if prompt_trace is not None:
            prompt_payload = build_prompt_payload(prompt_trace)
            tc.stage_completed(
                "prompt_assembly",
                summary=f"Prompt assembled — {prompt_payload.get('full_prompt_char_count', 0)} chars",
                payload=prompt_payload,
            )
            tc.stage_completed(
                "planner_llm_request",
                started_at_mono=planner_request_started,
                summary="Planner LLM call in progress (prompt sent)",
                payload={"prompt_char_count": prompt_payload.get("full_prompt_char_count", 0)},
            )

        llm_trace = pt.get("llm")
        if llm_trace is not None:
            raw_len = len(llm_trace.get("raw_response_text") or "")
            tc.stage_completed(
                "planner_llm_response",
                summary=f"Planner LLM response received — {raw_len} chars",
                payload=build_llm_response_payload(llm_trace),
            )

        parsed_plan = pt.get("parsed_plan")
        if parsed_plan is not None:
            tc.stage_completed(
                "planner_parsed_plan",
                summary=f"Parsed plan — table={parsed_plan.get('table')}, intent={parsed_plan.get('intent')}",
                payload=build_plan_payload(parsed_plan),
            )

        normalize = pt.get("normalize")
        if normalize is not None:
            tc.stage_completed(
                "normalize",
                summary="Plan normalization complete",
                payload=build_diff_payload(
                    normalize.get("before"),
                    normalize.get("after"),
                    {
                        "limit_clamped": normalize.get("limit_clamped"),
                        "clarification_cleanup_applied": normalize.get("clarification_cleanup_applied"),
                    },
                ),
            )

        repair = pt.get("repair")
        if repair is not None:
            tc.stage_completed(
                "repair",
                summary="Structural repair complete",
                payload=build_diff_payload(
                    repair.get("before"),
                    repair.get("after"),
                    {
                        "repair_applied": repair.get("repair_applied"),
                        "repair_actions": repair.get("repair_actions"),
                    },
                ),
            )

        semantic = pt.get("semantic")
        if semantic is not None:
            tc.stage_completed(
                "semantic",
                summary="Semantic resolution complete",
                payload=build_diff_payload(
                    semantic.get("before"),
                    semantic.get("after"),
                    {
                        "semantic_intent": semantic.get("semantic_intent"),
                        "root_entity": semantic.get("root_entity"),
                        "join_path_id": semantic.get("join_path_id"),
                        "diagnostics": semantic.get("diagnostics"),
                    },
                ),
            )

        canonicalize = pt.get("canonicalize")
        if canonicalize is not None:
            tc.stage_completed(
                "canonicalize",
                summary="Column canonicalization complete",
                payload=build_diff_payload(
                    canonicalize.get("before"),
                    canonicalize.get("after"),
                    {"stats": canonicalize.get("stats")},
                ),
            )

        filter_column_resolution = pt.get("filter_column_resolution") or {
            "any_changed": False,
            "total_filters": 0,
            "total_filters_seen": 0,
            "processed_filters": 0,
            "skipped_filters": 0,
            "changed_count": 0,
            "actions": [],
        }
        tc.stage_completed(
            "filter_column_resolution",
            summary=(
                f"Filter column grounding — seen={int(filter_column_resolution.get('total_filters_seen', 0))}, "
                f"changed={int(filter_column_resolution.get('changed_filters', filter_column_resolution.get('changed_count', 0)))}"
            ),
            payload=build_filter_column_resolution_payload(filter_column_resolution),
        )

        filter_value_resolution = pt.get("filter_value_resolution") or {
            "any_changed": False,
            "clarification_required": False,
            "total_filters_seen": 0,
            "processed_filters": 0,
            "skipped_filters": 0,
            "changed_filters": 0,
            "actions": [],
        }
        fvr_summary_parts = [
            f"Filter value resolution — seen={int(filter_value_resolution.get('total_filters_seen', 0))}",
            f"changed={int(filter_value_resolution.get('changed_filters', filter_value_resolution.get('changed_count', 0)))}",
        ]
        if filter_value_resolution.get("llm_tiebreak_used"):
            fvr_summary_parts.append("llm_tiebreak=yes")
        if filter_value_resolution.get("clarification_required"):
            fvr_summary_parts.append("clarification_required=yes")
        tc.stage_completed(
            "filter_value_resolution",
            summary=", ".join(fvr_summary_parts),
            payload=build_filter_value_resolution_payload(filter_value_resolution),
        )

        # Emit pending clarification stage if filter value resolution created one
        if filter_value_resolution.get("clarification_required") and filter_value_resolution.get("pending_clarification"):
            tc.stage_completed(
                "pending_clarification_created",
                summary="Pending clarification created — awaiting user reply",
                payload=safe_payload(filter_value_resolution.get("pending_clarification")),
            )

    def _emit_narrator_trace_stages(
        self,
        tc: TraceCollector,
        narrate_request_mono: float,
    ) -> None:
        """Unpack narrator trace into stage events for the live view."""
        from app.services.trace_serializer import (
            build_narrator_final_payload,
            build_narrator_llm_response_payload,
            build_narrator_prompt_payload,
            build_narrator_sanitize_payload,
        )

        narrator_trace = self._narrator.last_trace or {}
        tc.stage_completed(
            "narrator_prompt",
            started_at_mono=narrate_request_mono,
            summary="Narrator prompt prepared",
            payload=build_narrator_prompt_payload(narrator_trace),
        )

        if narrator_trace.get("raw_response") is not None or narrator_trace.get("error"):
            tc.stage_completed(
                "narrator_llm_response",
                summary="Narrator LLM response received",
                payload=build_narrator_llm_response_payload(narrator_trace),
            )

        if (
            narrator_trace.get("sanitized_response") is not None
            or narrator_trace.get("prompt_contract_violated")
            or narrator_trace.get("narrator_used_fallback_template")
        ):
            tc.stage_completed(
                "narrator_sanitize",
                summary="Narrator sanitization applied",
                payload=build_narrator_sanitize_payload(narrator_trace),
            )

        tc.stage_completed(
            "narrator_final_response",
            summary="Narrator final response ready",
            payload=build_narrator_final_payload(narrator_trace),
        )
