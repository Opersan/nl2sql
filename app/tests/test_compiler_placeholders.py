"""Tests for SQL compiler placeholder and ComputedMeasureSpec support.

Covers
------
* ``__COLUMN_REF__<name>`` filter values — single-table and multi-table paths
* ``__EXPR__<sql>``         filter values — single-table and multi-table paths
* ``ComputedMeasureSpec`` / ``expression_ref`` in SELECT (multi-table)
* q_106 (po_pending_delivery) compile smoke
* q_107 (po_distribution_amount) compile smoke
* q_109 (po_last_30_days) compile smoke
"""
from __future__ import annotations

import pytest

from app.core.exceptions import CompilationError
from app.domain.catalog_models import ColumnMetadata, ColumnType, TableMetadata
from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    ComputedMeasureSpec,
    FilterOp,
    FilterSpec,
    JoinCondition,
    JoinSpec,
    JoinType,
    QueryPlan,
)
from app.services.sql_compiler import SQLCompiler, _EXPRESSION_REGISTRY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _col(name: str, dtype: ColumnType = ColumnType.NUMBER) -> ColumnMetadata:
    return ColumnMetadata(name=name, data_type=dtype)


def _po_headers() -> TableMetadata:
    return TableMetadata(
        name="PO_HEADERS_ALL",
        columns=[
            _col("po_header_id"),
            _col("vendor_id"),
            _col("creation_date", ColumnType.DATE),
            _col("authorization_status", ColumnType.VARCHAR),
        ],
    )


def _po_lines() -> TableMetadata:
    return TableMetadata(
        name="PO_LINES_ALL",
        columns=[
            _col("po_line_id"),
            _col("po_header_id"),
            _col("item_id"),
            _col("line_num"),
            _col("item_description", ColumnType.VARCHAR),
            _col("quantity"),
            _col("unit_price"),
        ],
    )


def _po_shipments() -> TableMetadata:
    return TableMetadata(
        name="PO_LINE_LOCATIONS_ALL",
        columns=[
            _col("line_location_id"),
            _col("po_line_id"),
            _col("quantity_received"),
        ],
    )


def _po_distributions() -> TableMetadata:
    return TableMetadata(
        name="PO_DISTRIBUTIONS_ALL",
        columns=[
            _col("po_distribution_id"),
            _col("line_location_id"),
            _col("quantity_ordered"),
            _col("code_combination_id"),
        ],
    )


@pytest.fixture
def compiler() -> SQLCompiler:
    return SQLCompiler()


# ---------------------------------------------------------------------------
# Unit tests: _render_filter_value helper
# ---------------------------------------------------------------------------

class TestRenderFilterValue:
    """Direct tests for the module-level helper."""

    def test_column_ref_prefix_resolved(self) -> None:
        from app.services.sql_compiler import _render_filter_value
        raw, bind = _render_filter_value("__COLUMN_REF__quantity", resolve_col=lambda c: f"t.{c}")
        assert raw == "t.quantity"
        assert bind is None

    def test_column_ref_fallback_no_resolver(self) -> None:
        from app.services.sql_compiler import _render_filter_value
        raw, bind = _render_filter_value("__COLUMN_REF__quantity", resolve_col=None)
        assert raw == "quantity"
        assert bind is None

    def test_expr_prefix_inlined(self) -> None:
        from app.services.sql_compiler import _render_filter_value
        raw, bind = _render_filter_value("__EXPR__TRUNC(SYSDATE)-30")
        assert raw == "TRUNC(SYSDATE)-30"
        assert bind is None

    def test_plain_value_unchanged(self) -> None:
        from app.services.sql_compiler import _render_filter_value
        raw, bind = _render_filter_value("APPROVED")
        assert raw is None
        assert bind == "APPROVED"

    def test_int_value_unchanged(self) -> None:
        from app.services.sql_compiler import _render_filter_value
        raw, bind = _render_filter_value(42)
        assert raw is None
        assert bind == 42


# ---------------------------------------------------------------------------
# Unit tests: _expand_expression helper
# ---------------------------------------------------------------------------

