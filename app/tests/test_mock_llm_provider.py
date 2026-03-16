"""Unit tests for MockLLMProvider rule-based intent pipeline.

Covers all 13 intent categories, combined filters, and clarification
fallback.  Tests call ``generate_structured`` directly (no planner
service, no retrieval) so they validate the mock LLM in isolation.
"""

from __future__ import annotations

import pytest

from app.domain.query_plan import (
    AggregateFn,
    FilterOp,
    JoinType,
    QueryPlan,
)
from app.providers.llm.mock_llm import MockLLMProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def llm() -> MockLLMProvider:
    return MockLLMProvider()


def _wrap(msg: str) -> str:
    """Wrap a user message in the expected planner prompt format."""
    return f"<system>some template</system>\nKullanıcı sorusu: {msg}"


async def _plan(llm: MockLLMProvider, msg: str) -> QueryPlan:
    return await llm.generate_structured(_wrap(msg), QueryPlan)


# ===========================================================================
# 1 – Aktif çalışan
# ===========================================================================


class TestActiveEmployee:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Aktif çalışanları listele")
        assert plan.needs_clarification is False
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert any(f.column == "CIKIS_TARIHI" and f.op == FilterOp.IS_NULL for f in plan.filters)

    @pytest.mark.asyncio
    async def test_variant_personel(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "aktif personeli göster")
        assert plan.needs_clarification is False
        assert any(f.column == "CIKIS_TARIHI" and f.op == FilterOp.IS_NULL for f in plan.filters)


# ===========================================================================
# 2 – Ayrılan çalışan
# ===========================================================================


class TestTerminatedEmployee:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Ayrılan personelleri listele")
        assert plan.needs_clarification is False
        assert any(
            f.column == "CIKIS_TARIHI" and f.op == FilterOp.IS_NOT_NULL
            for f in plan.filters
        )

    @pytest.mark.asyncio
    async def test_variant_isten(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "işten ayrılan çalışanları getir")
        assert plan.needs_clarification is False
        assert any(f.op == FilterOp.IS_NOT_NULL for f in plan.filters)


# ===========================================================================
# 3 – Ad soyad / sicil no listeleme
# ===========================================================================


class TestNameProjection:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Çalışan sicil numarası ve ad soyadını getir")
        assert plan.needs_clarification is False
        assert "SICIL_NO" in plan.select_columns
        assert "AD" in plan.select_columns
        assert "SOYAD" in plan.select_columns


# ===========================================================================
# 4 – Son 1 yılda işe başlayan
# ===========================================================================


class TestLast1Year:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Son 1 yılda işe başlayan aktif personeli göster")
        assert plan.needs_clarification is False
        hire_filter = [f for f in plan.filters if f.column == "ISE_GIRIS_TARIHI"]
        assert len(hire_filter) >= 1
        assert hire_filter[0].op == FilterOp.GTE

    @pytest.mark.asyncio
    async def test_variant_bir(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Son bir yılda işe giren personel")
        assert plan.needs_clarification is False
        assert any(f.column == "ISE_GIRIS_TARIHI" for f in plan.filters)


# ===========================================================================
# 5 – Birim bazında sayı
# ===========================================================================


class TestUnitCount:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Birim bazında aktif çalışan sayısı nedir")
        assert plan.needs_clarification is False
        assert "BIRIM_ADI" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)


# ===========================================================================
# 6 – Departman bazında dağılım
# ===========================================================================


class TestDepartmentDistribution:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Departmanlara göre çalışan dağılımını ver")
        assert plan.needs_clarification is False
        assert "BIRIM_ADI" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)


# ===========================================================================
# 7 – Unvan bazında sayı
# ===========================================================================


class TestPositionCount:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Unvana göre kaç kişi var")
        assert plan.needs_clarification is False
        assert "UNVAN" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)


# ===========================================================================
# 8 – Lokasyon bazında sayı
# ===========================================================================


class TestLocationCount:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Lokasyon bazlı aktif çalışan sayılarını çıkar")
        assert plan.needs_clarification is False
        assert "LOCATION_ADI" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)
        # also expect active filter
        assert any(f.column == "CIKIS_TARIHI" and f.op == FilterOp.IS_NULL for f in plan.filters)


