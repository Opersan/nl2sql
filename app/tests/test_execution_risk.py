from __future__ import annotations

from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.services.catalog_service import CatalogService
from app.services.execution_risk import assess_pre_execution_risk
from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan

import pytest


@pytest.mark.asyncio
async def test_pre_execution_risk_blocks_invalid_date_literal() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("XXBT_PDKS_PER_DETAILS_V")
    assert table is not None

    plan = QueryPlan(
        intent="Tarih filtresi",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO"],
        filters=[FilterSpec(column="ISE_GIRIS_TARIHI", op=FilterOp.GTE, value="2024/01/01")],
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk["should_execute"] is False
    assert "oracle_date_type_error" in risk["pre_execution_risk_flags"]


@pytest.mark.asyncio
async def test_pre_execution_risk_marks_unbounded_listing_as_non_blocking() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("XXBT_PDKS_PER_DETAILS_V")
    assert table is not None

    plan = QueryPlan(
        intent="Tüm çalışanları getir",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "AD"],
        filters=[],
        limit=100,
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk["should_execute"] is True
    assert "high_risk_but_executable" in risk["pre_execution_risk_flags"]