class TestExpandExpression:
    def test_resolves_identifiers(self) -> None:
        from app.services.sql_compiler import _expand_expression
        resolved = _expand_expression(
            "quantity * unit_price",
            "MY_TABLE",
            resolve_col=lambda col, tbl: f"m.{col}",
        )
        assert resolved == "m.quantity * m.unit_price"

    def test_preserves_operators(self) -> None:
        from app.services.sql_compiler import _expand_expression
        resolved = _expand_expression(
            "qty + 1",
            None,
            resolve_col=lambda col, tbl: f"t.{col}" if col != "1" else col,
        )
        # "1" is not an identifier so it won't be passed to resolve_col
        assert "t.qty" in resolved
        assert " + " in resolved


# ---------------------------------------------------------------------------
# 1. __COLUMN_REF__ — single-table path
# ---------------------------------------------------------------------------

class TestColumnRefSingleTable:
    def test_no_bind_param_for_column_ref(self, compiler: SQLCompiler) -> None:
        """__COLUMN_REF__quantity should produce a column reference, not :pN."""
        table = _po_shipments()
        # Use quantity_received as both column and a reference column (same table for simplicity)
        plan = QueryPlan(
            intent="pending delivery",
            table="PO_LINE_LOCATIONS_ALL",
            select_columns=["po_line_id", "quantity_received"],
            filters=[
                FilterSpec(
                    column="quantity_received",
                    op=FilterOp.LT,
                    value="__COLUMN_REF__quantity_received",
                )
            ],
        )
        # quantity_received exists on the table — resolves to itself
        result = compiler.compile(plan, table)
        # Must NOT have a bind param for the filter value
        assert "quantity_received < quantity_received" in result.sql
        # The only bind param is the ROWNUM limit
        param_keys = [k for k in result.params if k != "p1"]
        assert param_keys == [], f"Unexpected bind params: {param_keys}"


# ---------------------------------------------------------------------------
# 2. __EXPR__ — single-table path
# ---------------------------------------------------------------------------

class TestExprSingleTable:
    def test_expr_inlined_no_bind_param(self, compiler: SQLCompiler) -> None:
        """__EXPR__TRUNC(SYSDATE)-30 should be inlined, not bound."""
        table = _po_headers()
        plan = QueryPlan(
            intent="last 30 days",
            table="PO_HEADERS_ALL",
            select_columns=["po_header_id", "creation_date"],
            filters=[
                FilterSpec(
                    column="creation_date",
                    op=FilterOp.GTE,
                    value="__EXPR__TRUNC(SYSDATE)-30",
                )
            ],
        )
        result = compiler.compile(plan, table)
        assert "creation_date >= TRUNC(SYSDATE)-30" in result.sql
        # Only ROWNUM bind param should exist
        assert set(result.params.keys()) == {"p1"}
        assert result.params["p1"] == 100


# ---------------------------------------------------------------------------
# 3. __COLUMN_REF__ — multi-table path
# ---------------------------------------------------------------------------

class TestColumnRefMultiTable:
    def test_column_ref_resolved_with_alias(self, compiler: SQLCompiler) -> None:
        """In multi-table context __COLUMN_REF__quantity → aliased column."""
        primary = _po_headers()
        extra = {
            "PO_LINES_ALL": _po_lines(),
            "PO_LINE_LOCATIONS_ALL": _po_shipments(),
        }
        plan = QueryPlan(
            intent="pending delivery",
            table="PO_HEADERS_ALL",
            select_columns=["po_header_id"],
            joins=[
                JoinSpec(
                    left_table="PO_HEADERS_ALL",
                    right_table="PO_LINES_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_HEADERS_ALL", left_column="po_header_id",
                        right_table="PO_LINES_ALL", right_column="po_header_id",
                    )],
                ),
                JoinSpec(
                    left_table="PO_LINES_ALL",
                    right_table="PO_LINE_LOCATIONS_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_LINES_ALL", left_column="po_line_id",
                        right_table="PO_LINE_LOCATIONS_ALL", right_column="po_line_id",
                    )],
                ),
            ],
            filters=[
                FilterSpec(
                    column="quantity_received",
                    table="PO_LINE_LOCATIONS_ALL",
                    op=FilterOp.LT,
                    value="__COLUMN_REF__quantity",
                )
            ],
        )
        result = compiler.compile(plan, primary, extra_tables=extra)
        # quantity_received is in PO_LINE_LOCATIONS_ALL (alias p3), quantity in PO_LINES_ALL (alias p2)
        assert "p3.quantity_received < p2.quantity" in result.sql
        # No bind param for the filter value
        param_keys_without_rownum = [k for k, v in result.params.items() if v != 100]
        assert param_keys_without_rownum == []


