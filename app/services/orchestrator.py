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
from typing import Any

from app.core.config import settings
from app.core.exceptions import CompilationError, PlannerError
from app.core.logging import get_logger
from app.domain.execution_models import (
    ErrorPhase,
    ExecutionResult,
    ExecutionStatus,
    OrchestrationResult,
    ValidationResult,
)
from app.domain.models import ChatResult
from app.domain.query_plan import QueryPlan
from app.providers.executor.base import ExecutorProvider
from app.services.narrator_service import NarratorService
from app.services.planner_service import PlannerService
from app.services.session_service import SessionService
from app.services.sql_compiler import SQLCompiler
from app.services.validation_repair_service import ValidationRepairService
from app.services.execution_risk import assess_pre_execution_risk, bind_summary, sql_fingerprint
from app.services.validation_service import ValidationService

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

    async def run_plan(self, plan: QueryPlan) -> OrchestrationResult:
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
    ) -> None:
        self._planner = planner
        self._orchestrator = orchestrator
        self._narrator = narrator
        self._sessions = session_service

    async def handle_message(
        self, session_id: str, message: str,
    ) -> ChatResult:
        """Process a single user turn end-to-end.

        Returns a ``ChatResult`` with status, answer, optional plan/SQL and
        rows preview.
        """
        # 1 – Session bookkeeping
        self._sessions.get_or_create(session_id)
        self._sessions.append_user_message(session_id, message)

        # 2 – Plan
        try:
            plan = await self._planner.plan(message)
        except PlannerError as exc:
            answer = f"Plan oluşturulurken hata: {exc}"
            self._sessions.append_assistant_message(session_id, answer)
            return ChatResult(
                session_id=session_id,
                status="execution_error",
                answer=answer,
                error_message=str(exc),
            )

        self._sessions.set_last_plan(session_id, plan)

        # 3 – Clarification short-circuit
        if plan.needs_clarification:
            answer = await self._narrator.narrate_clarification(plan)
            self._sessions.append_assistant_message(session_id, answer)
            return ChatResult(
                session_id=session_id,
                status="clarification",
                answer=answer,
                plan=plan,
            )

        # 4 – Deterministic pipeline (validate → compile → execute)
        result = await self._orchestrator.run_plan(plan)

        # 5a – Validation failure
        if result.failed_phase == ErrorPhase.VALIDATION:
            answer = await self._narrator.narrate_validation_error(
                message, result.validation,
            )
            self._sessions.append_assistant_message(session_id, answer)
            error_codes = [e.code for e in result.validation.errors]
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
            answer = await self._narrator.narrate_execution_error(
                message, result,
            )
            self._sessions.append_assistant_message(session_id, answer)
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
        answer = await self._narrator.narrate_success(message, result)
        self._sessions.append_assistant_message(session_id, answer)

        rows_preview = None
        if result.execution_result and result.execution_result.rows:
            rows_preview = result.execution_result.rows[
                : settings.max_rows_preview
            ]

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
