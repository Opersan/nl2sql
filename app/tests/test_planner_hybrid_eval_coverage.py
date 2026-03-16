"""Integration tests: PlannerService + MockLLMProvider.

Validates that the full planner pipeline (with document retrieval wired)
produces correct plans for the eval question set and that unnecessary
clarifications are avoided.
"""

from __future__ import annotations

import pytest

from app.domain.query_plan import AggregateFn, FilterOp, QueryPlan
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.providers.llm.mock_llm import MockLLMProvider
from app.services.catalog_service import CatalogService
from app.services.planner_service import PlannerService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def planner() -> PlannerService:
    """PlannerService wired with MockLLMProvider + InMemoryCatalog."""
    llm = MockLLMProvider()
    catalog = CatalogService(InMemoryCatalogProvider())
    return PlannerService(llm, catalog)


# ---------------------------------------------------------------------------
# Eval-set coverage: at least 10 intents produce plans (not clarification)
# ---------------------------------------------------------------------------

_EVAL_QUESTIONS: list[str] = [
    "Aktif çalışanları listele",                              # q_001
    "Çalışan sicil numarası ve ad soyadını getir",            # q_002
    "Son 1 yılda işe başlayan aktif personeli göster",        # q_003
    "Ayrılan personelleri listele",                           # q_004
    "Birim bazında aktif çalışan sayısı nedir",               # q_005
    "Departmanlara göre çalışan dağılımını ver",              # q_006
    "Unvana göre kaç kişi var",                               # q_007
    "Lokasyon bazlı aktif çalışan sayılarını çıkar",          # q_008
    "Mehmet Yılmaz'ın ekibindeki aktif çalışanları listele",  # q_009
    "Aktif stajyerleri göster",                               # q_010
    "Bordrolu aktif çalışanları listele",                     # q_011
    "10 yıldan uzun süredir çalışan personeli bul",           # q_012
    "Email adresi eksik olan aktif personeller kimler",       # q_013
    "Telefon dahili numarası olan aktif çalışanları getir",   # q_014
    "TC kimlik numarasını da içeren çalışan listesini ver",   # q_015
]


class TestEvalCoverage:
    """Verify ≥10 eval questions yield non-clarification plans."""

    @pytest.mark.asyncio
    async def test_at_least_10_non_clarification(self, planner: PlannerService) -> None:
        plans: list[QueryPlan] = []
        for q in _EVAL_QUESTIONS:
            plans.append(await planner.plan(q))

        non_clar = [p for p in plans if not p.needs_clarification]
        assert len(non_clar) >= 10, (
            f"Expected >=10 non-clarification plans, got {len(non_clar)}. "
            f"Clarified: {[q for q, p in zip(_EVAL_QUESTIONS, plans) if p.needs_clarification]}"
        )


class TestNoClarificationOnClear:
    """Known unambiguous queries must NOT return clarification."""

    _CLEAR_QUESTIONS = [
        "Aktif çalışanları listele",
        "Ayrılan personelleri listele",
        "Çalışan sicil numarası ve ad soyadını getir",
        "Son 1 yılda işe başlayan aktif personeli göster",
        "Birim bazında aktif çalışan sayısı nedir",
        "Departmanlara göre çalışan dağılımını ver",
        "Unvana göre kaç kişi var",
        "Lokasyon bazlı aktif çalışan sayılarını çıkar",
        "Email adresi eksik olan aktif personeller kimler",
        "Telefon dahili numarası olan aktif çalışanları getir",
        "Bordrolu aktif çalışanları listele",
        "Aktif stajyerleri göster",
        "10 yıldan uzun süredir çalışan personeli bul",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("question", _CLEAR_QUESTIONS)
    async def test_no_unnecessary_clarification(
        self, planner: PlannerService, question: str,
    ) -> None:
        plan = await planner.plan(question)
        assert plan.needs_clarification is False, (
            f"Unnecessary clarification for: {question!r}"
        )


# ---------------------------------------------------------------------------
# Aggregate queries: group_by + aggregation present
# ---------------------------------------------------------------------------


class TestAggregateQueries:
    @pytest.mark.asyncio
    async def test_birim_count(self, planner: PlannerService) -> None:
        plan = await planner.plan("Birim bazında aktif çalışan sayısı nedir")
        assert plan.group_by, "group_by should not be empty"
        assert "BIRIM_ADI" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)

    @pytest.mark.asyncio
    async def test_departman_dagilim(self, planner: PlannerService) -> None:
        plan = await planner.plan("Departmanlara göre çalışan dağılımını ver")
        assert "BIRIM_ADI" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)

    @pytest.mark.asyncio
    async def test_unvan_count(self, planner: PlannerService) -> None:
        plan = await planner.plan("Unvana göre kaç kişi var")
        assert "UNVAN" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)

    @pytest.mark.asyncio
    async def test_lokasyon_count(self, planner: PlannerService) -> None:
        plan = await planner.plan("Lokasyon bazlı aktif çalışan sayılarını çıkar")
        assert "LOCATION_ADI" in plan.group_by
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)


# ---------------------------------------------------------------------------
# Filter queries: correct filter produced
# ---------------------------------------------------------------------------


class TestFilterQueries:
    @pytest.mark.asyncio
    async def test_active_filter(self, planner: PlannerService) -> None:
        plan = await planner.plan("Aktif çalışanları listele")
        assert any(
            f.column == "CIKIS_TARIHI" and f.op == FilterOp.IS_NULL
            for f in plan.filters
        )

    @pytest.mark.asyncio
    async def test_terminated_filter(self, planner: PlannerService) -> None:
        plan = await planner.plan("Ayrılan personelleri listele")
        assert any(
            f.column == "CIKIS_TARIHI" and f.op == FilterOp.IS_NOT_NULL
            for f in plan.filters
        )

    @pytest.mark.asyncio
    async def test_email_null(self, planner: PlannerService) -> None:
        plan = await planner.plan("Email adresi eksik olan aktif personeller kimler")
        assert any(
            f.column == "EMAIL" and f.op == FilterOp.IS_NULL
            for f in plan.filters
        )

    @pytest.mark.asyncio
    async def test_phone_not_null(self, planner: PlannerService) -> None:
        plan = await planner.plan("Telefon dahili numarası olan aktif çalışanları getir")
        assert any(
            f.column == "DAHILI" and f.op == FilterOp.IS_NOT_NULL
            for f in plan.filters
        )

    @pytest.mark.asyncio
    async def test_payroll_flag(self, planner: PlannerService) -> None:
        plan = await planner.plan("Bordrolu aktif çalışanları listele")
        assert any(
            f.column == "BORDROLU" and f.op == FilterOp.EQ
            for f in plan.filters
        )

    @pytest.mark.asyncio
    async def test_intern(self, planner: PlannerService) -> None:
        plan = await planner.plan("Aktif stajyerleri göster")
        assert any(
            f.column == "STAJYER"
            and f.op == FilterOp.EQ
            and f.value == 1
            for f in plan.filters
        )

    @pytest.mark.asyncio
    async def test_tenure_10_years(self, planner: PlannerService) -> None:
        plan = await planner.plan("10 yıldan uzun süredir çalışan personeli bul")
        assert any(
            f.column == "ISE_GIRIS_TARIHI" and f.op == FilterOp.LTE
            for f in plan.filters
        )

    @pytest.mark.asyncio
    async def test_last_1_year(self, planner: PlannerService) -> None:
        plan = await planner.plan("Son 1 yılda işe başlayan aktif personeli göster")
        assert any(
            f.column == "ISE_GIRIS_TARIHI" and f.op == FilterOp.GTE
            for f in plan.filters
        )
