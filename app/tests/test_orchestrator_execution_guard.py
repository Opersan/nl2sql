from __future__ import annotations

import pytest

from app.domain.execution_models import ErrorPhase
from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.providers.executor.mock_executor import MockExecutor
from app.services.catalog_service import CatalogService
from app.services.orchestrator import Orchestrator
from app.services.sql_compiler import SQLCompiler
from app.services.validation_service import ValidationService


@pytest.mark.asyncio
async def test_orchestrator_blocks_pre_execution_date_risk() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = MockExecutor()
    orchestrator = Orchestrator(validator, compiler, executor)

    plan = QueryPlan(
        intent="Son siparişler",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "ISE_GIRIS_TARIHI"],
        filters=[FilterSpec(column="ISE_GIRIS_TARIHI", op=FilterOp.GTE, value="01/01/2024")],
    )

    result = await orchestrator.run_plan(plan)
    trace = orchestrator.last_trace or {}

    assert result.failed_phase == ErrorPhase.EXECUTION
    assert result.execution_result is not None
    assert "precheck_date_literal_invalid" in (result.execution_result.error_message or "")
    assert trace.get("pre_execution", {}).get("should_execute") is False
    assert trace.get("execute", {}).get("execution_skipped_reason") == "precheck_date_literal_invalid"