# ---------------------------------------------------------------------------
# 4. __EXPR__ — multi-table path
# ---------------------------------------------------------------------------

class TestExprMultiTable:
    def test_expr_inlined_multi_table(self, compiler: SQLCompiler) -> None:
        """__EXPR__ value is inlined in multi-table WHERE clause."""
        primary = _po_headers()
        extra = {"PO_LINES_ALL": _po_lines()}
        plan = QueryPlan(
            intent="recent lines",
            table="PO_HEADERS_ALL",
            select_columns=["po_header_id"],
            joins=[
                JoinSpec(
                    left_table="PO_HEADERS_ALL",
                    right_table="PO_LINES_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_HEADERS_ALL", left_column="po_header_id",
                        right_table="PO_LINES_ALL", right_column="po_header_id",
                    )],
                )
            ],
            filters=[
                FilterSpec(
                    column="creation_date",
                    table="PO_HEADERS_ALL",
                    op=FilterOp.GTE,
                    value="__EXPR__TRUNC(SYSDATE)-30",
                )
            ],
        )
        result = compiler.compile(plan, primary, extra_tables=extra)
        assert "p.creation_date >= TRUNC(SYSDATE)-30" in result.sql
        assert set(result.params.keys()) == {"p1"}


# ---------------------------------------------------------------------------
# 5. ComputedMeasureSpec — multi-table path
# ---------------------------------------------------------------------------

class TestComputedMeasureMultiTable:
    def test_computed_measure_appears_in_select(self, compiler: SQLCompiler) -> None:
        """PO_LINE_AMOUNT computed measure expands to aliased column expression."""
        primary = _po_headers()
        extra = {
            "PO_LINES_ALL": _po_lines(),
            "PO_LINE_LOCATIONS_ALL": _po_shipments(),
            "PO_DISTRIBUTIONS_ALL": _po_distributions(),
        }
        plan = QueryPlan(
            intent="distribution amount",
            table="PO_HEADERS_ALL",
            joins=[
                JoinSpec(
                    left_table="PO_HEADERS_ALL",
                    right_table="PO_LINES_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_HEADERS_ALL", left_column="po_header_id",
                        right_table="PO_LINES_ALL", right_column="po_header_id",
                    )],
                ),
                JoinSpec(
                    left_table="PO_LINES_ALL",
                    right_table="PO_LINE_LOCATIONS_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_LINES_ALL", left_column="po_line_id",
                        right_table="PO_LINE_LOCATIONS_ALL", right_column="po_line_id",
                    )],
                ),
                JoinSpec(
                    left_table="PO_LINE_LOCATIONS_ALL",
                    right_table="PO_DISTRIBUTIONS_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_LINE_LOCATIONS_ALL", left_column="line_location_id",
                        right_table="PO_DISTRIBUTIONS_ALL", right_column="line_location_id",
                    )],
                ),
            ],
            group_by=["code_combination_id"],
            aggregations=[
                AggregationSpec(function=AggregateFn.SUM, column="quantity_ordered", table="PO_DISTRIBUTIONS_ALL", alias="ordered_qty"),
            ],
            computed_measures=[
                ComputedMeasureSpec(
                    name="total_amount",
                    expression_ref="PO_LINE_AMOUNT",
                    alias="total_amount",
                    table="PO_LINES_ALL",
                )
            ],
        )
        result = compiler.compile(plan, primary, extra_tables=extra)
        # PO_LINE_AMOUNT → quantity * unit_price, both on PO_LINES_ALL (alias p2)
        assert "(p2.quantity * p2.unit_price) AS total_amount" in result.sql
        assert "total_amount" in result.selected_columns

    def test_unknown_expression_ref_raises(self, compiler: SQLCompiler) -> None:
        """An unknown expression_ref must raise CompilationError."""
        primary = _po_headers()
        extra = {"PO_LINES_ALL": _po_lines()}
        plan = QueryPlan(
            intent="test",
            table="PO_HEADERS_ALL",
            joins=[
                JoinSpec(
                    left_table="PO_HEADERS_ALL",
                    right_table="PO_LINES_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_HEADERS_ALL", left_column="po_header_id",
                        right_table="PO_LINES_ALL", right_column="po_header_id",
                    )],
                )
            ],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="cnt"),
            ],
            group_by=["po_header_id"],
            computed_measures=[
                ComputedMeasureSpec(name="bad", expression_ref="UNKNOWN_EXPR")
            ],
        )
        with pytest.raises(CompilationError, match="UNKNOWN_EXPR"):
            compiler.compile(plan, primary, extra_tables=extra)

    def test_expression_registry_contains_po_line_amount(self) -> None:
        assert "PO_LINE_AMOUNT" in _EXPRESSION_REGISTRY
        assert "quantity" in _EXPRESSION_REGISTRY["PO_LINE_AMOUNT"]
        assert "unit_price" in _EXPRESSION_REGISTRY["PO_LINE_AMOUNT"]