# ===========================================================================
# 9 – Email eksik
# ===========================================================================


class TestEmailMissing:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Email adresi eksik olan aktif personeller kimler")
        assert plan.needs_clarification is False
        assert any(f.column == "EMAIL" and f.op == FilterOp.IS_NULL for f in plan.filters)

    @pytest.mark.asyncio
    async def test_eposta_variant(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "e-posta adresi eksik çalışanlar")
        assert plan.needs_clarification is False
        assert any(f.column == "EMAIL" and f.op == FilterOp.IS_NULL for f in plan.filters)


# ===========================================================================
# 10 – Telefon dahili
# ===========================================================================


class TestPhoneExtension:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Telefon dahili numarası olan aktif çalışanları getir")
        assert plan.needs_clarification is False
        assert any(
            f.column == "DAHILI" and f.op == FilterOp.IS_NOT_NULL
            for f in plan.filters
        )


# ===========================================================================
# 11 – Bordrolu
# ===========================================================================


class TestPayroll:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Bordrolu aktif çalışanları listele")
        assert plan.needs_clarification is False
        assert any(
            f.column == "BORDROLU" and f.op == FilterOp.EQ
            for f in plan.filters
        )
        # also active
        assert any(f.column == "CIKIS_TARIHI" and f.op == FilterOp.IS_NULL for f in plan.filters)


# ===========================================================================
# 12 – Stajyer
# ===========================================================================


class TestIntern:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Aktif stajyerleri göster")
        assert plan.needs_clarification is False
        assert any(
            f.column == "STAJYER" and f.op == FilterOp.EQ and f.value == 1
            for f in plan.filters
        )
        # also active
        assert any(f.column == "CIKIS_TARIHI" and f.op == FilterOp.IS_NULL for f in plan.filters)


# ===========================================================================
# 13 – 10 yıldan uzun
# ===========================================================================


class TestTenure10Years:
    @pytest.mark.asyncio
    async def test_basic(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "10 yıldan uzun süredir çalışan personeli bul")
        assert plan.needs_clarification is False
        assert any(f.column == "ISE_GIRIS_TARIHI" and f.op == FilterOp.LTE for f in plan.filters)

    @pytest.mark.asyncio
    async def test_variant_on_yil(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "On yıldan fazla çalışanları getir")
        assert plan.needs_clarification is False
        assert any(f.column == "ISE_GIRIS_TARIHI" for f in plan.filters)


# ===========================================================================
# 14 – Kombinasyonlar
# ===========================================================================


