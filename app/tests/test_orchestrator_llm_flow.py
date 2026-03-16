"""Integration tests -- full LLM flow via mock LLM + mock executor.

These tests exercise the ChatOrchestrator end-to-end:

  user message -> PlannerService -> QueryPlan -> Orchestrator.run_plan
               -> NarratorService -> ChatResult
"""

from __future__ import annotations

import pytest

from app.core.exceptions import PlannerError
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.providers.executor.mock_executor import MockExecutor
from app.providers.llm.mock_llm import MockLLMProvider
from app.services.catalog_service import CatalogService
from app.services.narrator_service import NarratorService
from app.services.orchestrator import ChatOrchestrator, Orchestrator
from app.services.planner_service import PlannerService
from app.services.session_service import SessionService
from app.services.sql_compiler import SQLCompiler
from app.services.validation_service import ValidationService


@pytest.fixture
def chat_orchestrator() -> ChatOrchestrator:
    llm = MockLLMProvider()
    catalog_provider = InMemoryCatalogProvider()
    catalog = CatalogService(catalog_provider)
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = MockExecutor()

    planner = PlannerService(llm, catalog)
    narrator = NarratorService(llm)
    orchestrator = Orchestrator(validator, compiler, executor)
    sessions = SessionService()

    return ChatOrchestrator(planner, orchestrator, narrator, sessions)


# ---------------------------------------------------------------------------
# Success flow
# ---------------------------------------------------------------------------


class TestSuccessFlow:
    @pytest.mark.asyncio
    async def test_active_employees(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """'Aktif çalışanları listele' → success with rows."""
        result = await chat_orchestrator.handle_message(
            "s1", "Aktif çalışanları listele",
        )

        assert result.status == "success"
        assert result.answer  # non-empty Turkish narration
        assert result.plan is not None
        assert result.plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert result.sql is not None
        assert "ROWNUM" in result.sql
        # The mock executor has 7 active employees
        assert result.rows_preview is not None
        assert len(result.rows_preview) > 0

    @pytest.mark.asyncio
    async def test_generic_employee_query(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """'Çalışanları getir' → success flow."""
        result = await chat_orchestrator.handle_message(
            "s2", "Çalışanları getir",
        )

        assert result.status == "success"
        assert result.plan is not None
        assert result.rows_preview is not None


# ---------------------------------------------------------------------------
# Aggregate query
# ---------------------------------------------------------------------------


class TestAggregateFlow:
    @pytest.mark.asyncio
    async def test_count_by_unit(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """'Birim bazında çalışan sayısı' → aggregate + group_by success."""
        result = await chat_orchestrator.handle_message(
            "s3", "Birim bazında çalışan sayısı",
        )

        assert result.status == "success"
        assert result.plan is not None
        assert len(result.plan.aggregations) > 0
        assert result.rows_preview is not None


# ---------------------------------------------------------------------------
# Clarification flow
# ---------------------------------------------------------------------------


class TestClarificationFlow:
    @pytest.mark.asyncio
    async def test_unknown_query_clarification(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """Unrecognised query → clarification status."""
        result = await chat_orchestrator.handle_message(
            "s4", "xyz bilinmeyen 12345",
        )

        assert result.status == "clarification"
        assert result.answer  # should be a question
        assert result.plan is not None
        assert result.plan.needs_clarification is True


# ---------------------------------------------------------------------------
# Validation-error flow (restricted field)
# ---------------------------------------------------------------------------


class TestRestrictedFieldFlow:
    @pytest.mark.asyncio
    async def test_salary_request_rejected(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """'Kimlik numaralarını göster' → planner returns TC_NO, validation rejects."""
        result = await chat_orchestrator.handle_message(
            "s5", "Kimlik numaralarını göster",
        )

        assert result.status == "validation_error"
        assert "erişime kapalı" in result.answer.lower()
        assert result.error_code == "restricted_column"


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


class TestSessionState:
    @pytest.mark.asyncio
    async def test_session_tracks_messages(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """ChatOrchestrator should store user and assistant messages."""
        await chat_orchestrator.handle_message("s6", "Aktif çalışanları listele")

        session = chat_orchestrator._sessions.get_or_create("s6")  # noqa: SLF001
        # At least user + assistant
        assert len(session.messages) >= 2
        assert session.messages[0].role == "user"
        assert session.messages[1].role == "assistant"
        assert session.last_plan is not None


# ---------------------------------------------------------------------------
# Planner error flow
# ---------------------------------------------------------------------------


class TestPlannerErrorFlow:
    @pytest.mark.asyncio
    async def test_planner_error_returns_execution_error(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """When the planner raises PlannerError, result.status must be execution_error."""
        original_plan = chat_orchestrator._planner.plan  # noqa: SLF001

        async def failing_plan(msg: str) -> None:
            raise PlannerError("LLM is down", detail="connection timeout")

        chat_orchestrator._planner.plan = failing_plan  # type: ignore[assignment]  # noqa: SLF001
        try:
            result = await chat_orchestrator.handle_message("err-1", "test query")

            assert result.status == "execution_error"
            assert "hata" in result.answer.lower()
            assert result.error_message is not None
        finally:
            chat_orchestrator._planner.plan = original_plan  # type: ignore[assignment]  # noqa: SLF001


# ---------------------------------------------------------------------------
# Order-by flow
# ---------------------------------------------------------------------------


class TestOrderByFlow:
    @pytest.mark.asyncio
    async def test_ordered_query_e2e(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """'çalışanları sırala' should produce ordered results."""
        result = await chat_orchestrator.handle_message(
            "s7", "çalışanları sırala",
        )

        assert result.status == "success"
        assert result.plan is not None
        assert len(result.plan.order_by) > 0
        assert result.sql is not None
        assert "ORDER BY" in result.sql


# ---------------------------------------------------------------------------
# Clarification plan contract
# ---------------------------------------------------------------------------


class TestClarificationPlanContract:
    @pytest.mark.asyncio
    async def test_clarification_plan_has_no_query_artifacts(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """After planner normalization, clarification plans must be clean."""
        result = await chat_orchestrator.handle_message(
            "s8", "xyz bilinmeyen query 12345",
        )

        assert result.status == "clarification"
        assert result.plan is not None
        assert result.plan.needs_clarification is True
        assert result.plan.select_columns == []
        assert result.plan.filters == []
        assert result.plan.aggregations == []

    @pytest.mark.asyncio
    async def test_session_tracks_clarification_state(
        self, chat_orchestrator: ChatOrchestrator,
    ) -> None:
        """After a clarification, session should report pending clarification."""
        await chat_orchestrator.handle_message(
            "s9", "xyz bilinmeyen query 12345",
        )

        assert chat_orchestrator._sessions.is_pending_clarification("s9") is True  # noqa: SLF001
