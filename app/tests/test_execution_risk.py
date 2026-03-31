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
async def test_pre_execution_risk_allows_natural_relative_date_expression() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("PO_HEADERS_ALL")
    assert table is not None

    plan = QueryPlan(
        intent="Son 30 gunde olusturulan siparisler",
        table="PO_HEADERS_ALL",
        select_columns=["PO_HEADER_ID", "CREATION_DATE"],
        filters=[FilterSpec(column="CREATION_DATE", op=FilterOp.GTE, value="NOW - 30 DAYS")],
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk.should_execute is True
    assert "oracle_date_type_error" not in risk.pre_execution_risk_flags


@pytest.mark.asyncio
async def test_pre_execution_risk_allows_week_boundary_tokens() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("PO_HEADERS_ALL")
    assert table is not None

    plan = QueryPlan(
        intent="Bu hafta olusturulan siparisler",
        table="PO_HEADERS_ALL",
        select_columns=["PO_HEADER_ID", "CREATION_DATE"],
        filters=[
            FilterSpec(column="CREATION_DATE", op=FilterOp.GTE, value="this_week_start"),
            FilterSpec(column="CREATION_DATE", op=FilterOp.LT, value="this_week_end"),
        ],
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
        limit=10000,
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
        limit=10000,
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk.should_execute is False
    assert risk.execution_guard_reason == "execution_blocked_high_risk"
    assert "timeout_prone_large_join" in risk.pre_execution_risk_flags
    assert "timeout_prone_wide_listing" in risk.pre_execution_risk_flags
    assert risk.execution_skipped_reason == "precheck_timeout_prone_large_join"


@pytest.mark.asyncio
async def test_pre_execution_risk_safe_modes_structural_null_filtered_listing_on_wide_view() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("XXBT_PDKS_PER_DETAILS_V")
    assert table is not None

    plan = QueryPlan(
        intent="Aktif calisanlari listele",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "AD", "SOYAD", "EMAIL", "BIRIM_ADI"],
        filters=[FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL)],
        limit=10000,
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk.should_execute is True
    assert "timeout_prone_simple_listing" in risk.pre_execution_risk_flags
    assert risk.execution_guard_reason == "execution_safe_mode_applied"
    assert risk.execution_skipped_reason is None
    assert risk.safe_mode_applied is True
    assert risk.safe_mode_reason == "execution_safe_mode_applied"
    assert risk.effective_limit == 25


@pytest.mark.asyncio
async def test_pre_execution_risk_blocks_unfiltered_simple_listing_on_wide_view() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("XXBT_PDKS_PER_DETAILS_V")
    assert table is not None

    plan = QueryPlan(
        intent="Calisanlari listele",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "AD", "SOYAD", "EMAIL", "BIRIM_ADI"],
        filters=[],
        limit=10000,
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk.should_execute is False
    assert risk.execution_guard_reason == "execution_blocked_high_risk"
    assert risk.execution_skipped_reason == "precheck_timeout_prone_simple_listing"
    assert risk.safe_mode_applied is False


@pytest.mark.asyncio
async def test_pre_execution_risk_keeps_filtered_simple_listing_executable() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    table = await catalog.resolve_table("XXBT_PDKS_PER_DETAILS_V")
    assert table is not None

    plan = QueryPlan(
        intent="IT departmanindaki calisanlari getir",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "AD", "SOYAD", "EMAIL", "BIRIM_ADI"],
        filters=[FilterSpec(column="BIRIM_ADI", op=FilterOp.LIKE, value="%IT%")],
        limit=100,
    )

    risk = assess_pre_execution_risk(plan, table)
    assert risk.should_execute is True
    assert "timeout_prone_simple_listing" not in risk.pre_execution_risk_flags
