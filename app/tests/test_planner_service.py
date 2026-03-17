"""Tests for PlannerService."""

from __future__ import annotations

import pytest

from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.domain.semantic_models import PolicyRules, SemanticRegistry
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.providers.llm.mock_llm import MockLLMProvider
from app.services.catalog_service import CatalogService
from app.services import planner_service as planner_module
from app.services.planner_service import PlannerService


@pytest.fixture
def planner() -> PlannerService:
    llm = MockLLMProvider()
    catalog = CatalogService(InMemoryCatalogProvider())
    return PlannerService(llm, catalog)


# ---------------------------------------------------------------------------
# Basic planning
# ---------------------------------------------------------------------------


class TestBasicPlanning:
    @pytest.mark.asyncio
    async def test_simple_plan_active_employees(self, planner: PlannerService) -> None:
        """'Aktif çalışanları listele' must produce a valid plan with IS_NULL filter."""
        plan = await planner.plan("Aktif çalışanları listele")

        assert isinstance(plan, QueryPlan)
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert "SICIL_NO" in plan.select_columns
        assert any(f.op == FilterOp.IS_NULL for f in plan.filters)
        assert plan.needs_clarification is False

    @pytest.mark.asyncio
    async def test_aggregate_plan(self, planner: PlannerService) -> None:
        """'Birim bazında çalışan sayısı' must produce an aggregation plan."""
        plan = await planner.plan("Birim bazında çalışan sayısı")

        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert len(plan.aggregations) == 1
        assert plan.aggregations[0].function.value == "COUNT"
        assert "BIRIM_ADI" in plan.group_by

    @pytest.mark.asyncio
    async def test_salary_plan_produces_restricted_column(
        self, planner: PlannerService,
    ) -> None:
        """'Maaşları göster' should produce a plan that requests salary.

        Validation will reject it later; the planner simply translates the
        user intent.
        """
        plan = await planner.plan("Maaşları göster")

        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert "BORDROLU" in plan.select_columns

    @pytest.mark.asyncio
    async def test_generic_employee_query(self, planner: PlannerService) -> None:
        """'Çalışanları getir' must produce a valid plan."""
        plan = await planner.plan("Çalışanları getir")

        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert len(plan.select_columns) > 0
        assert plan.needs_clarification is False


# ---------------------------------------------------------------------------
# Clarification
# ---------------------------------------------------------------------------


class TestClarification:
    @pytest.mark.asyncio
    async def test_unknown_query_triggers_clarification(
        self, planner: PlannerService,
    ) -> None:
        """An unrecognised query should fall back to needs_clarification=True."""
        plan = await planner.plan("xyz bilinmeyen sorgu 12345")

        assert plan.needs_clarification is True
        assert plan.clarification_message is not None
        assert len(plan.clarification_message) > 0

    @pytest.mark.asyncio
    async def test_sensitive_guard_uses_registry_patterns(
        self,
        planner: PlannerService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        custom_registry = SemanticRegistry(
            policy_rules=PolicyRules(sensitive_intent_patterns=["kredi kart"]),
        )
        monkeypatch.setattr(planner_module, "_load_registry", lambda: custom_registry)

        plan = await planner.plan("Kredi kart numaralarını listele")

        assert plan.needs_clarification is True
        assert plan.intent == "clarification_required"

    def test_sensitive_guard_empty_patterns_is_safe(
        self,
        planner: PlannerService,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        empty_registry = SemanticRegistry(
            policy_rules=PolicyRules(sensitive_intent_patterns=[]),
        )
        monkeypatch.setattr(planner_module, "_load_registry", lambda: empty_registry)

        assert planner._is_sensitive_or_invalid_request("normal bir soru") is True  # noqa: SLF001
        assert "Sensitive policy patterns unavailable" in caplog.text

    def test_aggregation_intent_guard_triggers_for_non_aggregated_plan(
        self,
        planner: PlannerService,
    ) -> None:
        plan = QueryPlan(
            intent="listing_like",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO", "AD"],
        )

        guarded = planner._enforce_aggregation_intent_guard(  # noqa: SLF001
            plan,
            "departman bazında çalışan sayısı",
        )

        assert guarded.needs_clarification is True
        assert guarded.intent == "clarification_required"
        assert guarded.aggregations == []
        assert guarded.select_columns == []

    def test_aggregation_intent_guard_skips_filter_like_queries(
        self,
        planner: PlannerService,
    ) -> None:
        plan = QueryPlan(
            intent="listing_like",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO", "AD"],
        )

        guarded = planner._enforce_aggregation_intent_guard(  # noqa: SLF001
            plan,
            "Istanbul'daki çalışanları getir",
        )

        assert guarded is plan

    def test_aggregation_intent_guard_skips_short_domain_queries(
        self,
        planner: PlannerService,
    ) -> None:
        plan = QueryPlan(
            intent="listing_like",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO"],
        )

        guarded = planner._enforce_aggregation_intent_guard(  # noqa: SLF001
            plan,
            "çalışanlar",
        )

        assert guarded is plan

    def test_aggregation_intent_guard_does_not_touch_valid_aggregation(
        self,
        planner: PlannerService,
    ) -> None:
        from app.domain.query_plan import AggregateFn, AggregationSpec

        plan = QueryPlan(
            intent="agg",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="*")],
        )

        guarded = planner._enforce_aggregation_intent_guard(  # noqa: SLF001
            plan,
            "kaç çalışan var",
        )

        assert guarded is plan


