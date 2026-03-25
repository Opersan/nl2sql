"""End-to-end smoke tests using mock catalog + mock executor."""

from __future__ import annotations

import pytest

from app.domain.execution_models import ErrorPhase, ExecutionStatus
from app.domain.query_plan import (
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
from app.services.catalog_service import CatalogService
from app.services.orchestrator import Orchestrator
from app.services.sql_compiler import SQLCompiler
from app.services.validation_service import ValidationService


@pytest.fixture
def orchestrator() -> Orchestrator:
    provider = InMemoryCatalogProvider()
    catalog = CatalogService(provider)
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = MockExecutor()
    return Orchestrator(validator, compiler, executor)


# ---------------------------------------------------------------------------
# Happy path – simple select
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_simple_select(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="Bilgi Teknolojileri birimindeki çalışanları listele",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name", "last_name", "unit_name"],
            filters=[
                FilterSpec(
                    column="unit_name",
                    op=FilterOp.EQ,
                    value="Bilgi Teknolojileri",
                ),
            ],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.compiled_query is not None
        assert "FROM XXBT_PDKS_PER_DETAILS_V" in result.compiled_query.sql
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        assert result.execution_result.row_count > 0

        # All returned rows should be from the IT department
        for row in result.execution_result.rows:
            assert row["BIRIM_ADI"] == "Bilgi Teknolojileri"

    @pytest.mark.asyncio
    async def test_active_employees_is_null(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="Aktif çalışanları listele",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name", "last_name"],
            filters=[FilterSpec(column="quit_date", op=FilterOp.IS_NULL)],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        # Our dataset has 7 active employees (CIKIS_TARIHI is None)
        assert result.execution_result.row_count == 7

    @pytest.mark.asyncio
    async def test_in_filter_smoke(self, orchestrator: Orchestrator) -> None:
        """IN filter should return only rows matching the given list."""
        plan = QueryPlan(
            intent="Belirli birimlerde çalışanlar",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "unit_name"],
            filters=[
                FilterSpec(
                    column="unit_name",
                    op=FilterOp.IN,
                    value=["Muhasebe", "Hukuk"],
                ),
            ],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        for row in result.execution_result.rows:
            assert row["BIRIM_ADI"] in ("Muhasebe", "Hukuk")

    @pytest.mark.asyncio
    async def test_between_filter_smoke(self, orchestrator: Orchestrator) -> None:
        """BETWEEN filter should constrain rows to the given range."""
        from datetime import date

        plan = QueryPlan(
            intent="2019-2021 arası işe başlayanlar",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name", "start_date"],
            filters=[
                FilterSpec(
                    column="start_date",
                    op=FilterOp.BETWEEN,
                    value=[date(2019, 1, 1), date(2021, 12, 31)],
                ),
            ],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        for row in result.execution_result.rows:
            assert date(2019, 1, 1) <= row["ISE_GIRIS_TARIHI"] <= date(2021, 12, 31)

    @pytest.mark.asyncio
    async def test_multiple_filters_combined(self, orchestrator: Orchestrator) -> None:
        """Multiple filters should AND together correctly."""
        plan = QueryPlan(
            intent="Aktif IT çalışanları",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name", "unit_name"],
            filters=[
                FilterSpec(column="unit_name", op=FilterOp.EQ, value="Bilgi Teknolojileri"),
                FilterSpec(column="quit_date", op=FilterOp.IS_NULL),
            ],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        # BT has 4 employees, 1 quit → 3 active
        assert result.execution_result.row_count == 3
        for row in result.execution_result.rows:
            assert row["BIRIM_ADI"] == "Bilgi Teknolojileri"


# ---------------------------------------------------------------------------
# Empty result
# ---------------------------------------------------------------------------


class TestEmptyResult:
    @pytest.mark.asyncio
    async def test_no_matching_rows(self, orchestrator: Orchestrator) -> None:
        """Query that matches zero rows should return EMPTY status."""
        plan = QueryPlan(
            intent="Ghost department",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            filters=[
                FilterSpec(column="unit_name", op=FilterOp.EQ, value="Nonexistent Dept"),
            ],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.EMPTY
        assert result.execution_result.row_count == 0
        assert result.execution_result.rows == []


# ---------------------------------------------------------------------------
# Validation failure
# ---------------------------------------------------------------------------


class TestValidationFailure:
    @pytest.mark.asyncio
    async def test_invalid_table_stops_pipeline(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="bad table",
            table="nonexistent_table",
            select_columns=["id"],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is False
        assert result.failed_phase == ErrorPhase.VALIDATION
        assert result.ok is False
        assert result.compiled_query is None
        assert result.execution_result is None
        assert result.compilation_error is None

    @pytest.mark.asyncio
    async def test_restricted_column_stops_pipeline(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="salary query",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "salary"],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is False
        assert result.compiled_query is None

    @pytest.mark.asyncio
    async def test_select_star_stops_pipeline(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="select star",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["*"],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is False
        assert result.compiled_query is None

    @pytest.mark.asyncio
    async def test_invalid_order_by_can_be_dropped_and_revalidated(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="Çalışanları getir",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            order_by=[OrderSpec(column="nonexistent_alias", direction=SortDirection.ASC)],
        )

        result = await orchestrator.run_plan(plan)
        trace = orchestrator.last_trace or {}

        assert result.validation.ok is True
        assert result.failed_phase is None
        assert result.compiled_query is not None
        assert "ORDER BY" not in result.compiled_query.sql
        assert trace.get("validation_repair", {}).get("revalidated") is True
        assert "invalid_sort_column_dropped" in trace.get("validation_repair", {}).get("reason_codes", [])

    @pytest.mark.asyncio
    async def test_invalid_filter_naming_mismatch_repaired_and_executes(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="Adı bilinen çalışanları getir",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            filters=[FilterSpec(column="firstName", op=FilterOp.IS_NOT_NULL)],
        )

        result = await orchestrator.run_plan(plan)
        trace = orchestrator.last_trace or {}

        assert result.validation.ok is True
        assert result.failed_phase is None
        assert result.compiled_query is not None
        assert "AD" in result.compiled_query.sql
        assert "alias_to_canonical" in trace.get("validation_repair", {}).get("reason_codes", [])

    @pytest.mark.asyncio
    async def test_unknown_select_column_not_unsafely_repaired(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="bad select",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["totally_unknown_column"],
        )

        result = await orchestrator.run_plan(plan)
        trace = orchestrator.last_trace or {}

        assert result.validation.ok is False
        assert result.failed_phase == ErrorPhase.VALIDATION
        assert trace.get("validation_repair", {}).get("repaired") is False

    @pytest.mark.asyncio
    async def test_partial_repair_that_still_fails_revalidation_is_reported(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="mixed repairability",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["firstName", "totally_unknown_column"],
        )

        result = await orchestrator.run_plan(plan)
        trace = orchestrator.last_trace or {}

        assert result.validation.ok is False
        assert result.failed_phase == ErrorPhase.VALIDATION
        assert trace.get("validation_repair", {}).get("repaired") is True
        assert trace.get("validation_repair", {}).get("revalidate_ok") is False
        assert "revalidate_failed_after_repair" in trace.get("validation_repair", {}).get("reason_codes", [])

    @pytest.mark.asyncio
    async def test_already_valid_plan_does_not_enter_validation_repair(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="already valid",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
        )

        result = await orchestrator.run_plan(plan)
        trace = orchestrator.last_trace or {}

        assert result.validation.ok is True
        assert trace.get("validation_repair") is None



# ---------------------------------------------------------------------------
# Aggregate smoke test
# ---------------------------------------------------------------------------


class TestAggregateSmokeTest:
    @pytest.mark.asyncio
    async def test_count_per_unit(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="Birim bazında çalışan sayısı",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="reg_no",
                    alias="employee_count",
                ),
            ],
            group_by=["unit_name"],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.compiled_query is not None
        assert "COUNT(SICIL_NO) AS employee_count" in result.compiled_query.sql
        assert "GROUP BY BIRIM_ADI" in result.compiled_query.sql
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        assert result.execution_result.row_count > 0

        # Verify aggregate results make sense
        total = sum(r["employee_count"] for r in result.execution_result.rows)
        assert total == 9  # total rows in mock dataset

    @pytest.mark.asyncio
    async def test_count_without_group_by(self, orchestrator: Orchestrator) -> None:
        """Aggregate without GROUP BY should produce a single-row result."""
        plan = QueryPlan(
            intent="Toplam çalışan sayısı",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="reg_no",
                    alias="total",
                ),
            ],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        assert result.execution_result.row_count == 1
        assert result.execution_result.rows[0]["total"] == 9

    @pytest.mark.asyncio
    async def test_count_star_total(self, orchestrator: Orchestrator) -> None:
        """COUNT(*) should count all rows via the full pipeline."""
        plan = QueryPlan(
            intent="Toplam çalışan sayısı (COUNT *)",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="*",
                    alias="total",
                ),
            ],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.compiled_query is not None
        assert "COUNT(*) AS total" in result.compiled_query.sql
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        assert result.execution_result.row_count == 1
        assert result.execution_result.rows[0]["total"] == 9


# ---------------------------------------------------------------------------
# Order by smoke test
# ---------------------------------------------------------------------------


class TestOrderBySmokeTest:
    @pytest.mark.asyncio
    async def test_ordered_by_reg_no_desc(self, orchestrator: Orchestrator) -> None:
        plan = QueryPlan(
            intent="Son sicil numaralarına göre sırala",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            order_by=[OrderSpec(column="reg_no", direction=SortDirection.DESC)],
            limit=3,
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        assert result.execution_result.row_count == 3

        reg_nos = [r["SICIL_NO"] for r in result.execution_result.rows]
        assert reg_nos == sorted(reg_nos, reverse=True)


# ---------------------------------------------------------------------------
# Execution time tracking
# ---------------------------------------------------------------------------


class TestExecutionTimestamp:
    @pytest.mark.asyncio
    async def test_execution_time_populated(self, orchestrator: Orchestrator) -> None:
        """execution_time_ms should be non-negative."""
        plan = QueryPlan(
            intent="timing test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
        )

        result = await orchestrator.run_plan(plan)

        assert result.execution_result is not None
        assert result.execution_result.execution_time_ms is not None
        assert result.execution_result.execution_time_ms >= 0


# ---------------------------------------------------------------------------
# Validation ↔ Compiler ↔ Executor: aggregate consistency
# ---------------------------------------------------------------------------


class TestAggregateConsistencyE2E:
    """End-to-end tests proving that validation, compiler and executor agree
    on aggregate + group_by + select_columns + order_by semantics."""

    @pytest.mark.asyncio
    async def test_count_star_grouped_ordered_e2e(
        self, orchestrator: Orchestrator
    ) -> None:
        """COUNT(*) + GROUP BY + ORDER BY alias → full pipeline success."""
        plan = QueryPlan(
            intent="Birim bazında sayı (COUNT*), azalan sıra",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT, column="*", alias="cnt"
                ),
            ],
            group_by=["unit_name"],
            order_by=[OrderSpec(column="cnt", direction=SortDirection.DESC)],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.compiled_query is not None
        assert "COUNT(*) AS cnt" in result.compiled_query.sql
        assert "GROUP BY BIRIM_ADI" in result.compiled_query.sql
        assert "ORDER BY cnt DESC" in result.compiled_query.sql
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        assert result.execution_result.row_count > 0

        # Rows sorted descending by cnt
        counts = [r["cnt"] for r in result.execution_result.rows]
        assert counts == sorted(counts, reverse=True)

    @pytest.mark.asyncio
    async def test_order_by_aggregate_alias_e2e(
        self, orchestrator: Orchestrator
    ) -> None:
        """ORDER BY user-defined aggregate alias through the full pipeline."""
        plan = QueryPlan(
            intent="Birim bazında çalışan sayısı sıralı",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="reg_no",
                    alias="employee_count",
                ),
            ],
            group_by=["unit_name"],
            order_by=[
                OrderSpec(column="employee_count", direction=SortDirection.ASC),
            ],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.compiled_query is not None
        assert "ORDER BY employee_count ASC" in result.compiled_query.sql
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS

        # Ascending order
        counts = [r["employee_count"] for r in result.execution_result.rows]
        assert counts == sorted(counts)

    @pytest.mark.asyncio
    async def test_agg_select_no_group_rejected_e2e(
        self, orchestrator: Orchestrator
    ) -> None:
        """select_columns + aggregation without group_by → validation error,
        pipeline stops before compilation."""
        plan = QueryPlan(
            intent="invalid combo",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["unit_name"],
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT, column="reg_no", alias="cnt"
                ),
            ],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is False
        assert any(
            e.code == "aggregate_select_mismatch" for e in result.validation.errors
        )
        assert result.compiled_query is None
        assert result.execution_result is None

    @pytest.mark.asyncio
    async def test_count_star_filtered_e2e(
        self, orchestrator: Orchestrator
    ) -> None:
        """COUNT(*) + WHERE filter → correct filtered count."""
        plan = QueryPlan(
            intent="Aktif çalışan sayısı",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT, column="*", alias="active_count"
                ),
            ],
            filters=[FilterSpec(column="quit_date", op=FilterOp.IS_NULL)],
        )

        result = await orchestrator.run_plan(plan)

        assert result.validation.ok is True
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS
        assert result.execution_result.rows[0]["active_count"] == 7


# ---------------------------------------------------------------------------
# Error-phase separation
# ---------------------------------------------------------------------------


class TestErrorPhaseSeparation:
    """Verify that failed_phase and error details are properly separated
    across validation, compilation, and execution phases."""

    @pytest.mark.asyncio
    async def test_validation_phase_sets_failed_phase(
        self, orchestrator: Orchestrator
    ) -> None:
        """Validation failure → failed_phase == VALIDATION, no compilation_error."""
        plan = QueryPlan(
            intent="bad table",
            table="nonexistent_table",
            select_columns=["id"],
        )
        result = await orchestrator.run_plan(plan)

        assert result.failed_phase == ErrorPhase.VALIDATION
        assert result.ok is False
        assert result.compilation_error is None
        assert result.compiled_query is None
        assert result.execution_result is None

    @pytest.mark.asyncio
    async def test_success_has_no_failed_phase(
        self, orchestrator: Orchestrator
    ) -> None:
        """Successful pipeline → failed_phase is None, ok is True."""
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
        )
        result = await orchestrator.run_plan(plan)

        assert result.failed_phase is None
        assert result.ok is True
        assert result.compilation_error is None
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_resolved_table_carried_on_success(
        self, orchestrator: Orchestrator
    ) -> None:
        """On success, validation.resolved_table must carry the table metadata."""
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
        )
        result = await orchestrator.run_plan(plan)

        assert result.validation.resolved_table is not None
        assert result.validation.resolved_table.name == "XXBT_PDKS_PER_DETAILS_V"

    @pytest.mark.asyncio
    async def test_resolved_table_none_on_invalid_table(
        self, orchestrator: Orchestrator
    ) -> None:
        """On validation failure (bad table), resolved_table must be None."""
        plan = QueryPlan(
            intent="test",
            table="nonexistent",
            select_columns=["id"],
        )
        result = await orchestrator.run_plan(plan)

        assert result.validation.resolved_table is None
        assert result.failed_phase == ErrorPhase.VALIDATION

    @pytest.mark.asyncio
    async def test_empty_result_is_not_a_failure(
        self, orchestrator: Orchestrator
    ) -> None:
        """EMPTY execution result → ok is True, failed_phase is None."""
        plan = QueryPlan(
            intent="ghost",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            filters=[
                FilterSpec(
                    column="unit_name", op=FilterOp.EQ, value="NoSuchDept"
                ),
            ],
        )
        result = await orchestrator.run_plan(plan)

        assert result.ok is True
        assert result.failed_phase is None
        assert result.execution_result is not None
        assert result.execution_result.status == ExecutionStatus.EMPTY

    @pytest.mark.asyncio
    async def test_validation_errors_not_polluted_by_compilation(
        self, orchestrator: Orchestrator
    ) -> None:
        """Compilation errors must NOT be added to validation.errors."""
        # A valid plan that compiles and executes successfully.
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
        )
        result = await orchestrator.run_plan(plan)

        # No compilation_error code should appear in validation.errors.
        assert result.compilation_error is None
        codes = {e.code for e in result.validation.errors}
        assert "compilation_error" not in codes
