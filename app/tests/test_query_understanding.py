"""Tests for the query understanding pre-pass."""

from __future__ import annotations

from app.semantic.models import SemanticEntity, SemanticFoundation, LookupType
from app.semantic.registry import SemanticFoundationRegistry
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

    def test_intern_flag_filter_extracted(self) -> None:
        qu = analyze_query("Stajyer çalışanları göster")
        assert any(f["column_hint"] == "STAJYER" and f["value"] == "1" for f in qu.extracted_filters)

    def test_payroll_flag_filter_extracted(self) -> None:
        qu = analyze_query("Bordrolu çalışanları listele")
        assert any(f["column_hint"] == "BORDROLU" and f["value"] == "1" for f in qu.extracted_filters)

    def test_pending_po_status_values_extracted(self) -> None:
        qu = analyze_query("Onay bekleyen satın alma siparişlerini listele")
        values = {
            f["value"]
            for f in qu.extracted_filters
            if f["column_hint"] == "AUTHORIZATION_STATUS"
        }
        assert values == {"IN PROCESS", "INCOMPLETE", "PRE-APPROVED"}

    def test_cancelled_po_flag_extracted(self) -> None:
        qu = analyze_query("İptal edilmiş satın alma siparişlerini getir")
        assert any(f["column_hint"] == "CANCEL_FLAG" and f["value"] == "Y" for f in qu.extracted_filters)

    def test_title_filter_extracted(self) -> None:
        qu = analyze_query("Yönetici unvanlı çalışanları listele")
        assert any(f["column_hint"] == "UNVAN" and f["value"] == "yonetici" for f in qu.extracted_filters)

    def test_pending_po_status_values_come_from_registry_lookup_data(self) -> None:
        registry = SemanticFoundationRegistry(
            SemanticFoundation(
                entities=[
                    SemanticEntity(
                        entity_id="PO_HEADERS",
                        display_name="purchase_order_header",
                        module="PO",
                        root_table="PO_HEADERS_ALL",
                        default_tables=["PO_HEADERS_ALL"],
                        filter_signal_keywords={
                            "status_pending": ["onay bekleyen", "in process", "pre-approved"],
                        },
                        keywords=["satin alma siparisi"],
                    )
                ],
                lookups=[
                    LookupType(
                        lookup_type="PO_AUTHORIZATION_STATUS",
                        meaning="İşlemde",
                        decoded_value="In Process",
                        raw_value="WAITING_A",
                        domain="PO",
                        table_ref="PO_HEADERS_ALL.AUTHORIZATION_STATUS",
                    ),
                    LookupType(
                        lookup_type="PO_AUTHORIZATION_STATUS",
                        meaning="Ön Onaylı",
                        decoded_value="Pre-Approved",
                        raw_value="WAITING_B",
                        domain="PO",
                        table_ref="PO_HEADERS_ALL.AUTHORIZATION_STATUS",
                    ),
                ],
            )
        )

        qu = analyze_query("Onay bekleyen satın alma siparişlerini listele", registry=registry)
        values = {
            f["value"]
            for f in qu.extracted_filters
            if f["column_hint"] == "AUTHORIZATION_STATUS"
        }
        assert values == {"WAITING_A", "WAITING_B"}


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