# ---------------------------------------------------------------------------
# Alias handling
# ---------------------------------------------------------------------------


class TestAliasHandling:
    @pytest.mark.asyncio
    async def test_alias_in_natural_language(self, planner: PlannerService) -> None:
        """The planner should handle alias-containing queries.

        The mock returns a plan with select_columns; validation/compiler
        handles alias resolution.
        """
        plan = await planner.plan("Aktif çalışanları listele")

        # The plan should have valid column names (canonical or alias)
        assert plan.table is not None
        assert len(plan.select_columns) > 0 or len(plan.aggregations) > 0

    @pytest.mark.asyncio
    async def test_last_trace_contains_stage_snapshots(self, planner: PlannerService) -> None:
        await planner.plan("Aktif çalışanları listele")

        trace = planner.last_trace

        assert trace is not None
        assert trace["retrieval"] is not None
        assert trace["prompt"] is not None
        assert trace["llm"]["raw_response_text"] is not None
        assert trace["parsed_plan"] is not None
        assert trace["normalize"]["before"] is not None
        assert trace["repair"]["after"] is not None
        assert trace["semantic"]["after"] is not None
        assert trace["canonicalize"]["after"] is not None
        assert trace["final_plan"] is not None


# ---------------------------------------------------------------------------
# Plan normalization
# ---------------------------------------------------------------------------


class TestPlanNormalization:
    """Test the _normalize_plan safety checks applied after LLM output."""

    def test_clarification_strips_query_artifacts(
        self, planner: PlannerService,
    ) -> None:
        """Clarification plans must drop select/filter/agg/group/order artifacts."""
        plan = QueryPlan(
            intent="Belirsiz sorgu",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            filters=[FilterSpec(column="unit_name", op=FilterOp.EQ, value="IT")],
            needs_clarification=True,
            clarification_message="Hangi bilgi?",
        )
        normalized = planner._normalize_plan(plan)  # noqa: SLF001

        assert normalized.select_columns == []
        assert normalized.filters == []
        assert normalized.aggregations == []
        assert normalized.group_by == []
        assert normalized.order_by == []
        # Intent, table and clarification message are preserved
        assert normalized.needs_clarification is True
        assert normalized.clarification_message == "Hangi bilgi?"
        assert normalized.table == "XXBT_PDKS_PER_DETAILS_V"
        assert normalized.intent == "Belirsiz sorgu"

    def test_clarification_preserves_table_context(
        self, planner: PlannerService,
    ) -> None:
        """Table context should be kept for follow-up resolution."""
        plan = QueryPlan(
            intent="Belirsiz",
            table="XXBT_PDKS_PER_DETAILS_V",
            needs_clarification=True,
            clarification_message="Hangi alan?",
        )
        normalized = planner._normalize_plan(plan)  # noqa: SLF001

        assert normalized.table == "XXBT_PDKS_PER_DETAILS_V"

    def test_non_clarification_plan_unchanged(
        self, planner: PlannerService,
    ) -> None:
        """Normal plans with valid limits should pass through unmodified."""
        plan = QueryPlan(
            intent="Test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
        )
        normalized = planner._normalize_plan(plan)  # noqa: SLF001

        assert normalized is plan  # same reference = no mutation

    def test_limit_clamped_below_max_row_limit(
        self, planner: PlannerService,
    ) -> None:
        """Plans with limit exceeding max_row_limit must be clamped."""
        from app.core.config import settings

        if settings.max_row_limit >= 1000:
            pytest.skip("max_row_limit too high to test clamping with QueryPlan")

        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            limit=settings.max_row_limit + 1,
        )
        normalized = planner._normalize_plan(plan)  # noqa: SLF001

        assert normalized.limit == settings.max_row_limit


