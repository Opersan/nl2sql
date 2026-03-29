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
from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.providers.llm.mock_llm import MockLLMProvider
from app.providers.llm.prompts import build_narrator_prompt
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
        # trace["final_response"] holds the narration part only;
        # narrate_success appends a Markdown table after it.
        assert text.startswith(trace["final_response"])
        assert "narration_shape" in trace
        assert "narration_business_value_score" in trace
        assert "final_narration_quality" in trace
        assert trace["sanitizer_reason_code"] == "no_sanitization_needed"
        assert trace["raw_leak_reason_codes"] == []

    @pytest.mark.asyncio
    async def test_generic_output_uses_shape_fallback_template(self, narrator: NarratorService) -> None:
        async def _generic(_prompt: str) -> str:
            return "Sorgu işlendi."

        narrator._llm.generate_text = _generic  # type: ignore[assignment]  # noqa: SLF001
        result = _success_result(row_count=4)
        text = await narrator.narrate_success("Aktif çalışanları listele", result)
        trace = narrator.last_trace or {}

        assert "Sorgu işlendi" not in text
        assert trace.get("narrator_used_fallback_template") is True
        assert trace.get("sanitizer_reason_code") == "raw_unusable_final_safe"

    def test_prompt_uses_compact_contract_markers(self) -> None:
        prompt = build_narrator_prompt("Aktif çalışanları listele", "Sorgu başarılı.\nSatır sayısı: 6.")

        assert "Kullanıcı sorusu:" not in prompt
        assert "Sonuç özeti:" not in prompt
        assert "Yanıtını ver:" not in prompt
        assert "ISTEK<<<" in prompt
        assert "VERI_OZETI<<<" in prompt
        assert "TEK_CIKTI:" in prompt

    @pytest.mark.asyncio
    async def test_empty_raw_response_uses_safe_fallback_with_trace(self, narrator: NarratorService) -> None:
        async def _empty(_prompt: str) -> str:
            return ""

        narrator._llm.generate_text = _empty  # type: ignore[assignment]  # noqa: SLF001
        result = _success_result(row_count=5)

        text = await narrator.narrate_success("Aktif çalışanları listele", result)
        trace = narrator.last_trace or {}

        # narrate_success appends a Markdown table; check narration prefix only
        assert text.startswith("Toplam 5 kayıt listelendi.")
        assert trace.get("raw_response_empty") is True
        assert trace.get("raw_leak_reason_codes") == ["empty_raw_response"]
        assert trace.get("sanitizer_reason_code") == "raw_missing"

    @pytest.mark.asyncio
    async def test_success_response_stays_single_paragraph_turkish_shape(self, narrator: NarratorService) -> None:
        result = _success_result(row_count=6)

        text = await narrator.narrate_success("Aktif çalışanları listele", result)

        # The narration part (before the appended table) must be a single paragraph
        narration_part = text.split("\n\n")[0]
        assert "\n\n" not in narration_part
        assert "Thinking Process" not in text
        assert "Kullanıcı sorusu" not in text
        assert "Sonuç özeti" not in text


class TestNarrationShapeClassification:
    def test_shape_listing(self, narrator: NarratorService) -> None:
        assert narrator._infer_shape_from_summary("status=success\nshape=listing") == "listing"  # noqa: SLF001

    def test_shape_grouped_aggregate(self, narrator: NarratorService) -> None:
        assert narrator._infer_shape_from_summary("status=success\nshape=grouped_aggregate") == "grouped_aggregate"  # noqa: SLF001

    def test_shape_scalar_metric(self, narrator: NarratorService) -> None:
        assert narrator._infer_shape_from_summary("status=success\nshape=scalar_metric") == "scalar_metric"  # noqa: SLF001

    def test_shape_empty_result(self, narrator: NarratorService) -> None:
        assert narrator._infer_shape_from_summary("status=success\nsatır_sayısı=0") == "empty_result"  # noqa: SLF001

    def test_shape_clarification(self, narrator: NarratorService) -> None:
        assert narrator._infer_shape_from_summary("Açıklama gerekli. Mesaj: Hangi alan?") == "clarification"  # noqa: SLF001


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


# ---------------------------------------------------------------------------
# Filter values in narrator summary
# ---------------------------------------------------------------------------


class TestNarratorSummaryIncludesFilterValues:
    """The success summary sent to the narrator must contain filter values
    so the LLM can accurately describe which filters were applied."""

    def test_eq_filter_value_in_summary(self) -> None:
        plan = QueryPlan(
            intent="employee_list",
            table="XXBT_PDKS_PER_DETAILS_V",
            filters=[FilterSpec(column="BIRIM_ADI", op=FilterOp.EQ, value="ELEKTRİK DİZAYN")],
        )
        result = OrchestrationResult(
            validation=ValidationResult(),
            compiled_query=CompiledQuery(
                sql="SELECT ...",
                table="XXBT_PDKS_PER_DETAILS_V",
                selected_columns=["AD", "SOYAD"],
                debug_plan=plan,
            ),
            execution_result=ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                columns=["AD", "SOYAD"],
                rows=[{"ad": "Ali", "soyad": "Veli"}],
                row_count=1,
            ),
        )
        summary = NarratorService._build_success_summary(result)
        assert "ELEKTRİK DİZAYN" in summary
        assert "BIRIM_ADI" in summary

    def test_is_null_filter_has_no_value(self) -> None:
        plan = QueryPlan(
            intent="employee_list",
            table="XXBT_PDKS_PER_DETAILS_V",
            filters=[FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL)],
        )
        result = OrchestrationResult(
            validation=ValidationResult(),
            compiled_query=CompiledQuery(
                sql="SELECT ...",
                table="XXBT_PDKS_PER_DETAILS_V",
                selected_columns=["AD"],
                debug_plan=plan,
            ),
            execution_result=ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                columns=["AD"],
                rows=[{"ad": "Ali"}],
                row_count=1,
            ),
        )
        summary = NarratorService._build_success_summary(result)
        assert "CIKIS_TARIHI IS_NULL" in summary
        # IS_NULL should not have a trailing 'None'
        assert "None" not in summary
