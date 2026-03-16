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
from app.services.validation_service import ValidationService

logger = get_logger(__name__)


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
    ) -> None:
        self._validator = validation_service
        self._compiler = compiler
        self._executor = executor

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
        # 1 – Validate
        validation = await self._validator.validate(plan)

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
        try:
            compiled = self._compiler.compile(
                plan,
                table,
                extra_tables=validation.resolved_tables or None,
            )
        except CompilationError as exc:
            logger.error("Compilation error: %s", exc)
            return OrchestrationResult(
                validation=validation,
                failed_phase=ErrorPhase.COMPILATION,
                compilation_error=str(exc),
            )

        logger.info("Compiled SQL:\n%s", compiled.sql)
        logger.info("Params: %s", compiled.params)

        # 4 – Execute
        try:
            execution = await self._executor.execute(compiled)
        except Exception as exc:
            logger.error("Execution error: %s", exc)
            execution = ExecutionResult(
                status=ExecutionStatus.ERROR,
                error_message=str(exc),
            )

        # 5 – Determine execution-phase failure and return.
        failed = (
            ErrorPhase.EXECUTION
            if execution.status == ExecutionStatus.ERROR
            else None
        )

        return OrchestrationResult(
            validation=validation,
            compiled_query=compiled,
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
