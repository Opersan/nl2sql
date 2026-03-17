from __future__ import annotations

from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.services.query_plan_repair import QueryPlanRepairEngine


def test_pass_h_wrong_root_table_fix() -> None:
    engine = QueryPlanRepairEngine()
    plan = QueryPlan(
        intent="x",
        table="PO_HEADERS_ALL",
        semantic_intent="po_distribution_amount",
        select_columns=["po_header_id"],
    )

    repaired, result = engine.repair(plan, "dağıtım bazında tutar")

    assert repaired.table == "PO_DISTRIBUTIONS_ALL"
    assert any(a.repair_type == "H_wrong_root_table_fix" for a in result.actions)


def test_pass_i_missing_join_path_fix() -> None:
    engine = QueryPlanRepairEngine()
    plan = QueryPlan(
        intent="x",
        table="PO_HEADERS_ALL",
        semantic_intent="po_pending_delivery",
        select_columns=["po_header_id"],
        joins=[],
        filters=[
            FilterSpec(
                column="quantity_received",
                table="PO_LINE_LOCATIONS_ALL",
                op=FilterOp.LT,
                value=1,
            )
        ],
    )

    repaired, result = engine.repair(plan, "teslim bekleyen satırları göster")

    joined_tables = {j.right_table for j in repaired.joins}
    assert "PO_LINES_ALL" in joined_tables
    assert "PO_LINE_LOCATIONS_ALL" in joined_tables
    assert any(a.repair_type == "I_missing_join_path_fix" for a in result.actions)


def test_pass_j_filter_column_repair_alias() -> None:
    engine = QueryPlanRepairEngine()
    plan = QueryPlan(
        intent="x",
        table="XXBT_PDKS_PER_DETAILS_V",
        filters=[FilterSpec(column="MAIL", op=FilterOp.IS_NOT_NULL)],
        select_columns=["SICIL_NO"],
    )

    repaired, result = engine.repair(plan, "maili olan çalışanlar")

    assert repaired.filters[0].column == "email"
    assert any(a.repair_type == "J_filter_column_repair" for a in result.actions)
