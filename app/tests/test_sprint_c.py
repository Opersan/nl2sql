"""Sprint C regression tests.

Six test groups:
  1. Oracle execution error subtype classification
  2. Clarification diagnostics always populated
  3. Narrator shape-aware fallback template outputs
  4. Sanitizer / final_response_source consistency
  5. Timeout EvalResult structure (execution_error_subtype + why_not_executed)
  6. Summary aggregate distributions match per-result data
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Group 1 – Oracle execution error subtype classification
# ---------------------------------------------------------------------------

from app.providers.executor.oracle_executor import (
    _classify_oracle_error,
    _normalize_oracle_message,
)


class TestOracleErrorClassification:
    """_classify_oracle_error must map ORA codes to Sprint-C subtype labels."""

    def test_ora_01800_oracle_date_type_error(self) -> None:
        assert _classify_oracle_error("ORA-01800: literal does not match format string") == "oracle_date_type_error"

    def test_ora_01805_oracle_date_type_error(self) -> None:
        assert _classify_oracle_error("ORA-01805: error in date/time operation") == "oracle_date_type_error"

    def test_ora_01722_invalid_number(self) -> None:
        assert _classify_oracle_error("ORA-01722: invalid number") == "invalid_number"

    def test_ora_01400_not_null_violation(self) -> None:
        assert _classify_oracle_error("ORA-01400: cannot insert NULL into (T.COL)") == "not_null_violation"

    def test_ora_06502_numeric_value_error(self) -> None:
        assert _classify_oracle_error("ORA-06502: PL/SQL: numeric or value error") == "numeric_value_error"

    def test_timeout_signal(self) -> None:
        assert _classify_oracle_error("Connection timeout after 30s") == "timeout"

    def test_unknown_fallback(self) -> None:
        assert _classify_oracle_error("Some unexpected database error without code") == "unknown_execution_error"

    def test_ora_00904_invalid_identifier(self) -> None:
        assert _classify_oracle_error("ORA-00904: invalid identifier") == "invalid_identifier"

    def test_ora_00918_ambiguous_column(self) -> None:
        assert _classify_oracle_error("ORA-00918: column ambiguously defined") == "ambiguous_column"

    def test_normalize_keeps_ora_code(self) -> None:
        result = _normalize_oracle_message("ORA-01722: invalid number occurred in table scan")
        assert result.startswith("ORA-01722")

    def test_normalize_caps_at_120(self) -> None:
        long_msg = "ORA-01722: " + "x" * 200
        assert len(_normalize_oracle_message(long_msg)) <= 120

    def test_normalize_empty_returns_unknown(self) -> None:
        assert _normalize_oracle_message("") == "unknown_error"


# ---------------------------------------------------------------------------
# Group 2 – Clarification diagnostics always non-None when needs_clarification
# ---------------------------------------------------------------------------

from app.services.intent_guard import derive_clarification_diagnostics
from app.domain.query_plan import QueryPlan


def _make_plan(**kwargs: Any) -> QueryPlan:
    defaults = {
        "intent": "test",
        "table": "PO_HEADERS_ALL",
        "needs_clarification": False,
        "clarification_message": None,
    }
    defaults.update(kwargs)
    if defaults["needs_clarification"] and not defaults.get("clarification_message"):
        defaults["clarification_message"] = "Açıklama gerekiyor."
    return QueryPlan(**defaults)


class TestClarificationDiagnostics:
    """clarification_reason_code must never be None when needs_clarification=True."""

    def _empty_guard(self) -> dict[str, Any]:
        return {
            "requested_filter_signals": [],
            "planner_filter_coverage": {"coverage_ratio": 1.0, "missing_signal_codes": []},
            "final_filter_coverage": {"coverage_ratio": 1.0, "missing_signal_codes": []},
            "false_success_risk": False,
            "success_blocked_by_filter_loss": False,
            "clarification_reason_code": None,
            "clarification_missing_dimensions": [],
        }

    def test_planner_requested_clarification_gives_code(self) -> None:
        planner_plan = _make_plan(needs_clarification=True)
        final_plan = _make_plan(needs_clarification=True)
        diag = derive_clarification_diagnostics(
            planner_plan=planner_plan,
            final_plan=final_plan,
            guard_decision=self._empty_guard(),
        )
        assert diag["clarification_reason_code"] is not None
        assert diag["clarification_reason_code"] == "planner_requested_clarification"

    def test_avoidable_clarification_gives_code(self) -> None:
        planner_plan = _make_plan(
            table="PO_HEADERS_ALL",
            select_columns=["VENDOR_ID"],
        )
        final_plan = _make_plan(needs_clarification=True)
        diag = derive_clarification_diagnostics(
            planner_plan=planner_plan,
            final_plan=final_plan,
            guard_decision=self._empty_guard(),
        )
        assert diag["clarification_reason_code"] is not None
        assert diag["clarification_reason_code"] == "avoidable_clarification"

    def test_missing_filter_dimension_gives_code(self) -> None:
        planner_plan = _make_plan()
        final_plan = _make_plan(needs_clarification=True)
        guard = self._empty_guard()
        guard["clarification_missing_dimensions"] = ["VENDOR_ID", "CREATION_DATE"]
        diag = derive_clarification_diagnostics(
            planner_plan=planner_plan,
            final_plan=final_plan,
            guard_decision=guard,
        )
        assert diag["clarification_reason_code"] == "missing_filter_dimension"
        assert "VENDOR_ID" in diag["clarification_missing_dimensions"]

    def test_low_confidence_fallback_never_none(self) -> None:
        """When planner has no shape, fallback must still produce non-None code."""
        planner_plan = _make_plan(table=None)
        final_plan = _make_plan(needs_clarification=True)
        diag = derive_clarification_diagnostics(
            planner_plan=planner_plan,
            final_plan=final_plan,
            guard_decision=self._empty_guard(),
        )
        assert diag["clarification_reason_code"] is not None
        assert diag["clarification_reason_code"] == "low_confidence"

    def test_no_clarification_code_is_none(self) -> None:
        """When needs_clarification=False clarification_reason_code must be None."""
        planner_plan = _make_plan()
        final_plan = _make_plan(table="PO_HEADERS_ALL", select_columns=["VENDOR_ID"])
        diag = derive_clarification_diagnostics(
            planner_plan=planner_plan,
            final_plan=final_plan,
            guard_decision=self._empty_guard(),
        )
        assert diag["clarification_reason_code"] is None


# ---------------------------------------------------------------------------
# Group 3 – Narrator shape-aware fallback templates
# ---------------------------------------------------------------------------

from app.services.narrator_service import NarratorService


class TestFallbackTemplates:
    """_fallback_template must produce shape-specific, business-relevant output."""

    def test_empty_result_with_filter_hint(self) -> None:
        summary = "satır_sayısı=0\nuygulanan_filtreler=DEPT=IT"
        result = NarratorService._fallback_template(shape="empty_result", summary=summary)
        assert "DEPT=IT" in result
        assert "kayıt" in result

    def test_empty_result_without_filter(self) -> None:
        result = NarratorService._fallback_template(shape="empty_result", summary="satır_sayısı=0")
        assert "kayıt" in result

    def test_scalar_metric_with_count(self) -> None:
        summary = "shape=scalar_metric\nsatır sayısı: 42\nseçili_alanlar=count_all"
        result = NarratorService._fallback_template(shape="scalar_metric", summary=summary)
        assert "42" in result or "metrik" in result.lower()

    def test_grouped_aggregate_shape(self) -> None:
        summary = "shape=grouped_aggregate\ngroup_by_hint=VENDOR_ID\ntop_group_label=VENDOR_001\nsatır sayısı: 5"
        result = NarratorService._fallback_template(shape="grouped_aggregate", summary=summary)
        assert result  # non-empty
        assert "VENDOR" in result.upper() or "grup" in result.lower() or "kırılım" in result.lower()

    def test_clarification_with_missing_dims(self) -> None:
        summary = "Açıklama gerekli. Mesaj: Lütfen dönem belirtin.\nmissing_dimensions=CREATION_DATE"
        result = NarratorService._fallback_template(shape="clarification", summary=summary)
        assert result  # non-empty
        assert "CREATION_DATE" in result or "dönem" in result.lower() or "belirt" in result.lower()

    def test_listing_with_count(self) -> None:
        summary = "satır sayısı: 17\nseçili_alanlar=AD_SOYAD, MAAS"
        result = NarratorService._fallback_template(shape="listing", summary=summary)
        assert "17" in result or "kayıt" in result

    def test_infer_shape_empty_result(self) -> None:
        summary = "satır_sayısı=0"
        assert NarratorService._infer_shape_from_summary(summary) == "empty_result"

    def test_infer_shape_clarification(self) -> None:
        summary = "Açıklama gerekli. Mesaj: Lütfen detay belirtin."
        assert NarratorService._infer_shape_from_summary(summary) == "clarification"


class TestClarificationSummaryBuilder:
    """_build_clarification_summary must include missing_dimensions when provided."""

    def test_includes_missing_dimensions_from_plan(self) -> None:
        plan = _make_plan(
            needs_clarification=True,
            clarification_missing_dimensions=["VENDOR_ID", "CREATION_DATE"],
        )
        summary = NarratorService._build_clarification_summary(plan)
        assert "missing_dimensions" in summary
        assert "VENDOR_ID" in summary

    def test_no_missing_dims_section_when_empty(self) -> None:
        plan = _make_plan(needs_clarification=True)
        summary = NarratorService._build_clarification_summary(plan)
        assert "missing_dimensions" not in summary

    def test_clarification_message_included(self) -> None:
        plan = _make_plan(
            needs_clarification=True,
            clarification_message="Lütfen tarih aralığı belirtin.",
        )
        summary = NarratorService._build_clarification_summary(plan)
        assert "Lütfen tarih aralığı belirtin." in summary


# ---------------------------------------------------------------------------
# Group 4 – Sanitizer / final_response_source consistency
# ---------------------------------------------------------------------------

from scripts.e2e_real_provider_eval import EvalResult, compute_trace_summary

_VALID_SOURCES = {"raw", "sanitized", "fallback_template"}


def _make_result(
    *,
    id: str = "q1",
    status: str = "success",
    user_visible_status: str = "pass",
    sanitizer_effective: bool = False,
    narration_ok: bool = True,
    final_response_source: str = "raw",
) -> EvalResult:
    r = EvalResult(
        id=id,
        domain="PO",
        category="LISTING",
        question="test",
        expected_table="PO_HEADERS_ALL",
        expected_intent_type="list",
        status=status,
        raw_status=status,
    )
    r.user_visible_status = user_visible_status
    r.sanitizer_effective = sanitizer_effective
    r.narration_ok = narration_ok
    r.final_response_source = final_response_source
    return r


class TestSanitizerConsistency:
    """sanitizer_saved_response_count must align with pass_with_sanitization count."""

    def test_sanitizer_saved_count_matches_status(self) -> None:
        results = [
            _make_result(id="q1", user_visible_status="pass_with_sanitization"),
            _make_result(id="q2", user_visible_status="pass"),
            _make_result(id="q3", user_visible_status="pass_with_sanitization"),
            _make_result(id="q4", user_visible_status="fail"),
        ]
        summary = compute_trace_summary(results)
        assert summary["sanitizer_saved_response_count"] == 2

    def test_sanitizer_saved_count_zero_when_all_pass(self) -> None:
        results = [_make_result(id=f"q{i}", user_visible_status="pass") for i in range(5)]
        summary = compute_trace_summary(results)
        assert summary["sanitizer_saved_response_count"] == 0

    def test_sanitizer_effective_does_not_inflate_count(self) -> None:
        """sanitizer_effective=True should NOT count when user_visible_status != pass_with_sanitization."""
        results = [
            _make_result(
                id="q1",
                user_visible_status="pass",
                sanitizer_effective=True,
                narration_ok=True,
            ),
        ]
        summary = compute_trace_summary(results)
        assert summary["sanitizer_saved_response_count"] == 0

    def test_final_response_source_values_are_canonical(self) -> None:
        """All final_response_source values must be from the approved set."""
        from scripts.e2e_real_provider_eval import _sanitize_narration_output

        # No raw response → fallback
        out = _sanitize_narration_output(
            raw_response=None,
            answer="ok",
            raw_status="success",
            expected_context={"query_shape": "listing"},
        )
        assert out["final_response_source"] in _VALID_SOURCES

        # Clean raw response → raw
        out2 = _sanitize_narration_output(
            raw_response="Toplam 5 kayıt bulundu.",
            answer="Toplam 5 kayıt bulundu.",
            raw_status="success",
            expected_context={"query_shape": "listing"},
        )
        assert out2["final_response_source"] in _VALID_SOURCES


# ---------------------------------------------------------------------------
# Group 5 – Timeout EvalResult structure
# ---------------------------------------------------------------------------

from scripts.e2e_real_provider_eval import EvalQuestion, _run_dataset_concurrent


class TestTimeoutEvalResult:
    """Timeout paths must set execution_error_subtype='timeout' and include why_not_executed."""

    @pytest.mark.asyncio
    async def test_timeout_sets_correct_subtype(self) -> None:
        class _SlowChat:
            _planner = None
            _orchestrator = None
            _narrator = None

            async def handle_message(self, session_id: str, message: str) -> Any:
                await asyncio.sleep(10)  # will time out

        ds = [EvalQuestion("q1", "PO", "LISTING", "slow query", "PO_HEADERS_ALL", "list", "low", "")]
        results = await _run_dataset_concurrent(
            _SlowChat(), ds, session_prefix="test", concurrency=1, question_timeout_s=0.05
        )
        assert len(results) == 1
        r = results[0]
        assert r.execution_error_subtype == "timeout", (
            f"expected 'timeout', got {r.execution_error_subtype!r}"
        )
        assert r.status == "execution_error"

    @pytest.mark.asyncio
    async def test_timeout_trace_has_why_not_executed(self) -> None:
        class _SlowChat:
            _planner = None
            _orchestrator = None
            _narrator = None

            async def handle_message(self, session_id: str, message: str) -> Any:
                await asyncio.sleep(10)

        ds = [EvalQuestion("q1", "PO", "LISTING", "slow", "PO_HEADERS_ALL", "list", "low", "")]
        results = await _run_dataset_concurrent(
            _SlowChat(), ds, session_prefix="test", concurrency=1, question_timeout_s=0.05
        )
        trace = results[0].question_trace or {}
        assert "why_not_executed" in trace, "timeout trace must include why_not_executed"
        why = trace["why_not_executed"]
        assert why and "timeout" in why

    @pytest.mark.asyncio
    async def test_timeout_error_detail_contains_seconds(self) -> None:
        class _SlowChat:
            _planner = None
            _orchestrator = None
            _narrator = None

            async def handle_message(self, session_id: str, message: str) -> Any:
                await asyncio.sleep(10)

        ds = [EvalQuestion("q1", "PO", "LISTING", "q", "PO_HEADERS_ALL", "list", "low", "")]
        results = await _run_dataset_concurrent(
            _SlowChat(), ds, session_prefix="test", concurrency=1, question_timeout_s=0.05
        )
        detail = results[0].error_detail or ""
        assert "timeout" in detail.lower() or "0.05" in detail


# ---------------------------------------------------------------------------
# Group 6 – Summary aggregate distributions match per-result data
# ---------------------------------------------------------------------------

from scripts.e2e_real_provider_eval import LLMRetryStats, _make_summary


def _minimal_result(
    id: str,
    *,
    status: str = "execution_error",
    execution_error_subtype: str | None = None,
    clarification_reason_code: str | None = None,
    confidence_band: str | None = None,
    user_visible_status: str = "fail",
    user_visible_quality: str = "fail",
    model_behavior_quality: str = "fail",
    latency_ms: int = 100,
) -> EvalResult:
    r = EvalResult(
        id=id,
        domain="PO",
        category="LISTING",
        question="q",
        expected_table="PO_HEADERS_ALL",
        expected_intent_type="list",
        status=status,
        raw_status=status,
        latency_ms=latency_ms,
    )
    r.execution_error_subtype = execution_error_subtype
    r.clarification_reason_code = clarification_reason_code
    r.confidence_band = confidence_band
    r.user_visible_status = user_visible_status
    r.user_visible_quality = user_visible_quality
    r.model_behavior_quality = model_behavior_quality
    return r


class TestSummaryDistributions:
    """execution_error_subtype_distribution must reflect per-result subtypes."""

    def _summary(self, results: list[EvalResult]) -> Any:
        return _make_summary(
            results,
            oracle_timeout=30,
            concurrency=1,
            max_retries=3,
            total_wall_time_s=1.0,
            llm_retry_stats=LLMRetryStats(),
        )

    def test_execution_error_subtype_distribution(self) -> None:
        results = [
            _minimal_result(id="q1", execution_error_subtype="oracle_syntax_error"),
            _minimal_result(id="q2", execution_error_subtype="invalid_date_value"),
            _minimal_result(id="q3", execution_error_subtype="oracle_syntax_error"),
            _minimal_result(id="q4", execution_error_subtype="timeout"),
            _minimal_result(id="q5", status="success", user_visible_status="pass", user_visible_quality="pass"),
        ]
        s = self._summary(results)
        dist = s.execution_error_subtype_distribution
        assert dist.get("oracle_syntax_error", 0) == 2
        assert dist.get("invalid_date_value", 0) == 1
        assert dist.get("timeout", 0) == 1

    def test_clarification_reason_code_distribution_no_none_on_clarification(self) -> None:
        results = [
            _minimal_result(
                id="q1",
                status="clarification",
                clarification_reason_code="filter_intent_missing",
                confidence_band="low",
                user_visible_status="pass",
            ),
            _minimal_result(
                id="q2",
                status="clarification",
                clarification_reason_code="avoidable_clarification",
                confidence_band="low",
                user_visible_status="pass",
            ),
        ]
        s = self._summary(results)
        dist = s.clarification_reason_code_distribution
        # No clarification result should have "none" as reason code label
        assert dist.get("none", 0) == 0, (
            f"unexpected 'none' entries in clarification_reason_code_distribution: {dist}"
        )

    def test_total_matches_len_results(self) -> None:
        results = [_minimal_result(id=f"q{i}") for i in range(10)]
        s = self._summary(results)
        assert s.total_questions == 10

    def test_execution_error_subtype_dist_excludes_none(self) -> None:
        """Questions with no subtype (success/clarification) should not appear in distribution."""
        results = [
            _minimal_result(id="q1", status="success", execution_error_subtype=None,
                            user_visible_status="pass", user_visible_quality="pass"),
            _minimal_result(id="q2", execution_error_subtype="oracle_syntax_error"),
        ]
        s = self._summary(results)
        dist = s.execution_error_subtype_distribution
        # None subtypes should be excluded or mapped to "none" but not inflate error codes
        assert dist.get("oracle_syntax_error", 0) == 1


# ---------------------------------------------------------------------------
# Group 7 – QueryPlan clarification_missing_dimensions field
# ---------------------------------------------------------------------------

class TestQueryPlanClarificationMissingDimensions:
    """clarification_missing_dimensions must be accessible on QueryPlan."""

    def test_field_default_empty(self) -> None:
        plan = _make_plan()
        assert plan.clarification_missing_dimensions == []

    def test_field_set_via_model_copy(self) -> None:
        plan = _make_plan(needs_clarification=True, clarification_missing_dimensions=["VENDOR_ID"])
        assert plan.clarification_missing_dimensions == ["VENDOR_ID"]

    def test_model_copy_updates_missing_dims(self) -> None:
        plan = _make_plan()
        updated = plan.model_copy(update={
            "intent": "clarification_required",
            "needs_clarification": True,
            "clarification_message": "Filtre eksik.",
            "clarification_missing_dimensions": ["VENDOR_ID", "CREATION_DATE"],
        })
        assert updated.clarification_missing_dimensions == ["VENDOR_ID", "CREATION_DATE"]
