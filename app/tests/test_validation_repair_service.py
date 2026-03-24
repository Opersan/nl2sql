from __future__ import annotations

import pytest

from app.domain.execution_models import ValidationResult
from app.domain.query_plan import AggregateFn, AggregationSpec, FilterOp, FilterSpec, OrderSpec, QueryPlan, SortDirection
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.services.catalog_service import CatalogService
from app.services.validation_repair_service import ValidationRepairService
from app.services.validation_service import ValidationService


@pytest.fixture
def repair_service() -> ValidationRepairService:
    return ValidationRepairService(CatalogService(InMemoryCatalogProvider()))


@pytest.fixture
def validator() -> ValidationService:
    provider = InMemoryCatalogProvider()
    catalog = CatalogService(provider)
    return ValidationService(catalog)


class TestValidationRepairService:
    @pytest.mark.asyncio
    async def test_invalid_order_by_mapped_to_selected_column(self, repair_service: ValidationRepairService, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="ordered list",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["first_name"],
            order_by=[OrderSpec(column="firstName", direction=SortDirection.ASC)],
        )
        initial = await validator.validate(plan)
        assert initial.ok is False

        repaired, repair_result, trace = await repair_service.repair(plan, initial)

        assert repair_result.repair_applied is True
        assert repaired.order_by[0].column == "AD"
        assert "invalid_sort_column_mapped" in trace["reasons"]

    @pytest.mark.asyncio
    async def test_known_synonym_filter_repaired(self, repair_service: ValidationRepairService, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="mail filter",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            filters=[FilterSpec(column="mail", op=FilterOp.IS_NOT_NULL)],
        )
        initial = ValidationResult(ok=False)
        resolved_table = await validator._catalog.resolve_table("XXBT_PDKS_PER_DETAILS_V")
        assert resolved_table is not None
        initial.resolved_table = resolved_table
        initial.add_error("invalid_column", "invalid filter", field="filters[0].column")

        repaired, repair_result, trace = await repair_service.repair(plan, initial)

        assert repair_result.repair_applied is True
        assert repaired.filters[0].column == "EMAIL"
        assert "known_synonym_repaired" in trace["reasons"]

    @pytest.mark.asyncio
    async def test_order_by_invalid_column_dropped_when_no_safe_mapping(self, repair_service: ValidationRepairService, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="drop bad order",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            order_by=[OrderSpec(column="not_a_real_column", direction=SortDirection.ASC)],
        )
        initial = await validator.validate(plan)
        repaired, repair_result, trace = await repair_service.repair(plan, initial)

        assert repair_result.repair_applied is True
        assert repaired.order_by == []
        assert "invalid_sort_column_dropped" in trace["reasons"]

    @pytest.mark.asyncio
    async def test_unsafe_unknown_select_not_repaired(self, repair_service: ValidationRepairService, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="unsafe",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["mystery_field"],
        )
        initial = await validator.validate(plan)
        repaired, repair_result, trace = await repair_service.repair(plan, initial)

        assert repair_result.repair_applied is False
        assert repaired == plan
        assert "repair_skipped_low_confidence" in trace["skipped_reason_codes"]

    @pytest.mark.asyncio
    async def test_non_invalid_column_error_not_repaired(self, repair_service: ValidationRepairService) -> None:
        result = ValidationResult(ok=False)
        result.add_error("restricted_column", "restricted", field="salary")
        plan = QueryPlan(intent="x", table="XXBT_PDKS_PER_DETAILS_V", select_columns=["salary"])

        repaired, repair_result, trace = await repair_service.repair(plan, result)

        assert repair_result.repair_applied is False
        assert repaired == plan
        assert "repair_skipped_low_confidence" in trace["skipped_reason_codes"]

    @pytest.mark.asyncio
    async def test_aggregate_order_by_alias_mapped_from_naming_mismatch(self, repair_service: ValidationRepairService, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="agg order",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["unit_name"],
            aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="employee_count")],
            group_by=["unit_name"],
            order_by=[OrderSpec(column="employeeCount", direction=SortDirection.DESC)],
        )
        initial = await validator.validate(plan)
        repaired, repair_result, trace = await repair_service.repair(plan, initial)

        assert repair_result.repair_applied is True
        assert repaired.order_by[0].column == "employee_count"
        assert "invalid_sort_column_mapped" in trace["reasons"]