# ---------------------------------------------------------------------------
# 6. q_106 compile smoke — po_pending_delivery
# ---------------------------------------------------------------------------

class TestQ106Smoke:
    def test_q106_pending_delivery_compile(self, compiler: SQLCompiler) -> None:
        """Full q_106 plan: 2-step JOIN + __COLUMN_REF__ filter."""
        primary = _po_headers()
        extra = {
            "PO_LINES_ALL": _po_lines(),
            "PO_LINE_LOCATIONS_ALL": _po_shipments(),
        }
        plan = QueryPlan(
            intent="Teslim bekleyen satırları göster",
            table="PO_HEADERS_ALL",
            select_columns=["po_header_id", "quantity_received"],
            joins=[
                JoinSpec(
                    left_table="PO_HEADERS_ALL",
                    right_table="PO_LINES_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_HEADERS_ALL", left_column="po_header_id",
                        right_table="PO_LINES_ALL", right_column="po_header_id",
                    )],
                ),
                JoinSpec(
                    left_table="PO_LINES_ALL",
                    right_table="PO_LINE_LOCATIONS_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_LINES_ALL", left_column="po_line_id",
                        right_table="PO_LINE_LOCATIONS_ALL", right_column="po_line_id",
                    )],
                ),
            ],
            filters=[
                FilterSpec(
                    column="quantity_received",
                    table="PO_LINE_LOCATIONS_ALL",
                    op=FilterOp.LT,
                    value="__COLUMN_REF__quantity",
                )
            ],
        )
        result = compiler.compile(plan, primary, extra_tables=extra)
        # Filter must reference aliased column, not a bind param
        assert "p3.quantity_received < p2.quantity" in result.sql
        # JOIN clauses present
        assert "INNER JOIN PO_LINES_ALL p2" in result.sql
        assert "INNER JOIN PO_LINE_LOCATIONS_ALL p3" in result.sql
        # No bind param for the filter value
        param_values_non_rownum = [v for v in result.params.values() if v != 100]
        assert param_values_non_rownum == []


# ---------------------------------------------------------------------------
# 7. q_107 compile smoke — po_distribution_amount
# ---------------------------------------------------------------------------

