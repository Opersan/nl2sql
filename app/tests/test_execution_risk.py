from __future__ import annotations

from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.services.catalog_service import CatalogService
from app.services.execution_risk import assess_pre_execution_risk
from app.domain.query_plan import FilterOp, FilterSpec, JoinCondition, JoinSpec, JoinType, OrderSpec, QueryPlan, SortDirection

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
        filters=[FilterSpec(column="ISE_GIRIS_TARIHI", op=FilterOp.GTE, value="2024-13-01")],
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk.should_execute is False
    assert "oracle_date_type_error" in risk.pre_execution_risk_flags


@pytest.mark.asyncio
async def test_pre_execution_risk_allows_relative_date_sentinel() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("XXBT_PDKS_PER_DETAILS_V")
    assert table is not None

    plan = QueryPlan(
        intent="Son 30 gunde girenler",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO"],
        filters=[FilterSpec(column="ISE_GIRIS_TARIHI", op=FilterOp.GTE, value="__RELATIVE_DATE_LAST_30_DAYS__")],
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk.should_execute is True
    assert "oracle_date_type_error" not in risk.pre_execution_risk_flags


@pytest.mark.asyncio
async def test_pre_execution_risk_allows_date_is_null_filter() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("XXBT_PDKS_PER_DETAILS_V")
    assert table is not None

    plan = QueryPlan(
        intent="Aktif calisanlar",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO"],
        filters=[FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL)],
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk.should_execute is True
    assert "oracle_date_type_error" not in risk.pre_execution_risk_flags


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
    assert risk.should_execute is True
    assert "high_risk_but_executable" in risk.pre_execution_risk_flags


@pytest.mark.asyncio
async def test_pre_execution_risk_blocks_timeout_prone_wide_joined_listing() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("PO_HEADERS_ALL")
    assert table is not None

    plan = QueryPlan(
        intent="Genis siparis listesi",
        table="PO_HEADERS_ALL",
        select_columns=["segment1", "authorization_status", "line_num", "item_description"],
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
        limit=100,
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk.should_execute is False
    assert "timeout_prone_wide_listing" in risk.pre_execution_risk_flags
    assert risk.execution_skipped_reason == "precheck_timeout_prone_shape"