class TestCombinations:
    @pytest.mark.asyncio
    async def test_active_intern(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Aktif stajyerleri göster")
        assert plan.needs_clarification is False
        ops = {(f.column, f.op) for f in plan.filters}
        assert ("CIKIS_TARIHI", FilterOp.IS_NULL) in ops
        assert ("STAJYER", FilterOp.EQ) in ops

    @pytest.mark.asyncio
    async def test_payroll_active(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Bordrolu aktif çalışanları listele")
        assert plan.needs_clarification is False
        ops = {(f.column, f.op) for f in plan.filters}
        assert ("CIKIS_TARIHI", FilterOp.IS_NULL) in ops
        assert ("BORDROLU", FilterOp.EQ) in ops

    @pytest.mark.asyncio
    async def test_location_active_count(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Lokasyon bazlı aktif çalışan sayılarını çıkar")
        assert plan.needs_clarification is False
        assert "LOCATION_ADI" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)
        assert any(f.column == "CIKIS_TARIHI" and f.op == FilterOp.IS_NULL for f in plan.filters)

    @pytest.mark.asyncio
    async def test_email_missing_active(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Email adresi eksik olan aktif personeller kimler")
        assert plan.needs_clarification is False
        ops = {(f.column, f.op) for f in plan.filters}
        assert ("EMAIL", FilterOp.IS_NULL) in ops
        assert ("CIKIS_TARIHI", FilterOp.IS_NULL) in ops


# ===========================================================================
# 15 – Clarification fallback
# ===========================================================================


class TestClarificationFallback:
    @pytest.mark.asyncio
    async def test_ambiguous(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "xyz bilgisi nedir?")
        assert plan.needs_clarification is True
        assert plan.clarification_message is not None

    @pytest.mark.asyncio
    async def test_very_short(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "asdf")
        assert plan.needs_clarification is True


# ===========================================================================
# 16 – PO domain deterministic intents (Sprint 6)
# ===========================================================================


class TestPODomainIntents:
    @pytest.mark.asyncio
    async def test_open_purchase_orders(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Açık satınalma siparişlerini listele")
        assert plan.needs_clarification is False
        assert plan.table == "PO_HEADERS_ALL"
        assert "po_header_id" in plan.select_columns
        assert "authorization_status" in plan.select_columns
        assert any(f.column == "authorization_status" and f.op == FilterOp.NEQ for f in plan.filters)

    @pytest.mark.asyncio
    async def test_unapproved_waiting_pos(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Onaysız bekleyen PO'ları getir")
        assert plan.table == "PO_HEADERS_ALL"
        assert any(f.column == "authorization_status" and f.value == "APPROVED" for f in plan.filters)

    @pytest.mark.asyncio
    async def test_unclosed_pos(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Kapatılmamış PO'ları göster")
        assert plan.table == "PO_HEADERS_ALL"
        assert any(f.column == "authorization_status" and f.op == FilterOp.NEQ for f in plan.filters)

    @pytest.mark.asyncio
    async def test_count_by_vendor(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Tedarikçiye göre PO sayısı")
        assert plan.table == "PO_HEADERS_ALL"
        assert "vendor_id" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)

    @pytest.mark.asyncio
    async def test_quantity_by_line(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Kalem bazında sipariş miktarı")
        assert plan.table == "PO_HEADERS_ALL"
        assert len(plan.joins) >= 1
        assert plan.joins[0].right_table == "PO_LINES_ALL"
        assert any(a.function == AggregateFn.SUM for a in plan.aggregations)

    @pytest.mark.asyncio
    async def test_pending_delivery_lines(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Teslim bekleyen satırları göster")
        assert plan.table == "PO_HEADERS_ALL"
        assert len(plan.joins) >= 2
        assert plan.joins[0].right_table == "PO_LINES_ALL"
        assert plan.joins[1].right_table == "PO_LINE_LOCATIONS_ALL"
        assert any(f.column == "quantity_received" and f.op == FilterOp.LT for f in plan.filters)

    @pytest.mark.asyncio
    async def test_distribution_amount_analysis(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Dağıtım bazında tutar analizi")
        # Distribution plan now uses PO_DISTRIBUTIONS_ALL as primary table
        # to ensure select_columns pass catalog validation.
        assert plan.table == "PO_DISTRIBUTIONS_ALL"
        assert "po_distribution_id" in plan.select_columns or "quantity_ordered" in plan.select_columns

    @pytest.mark.asyncio
    async def test_item_based_po_lines(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Ürün bazında PO satırları")
        assert plan.table == "PO_HEADERS_ALL"
        join_targets = [j.right_table for j in plan.joins]
        assert "PO_LINES_ALL" in join_targets
        assert "MTL_SYSTEM_ITEMS_B" in join_targets
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)

    @pytest.mark.asyncio
    async def test_last_30_days_opened_pos(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Son 30 günde açılan PO'lar")
        assert plan.table == "PO_HEADERS_ALL"
        assert any(f.column == "creation_date" and f.op == FilterOp.GTE for f in plan.filters)

    @pytest.mark.asyncio
    async def test_po_join_chain_is_inner(self, llm: MockLLMProvider) -> None:
        plan = await _plan(llm, "Dağıtım bazında sipariş tutar analizi")
        # Distribution plan targets PO_DISTRIBUTIONS_ALL directly (no join chain).
        assert plan.table == "PO_DISTRIBUTIONS_ALL"
        for j in plan.joins:
            assert j.join_type == JoinType.INNER


# ===========================================================================
# 16 – Narrator (generate_text) – smoke
# ===========================================================================


class TestNarrator:
    @pytest.mark.asyncio
    async def test_row_count(self, llm: MockLLMProvider) -> None:
        text = await llm.generate_text("Satır sayısı: 5")
        assert "5" in text

    @pytest.mark.asyncio
    async def test_empty_result(self, llm: MockLLMProvider) -> None:
        text = await llm.generate_text("Satır sayısı: 0")
        assert "bulunamadı" in text