class TestQ107Smoke:
    def test_q107_distribution_amount_compile(self, compiler: SQLCompiler) -> None:
        """Full q_107 plan: 3-step JOIN + ComputedMeasureSpec."""
        primary = _po_headers()
        extra = {
            "PO_LINES_ALL": _po_lines(),
            "PO_LINE_LOCATIONS_ALL": _po_shipments(),
            "PO_DISTRIBUTIONS_ALL": _po_distributions(),
        }
        plan = QueryPlan(
            intent="Dağıtım bazında tutar analizi",
            table="PO_HEADERS_ALL",
            joins=[
                JoinSpec(
                    left_table="PO_HEADERS_ALL",
                    right_table="PO_LINES_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_HEADERS_ALL", left_column="po_header_id",
                        right_table="PO_LINES_ALL", right_column="po_header_id",
                    )],
                ),
                JoinSpec(
                    left_table="PO_LINES_ALL",
                    right_table="PO_LINE_LOCATIONS_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_LINES_ALL", left_column="po_line_id",
                        right_table="PO_LINE_LOCATIONS_ALL", right_column="po_line_id",
                    )],
                ),
                JoinSpec(
                    left_table="PO_LINE_LOCATIONS_ALL",
                    right_table="PO_DISTRIBUTIONS_ALL",
                    join_type=JoinType.INNER,
                    on=[JoinCondition(
                        left_table="PO_LINE_LOCATIONS_ALL", left_column="line_location_id",
                        right_table="PO_DISTRIBUTIONS_ALL", right_column="line_location_id",
                    )],
                ),
            ],
            group_by=["code_combination_id"],
            aggregations=[
                AggregationSpec(function=AggregateFn.SUM, column="quantity_ordered", table="PO_DISTRIBUTIONS_ALL", alias="ordered_qty"),
                AggregationSpec(function=AggregateFn.SUM, column="unit_price", table="PO_LINES_ALL", alias="price_sum"),
            ],
            computed_measures=[
                ComputedMeasureSpec(
                    name="total_amount",
                    expression_ref="PO_LINE_AMOUNT",
                    alias="total_amount",
                    table="PO_LINES_ALL",
                )
            ],
        )
        result = compiler.compile(plan, primary, extra_tables=extra)
        # Computed measure in SELECT
        assert "(p2.quantity * p2.unit_price) AS total_amount" in result.sql
        # Standard aggregations
        assert "SUM(p4.quantity_ordered) AS ordered_qty" in result.sql
        assert "SUM(p2.unit_price) AS price_sum" in result.sql
        # GROUP BY
        assert "GROUP BY" in result.sql
        # All 3 expected output columns tracked
        assert "ordered_qty" in result.selected_columns
        assert "price_sum" in result.selected_columns
        assert "total_amount" in result.selected_columns
        # JOIN chain
        assert "INNER JOIN PO_DISTRIBUTIONS_ALL p4" in result.sql


# ---------------------------------------------------------------------------
# 8. q_109 compile smoke — po_last_30_days (single-table)
# ---------------------------------------------------------------------------

class TestQ109Smoke:
    def test_q109_last_30_days_compile(self, compiler: SQLCompiler) -> None:
        """Full q_109 plan: single-table + __EXPR__ filter."""
        table = _po_headers()
        plan = QueryPlan(
            intent="Son 30 günde açılan PO'lar",
            table="PO_HEADERS_ALL",
            select_columns=["po_header_id", "creation_date"],
            filters=[
                FilterSpec(
                    column="creation_date",
                    table="PO_HEADERS_ALL",
                    op=FilterOp.GTE,
                    value="__EXPR__TRUNC(SYSDATE)-30",
                )
            ],
        )
        result = compiler.compile(plan, table)
        # Raw expression inlined — no bind param for the date filter
        assert "creation_date >= TRUNC(SYSDATE)-30" in result.sql
        assert "TRUNC(SYSDATE)-30" in result.sql
        # Exactly one bind param — the ROWNUM limit
        assert set(result.params.keys()) == {"p1"}
        assert result.params["p1"] == 100
        assert "FROM PO_HEADERS_ALL" in result.sql