# ---------------------------------------------------------------------------
# Restricted field strategy
# ---------------------------------------------------------------------------


class TestRestrictedFieldStrategy:
    """The planner does NOT block restricted fields; validation does."""

    @pytest.mark.asyncio
    async def test_restricted_field_flows_through_planner(
        self, planner: PlannerService,
    ) -> None:
        """Planner should produce a plan with restricted columns intact."""
        plan = await planner.plan("Maaşları göster")

        assert "BORDROLU" in plan.select_columns
        assert plan.needs_clarification is False

    @pytest.mark.asyncio
    async def test_clarification_e2e_strips_artifacts(
        self, planner: PlannerService,
    ) -> None:
        """End-to-end: unrecognised query produces clean clarification plan."""
        plan = await planner.plan("xyz bilinmeyen 12345")

        assert plan.needs_clarification is True
        assert plan.clarification_message is not None
        assert plan.select_columns == []
        assert plan.filters == []
        assert plan.aggregations == []


# ---------------------------------------------------------------------------
# Order-by plan via mock
# ---------------------------------------------------------------------------


class TestOrderByPlan:
    @pytest.mark.asyncio
    async def test_order_by_plan_produced(
        self, planner: PlannerService,
    ) -> None:
        """'Çalışanları sırala' should produce a plan with order_by."""
        plan = await planner.plan("çalışanları sırala")

        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert len(plan.order_by) > 0
        assert plan.needs_clarification is False


# ---------------------------------------------------------------------------
# PO-domain planner integration (Sprint 6)
# ---------------------------------------------------------------------------


class TestPOPlanning:
    @pytest.mark.asyncio
    async def test_po_open_orders(self, planner: PlannerService) -> None:
        plan = await planner.plan("Açık satınalma siparişlerini listele")

        assert plan.needs_clarification is False
        assert plan.table == "PO_HEADERS_ALL"
        assert "po_header_id" in plan.select_columns
        assert "authorization_status" in plan.select_columns

    @pytest.mark.asyncio
    async def test_po_vendor_count(self, planner: PlannerService) -> None:
        plan = await planner.plan("Tedarikçiye göre PO sayısı")

        assert plan.table == "PO_HEADERS_ALL"
        assert "vendor_id" in plan.group_by
        assert len(plan.aggregations) == 1
        assert plan.aggregations[0].function.value == "COUNT"

    @pytest.mark.asyncio
    async def test_po_pending_delivery_join_path(self, planner: PlannerService) -> None:
        plan = await planner.plan("Teslim bekleyen satırları getir")

        assert plan.table == "PO_HEADERS_ALL"
        assert len(plan.joins) >= 2
        assert plan.joins[0].left_table == "PO_HEADERS_ALL"
        assert plan.joins[0].right_table == "PO_LINES_ALL"
        assert plan.joins[1].left_table == "PO_LINES_ALL"
        assert plan.joins[1].right_table == "PO_LINE_LOCATIONS_ALL"

    @pytest.mark.asyncio
    async def test_po_distribution_amount_join_path(
        self, planner: PlannerService,
    ) -> None:
        plan = await planner.plan("Dağıtım bazında tutar analizi")

        assert plan.table == "PO_HEADERS_ALL"
        assert len(plan.joins) >= 3
        assert plan.joins[0].right_table == "PO_LINES_ALL"
        assert plan.joins[1].right_table == "PO_LINE_LOCATIONS_ALL"
        assert plan.joins[2].right_table == "PO_DISTRIBUTIONS_ALL"

    @pytest.mark.asyncio
    async def test_po_item_lines_join_path(self, planner: PlannerService) -> None:
        plan = await planner.plan("Ürün bazında PO satırları")

        assert plan.table == "PO_HEADERS_ALL"
        targets = [j.right_table for j in plan.joins]
        assert "PO_LINES_ALL" in targets
        assert "MTL_SYSTEM_ITEMS_B" in targets

    @pytest.mark.asyncio
    async def test_po_last_30_days(self, planner: PlannerService) -> None:
        plan = await planner.plan("Son bir ayda açılan PO'ları göster")

        assert plan.table == "PO_HEADERS_ALL"
        assert any(f.column == "creation_date" and f.op == FilterOp.GTE for f in plan.filters)
