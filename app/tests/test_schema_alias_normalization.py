from __future__ import annotations

import pytest

from app.domain.query_plan import AggregateFn, AggregationSpec, FilterOp, FilterSpec, QueryPlan
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.services.catalog_service import CatalogService
from app.services.validation_service import ValidationService


@pytest.fixture
def validator() -> ValidationService:
    return ValidationService(CatalogService(InMemoryCatalogProvider()))


def test_alias_normalization_map_values() -> None:
    assert ValidationService._normalize_column_identifier("MAIL") == "email"  # noqa: SLF001
    assert ValidationService._normalize_column_identifier("giris_tarihi") == "hire_date"  # noqa: SLF001
    assert ValidationService._normalize_column_identifier("cikis_tarihi") == "termination_date"  # noqa: SLF001


@pytest.mark.asyncio
async def test_select_alias_mail_resolves(validator: ValidationService) -> None:
    plan = QueryPlan(intent="x", table="XXBT_PDKS_PER_DETAILS_V", select_columns=["MAIL"])
    result = await validator.validate(plan)
    assert result.ok is True


@pytest.mark.asyncio
async def test_filter_alias_mail_resolves(validator: ValidationService) -> None:
    plan = QueryPlan(
        intent="x",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO"],
        filters=[FilterSpec(column="MAIL", op=FilterOp.IS_NOT_NULL)],
    )
    result = await validator.validate(plan)
    assert result.ok is True


@pytest.mark.asyncio
async def test_aggregate_alias_mail_resolves(validator: ValidationService) -> None:
    plan = QueryPlan(
        intent="x",
        table="XXBT_PDKS_PER_DETAILS_V",
        aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="MAIL")],
    )
    result = await validator.validate(plan)
    assert result.ok is True
