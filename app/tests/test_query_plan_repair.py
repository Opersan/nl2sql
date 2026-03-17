from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.domain.catalog_models import CatalogSnapshot
from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
    SortDirection,
)
from app.services.planner_service import PlannerService
from app.services.query_plan_repair import (
    QueryPlanRepairEngine,
    RepairAction,
    RepairResult,
    _is_expression,
    _split_qualifier,
)


@pytest.fixture()
def engine() -> QueryPlanRepairEngine:
    return QueryPlanRepairEngine()


def _plan(**kwargs) -> QueryPlan:
    kwargs.setdefault("intent", "test")
    kwargs.setdefault("table", "PO_HEADERS_ALL")
    return QueryPlan(**kwargs)


def _clar_plan(**kwargs) -> QueryPlan:
    kwargs.setdefault("intent", "Yanit yorumlanamadi")
    kwargs.setdefault("needs_clarification", True)
    kwargs.setdefault("clarification_message", "x")
    return QueryPlan(**kwargs)


class TestHelpers:
    def test_split_qualifier(self) -> None:
        col, tbl = _split_qualifier("PO_HEADERS_ALL.vendor_id")
        assert col == "vendor_id"
        assert tbl == "PO_HEADERS_ALL"

    def test_is_expression(self) -> None:
        assert _is_expression("SUM(quantity)") is True
        assert _is_expression("vendor_id") is False


class TestSyntaxNormalize:
    def test_qualified_columns_stripped(self, engine: QueryPlanRepairEngine) -> None:
        plan = _plan(
            select_columns=["PO_HEADERS_ALL.vendor_id"],
            group_by=["PO_HEADERS_ALL.vendor_id"],
            order_by=[OrderSpec(column="PO_HEADERS_ALL.creation_date", direction=SortDirection.DESC)],
            filters=[FilterSpec(column="PO_HEADERS_ALL.creation_date", op=FilterOp.IS_NULL, value=None)],
            aggregations=[AggregationSpec(function=AggregateFn.SUM, column="PO_LINES_ALL.quantity")],
        )
        repaired, result = engine.repair(plan, "ignored")

        assert repaired.select_columns == ["vendor_id"]
        assert repaired.group_by == ["vendor_id"]
        assert repaired.order_by[0].column == "creation_date"
        assert repaired.order_by[0].table == "PO_HEADERS_ALL"
        assert repaired.filters[0].column == "creation_date"
        assert repaired.filters[0].table == "PO_HEADERS_ALL"
        assert repaired.filters[0].op == FilterOp.IS_NULL
        assert repaired.aggregations[0].column == "quantity"
        assert repaired.aggregations[0].table == "PO_LINES_ALL"
        assert any(a.repair_type == "syntax_normalize" for a in result.actions)

    def test_like_and_date_literal_normalization(self, engine: QueryPlanRepairEngine) -> None:
        plan = _plan(
            filters=[
                FilterSpec(column="creation_date", op=FilterOp.GTE, value="10.03.2026"),
                FilterSpec(column="item_description", op=FilterOp.LIKE, value="printer"),
            ]
        )
        repaired, _ = engine.repair(plan, "ignored")

        assert repaired.filters[0].value == "2026-03-10"
        assert repaired.filters[1].value == "%printer%"


class TestRegistryEnforcement:
    def test_child_table_anchors_to_registry_root(self, engine: QueryPlanRepairEngine) -> None:
        plan = _plan(table="PO_LINES_ALL")
        repaired, result = engine.repair(plan, "any message")
        assert repaired.table == "PO_HEADERS_ALL"
        assert any(a.repair_type == "semantic_enforce" for a in result.actions)

    def test_semantic_intent_applies_registry_defaults(self, engine: QueryPlanRepairEngine) -> None:
        plan = _plan(
            table="PO_HEADERS_ALL",
            semantic_intent="po_distribution_amount",
            aggregations=[],
            group_by=[],
            joins=[],
            filters=[],
        )
        repaired, _ = engine.repair(plan, "ignored")

        assert repaired.join_path_id == "po.header_lines_shipments_distributions"
        assert repaired.table == "PO_HEADERS_ALL"
        assert repaired.group_by == ["code_combination_id"]
        assert len(repaired.aggregations) == 2
        assert repaired.aggregations[0].table == "PO_DISTRIBUTIONS_ALL"
        assert repaired.aggregations[1].table == "PO_LINES_ALL"

    def test_message_does_not_drive_domain_routing(self, engine: QueryPlanRepairEngine) -> None:
        plan = _plan(table="PO_HEADERS_ALL")
        repaired, _ = engine.repair(plan, "calisan listesi")
        assert repaired.table == "PO_HEADERS_ALL"

    def test_no_degenerate_table_injection_from_message(self, engine: QueryPlanRepairEngine) -> None:
        plan = QueryPlan(intent="x", table=None)
        repaired, _ = engine.repair(plan, "satinalma siparisleri")
        assert repaired.table is None


class TestClarificationPolicy:
    def test_stable_registry_intent_can_rescue(self, engine: QueryPlanRepairEngine) -> None:
        plan = _clar_plan(table="PO_HEADERS_ALL", semantic_intent="po_open_orders")
        repaired, result = engine.repair(plan, "ignored")
        assert repaired.needs_clarification is False
        assert repaired.clarification_message is None
        assert any(a.repair_type == "clarification_policy" for a in result.actions)

    def test_non_stable_intent_does_not_rescue(self, engine: QueryPlanRepairEngine) -> None:
        plan = _clar_plan(table="XXBT_PDKS_PER_DETAILS_V", semantic_intent="emp_generic_list")
        repaired, _ = engine.repair(plan, "ignored")
        assert repaired.needs_clarification is True

    def test_no_intent_no_rescue(self, engine: QueryPlanRepairEngine) -> None:
        plan = _clar_plan(table="PO_HEADERS_ALL")
        repaired, _ = engine.repair(plan, "satinalma siparisleri")
        assert repaired.needs_clarification is True


class TestRepairResult:
    def test_record_marks_applied(self) -> None:
        rr = RepairResult()
        rr.record(RepairAction("x", "d", "f", "o", "n"))
        assert rr.repair_applied is True
        assert rr.repaired_fields_count == 1


class TestPlannerIntegration:
    @pytest.mark.asyncio
    async def test_planner_service_exposes_repair_result(self) -> None:
        mock_llm = AsyncMock()
        mock_llm.generate_structured.return_value = QueryPlan(intent="x", table="PO_LINES_ALL")

        mock_catalog = AsyncMock()
        mock_catalog.get_relevant_context.return_value = CatalogSnapshot(tables=[])

        service = PlannerService(llm=mock_llm, catalog=mock_catalog)
        out = await service.plan("anything")

        assert out.table == "PO_HEADERS_ALL"
        assert service.last_repair_result is not None
        assert any(a.repair_type == "semantic_enforce" for a in service.last_repair_result.actions)
