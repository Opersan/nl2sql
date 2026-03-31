from __future__ import annotations

import pytest

from app.domain.execution_models import ErrorPhase
from app.domain.execution_models import ExecutionResult, ExecutionStatus
from app.domain.query_plan import FilterOp, FilterSpec, JoinCondition, JoinSpec, JoinType, OrderSpec, QueryPlan, SortDirection
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.providers.executor.mock_executor import MockExecutor
from app.services.catalog_service import CatalogService
from app.services.orchestrator import Orchestrator
from app.services.sql_compiler import SQLCompiler
from app.services.validation_service import ValidationService


class CountingExecutor(MockExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.execute_calls = 0
        self.last_compiled_query = None

    async def execute(self, compiled_query):  # type: ignore[override]
        self.execute_calls += 1
        self.last_compiled_query = compiled_query
        return ExecutionResult(status=ExecutionStatus.SUCCESS)


@pytest.mark.asyncio
async def test_orchestrator_blocks_pre_execution_date_risk() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = CountingExecutor()
    orchestrator = Orchestrator(validator, compiler, executor)

    plan = QueryPlan(
        intent="Son siparişler",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "ISE_GIRIS_TARIHI"],
        filters=[FilterSpec(column="ISE_GIRIS_TARIHI", op=FilterOp.GTE, value="2024-13-01")],
    )

    result = await orchestrator.run_plan(plan)
    trace = orchestrator.last_trace or {}

    assert result.failed_phase == ErrorPhase.EXECUTION
    assert result.compiled_query is not None
    assert result.execution_result is not None
    assert "precheck_date_literal_invalid" in (result.execution_result.error_message or "")
    assert executor.execute_calls == 0
    assert trace.get("compile", {}).get("ok") is True
    assert trace.get("pre_execution", {}).get("should_execute") is False
    assert trace.get("pre_execution", {}).get("executed_sql_fingerprint")
    assert trace.get("pre_execution", {}).get("bind_summary", {}).get("bind_count") == 2
    assert trace.get("pre_execution", {}).get("should_execute") is False
    assert trace.get("execute", {}).get("execution_skipped_reason") == "precheck_date_literal_invalid"
    assert trace.get("execute", {}).get("why_not_executed") == "precheck_date_literal_invalid"
    assert trace.get("execute", {}).get("status") == "skipped"


@pytest.mark.asyncio
async def test_orchestrator_blocks_timeout_prone_wide_joined_listing() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = CountingExecutor()
    orchestrator = Orchestrator(validator, compiler, executor)

    plan = QueryPlan(
        intent="Genis siparis listesi",
        table="PO_HEADERS_ALL",
        select_columns=["vendor_id", "authorization_status", "line_num", "item_description"],
        joins=[
            JoinSpec(
                left_table="PO_HEADERS_ALL",
                right_table="PO_LINES_ALL",
                join_type=JoinType.INNER,
                on=[
                    JoinCondition(
                        left_table="PO_HEADERS_ALL",
                        left_column="PO_HEADER_ID",
                        right_table="PO_LINES_ALL",
                        right_column="PO_HEADER_ID",
                    )
                ],
            )
        ],
        order_by=[OrderSpec(column="creation_date", table="PO_HEADERS_ALL", direction=SortDirection.DESC)],
        limit=10000,
    )

    result = await orchestrator.run_plan(plan)
    trace = orchestrator.last_trace or {}

    assert result.failed_phase == ErrorPhase.EXECUTION
    assert result.compiled_query is not None
    assert result.execution_result is not None
    assert "precheck_timeout_prone_large_join" in (result.execution_result.error_message or "")
    assert executor.execute_calls == 0
    assert trace.get("pre_execution", {}).get("should_execute") is False
    assert trace.get("pre_execution", {}).get("execution_guard_reason") == "execution_blocked_high_risk"
    assert trace.get("execute", {}).get("execution_skipped_reason") == "precheck_timeout_prone_large_join"


@pytest.mark.asyncio
async def test_orchestrator_sets_execution_subtype_for_date_precheck_block() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = CountingExecutor()
    orchestrator = Orchestrator(validator, compiler, executor)

    plan = QueryPlan(
        intent="Son siparisler",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "ISE_GIRIS_TARIHI"],
        filters=[FilterSpec(column="ISE_GIRIS_TARIHI", op=FilterOp.GTE, value="2024-13-01")],
    )

    result = await orchestrator.run_plan(plan)
    trace = orchestrator.last_trace or {}

    assert result.execution_result is not None
    assert result.execution_result.execution_error_subtype == "oracle_date_type_error"
    assert trace.get("execute", {}).get("execution_error_subtype") == "oracle_date_type_error"


@pytest.mark.asyncio
async def test_orchestrator_safe_modes_valid_simple_listing_with_lower_limit() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = CountingExecutor()
    orchestrator = Orchestrator(validator, compiler, executor)

    plan = QueryPlan(
        intent="Aktif calisanlari listele",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "AD", "SOYAD", "EMAIL", "BIRIM_ADI"],
        filters=[FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL)],
        limit=100,
    )

    result = await orchestrator.run_plan(plan)
    trace = orchestrator.last_trace or {}

    assert result.failed_phase is None
    assert result.execution_result is not None
    assert result.execution_result.status == ExecutionStatus.SUCCESS
    assert executor.execute_calls == 1
    assert executor.last_compiled_query is not None
    assert executor.last_compiled_query.debug_plan is not None
    assert executor.last_compiled_query.debug_plan.limit == 25
    assert result.compiled_query is not None
    assert result.compiled_query.debug_plan is not None
    assert result.compiled_query.debug_plan.limit == 25
    assert trace.get("pre_execution", {}).get("execution_guard_reason") == "execution_safe_mode_applied"
    assert trace.get("pre_execution", {}).get("safe_mode_applied") is True
    assert trace.get("pre_execution", {}).get("original_limit") == 10000
    assert trace.get("pre_execution", {}).get("effective_limit") == 25
    assert trace.get("execute", {}).get("execution_skipped_reason") is None
    assert trace.get("execute", {}).get("safe_mode_applied") is True


@pytest.mark.asyncio
async def test_orchestrator_blocks_unfiltered_simple_listing_as_high_risk() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = CountingExecutor()
    orchestrator = Orchestrator(validator, compiler, executor)

    plan = QueryPlan(
        intent="Calisanlari listele",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "AD", "SOYAD", "EMAIL", "BIRIM_ADI"],
        filters=[],
        limit=10000,
    )

    result = await orchestrator.run_plan(plan)
    trace = orchestrator.last_trace or {}

    assert result.failed_phase == ErrorPhase.EXECUTION
    assert result.execution_result is not None
    assert result.execution_result.error_message == "precheck_timeout_prone_simple_listing"
    assert result.execution_result.execution_error_subtype == "timeout"
    assert executor.execute_calls == 0
    assert trace.get("pre_execution", {}).get("execution_guard_reason") == "execution_blocked_high_risk"
    assert trace.get("execute", {}).get("execution_skipped_reason") == "precheck_timeout_prone_simple_listing"
    assert trace.get("execute", {}).get("safe_mode_applied") is False
