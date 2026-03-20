"""Tests for the query understanding pre-pass."""

from __future__ import annotations

import pytest

from app.services.query_understanding import QueryUnderstanding, analyze_query


class TestModuleDetection:
    """Ensure the pre-pass correctly identifies HR vs PO modules."""

    def test_hr_employee_query(self) -> None:
        qu = analyze_query("Istanbul'daki çalışanları getir")
        assert "HR" in qu.inferred_modules
        assert "PO" not in qu.inferred_modules

    def test_po_purchase_query(self) -> None:
        qu = analyze_query("Onaylanmış satınalma siparişlerini listele")
        assert "PO" in qu.inferred_modules

    def test_hr_salary_query(self) -> None:
        qu = analyze_query("Maaşı 10000'den fazla olan personeli göster")
        assert "HR" in qu.inferred_modules

    def test_ambiguous_query_both_modules(self) -> None:
        qu = analyze_query("Çalışanların satınalma siparişlerini getir")
        assert qu.multi_entity_flag is True

    def test_empty_query(self) -> None:
        qu = analyze_query("")
        assert qu.inferred_modules == []


class TestEntityConfidence:
    """Confidence should be high for clear single-module queries."""

    def test_clear_hr_high_confidence(self) -> None:
        qu = analyze_query("Aktif personel listesi")
        assert qu.entity_confidence == "high"

    def test_clear_po_high_confidence(self) -> None:
        qu = analyze_query("Açık satınalma siparişleri")
        assert qu.entity_confidence in ("high", "medium")

    def test_ambiguous_low_confidence(self) -> None:
        qu = analyze_query("Listeyi göster")
        assert qu.entity_confidence in ("low", "medium")


class TestFilterExtraction:
    """Verify that location, status, and time hints are extracted."""

    def test_city_filter_extracted(self) -> None:
        qu = analyze_query("Istanbul'daki çalışanları getir")
        dims = [f["dimension"] for f in qu.extracted_filters]
        assert "location" in dims

    def test_status_filter_extracted(self) -> None:
        qu = analyze_query("Aktif personeli göster")
        dims = [f["dimension"] for f in qu.extracted_filters]
        assert "status" in dims


class TestOutputType:
    """Detect aggregation vs listing output type."""

    def test_aggregation_detected(self) -> None:
        qu = analyze_query("Departman bazında çalışan sayısı")
        assert qu.requested_output_type == "aggregation"

    def test_listing_default(self) -> None:
        qu = analyze_query("Çalışanları getir")
        assert qu.requested_output_type == "list"


class TestTimeHints:
    """Time/date hint extraction from user query."""

    def test_year_extracted(self) -> None:
        qu = analyze_query("2024 yılında giren çalışanlar")
        assert len(qu.extracted_time_hints) >= 1

    def test_month_extracted(self) -> None:
        qu = analyze_query("Son 3 ayda açılan siparişler")
        assert len(qu.extracted_time_hints) >= 1


class TestTraceDict:
    """as_trace_dict() should return a serializable dict."""

    def test_trace_dict_serializable(self) -> None:
        qu = analyze_query("Aktif çalışanları listele")
        trace = qu.as_trace_dict()
        assert isinstance(trace, dict)
        assert "inferred_modules" in trace
        assert "entity_confidence" in trace
        assert "extracted_filters" in trace
