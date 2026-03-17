"""Tests for NarratorService."""

from __future__ import annotations

import pytest

from app.domain.execution_models import (
    CompiledQuery,
    ErrorPhase,
    ExecutionResult,
    ExecutionStatus,
    OrchestrationResult,
    ValidationResult,
)
from app.domain.query_plan import QueryPlan
from app.providers.llm.mock_llm import MockLLMProvider
from app.services.narrator_service import NarratorService


@pytest.fixture
def narrator() -> NarratorService:
    return NarratorService(MockLLMProvider())


def _success_result(row_count: int = 3) -> OrchestrationResult:
    """Create a minimal successful OrchestrationResult."""
    rows = [{"reg_no": 1000 + i, "first_name": f"User{i}"} for i in range(row_count)]
    return OrchestrationResult(
        validation=ValidationResult(),
        compiled_query=CompiledQuery(
            sql="SELECT * FROM (...) WHERE ROWNUM <= :p1",
            params={"p1": 100},
            table="XXBT_PDKS_PER_DETAILS_V",
            selected_columns=["reg_no", "first_name"],
        ),
        execution_result=ExecutionResult(
            status=ExecutionStatus.SUCCESS if row_count > 0 else ExecutionStatus.EMPTY,
            columns=["reg_no", "first_name"],
            rows=rows,
            row_count=row_count,
        ),
    )


# ---------------------------------------------------------------------------
# Success narration
# ---------------------------------------------------------------------------


class TestSuccessNarration:
    @pytest.mark.asyncio
    async def test_success_with_rows(self, narrator: NarratorService) -> None:
        """Successful query with rows should mention the row count."""
        result = _success_result(row_count=6)
        text = await narrator.narrate_success("Aktif çalışanları listele", result)

        assert isinstance(text, str)
        assert len(text) > 0
        # Mock narrator should produce "6 kayıt bulundu."
        assert "6" in text

    @pytest.mark.asyncio
    async def test_empty_result(self, narrator: NarratorService) -> None:
        """Empty result should convey 'no records found'."""
        result = _success_result(row_count=0)
        text = await narrator.narrate_success("Var olmayan birim", result)

        assert "bulunamadı" in text.lower()

    @pytest.mark.asyncio
    async def test_last_trace_records_raw_and_final_response(
        self, narrator: NarratorService,
    ) -> None:
        result = _success_result(row_count=2)

        text = await narrator.narrate_success("Aktif çalışanları listele", result)
        trace = narrator.last_trace

        assert trace is not None
        assert trace["prompt_length"] > 0
        assert trace["raw_response"] is not None
        assert trace["final_response"] == text


# ---------------------------------------------------------------------------
# Validation-error narration
# ---------------------------------------------------------------------------


class TestValidationErrorNarration:
    @pytest.mark.asyncio
    async def test_restricted_column_error(self, narrator: NarratorService) -> None:
        """Restricted column error should mention access restriction."""
        validation = ValidationResult()
        validation.add_error(
            "restricted_column",
            "Kısıtlı kolon kullanılamaz: 'salary'.",
            field="select_columns",
        )
        text = await narrator.narrate_validation_error("Maaşları göster", validation)

        assert "erişime kapalı" in text.lower()

    @pytest.mark.asyncio
    async def test_general_validation_error(self, narrator: NarratorService) -> None:
        """General validation errors should produce a user-friendly message."""
        validation = ValidationResult()
        validation.add_error(
            "invalid_column",
            "Kolon bulunamadı: 'xyz'.",
            field="select_columns",
        )
        text = await narrator.narrate_validation_error("xyz göster", validation)

        assert isinstance(text, str)
        assert len(text) > 0


# ---------------------------------------------------------------------------
# Execution-error narration
# ---------------------------------------------------------------------------


class TestExecutionErrorNarration:
    @pytest.mark.asyncio
    async def test_execution_error(self, narrator: NarratorService) -> None:
        """Execution error should produce an informative message."""
        result = OrchestrationResult(
            validation=ValidationResult(),
            compiled_query=CompiledQuery(
                sql="SELECT ...",
                table="XXBT_PDKS_PER_DETAILS_V",
            ),
            execution_result=ExecutionResult(
                status=ExecutionStatus.ERROR,
                error_message="ORA-00942: table or view does not exist",
            ),
        )
        text = await narrator.narrate_execution_error("test", result)

        assert isinstance(text, str)
        assert len(text) > 0


# ---------------------------------------------------------------------------
# Clarification narration
# ---------------------------------------------------------------------------


class TestClarificationNarration:
    @pytest.mark.asyncio
    async def test_clarification_message(self, narrator: NarratorService) -> None:
        """Clarification plan should produce a question for the user."""
        plan = QueryPlan(
            intent="Belirsiz sorgu",
            table="XXBT_PDKS_PER_DETAILS_V",
            needs_clarification=True,
            clarification_message="Hangi alanları görmek istiyorsunuz?",
        )
        text = await narrator.narrate_clarification(plan)

        assert isinstance(text, str)
        assert len(text) > 0


# ---------------------------------------------------------------------------
# Compilation-error narration
# ---------------------------------------------------------------------------


class TestCompilationErrorNarration:
    @pytest.mark.asyncio
    async def test_compilation_error_narrated(self, narrator: NarratorService) -> None:
        """Compilation-phase error should produce an informative message."""
        result = OrchestrationResult(
            validation=ValidationResult(),
            compilation_error="Kolon çözümlenemedi: 'ghost'",
            failed_phase=ErrorPhase.COMPILATION,
        )
        text = await narrator.narrate_execution_error("test", result)

        assert isinstance(text, str)
        assert len(text) > 0


# ---------------------------------------------------------------------------
# No data fabrication contract
# ---------------------------------------------------------------------------


class TestNarratorDoesNotFabricateData:
    """The narrator must never invent rows, names or counts."""

    @pytest.mark.asyncio
    async def test_empty_result_no_fabrication(
        self, narrator: NarratorService,
    ) -> None:
        """Empty result narration must not mention invented data."""
        result = _success_result(row_count=0)
        text = await narrator.narrate_success("Var olmayan sorgu", result)

        assert "bulunamadı" in text.lower()
        # Should not contain any mock dataset names
        assert "Ahmet" not in text
        assert "1001" not in text

    @pytest.mark.asyncio
    async def test_success_does_not_expose_sql(
        self, narrator: NarratorService,
    ) -> None:
        """Success narration must not expose raw SQL."""
        result = _success_result(row_count=3)
        text = await narrator.narrate_success("test", result)

        assert "SELECT" not in text
        assert "ROWNUM" not in text
        assert "FROM" not in text
