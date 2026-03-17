from __future__ import annotations

from app.domain.catalog_models import ColumnMetadata, ColumnType, TableMetadata
from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.services.sql_compiler import SQLCompiler, _render_filter_value


def _po_headers() -> TableMetadata:
    return TableMetadata(
        name="PO_HEADERS_ALL",
        columns=[
            ColumnMetadata(name="po_header_id", data_type=ColumnType.NUMBER),
            ColumnMetadata(name="creation_date", data_type=ColumnType.DATE),
        ],
    )


def test_relative_date_expression_normalized() -> None:
    raw, bind = _render_filter_value("__EXPR__CURRENT_DATE - 30")
    assert raw == "TRUNC(SYSDATE)-30"
    assert bind is None


def test_extract_year_expression_normalized_in_select() -> None:
    compiler = SQLCompiler()
    plan = QueryPlan(
        intent="x",
        table="PO_HEADERS_ALL",
        select_columns=["EXTRACT(YEAR FROM creation_date)"],
    )
    result = compiler.compile(plan, _po_headers())
    assert "TO_CHAR(creation_date,'YYYY')" in result.sql


def test_relative_date_filter_compiles_with_canonical_expr() -> None:
    compiler = SQLCompiler()
    plan = QueryPlan(
        intent="x",
        table="PO_HEADERS_ALL",
        select_columns=["po_header_id"],
        filters=[FilterSpec(column="creation_date", op=FilterOp.GTE, value="__EXPR__SYSDATE-30")],
    )
    result = compiler.compile(plan, _po_headers())
    assert "creation_date >= TRUNC(SYSDATE)-30" in result.sql
