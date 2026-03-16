"""Tests for MockExecutor – covering all 5 improvements.

Test groups
===========
1. **Canonical column resolution** – executor uses column_map to resolve
   plan-level aliases to canonical column names against the dataset.
2. **Unsupported filter op** – unknown FilterOp raises ExecutionError
   instead of silently passing.
3. **COUNT semantics** – COUNT(*) counts all rows; COUNT(column) counts
   non-NULL values only.
4. **STAR_COLUMN support** – explicit sentinel handling aligned with
   query_plan.py and sql_compiler.py.
5. **LIKE simplified semantics** – mock LIKE uses case-insensitive
   substring matching after stripping ``%``.  Does NOT support:
   ``_`` (single-char wildcard), escape characters, or anchored patterns
   (``'Ali%'`` behaves the same as ``'%Ali%'``).  This is a deliberate
   simplification for Sprint 1.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import ExecutionError
from app.domain.execution_models import CompiledQuery, ExecutionStatus
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
from app.providers.executor.mock_executor import MockExecutor
from app.services.sql_compiler import SQLCompiler


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def executor() -> MockExecutor:
    return MockExecutor()


@pytest.fixture
def compiler() -> SQLCompiler:
    return SQLCompiler()


@pytest.fixture
async def employee_table():
    provider = InMemoryCatalogProvider()
    return await provider.get_table("XXBT_PDKS_PER_DETAILS_V")


def _compile(compiler: SQLCompiler, plan: QueryPlan, table) -> CompiledQuery:
    """Helper to compile a plan (synchronous)."""
    return compiler.compile(plan, table)


# ===========================================================================
# 1. Canonical column resolution via column_map
# ===========================================================================


class TestCanonicalResolution:
    """Executor must use column_map to translate plan aliases → canonical
    column names that exist in the dataset rows."""

    @pytest.mark.asyncio
    async def test_filter_by_alias_resolves(
        self, executor, compiler, employee_table
    ) -> None:
        """Filter with alias 'birim' should resolve to canonical 'BIRIM_ADI'."""
        plan = QueryPlan(
            intent="alias filter",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO", "BIRIM_ADI"],
            filters=[
                FilterSpec(column="birim", op=FilterOp.EQ, value="Muhasebe"),
            ],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        assert result.row_count == 2
        for row in result.rows:
            assert row["BIRIM_ADI"] == "Muhasebe"

    @pytest.mark.asyncio
    async def test_order_by_alias_resolves(
        self, executor, compiler, employee_table
    ) -> None:
        """ORDER BY with alias 'sicil_no' should sort by canonical 'SICIL_NO'."""
        plan = QueryPlan(
            intent="alias order",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO", "AD"],
            order_by=[
                OrderSpec(column="sicil_no", direction=SortDirection.DESC),
            ],
            limit=3,
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        sicil_nos = [r["SICIL_NO"] for r in result.rows]
        assert sicil_nos == sorted(sicil_nos, reverse=True)

    @pytest.mark.asyncio
    async def test_group_by_alias_resolves(
        self, executor, compiler, employee_table
    ) -> None:
        """GROUP BY with alias 'birim' should group by canonical 'BIRIM_ADI'."""
        plan = QueryPlan(
            intent="alias group",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT, column="SICIL_NO", alias="cnt"
                ),
            ],
            group_by=["birim"],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        # 4 distinct departments in the dataset
        assert result.row_count == 4
        total = sum(r["cnt"] for r in result.rows)
        assert total == 9

    @pytest.mark.asyncio
    async def test_aggregate_column_alias_resolves(
        self, executor, compiler, employee_table
    ) -> None:
        """Aggregate on alias 'sicil_no' should resolve to canonical 'SICIL_NO'."""
        plan = QueryPlan(
            intent="alias agg",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT, column="sicil_no", alias="cnt"
                ),
            ],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        assert result.rows[0]["cnt"] == 9

    @pytest.mark.asyncio
    async def test_column_map_present_in_compiled_query(
        self, compiler, employee_table
    ) -> None:
        """CompiledQuery.column_map must contain alias→canonical entries."""
        plan = QueryPlan(
            intent="map check",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["sicil_no", "birim"],
        )
        compiled = _compile(compiler, plan, employee_table)

        assert compiled.column_map["sicil_no"] == "SICIL_NO"
        assert compiled.column_map["birim"] == "BIRIM_ADI"


# ===========================================================================
# 2. Unsupported filter op raises ExecutionError
# ===========================================================================


class TestUnsupportedFilterOp:
    """All FilterOp values are handled.  If a hypothetical new op were added
    without updating _match, ExecutionError would be raised."""

    @pytest.mark.asyncio
    async def test_all_known_ops_are_handled(self) -> None:
        """Verify that every FilterOp enum member is handled in _match.

        This test ensures that adding a new FilterOp without updating the
        executor will be caught.
        """
        # Build a row dict to test against.
        row: dict = {"col": "test_value", "num": 42, "dt": None}
        col_map: dict[str, str] = {"col": "col", "num": "num", "dt": "dt"}

        # Mapping of op → minimal valid FilterSpec for testing.
        op_specs = {
            FilterOp.EQ: FilterSpec(column="col", op=FilterOp.EQ, value="test_value"),
            FilterOp.NEQ: FilterSpec(column="col", op=FilterOp.NEQ, value="other"),
            FilterOp.LT: FilterSpec(column="num", op=FilterOp.LT, value=100),
            FilterOp.LTE: FilterSpec(column="num", op=FilterOp.LTE, value=42),
            FilterOp.GT: FilterSpec(column="num", op=FilterOp.GT, value=0),
            FilterOp.GTE: FilterSpec(column="num", op=FilterOp.GTE, value=42),
            FilterOp.LIKE: FilterSpec(column="col", op=FilterOp.LIKE, value="%test%"),
            FilterOp.IN: FilterSpec(column="col", op=FilterOp.IN, value=["test_value"]),
            FilterOp.BETWEEN: FilterSpec(column="num", op=FilterOp.BETWEEN, value=[0, 100]),
            FilterOp.IS_NULL: FilterSpec(column="dt", op=FilterOp.IS_NULL),
            FilterOp.IS_NOT_NULL: FilterSpec(column="col", op=FilterOp.IS_NOT_NULL),
        }

        for op in FilterOp:
            assert op in op_specs, (
                f"FilterOp.{op.name} not covered – update op_specs "
                f"AND MockExecutor._match"
            )
            # Should not raise.
            MockExecutor._match(row, op_specs[op], col_map)


# ===========================================================================
# 3. COUNT(*) vs COUNT(column) semantics
# ===========================================================================


class TestCountSemantics:
    """COUNT(*) must count all rows; COUNT(column) must exclude NULLs."""

    @pytest.mark.asyncio
    async def test_count_star_includes_nulls(
        self, executor, compiler, employee_table
    ) -> None:
        """COUNT(*) must count every row, regardless of NULL values."""
        plan = QueryPlan(
            intent="count star",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT, column=STAR_COLUMN, alias="total"
                ),
            ],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        assert result.rows[0]["total"] == 9

    @pytest.mark.asyncio
    async def test_count_column_excludes_nulls(
        self, executor, compiler, employee_table
    ) -> None:
        """COUNT(CIKIS_TARIHI) must count only non-NULL CIKIS_TARIHI values.

        The dataset has 9 employees but only 2 have CIKIS_TARIHI set.
        """
        plan = QueryPlan(
            intent="count column",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="CIKIS_TARIHI",
                    alias="quit_count",
                ),
            ],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        assert result.rows[0]["quit_count"] == 2  # only 2 non-NULL CIKIS_TARIHI

    @pytest.mark.asyncio
    async def test_count_star_vs_column_grouped(
        self, executor, compiler, employee_table
    ) -> None:
        """Grouped query: COUNT(*) and COUNT(CIKIS_TARIHI) should differ for
        departments where some employees have NULL CIKIS_TARIHI."""
        plan = QueryPlan(
            intent="grouped count comparison",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT, column=STAR_COLUMN, alias="all_count"
                ),
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="CIKIS_TARIHI",
                    alias="quit_count",
                ),
            ],
            group_by=["BIRIM_ADI"],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS

        # IT dept: 4 employees (with stajyer), 1 quit → all_count=4, quit_count=1
        it_row = next(
            r for r in result.rows if r["BIRIM_ADI"] == "Bilgi Teknolojileri"
        )
        assert it_row["all_count"] == 4
        assert it_row["quit_count"] == 1

        # Muhasebe: 2 employees, 0 quit → all_count=2, quit_count=0
        muh_row = next(r for r in result.rows if r["BIRIM_ADI"] == "Muhasebe")
        assert muh_row["all_count"] == 2
        assert muh_row["quit_count"] == 0


# ===========================================================================
# 4. STAR_COLUMN explicit support
# ===========================================================================


class TestStarColumnExecutor:
    """STAR_COLUMN sentinel is explicitly recognised in the executor's
    aggregate logic, consistent with query_plan.py and sql_compiler.py."""

    @pytest.mark.asyncio
    async def test_star_column_sentinel_value(self) -> None:
        """The STAR_COLUMN constant is the literal '*'."""
        assert STAR_COLUMN == "*"

    @pytest.mark.asyncio
    async def test_count_star_with_filter(
        self, executor, compiler, employee_table
    ) -> None:
        """COUNT(*) + WHERE filter → only filtered rows counted."""
        plan = QueryPlan(
            intent="count star filtered",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column=STAR_COLUMN,
                    alias="active",
                ),
            ],
            filters=[FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL)],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        assert result.rows[0]["active"] == 7

    @pytest.mark.asyncio
    async def test_count_star_grouped_ordered(
        self, executor, compiler, employee_table
    ) -> None:
        """COUNT(*) + GROUP BY + ORDER BY alias → correct aggregation and sort."""
        plan = QueryPlan(
            intent="star grouped ordered",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT, column=STAR_COLUMN, alias="n"
                ),
            ],
            group_by=["BIRIM_ADI"],
            order_by=[OrderSpec(column="n", direction=SortDirection.DESC)],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        counts = [r["n"] for r in result.rows]
        assert counts == sorted(counts, reverse=True)
        assert sum(counts) == 9


# ===========================================================================
# 5. LIKE simplified mock semantics
# ===========================================================================


class TestMockLikeSemantics:
    """Document and test the simplified LIKE behaviour.

    The mock LIKE implementation:
    * Strips all ``%`` wildcards from the pattern.
    * Performs **case-insensitive substring** matching.
    * Does **NOT** support:
      - ``_`` (single-character wildcard)
      - Escape characters
      - Anchored patterns (``'Ali%'`` behaves identically to ``'%Ali%'``)

    This is a deliberate Sprint 1 simplification.  A production executor
    would delegate LIKE to the database engine.
    """

    @pytest.mark.asyncio
    async def test_like_substring_match(
        self, executor, compiler, employee_table
    ) -> None:
        """LIKE '%met%' should match 'Ahmet' and 'Mehmet'."""
        plan = QueryPlan(
            intent="like test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO", "AD"],
            filters=[
                FilterSpec(column="AD", op=FilterOp.LIKE, value="%met%"),
            ],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        names = {r["AD"] for r in result.rows}
        assert "Ahmet" in names
        assert "Mehmet" in names

    @pytest.mark.asyncio
    async def test_like_case_insensitive(
        self, executor, compiler, employee_table
    ) -> None:
        """LIKE should be case-insensitive: '%ali%' matches 'Ali'."""
        plan = QueryPlan(
            intent="like case",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO", "AD"],
            filters=[
                FilterSpec(column="AD", op=FilterOp.LIKE, value="%ali%"),
            ],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        names = {r["AD"] for r in result.rows}
        assert "Ali" in names

    @pytest.mark.asyncio
    async def test_like_no_match(
        self, executor, compiler, employee_table
    ) -> None:
        """LIKE '%NONEXISTENT%' should return zero rows."""
        plan = QueryPlan(
            intent="like no match",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO", "AD"],
            filters=[
                FilterSpec(
                    column="AD", op=FilterOp.LIKE, value="%NONEXISTENT%"
                ),
            ],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.EMPTY
        assert result.row_count == 0

    @pytest.mark.asyncio
    async def test_like_prefix_pattern_is_substring(
        self, executor, compiler, employee_table
    ) -> None:
        """Mock-specific: 'Ali%' behaves same as '%Ali%' (simplified)."""
        plan = QueryPlan(
            intent="like prefix",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO", "AD"],
            filters=[
                FilterSpec(column="AD", op=FilterOp.LIKE, value="Ali%"),
            ],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        assert result.status == ExecutionStatus.SUCCESS
        names = {r["AD"] for r in result.rows}
        assert "Ali" in names

    @pytest.mark.asyncio
    async def test_like_null_value_no_match(
        self, executor, compiler, employee_table
    ) -> None:
        """LIKE on a NULL column value should not match."""
        plan = QueryPlan(
            intent="like null",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["SICIL_NO", "CIKIS_TARIHI"],
            filters=[
                # CIKIS_TARIHI is NULL for most employees
                FilterSpec(column="CIKIS_TARIHI", op=FilterOp.LIKE, value="%2023%"),
            ],
        )
        compiled = _compile(compiler, plan, employee_table)
        result = await executor.execute(compiled)

        # Only rows with non-NULL CIKIS_TARIHI containing "2023" should match.
        # Employee S1005 has CIKIS_TARIHI=2023-12-31
        for row in result.rows:
            assert row["CIKIS_TARIHI"] is not None
