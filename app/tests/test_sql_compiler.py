"""Tests for the SQL compiler."""

from __future__ import annotations

import datetime

import pytest

from app.core.exceptions import CompilationError
from app.domain.query_plan import (
    STAR_COLUMN,
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
    SortDirection,
)
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.services.sql_compiler import SQLCompiler


@pytest.fixture
def compiler() -> SQLCompiler:
    return SQLCompiler()


@pytest.fixture
async def employee_table():
    """Return the employee TableMetadata from the in-memory catalog."""
    provider = InMemoryCatalogProvider()
    return await provider.get_table("XXBT_PDKS_PER_DETAILS_V")


# ---------------------------------------------------------------------------
# Basic SELECT
# ---------------------------------------------------------------------------


class TestBasicSelect:
    @pytest.mark.asyncio
    async def test_simple_select(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="list employees",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name", "last_name"],
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT SICIL_NO, AD, SOYAD" in result.sql
        assert "FROM XXBT_PDKS_PER_DETAILS_V" in result.sql
        # Oracle legacy ROWNUM wrapping with bind parameter
        assert "WHERE ROWNUM <= :p1" in result.sql
        assert result.params["p1"] == 100
        assert result.table == "XXBT_PDKS_PER_DETAILS_V"
        assert result.selected_columns == ["SICIL_NO", "AD", "SOYAD"]

    @pytest.mark.asyncio
    async def test_alias_resolved_to_canonical(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="list departments",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["sicil_no", "department"],
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT SICIL_NO, BIRIM_ADI" in result.sql

    @pytest.mark.asyncio
    async def test_compilation_error_on_empty_select(self, compiler: SQLCompiler, employee_table) -> None:
        """Compiler must raise CompilationError when no columns and no aggregations."""
        plan = QueryPlan(
            intent="empty",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=[],
        )
        with pytest.raises(CompilationError):
            compiler.compile(plan, employee_table)


# ---------------------------------------------------------------------------
# WHERE filters
# ---------------------------------------------------------------------------


class TestWhereFilters:
    @pytest.mark.asyncio
    async def test_equality_filter(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="find by unit",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            filters=[FilterSpec(column="unit_name", op=FilterOp.EQ, value="IT")],
        )
        result = compiler.compile(plan, employee_table)

        assert "WHERE BIRIM_ADI = :p1" in result.sql
        assert result.params["p1"] == "IT"

    @pytest.mark.asyncio
    async def test_between_filter(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="date range",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "start_date"],
            filters=[
                FilterSpec(
                    column="start_date",
                    op=FilterOp.BETWEEN,
                    value=["2020-01-01", "2023-12-31"],
                ),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "ISE_GIRIS_TARIHI BETWEEN :p1 AND :p2" in result.sql
        assert result.params["p1"] == datetime.date(2020, 1, 1)
        assert result.params["p2"] == datetime.date(2023, 12, 31)

    @pytest.mark.asyncio
    async def test_turkish_style_date_literal_is_coerced_to_date_bind(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="turkish date",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "start_date"],
            filters=[FilterSpec(column="start_date", op=FilterOp.GTE, value="01/02/2024")],
        )

        result = compiler.compile(plan, employee_table)

        assert result.params["p1"] == datetime.date(2024, 2, 1)

    @pytest.mark.asyncio
    async def test_relative_date_sentinel_is_coerced_to_date_bind(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="relative sentinel",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "start_date"],
            filters=[FilterSpec(column="start_date", op=FilterOp.GTE, value="__RELATIVE_DATE_LAST_30_DAYS__")],
        )

        result = compiler.compile(plan, employee_table)

        assert isinstance(result.params["p1"], datetime.date)

    @pytest.mark.asyncio
    async def test_natural_relative_date_expression_is_coerced_to_date_bind(self, compiler: SQLCompiler) -> None:
        provider = InMemoryCatalogProvider()
        po_headers = await provider.get_table("PO_HEADERS_ALL")
        assert po_headers is not None

        plan = QueryPlan(
            intent="son 30 gun po",
            table="PO_HEADERS_ALL",
            select_columns=["PO_HEADER_ID", "CREATION_DATE"],
            filters=[FilterSpec(column="CREATION_DATE", op=FilterOp.GTE, value="NOW - 30 DAYS")],
        )

        result = compiler.compile(plan, po_headers)

        assert isinstance(result.params["p1"], datetime.date)

    @pytest.mark.asyncio
    async def test_week_boundary_tokens_are_coerced_to_date_binds(self, compiler: SQLCompiler) -> None:
        provider = InMemoryCatalogProvider()
        po_headers = await provider.get_table("PO_HEADERS_ALL")
        assert po_headers is not None

        plan = QueryPlan(
            intent="bu hafta siparisler",
            table="PO_HEADERS_ALL",
            select_columns=["PO_HEADER_ID", "CREATION_DATE"],
            filters=[
                FilterSpec(column="CREATION_DATE", op=FilterOp.GTE, value="this_week_start"),
                FilterSpec(column="CREATION_DATE", op=FilterOp.LT, value="this_week_end"),
            ],
        )

        result = compiler.compile(plan, po_headers)

        assert isinstance(result.params["p1"], datetime.date)
        assert isinstance(result.params["p2"], datetime.date)

    @pytest.mark.asyncio
    async def test_timestamp_datetime_value_is_coerced_to_date_bind(self, compiler: SQLCompiler) -> None:
        provider = InMemoryCatalogProvider()
        po_headers = await provider.get_table("PO_HEADERS_ALL")
        assert po_headers is not None

        plan = QueryPlan(
            intent="timestamp bind",
            table="PO_HEADERS_ALL",
            select_columns=["PO_HEADER_ID", "CREATION_DATE"],
            filters=[
                FilterSpec(
                    column="CREATION_DATE",
                    op=FilterOp.GTE,
                    value=datetime.datetime(2024, 3, 25, 14, 30, 0),
                )
            ],
        )

        result = compiler.compile(plan, po_headers)

        assert result.params["p1"] == datetime.date(2024, 3, 25)

    @pytest.mark.asyncio
    async def test_in_filter(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="multiple units",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "unit_name"],
            filters=[
                FilterSpec(
                    column="unit_name",
                    op=FilterOp.IN,
                    value=["IT", "HR", "Finance"],
                ),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "BIRIM_ADI IN (:p1, :p2, :p3)" in result.sql
        assert result.params["p1"] == "IT"
        assert result.params["p2"] == "HR"
        assert result.params["p3"] == "Finance"

    @pytest.mark.asyncio
    async def test_is_null_filter(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="active employees",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            filters=[FilterSpec(column="quit_date", op=FilterOp.IS_NULL)],
        )
        result = compiler.compile(plan, employee_table)

        assert "CIKIS_TARIHI IS NULL" in result.sql
        # No bind param for IS NULL; only the ROWNUM limit param
        assert result.params == {"p1": 100}

    @pytest.mark.asyncio
    async def test_is_not_null_filter(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="resigned employees",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "quit_date"],
            filters=[FilterSpec(column="quit_date", op=FilterOp.IS_NOT_NULL)],
        )
        result = compiler.compile(plan, employee_table)

        assert "CIKIS_TARIHI IS NOT NULL" in result.sql

    @pytest.mark.asyncio
    async def test_like_filter(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="name search",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            filters=[
                FilterSpec(column="first_name", op=FilterOp.LIKE, value="%Ali%"),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "AD LIKE :p1" in result.sql
        assert result.params["p1"] == "%Ali%"

    @pytest.mark.asyncio
    async def test_multiple_filters_combined(self, compiler: SQLCompiler, employee_table) -> None:
        """Multiple filters must produce AND-joined WHERE clause with sequential params."""
        plan = QueryPlan(
            intent="compound filter",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name", "unit_name"],
            filters=[
                FilterSpec(column="unit_name", op=FilterOp.EQ, value="IT"),
                FilterSpec(column="quit_date", op=FilterOp.IS_NULL),
                FilterSpec(column="reg_no", op=FilterOp.GT, value=1000),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "BIRIM_ADI = :p1" in result.sql
        assert "CIKIS_TARIHI IS NULL" in result.sql
        assert "SICIL_NO > :p2" in result.sql
        assert " AND " in result.sql
        assert result.params["p1"] == "IT"
        assert result.params["p2"] == 1000

    @pytest.mark.asyncio
    async def test_compilation_error_on_unknown_column(self, compiler: SQLCompiler, employee_table) -> None:
        """Compiler must raise when filter references a column not in catalog."""
        plan = QueryPlan(
            intent="bad col",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            filters=[FilterSpec(column="nonexistent", op=FilterOp.EQ, value="x")],
        )
        with pytest.raises(CompilationError):
            compiler.compile(plan, employee_table)


# ---------------------------------------------------------------------------
# Aggregations & GROUP BY
# ---------------------------------------------------------------------------


class TestAggregation:
    @pytest.mark.asyncio
    async def test_count_with_group_by(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="count per unit",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT BIRIM_ADI, COUNT(SICIL_NO) AS cnt" in result.sql
        assert "GROUP BY BIRIM_ADI" in result.sql

    @pytest.mark.asyncio
    async def test_aggregate_without_group_by(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="total count",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no"),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "COUNT(SICIL_NO) AS COUNT_reg_no" in result.sql
        assert "GROUP BY" not in result.sql

    @pytest.mark.asyncio
    async def test_aggregate_alias_in_selected_columns(self, compiler: SQLCompiler, employee_table) -> None:
        """selected_columns should include both group_by columns and aggregate aliases."""
        plan = QueryPlan(
            intent="count per unit",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
        )
        result = compiler.compile(plan, employee_table)

        assert result.selected_columns == ["BIRIM_ADI", "cnt"]

    @pytest.mark.asyncio
    async def test_turkish_alias_in_group_by(self, compiler: SQLCompiler, employee_table) -> None:
        """Column alias 'birim' should resolve to 'unit_name' in GROUP BY."""
        plan = QueryPlan(
            intent="count per department alias",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="sicil_no", alias="cnt"),
            ],
            group_by=["birim"],
        )
        result = compiler.compile(plan, employee_table)

        assert "GROUP BY BIRIM_ADI" in result.sql
        assert "COUNT(SICIL_NO)" in result.sql

    @pytest.mark.asyncio
    async def test_count_star(self, compiler: SQLCompiler, employee_table) -> None:
        """COUNT(*) should produce literal COUNT(*) without column resolution."""
        plan = QueryPlan(
            intent="total count star",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="total"),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "COUNT(*) AS total" in result.sql
        assert result.selected_columns == ["total"]


# ---------------------------------------------------------------------------
# ORDER BY & LIMIT
# ---------------------------------------------------------------------------


class TestOrderAndLimit:
    @pytest.mark.asyncio
    async def test_order_by(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="ordered list",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "last_name"],
            order_by=[OrderSpec(column="last_name", direction=SortDirection.DESC)],
        )
        result = compiler.compile(plan, employee_table)

        assert "ORDER BY SOYAD DESC" in result.sql

    @pytest.mark.asyncio
    async def test_custom_limit(self, compiler: SQLCompiler, employee_table) -> None:
        plan = QueryPlan(
            intent="top 5",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            limit=5,
        )
        result = compiler.compile(plan, employee_table)

        assert "WHERE ROWNUM <= :p1" in result.sql
        assert result.params["p1"] == 5

    @pytest.mark.asyncio
    async def test_debug_plan_attached(self, compiler: SQLCompiler, employee_table) -> None:
        """CompiledQuery must carry the original QueryPlan for mock execution."""
        plan = QueryPlan(
            intent="debug check",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
        )
        result = compiler.compile(plan, employee_table)

        assert result.debug_plan is not None
        assert result.debug_plan.intent == "debug check"


# ---------------------------------------------------------------------------
# STAR_COLUMN contract – query_plan ↔ compiler
# ---------------------------------------------------------------------------


class TestStarColumnContract:
    """Verify the STAR_COLUMN sentinel is consistent between query_plan.py
    and sql_compiler.py, and that it is correctly handled at every layer."""

    def test_star_column_sentinel_value(self) -> None:
        """STAR_COLUMN must be the literal string '*'."""
        assert STAR_COLUMN == "*"

    @pytest.mark.asyncio
    async def test_count_star_uses_literal_star(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """Compiler must emit COUNT(*) – not COUNT(some_column)."""
        plan = QueryPlan(
            intent="count star literal",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column=STAR_COLUMN, alias="n"),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "COUNT(*) AS n" in result.sql
        assert result.selected_columns == ["n"]

    @pytest.mark.asyncio
    async def test_count_star_no_resolve_attempt(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """STAR_COLUMN must bypass _resolve() – '*' is not a table column."""
        plan = QueryPlan(
            intent="star bypass",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column=STAR_COLUMN),
            ],
        )
        # Should NOT raise CompilationError
        result = compiler.compile(plan, employee_table)
        assert "COUNT(*)" in result.sql

    @pytest.mark.asyncio
    async def test_count_star_with_group_by(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """COUNT(*) combined with GROUP BY should produce valid SQL."""
        plan = QueryPlan(
            intent="count star grouped",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column=STAR_COLUMN, alias="cnt"),
            ],
            group_by=["unit_name"],
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT BIRIM_ADI, COUNT(*) AS cnt" in result.sql
        assert "GROUP BY BIRIM_ADI" in result.sql
        assert result.selected_columns == ["BIRIM_ADI", "cnt"]

    @pytest.mark.asyncio
    async def test_count_star_with_filter(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """COUNT(*) + WHERE filter should produce valid SQL."""
        plan = QueryPlan(
            intent="count star filtered",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column=STAR_COLUMN, alias="active"),
            ],
            filters=[FilterSpec(column="quit_date", op=FilterOp.IS_NULL)],
        )
        result = compiler.compile(plan, employee_table)

        assert "COUNT(*) AS active" in result.sql
        assert "WHERE CIKIS_TARIHI IS NULL" in result.sql


# ---------------------------------------------------------------------------
# ORDER BY aggregate alias – compiler support
# ---------------------------------------------------------------------------


class TestOrderByAggAlias:
    """Verify that ORDER BY correctly emits aggregate aliases without
    attempting to resolve them as table columns."""

    @pytest.mark.asyncio
    async def test_order_by_custom_alias(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """ORDER BY a user-supplied aggregate alias → literal alias in SQL."""
        plan = QueryPlan(
            intent="order by custom alias",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
            order_by=[OrderSpec(column="cnt", direction=SortDirection.DESC)],
        )
        result = compiler.compile(plan, employee_table)

        assert "ORDER BY cnt DESC" in result.sql

    @pytest.mark.asyncio
    async def test_order_by_auto_alias(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """ORDER BY the auto-generated alias (e.g. COUNT_reg_no)."""
        plan = QueryPlan(
            intent="order by auto alias",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no"),
            ],
            group_by=["unit_name"],
            order_by=[
                OrderSpec(column="COUNT_reg_no", direction=SortDirection.ASC),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "ORDER BY COUNT_reg_no ASC" in result.sql

    @pytest.mark.asyncio
    async def test_order_by_count_star_alias(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """ORDER BY the alias of a COUNT(*) aggregate."""
        plan = QueryPlan(
            intent="order by count star alias",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column=STAR_COLUMN, alias="total"),
            ],
            group_by=["unit_name"],
            order_by=[OrderSpec(column="total", direction=SortDirection.DESC)],
        )
        result = compiler.compile(plan, employee_table)

        assert "COUNT(*) AS total" in result.sql
        assert "ORDER BY total DESC" in result.sql

    @pytest.mark.asyncio
    async def test_order_by_mixed_column_and_alias(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """ORDER BY can mix a table column and an aggregate alias."""
        plan = QueryPlan(
            intent="mixed order",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
            order_by=[
                OrderSpec(column="unit_name", direction=SortDirection.ASC),
                OrderSpec(column="cnt", direction=SortDirection.DESC),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "ORDER BY BIRIM_ADI ASC, cnt DESC" in result.sql

    @pytest.mark.asyncio
    async def test_order_by_unknown_alias_raises(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """ORDER BY on a name that is neither a table column nor an agg alias
        must raise CompilationError."""
        plan = QueryPlan(
            intent="bad order alias",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            order_by=[OrderSpec(column="ghost_alias", direction=SortDirection.ASC)],
        )
        with pytest.raises(CompilationError):
            compiler.compile(plan, employee_table)

    @pytest.mark.asyncio
    async def test_order_by_alias_case_insensitive(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """ORDER BY alias matching must be case-insensitive (casefold_tr).

        Plan uses 'CNT' but aggregate defines alias as 'cnt'.  The compiler
        must match them and emit the canonical alias form in the SQL.
        """
        plan = QueryPlan(
            intent="case insensitive alias order",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT, column="reg_no", alias="cnt"
                ),
            ],
            group_by=["unit_name"],
            order_by=[OrderSpec(column="CNT", direction=SortDirection.DESC)],
        )
        result = compiler.compile(plan, employee_table)

        # Must emit the canonical alias form, not the plan-level casing.
        assert "ORDER BY cnt DESC" in result.sql


# ---------------------------------------------------------------------------
# Aggregate + GROUP BY + select_columns – full consistency
# ---------------------------------------------------------------------------


class TestAggGroupBySelectConsistency:
    """Demonstrate that when validation passes, the compiler produces correct
    SQL for various aggregate + group_by ± select_columns scenarios."""

    @pytest.mark.asyncio
    async def test_agg_only_no_group(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """Pure aggregate, no group_by, no select_columns → single agg in SELECT."""
        plan = QueryPlan(
            intent="pure agg",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="total"),
            ],
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT COUNT(SICIL_NO) AS total" in result.sql
        assert "GROUP BY" not in result.sql
        assert result.selected_columns == ["total"]

    @pytest.mark.asyncio
    async def test_agg_with_group_by_no_select(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """Aggregate + group_by, no select_columns → group cols + agg expr."""
        plan = QueryPlan(
            intent="agg grouped",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT BIRIM_ADI, COUNT(SICIL_NO) AS cnt" in result.sql
        assert "GROUP BY BIRIM_ADI" in result.sql
        assert result.selected_columns == ["BIRIM_ADI", "cnt"]

    @pytest.mark.asyncio
    async def test_multiple_aggregates_single_group(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """Multiple aggregates + single group_by column."""
        plan = QueryPlan(
            intent="multi agg",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
                AggregationSpec(function=AggregateFn.COUNT, column=STAR_COLUMN, alias="total"),
            ],
            group_by=["unit_name"],
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT BIRIM_ADI, COUNT(SICIL_NO) AS cnt, COUNT(*) AS total" in result.sql
        assert "GROUP BY BIRIM_ADI" in result.sql
        assert result.selected_columns == ["BIRIM_ADI", "cnt", "total"]

    @pytest.mark.asyncio
    async def test_aggregate_with_alias_in_group_by(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """Turkish alias 'birim' in group_by resolves to canonical 'unit_name'."""
        plan = QueryPlan(
            intent="alias group",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["birim"],
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT BIRIM_ADI, COUNT(SICIL_NO) AS cnt" in result.sql
        assert "GROUP BY BIRIM_ADI" in result.sql

    @pytest.mark.asyncio
    async def test_full_pipeline_agg_group_order_limit(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """Complete aggregate query: agg + group_by + order_by (alias) + limit."""
        plan = QueryPlan(
            intent="full pipeline",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="employee_count"),
            ],
            group_by=["unit_name"],
            order_by=[OrderSpec(column="employee_count", direction=SortDirection.DESC)],
            limit=5,
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT BIRIM_ADI, COUNT(SICIL_NO) AS employee_count" in result.sql
        assert "GROUP BY BIRIM_ADI" in result.sql
        assert "ORDER BY employee_count DESC" in result.sql
        assert "WHERE ROWNUM <= :p1" in result.sql
        assert result.params["p1"] == 5
        assert result.selected_columns == ["BIRIM_ADI", "employee_count"]

    @pytest.mark.asyncio
    async def test_count_star_group_order_full(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """COUNT(*) + group + order by alias – full chain."""
        plan = QueryPlan(
            intent="count star full",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column=STAR_COLUMN, alias="n"),
            ],
            group_by=["unit_name"],
            order_by=[OrderSpec(column="n", direction=SortDirection.DESC)],
            limit=3,
        )
        result = compiler.compile(plan, employee_table)

        assert "SELECT BIRIM_ADI, COUNT(*) AS n" in result.sql
        assert "GROUP BY BIRIM_ADI" in result.sql
        assert "ORDER BY n DESC" in result.sql
        assert "WHERE ROWNUM <= :p1" in result.sql
        assert result.params["p1"] == 3


# ---------------------------------------------------------------------------
# Partition-by / Per-group top-N (ROW_NUMBER analytic)
# ---------------------------------------------------------------------------


class TestPartitionBy:
    """Tests for ROW_NUMBER() OVER (PARTITION BY ...) SQL generation."""

    @pytest.mark.asyncio
    async def test_partition_by_generates_row_number(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """partition_by + order_by → ROW_NUMBER OVER SQL."""
        plan = QueryPlan(
            intent="Her lokasyon icin en kidemli calisan",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["full_name", "location_name", "start_date"],
            partition_by=["location_name"],
            order_by=[OrderSpec(column="start_date", direction=SortDirection.ASC)],
            rank_limit=1,
            limit=100,
        )
        result = compiler.compile(plan, employee_table)

        assert "ROW_NUMBER() OVER" in result.sql
        assert "PARTITION BY LOCATION_ADI" in result.sql
        assert "ORDER BY ISE_GIRIS_TARIHI ASC" in result.sql
        assert "rn <= :p1" in result.sql
        assert "ROWNUM <= :p2" in result.sql
        assert result.params["p1"] == 1
        assert result.params["p2"] == 100

    @pytest.mark.asyncio
    async def test_partition_by_with_filter(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """partition_by with a WHERE filter should include the filter inside the subquery."""
        plan = QueryPlan(
            intent="Her birim icin en kidemli aktif calisan",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["full_name", "unit_name", "start_date"],
            partition_by=["unit_name"],
            order_by=[OrderSpec(column="start_date", direction=SortDirection.ASC)],
            filters=[FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL)],
            rank_limit=1,
        )
        result = compiler.compile(plan, employee_table)

        assert "ROW_NUMBER() OVER" in result.sql
        assert "PARTITION BY BIRIM_ADI" in result.sql
        assert "CIKIS_TARIHI IS NULL" in result.sql

    @pytest.mark.asyncio
    async def test_partition_by_rank_limit_greater_than_one(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """rank_limit > 1 returns top-N per group."""
        plan = QueryPlan(
            intent="Her lokasyon icin en kidemli 3 calisan",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["full_name", "location_name"],
            partition_by=["location_name"],
            order_by=[OrderSpec(column="start_date", direction=SortDirection.ASC)],
            rank_limit=3,
            limit=500,
        )
        result = compiler.compile(plan, employee_table)

        assert result.params["p1"] == 3
        assert result.params["p2"] == 500

    @pytest.mark.asyncio
    async def test_partition_by_without_order_by_raises(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """partition_by without order_by must raise CompilationError."""
        plan = QueryPlan(
            intent="broken partition",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["full_name"],
            partition_by=["location_name"],
        )
        with pytest.raises(CompilationError):
            compiler.compile(plan, employee_table)

    @pytest.mark.asyncio
    async def test_partition_by_no_fetch_first(
        self, compiler: SQLCompiler, employee_table
    ) -> None:
        """Partition queries must never emit FETCH FIRST."""
        plan = QueryPlan(
            intent="partition no fetch",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["full_name"],
            partition_by=["location_name"],
            order_by=[OrderSpec(column="start_date", direction=SortDirection.ASC)],
        )
        result = compiler.compile(plan, employee_table)

        assert "FETCH" not in result.sql.upper()
        assert "OFFSET" not in result.sql.upper()


# ---------------------------------------------------------------------------
# Oracle legacy ROWNUM regression
# ---------------------------------------------------------------------------


class TestOracleRownumRegression:
    """Oracle legacy ROWNUM must be used exclusively.

    FETCH FIRST / OFFSET syntax must NEVER appear in compiled output.
    These tests serve as regression guards for Sprint 3 when a real Oracle
    executor is introduced.
    """

    @pytest.mark.asyncio
    async def test_no_fetch_first_in_simple_select(
        self, compiler: SQLCompiler, employee_table,
    ) -> None:
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"], limit=10,
        )
        result = compiler.compile(plan, employee_table)
        sql_upper = result.sql.upper()

        assert "FETCH" not in sql_upper
        assert "OFFSET" not in sql_upper
        assert "ROWNUM" in sql_upper

    @pytest.mark.asyncio
    async def test_no_fetch_first_in_aggregate(
        self, compiler: SQLCompiler, employee_table,
    ) -> None:
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="n"),
            ],
            group_by=["unit_name"],
            limit=5,
        )
        result = compiler.compile(plan, employee_table)
        sql_upper = result.sql.upper()

        assert "FETCH" not in sql_upper
        assert "OFFSET" not in sql_upper
        assert "ROWNUM" in sql_upper

    @pytest.mark.asyncio
    async def test_no_fetch_first_in_ordered_query(
        self, compiler: SQLCompiler, employee_table,
    ) -> None:
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            order_by=[OrderSpec(column="reg_no", direction=SortDirection.DESC)],
            limit=3,
        )
        result = compiler.compile(plan, employee_table)
        sql_upper = result.sql.upper()

        assert "FETCH" not in sql_upper
        assert "OFFSET" not in sql_upper
        assert "ROWNUM" in sql_upper

    @pytest.mark.asyncio
    async def test_rownum_with_default_limit(
        self, compiler: SQLCompiler, employee_table,
    ) -> None:
        """Default limit=100 must produce ROWNUM <= :p1 with bind value 100."""
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
        )
        result = compiler.compile(plan, employee_table)

        assert "WHERE ROWNUM <= :p1" in result.sql
        assert result.params["p1"] == 100

    @pytest.mark.asyncio
    async def test_rownum_wraps_order_by(
        self, compiler: SQLCompiler, employee_table,
    ) -> None:
        """ORDER BY must be INSIDE the ROWNUM subquery wrapper."""
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            order_by=[OrderSpec(column="reg_no", direction=SortDirection.DESC)],
            limit=5,
        )
        result = compiler.compile(plan, employee_table)

        rownum_pos = result.sql.find("ROWNUM")
        order_pos = result.sql.find("ORDER BY")
        assert order_pos < rownum_pos, (
            "ORDER BY must appear before ROWNUM in the wrapped query"
        )

    @pytest.mark.asyncio
    async def test_rownum_limit_is_bind_param_not_literal(
        self, compiler: SQLCompiler, employee_table,
    ) -> None:
        """ROWNUM limit must be a named bind parameter, never a literal."""
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"], limit=42,
        )
        result = compiler.compile(plan, employee_table)

        assert ":p1" in result.sql
        # The literal "42" should not appear in SQL except as a bind param value
        sql_without_bind = result.sql.replace(":p1", "BIND")
        assert "42" not in sql_without_bind


# ---------------------------------------------------------------------------
# Column map contract
# ---------------------------------------------------------------------------


class TestColumnMapContract:
    """Verify CompiledQuery.column_map is populated correctly."""

    @pytest.mark.asyncio
    async def test_alias_appears_in_column_map(
        self, compiler: SQLCompiler, employee_table,
    ) -> None:
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["sicil_no", "birim"],
        )
        result = compiler.compile(plan, employee_table)

        assert result.column_map["sicil_no"] == "SICIL_NO"
        assert result.column_map["birim"] == "BIRIM_ADI"

    @pytest.mark.asyncio
    async def test_canonical_names_in_column_map(
        self, compiler: SQLCompiler, employee_table,
    ) -> None:
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
        )
        result = compiler.compile(plan, employee_table)

        assert result.column_map["reg_no"] == "SICIL_NO"
        assert result.column_map["first_name"] == "AD"

    @pytest.mark.asyncio
    async def test_filter_column_in_column_map(
        self, compiler: SQLCompiler, employee_table,
    ) -> None:
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            filters=[FilterSpec(column="birim", op=FilterOp.EQ, value="IT")],
        )
        result = compiler.compile(plan, employee_table)

        assert result.column_map["birim"] == "BIRIM_ADI"
