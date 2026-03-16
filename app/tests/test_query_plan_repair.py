"""Unit and integration tests for QueryPlanRepairEngine.

Coverage target: 25+ tests across all five repair types (C, D, E, F, G)
plus edge cases, audit-trail verification, and pipeline integration.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
    SortDirection,
)
from app.services.query_plan_repair import (
    QueryPlanRepairEngine,
    RepairAction,
    RepairResult,
    _is_expression,
    _split_qualifier,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    return QueryPlanRepairEngine()


def _make_plan(**kwargs) -> QueryPlan:
    """Return a minimal valid QueryPlan with sensible defaults."""
    kwargs.setdefault("intent", "test intent")
    kwargs.setdefault("table", "SOME_TABLE")
    return QueryPlan(**kwargs)


def _make_clarification_plan(**kwargs) -> QueryPlan:
    kwargs.setdefault("intent", "Yanit yorumlanamadi")
    kwargs.setdefault("needs_clarification", True)
    kwargs.setdefault("clarification_message", "Lutfen sorunuzu aciklayiniz")
    return QueryPlan(**kwargs)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_split_qualifier_with_dot(self):
        col, tbl = _split_qualifier("PO_HEADERS_ALL.segment1")
        assert col == "segment1"
        assert tbl == "PO_HEADERS_ALL"

    def test_split_qualifier_no_dot(self):
        col, tbl = _split_qualifier("segment1")
        assert col == "segment1"
        assert tbl is None

    def test_split_qualifier_table_uppercased(self):
        col, tbl = _split_qualifier("po_lines_all.quantity")
        assert tbl == "PO_LINES_ALL"
        assert col == "quantity"

    def test_is_expression_with_paren(self):
        assert _is_expression("SUM(quantity)") is True

    def test_is_expression_plain_column(self):
        assert _is_expression("segment1") is False

    def test_is_expression_with_space(self):
        assert _is_expression("col name") is True

    def test_split_qualifier_numeric_suffix(self):
        col, tbl = _split_qualifier("MTL_SYSTEM_ITEMS_B.inventory_item_id")
        assert tbl == "MTL_SYSTEM_ITEMS_B"
        assert col == "inventory_item_id"


# ---------------------------------------------------------------------------
# Pass C — Qualified-column stripping
# ---------------------------------------------------------------------------

class TestPassC:
    def test_stripped_select_columns(self, engine):
        plan = _make_plan(select_columns=["PO_HEADERS_ALL.segment1", "PO_HEADERS_ALL.vendor_id"])
        repaired, result = engine.repair(plan)

        assert repaired.select_columns == ["segment1", "vendor_id"]
        assert result.repair_applied is True
        assert len(result.actions) == 2
        assert all(a.repair_type == "C_qualified_column" for a in result.actions)

    def test_stripped_group_by(self, engine):
        plan = _make_plan(group_by=["PO_LINES_ALL.line_num"])
        repaired, result = engine.repair(plan)

        assert repaired.group_by == ["line_num"]
        assert result.repair_applied is True

    def test_stripped_order_by_column_and_table_injected(self, engine):
        plan = _make_plan(
            order_by=[OrderSpec(column="PO_HEADERS_ALL.creation_date", direction=SortDirection.DESC)]
        )
        repaired, result = engine.repair(plan)

        assert repaired.order_by[0].column == "creation_date"
        assert repaired.order_by[0].table == "PO_HEADERS_ALL"
        assert result.repair_applied is True

    def test_stripped_filter_column_table_populated(self, engine):
        plan = _make_plan(
            filters=[FilterSpec(column="PO_HEADERS_ALL.segment1", op=FilterOp.EQ, value="PO-1234")]
        )
        repaired, result = engine.repair(plan)

        flt = repaired.filters[0]
        assert flt.column == "segment1"
        assert flt.table == "PO_HEADERS_ALL"
        assert result.repair_applied is True

    def test_stripped_aggregation_column_table_populated(self, engine):
        plan = _make_plan(
            aggregations=[
                AggregationSpec(function=AggregateFn.SUM, column="PO_LINES_ALL.quantity")
            ]
        )
        repaired, result = engine.repair(plan)

        agg = repaired.aggregations[0]
        assert agg.column == "quantity"
        assert agg.table == "PO_LINES_ALL"
        assert result.repair_applied is True

    def test_expression_columns_preserved(self, engine):
        plan = _make_plan(select_columns=["SUM(quantity)", "COUNT(*)"])
        repaired, result = engine.repair(plan)

        assert repaired.select_columns == ["SUM(quantity)", "COUNT(*)"]
        assert result.repair_applied is False

    def test_plain_columns_unchanged(self, engine):
        plan = _make_plan(
            select_columns=["vendor_id", "creation_date"],
            group_by=["vendor_id"],
        )
        repaired, result = engine.repair(plan)

        assert repaired.select_columns == ["vendor_id", "creation_date"]
        assert repaired.group_by == ["vendor_id"]
        assert result.repair_applied is False

    def test_filter_with_existing_table_not_overridden(self, engine):
        """If FilterSpec.table is already set, the qualifier should not be overwritten."""
        plan = _make_plan(
            filters=[FilterSpec(
                column="PO_LINES_ALL.quantity",
                table="PO_DISTRIBUTIONS_ALL",  # already set
                op=FilterOp.GT,
                value=0,
            )]
        )
        repaired, result = engine.repair(plan)

        flt = repaired.filters[0]
        # Column is still qualified (no strip when table is already set)
        assert flt.table == "PO_DISTRIBUTIONS_ALL"

    def test_mixed_qualified_and_bare_select_columns(self, engine):
        plan = _make_plan(
            select_columns=["PO_HEADERS_ALL.vendor_id", "creation_date", "PO_HEADERS_ALL.segment1"]
        )
        repaired, result = engine.repair(plan)

        assert repaired.select_columns == ["vendor_id", "creation_date", "segment1"]
        assert result.repaired_fields_count == 2

    def test_action_metadata_correct(self, engine):
        plan = _make_plan(select_columns=["PO_HEADERS_ALL.segment1"])
        _, result = engine.repair(plan)

        action = result.actions[0]
        assert action.repair_type == "C_qualified_column"
        assert action.original_value == "PO_HEADERS_ALL.segment1"
        assert action.repaired_value == "segment1"
        assert action.field_path == "select_columns[0]"


# ---------------------------------------------------------------------------
# Pass E — Anchor-table repair
# ---------------------------------------------------------------------------

class TestPassE:
    def test_child_table_redirected_to_root(self, engine):
        plan = _make_plan(table="PO_LINES_ALL")
        repaired, result = engine.repair(plan, user_message="kalem sayısı")

        assert repaired.table == "PO_HEADERS_ALL"
        e_actions = [a for a in result.actions if a.repair_type == "E_anchor_table"]
        assert len(e_actions) == 1
        assert e_actions[0].original_value == "PO_LINES_ALL"
        assert e_actions[0].repaired_value == "PO_HEADERS_ALL"

    def test_deeper_child_table_redirected(self, engine):
        plan = _make_plan(table="PO_LINE_LOCATIONS_ALL")
        repaired, result = engine.repair(plan)

        assert repaired.table == "PO_HEADERS_ALL"
        assert any(a.repair_type == "E_anchor_table" for a in result.actions)

    def test_root_table_unchanged(self, engine):
        plan = _make_plan(table="PO_HEADERS_ALL")
        repaired, result = engine.repair(plan)

        assert repaired.table == "PO_HEADERS_ALL"
        assert not any(a.repair_type == "E_anchor_table" for a in result.actions)

    def test_unknown_table_unchanged(self, engine):
        plan = _make_plan(table="CUSTOM_TABLE_XYZ")
        repaired, result = engine.repair(plan)

        assert repaired.table == "CUSTOM_TABLE_XYZ"
        assert not any(a.repair_type == "E_anchor_table" for a in result.actions)

    def test_clarification_plan_skips_anchor_repair(self, engine):
        plan = _make_clarification_plan(table="PO_LINES_ALL")
        repaired, result = engine.repair(plan, user_message="kalem")

        assert repaired.table == "PO_LINES_ALL"
        assert not any(a.repair_type == "E_anchor_table" for a in result.actions)

    def test_distributions_child_table(self, engine):
        plan = _make_plan(table="PO_DISTRIBUTIONS_ALL")
        repaired, result = engine.repair(plan)

        assert repaired.table == "PO_HEADERS_ALL"


# ---------------------------------------------------------------------------
# Pass F — Clarification rescue
# ---------------------------------------------------------------------------

class TestPassF:
    def test_rescue_on_keyword_match(self, engine):
        """When the user message matches PO keywords, clarification is rescued."""
        plan = _make_clarification_plan(table=None)
        repaired, result = engine.repair(plan, user_message="satınalma siparişleri listele")

        assert repaired.needs_clarification is False
        assert repaired.clarification_message is None
        assert repaired.semantic_intent is not None
        f_actions = [a for a in result.actions if a.repair_type == "F_clarification_rescue"]
        assert len(f_actions) == 1

    def test_no_rescue_when_no_keyword_match(self, engine):
        """Clarification is preserved when no registry keyword matches."""
        plan = _make_clarification_plan(table=None)
        repaired, result = engine.repair(plan, user_message="çok karmaşık bir şey")

        assert repaired.needs_clarification is True
        assert not any(a.repair_type == "F_clarification_rescue" for a in result.actions)

    def test_rescue_clears_both_clarification_fields(self, engine):
        plan = _make_clarification_plan(table=None)
        repaired, result = engine.repair(plan, user_message="sipariş kalemleri")

        assert repaired.needs_clarification is False
        assert repaired.clarification_message is None

    def test_rescue_table_injected_from_entity(self, engine):
        plan = _make_clarification_plan(table=None)
        repaired, result = engine.repair(plan, user_message="tedarikçi siparişleri")

        # table should be injected from entity root
        assert repaired.table is not None

    def test_not_rescue_when_not_clarification(self, engine):
        plan = _make_plan(table="PO_HEADERS_ALL")
        repaired, result = engine.repair(plan, user_message="satınalma")

        assert not any(a.repair_type == "F_clarification_rescue" for a in result.actions)

    def test_rescue_on_table_candidate_match(self, engine):
        """Rescue should also trigger when candidate_tables has an entity table."""
        plan = _make_clarification_plan(
            table=None,
            candidate_tables=["PO_HEADERS_ALL"],
        )
        repaired, result = engine.repair(plan, user_message="liste ver")

        assert repaired.needs_clarification is False


# ---------------------------------------------------------------------------
# Pass D — Group-by auto fill
# ---------------------------------------------------------------------------

class TestPassD:
    def test_group_by_filled_from_select(self, engine):
        plan = _make_plan(
            select_columns=["vendor_id", "creation_date"],
            aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="*")],
            group_by=[],
        )
        repaired, result = engine.repair(plan)

        assert set(repaired.group_by) == {"vendor_id", "creation_date"}
        assert any(a.repair_type == "D_group_by_fill" for a in result.actions)

    def test_group_by_not_touched_when_already_set(self, engine):
        plan = _make_plan(
            select_columns=["vendor_id"],
            aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="*")],
            group_by=["vendor_id"],
        )
        repaired, result = engine.repair(plan)

        assert repaired.group_by == ["vendor_id"]
        assert not any(a.repair_type == "D_group_by_fill" for a in result.actions)

    def test_group_by_not_filled_without_aggregations(self, engine):
        plan = _make_plan(
            select_columns=["vendor_id", "creation_date"],
            group_by=[],
        )
        repaired, result = engine.repair(plan)

        assert repaired.group_by == []
        assert not any(a.repair_type == "D_group_by_fill" for a in result.actions)

    def test_agg_columns_excluded_from_group_by_fill(self, engine):
        """The aggregated column itself must not appear in group_by."""
        plan = _make_plan(
            select_columns=["vendor_id", "quantity"],
            aggregations=[AggregationSpec(function=AggregateFn.SUM, column="quantity")],
            group_by=[],
        )
        repaired, result = engine.repair(plan)

        assert "quantity" not in repaired.group_by
        assert "vendor_id" in repaired.group_by

    def test_expressions_excluded_from_group_by_fill(self, engine):
        plan = _make_plan(
            select_columns=["vendor_id", "SUM(quantity)"],
            aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="*")],
            group_by=[],
        )
        repaired, result = engine.repair(plan)

        assert "SUM(quantity)" not in repaired.group_by
        assert "vendor_id" in repaired.group_by

    def test_no_fill_when_only_agg_cols_in_select(self, engine):
        """No group_by fill if every select column is an aggregate column."""
        plan = _make_plan(
            select_columns=["quantity"],
            aggregations=[AggregationSpec(function=AggregateFn.SUM, column="quantity")],
            group_by=[],
        )
        repaired, result = engine.repair(plan)

        assert repaired.group_by == []


# ---------------------------------------------------------------------------
# Pass G — Degenerate-plan guard
# ---------------------------------------------------------------------------

class TestPassG:
    def test_table_injected_on_keyword_match(self, engine):
        plan = QueryPlan(intent="satınalma listesi", table=None)
        repaired, result = engine.repair(plan, user_message="satınalma siparişlerini göster")

        assert repaired.table is not None
        assert any(a.repair_type == "G_degenerate_plan" for a in result.actions)

    def test_no_injection_when_table_present(self, engine):
        plan = _make_plan(table="PO_HEADERS_ALL")
        repaired, result = engine.repair(plan, user_message="satınalma")

        assert not any(a.repair_type == "G_degenerate_plan" for a in result.actions)

    def test_no_injection_without_keyword_match(self, engine):
        plan = QueryPlan(intent="bilinmeyen intent", table=None)
        repaired, result = engine.repair(plan, user_message="xyz123 bilinmeyen sorgu")

        assert repaired.table is None
        assert not any(a.repair_type == "G_degenerate_plan" for a in result.actions)

    def test_no_injection_for_clarification_plan(self, engine):
        plan = _make_clarification_plan(table=None)
        repaired, result = engine.repair(plan, user_message="satınalma")

        # F-rescue may have fired but G should not
        assert not any(a.repair_type == "G_degenerate_plan" for a in result.actions)


# ---------------------------------------------------------------------------
# Audit trail / RepairResult tests
# ---------------------------------------------------------------------------

class TestRepairResult:
    def test_no_repair_false_by_default(self, engine):
        plan = _make_plan()
        _, result = engine.repair(plan)

        assert result.repair_applied is False
        assert result.repaired_fields_count == 0
        assert result.actions == []

    def test_multiple_actions_accumulated(self, engine):
        plan = _make_plan(
            table="PO_LINES_ALL",
            select_columns=["PO_HEADERS_ALL.vendor_id"],
        )
        _, result = engine.repair(plan)

        assert result.repaired_fields_count >= 2  # C + E both fire

    def test_repair_result_record(self):
        rr = RepairResult()
        rr.record(RepairAction("C_qualified_column", "desc", "field", "old", "new"))
        assert rr.repair_applied is True
        assert rr.repaired_fields_count == 1


# ---------------------------------------------------------------------------
# Pipeline interaction / integration
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    def test_c_before_e_pipeline_order(self, engine):
        """After C strips a qualified column, E should still redirect the table."""
        plan = _make_plan(
            table="PO_LINES_ALL",
            select_columns=["PO_HEADERS_ALL.vendor_id"],
        )
        repaired, result = engine.repair(plan, user_message="kalem listesi")

        assert repaired.table == "PO_HEADERS_ALL"
        assert repaired.select_columns == ["vendor_id"]
        types = [a.repair_type for a in result.actions]
        # C action recorded before E action
        assert types.index("C_qualified_column") < types.index("E_anchor_table")

    def test_repair_engine_returns_tuple(self, engine):
        plan = _make_plan()
        out = engine.repair(plan)

        assert isinstance(out, tuple)
        assert len(out) == 2
        assert isinstance(out[0], QueryPlan)
        assert isinstance(out[1], RepairResult)

    def test_no_mutation_on_original_plan(self, engine):
        """Repair must not mutate the input plan (model_copy creates new objects)."""
        plan = _make_plan(select_columns=["PO_HEADERS_ALL.vendor_id"])
        original_select = list(plan.select_columns)
        repaired, _ = engine.repair(plan)

        assert plan.select_columns == original_select
        assert repaired.select_columns != original_select

    @pytest.mark.asyncio
    async def test_planner_service_exposes_repair_result(self):
        """PlannerService.last_repair_result is populated after plan()."""
        from unittest.mock import AsyncMock
        from app.services.planner_service import PlannerService
        from app.domain.catalog_models import CatalogSnapshot

        mock_llm = AsyncMock()
        mock_llm.generate_structured.return_value = QueryPlan(
            intent="satınalma listesi",
            table="PO_LINES_ALL",
        )

        mock_catalog = AsyncMock()
        mock_catalog.get_relevant_context.return_value = CatalogSnapshot(tables=[])

        service = PlannerService(llm=mock_llm, catalog=mock_catalog)

        await service.plan("kalem listesi")
        assert service.last_repair_result is not None
        # anchor repair should have fired (PO_LINES_ALL → PO_HEADERS_ALL)
        e_actions = [
            a for a in service.last_repair_result.actions
            if a.repair_type == "E_anchor_table"
        ]
        assert len(e_actions) == 1
