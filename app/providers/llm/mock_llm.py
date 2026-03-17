"""Deterministic mock LLM provider for testing.

Provides rule-based, deterministic responses for Turkish NL queries.
Uses a keyword/regex pipeline to classify intent, detect filters,
aggregations, and projections — then builds a ``QueryPlan`` accordingly.

Supports multi-table JOIN plans (Sprint 5) for queries requiring
DEPARTMENT, POSITION, LOCATION, or ASSIGNMENT table joins.

No network calls are made.  No randomness.
"""

from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel

from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    JoinCondition,
    JoinSpec,
    JoinType,
    OrderSpec,
    QueryPlan,
    SortDirection,
)
from app.providers.llm.base import LLMProvider
from app.utils.turkish import casefold_tr

T = TypeVar("T", bound=BaseModel)


# ===================================================================
# Keyword / regex constants
# ===================================================================

# Person-entity keywords — used to verify the query is about employees
_PERSON_KW = {"çalışan", "personel", "eleman", "kişi", "sicil"}

# Listing verbs — signal a listing / retrieval intent
# Includes ASCII approximations typed without special Turkish characters.
_LIST_VERBS = {"getir", "listele", "göster", "çıkar", "ver", "bul", "goster", "cikar"}

# Active / terminated status keywords
_ACTIVE_KW = {"aktif"}
_TERMINATED_KW = {"ayrılan", "ayrılmış", "işten ayrılan", "isten ayrılan"}

# Org-dimension keywords → column for GROUP BY
# NOTE: XXBT_PDKS_PER_DETAILS_V uses BIRIM_ADI, UNVAN, LOCATION_ADI.
_ORG_DIMENSION_MAP: dict[str, str] = {
    "birim": "BIRIM_ADI",
    "departman": "BIRIM_ADI",
    "unvan": "UNVAN",
    "pozisyon": "UNVAN",
    "lokasyon": "LOCATION_ADI",
    "ekip": "BIRIM_ADI",   # "ekip" ≈ birim fallback
}

# Communication / contact keywords
_EMAIL_KW = {"email", "e-posta", "mail"}
_PHONE_KW = {"telefon", "dahili", "extension"}

# Employment-type keywords
_PAYROLL_KW = {"bordrolu"}
_INTERN_KW = {"stajyer", "stajyerler"}

# Tenure / time keywords  (compiled regexes below)
_RE_LAST_1_YEAR = re.compile(
    r"son\s+(?:1|bir)\s+y[ıi]l|1\s+y[ıi]lda\s+i[şs]e\s+ba[şs]la",
    re.IGNORECASE,
)
_RE_TENURE_10 = re.compile(
    r"(?:10|on)\s+y[ıi]l(?:dan)?\s+(?:uzun|fazla|[üu]st[üu])",
    re.IGNORECASE,
)

# Last 6 months: "son 6 ayda", "son alti ayda" etc.
_RE_LAST_6M = re.compile(
    r"son\s+(?:6|alt[ıi])\s+ay",
    re.IGNORECASE,
)

# Specific hire year: "2024 yılında", "2023 yilinda" etc.
_RE_YEAR_HIRE = re.compile(
    r"\b(20\d\d)\s+y[ıi]l[ıi]nda\b",
    re.IGNORECASE,
)

# Aggregate signal keywords
# "kac" / "say" / "dagilim" are ASCII approximations of "kaç" / "say" / "dağılım".
_AGG_KW = {"sayı", "sayısı", "kaç", "dağılım", "dağılımı", "adet", "kac", "say", "dagilim"}

# Projection hint: ad-soyad / sicil listing
_RE_NAME_PROJECTION = re.compile(
    r"(?:ad\s*soyad|sicil\s*(?:no|numar)|isim)|"
    r"(?:first_name|last_name|reg_no)",
    re.IGNORECASE,
)

# Salary keywords — XXBT_PDKS_PER_DETAILS_V has no salary column; these
# trigger the BORDROLU (payroll flag) safe-fallback projection instead.
# ASCII variants included ("maas", "maasli", "maasi", "ucret").
_SALARY_KW = {"maaş", "salary", "maaşlar", "maaşları", "maas", "maasli", "maasi", "ucret"}

# TC / identity keywords (restricted column demo)
_TC_KW = {"tc", "kimlik", "nüfus", "nufus", "tc_no", "kimlik_no"}

# Sort / order keywords
_RE_SORT = re.compile(
    r"s[ıi]rala|s[ıi]ral[ıi]|isme\s+g[öo]re\s+s[ıi]rala",
    re.IGNORECASE,
)

# ===================================================================
# Multi-table constants (Sprint 5)
# ===================================================================

# Keywords that signal a multi-table JOIN dimension
_MULTI_TABLE_DEPT_KW = {"departman bazında", "departmana göre", "departman kırılımında"}
_MULTI_TABLE_POS_KW = {"pozisyona göre", "pozisyon bazında", "pozisyon kırılımında"}
_MULTI_TABLE_LOC_KW = {"şehir bazında", "şehre göre", "lokasyon bazında", "lokasyona göre"}

# Assignment history pattern
_RE_ASSIGNMENT_HISTORY = re.compile(
    r"atama\s+ge[çc]mi[şs]|g[öo]revlendirme\s+ge[çc]mi[şs]|"
    r"pozisyon\s+de[ğg]i[şs]ikli[ğg]i",
    re.IGNORECASE,
)


# ===================================================================
# PO domain constants (Sprint 6)
# ===================================================================

_PO_DOMAIN_KW = {
    "satınalma",
    "satın alma",
    "sipariş",
    "satinalma",
    "purchase order",
    # ASCII approximations typed without Turkish characters:
    "satin alma",
    "satinalim",
    "siparis",  # matches siparisleri, siparisler too (substring)
    "po_headers",  # catches literal table-name references like "PO_HEADERS_ALL"
}
_PO_VENDOR_KW = {"tedarikçi", "vendor", "satıcı", "satici", "tedarikci"}
_PO_LINE_KW = {"kalem", "satır", "satir", "line"}
_PO_ITEM_KW = {"ürün", "urun", "item", "malzeme", "stok"}
_PO_DIST_KW = {"dağıtım", "dagitim", "distribution", "muhasebe"}
_PO_PENDING_KW = {"bekleyen", "teslim bekleyen"}
_PO_UNAPPROVED_KW = {"onaysız", "onaysiz", "onay bekleyen", "bekleyen"}
_PO_UNCLOSED_KW = {"kapatılmamış", "kapatilmamis", "açık", "acik"}
_PO_LAST_30D_KW = {
    "son 30 gün",
    "son 30 gun",
    "son bir ay",
    "son 1 ay",
}
_RE_PO_TOKEN = re.compile(r"\bpo\b", re.IGNORECASE)


# ===================================================================
# Internal helper functions
# ===================================================================


def _normalize(text: str) -> str:
    """Turkish-aware lowercase + collapse whitespace."""
    folded = casefold_tr(text.strip())
    return re.sub(r"\s+", " ", folded)


def _tokens(text: str) -> set[str]:
    """Split normalised text into a word-set."""
    return set(text.split())


def _has_any(words: set[str], keywords: set[str]) -> bool:
    """Return True if *any* keyword appears in *words*."""
    return bool(words & keywords)


def _has_substr(text: str, keywords: set[str]) -> bool:
    """Return True if any keyword is a substring of *text*."""
    return any(k in text for k in keywords)


# ===================================================================
# Multi-table plan builder (Sprint 5)
# ===================================================================


def _try_multi_table_plan(norm: str, words: set[str]) -> QueryPlan | None:
    """Attempt to build a multi-table JOIN plan.

    Returns ``None`` if the query does not match any multi-table pattern,
    in which case the caller falls back to single-table logic.
    """
    has_agg = _has_any(words, _AGG_KW) or any(
        k in norm for k in ("sayısı", "sayı", "kaç", "dağılım", "dağılımı")
    )

    # ── Assignment history ────────────────────────────────────
    if _RE_ASSIGNMENT_HISTORY.search(norm):
        return QueryPlan(
            intent="Çalışanın atama geçmişini göster",
            table="ASSIGNMENT",
            select_columns=[
                "effective_start_date",
                "effective_end_date",
                "department_name",
                "position_name",
            ],
            joins=[
                JoinSpec(
                    left_table="ASSIGNMENT",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="ASSIGNMENT",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
                JoinSpec(
                    left_table="ASSIGNMENT",
                    right_table="POSITION",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="ASSIGNMENT",
                            left_column="position_id",
                            right_table="POSITION",
                            right_column="position_id",
                        ),
                    ],
                ),
            ],
            order_by=[
                OrderSpec(
                    column="effective_start_date",
                    direction=SortDirection.DESC,
                    table="ASSIGNMENT",
                ),
            ],
        )

    # ── Department + Position combined dimension ──────────────
    has_dept = _has_substr(norm, _MULTI_TABLE_DEPT_KW)
    has_pos = _has_substr(norm, _MULTI_TABLE_POS_KW)

    if has_dept and has_pos and has_agg:
        return QueryPlan(
            intent="Departman ve pozisyon bazında aktif çalışan sayısını göster",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["department_name", "position_name"],
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="*",
                    alias="employee_count",
                ),
            ],
            filters=[
                FilterSpec(column="quit_date", op=FilterOp.IS_NULL, table="XXBT_PDKS_PER_DETAILS_V"),
            ],
            group_by=["department_name", "position_name"],
            order_by=[
                OrderSpec(column="department_name", direction=SortDirection.ASC),
                OrderSpec(column="employee_count", direction=SortDirection.DESC),
            ],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="POSITION",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="position_id",
                            right_table="POSITION",
                            right_column="position_id",
                        ),
                    ],
                ),
            ],
        )

    # ── Department-only dimension ─────────────────────────────
    if has_dept and has_agg:
        return QueryPlan(
            intent="Departman bazında aktif çalışan sayısını göster",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["department_name"],
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="*",
                    alias="employee_count",
                ),
            ],
            filters=[
                FilterSpec(column="quit_date", op=FilterOp.IS_NULL, table="XXBT_PDKS_PER_DETAILS_V"),
            ],
            group_by=["department_name"],
            order_by=[
                OrderSpec(column="employee_count", direction=SortDirection.DESC),
            ],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
            ],
        )

    # ── Position-only dimension ───────────────────────────────
    if has_pos and has_agg:
        return QueryPlan(
            intent="Pozisyona göre aktif çalışan dağılımını göster",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["position_name"],
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="*",
                    alias="employee_count",
                ),
            ],
            filters=[
                FilterSpec(column="quit_date", op=FilterOp.IS_NULL, table="XXBT_PDKS_PER_DETAILS_V"),
            ],
            group_by=["position_name"],
            order_by=[
                OrderSpec(column="employee_count", direction=SortDirection.DESC),
            ],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="POSITION",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="position_id",
                            right_table="POSITION",
                            right_column="position_id",
                        ),
                    ],
                ),
            ],
        )

    # ── Location / city dimension ─────────────────────────────
    has_loc = _has_substr(norm, _MULTI_TABLE_LOC_KW)
    if has_loc and has_agg:
        return QueryPlan(
            intent="Şehir bazında aktif çalışan sayısını göster",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["city"],
            aggregations=[
                AggregationSpec(
                    function=AggregateFn.COUNT,
                    column="*",
                    alias="employee_count",
                ),
            ],
            filters=[
                FilterSpec(column="quit_date", op=FilterOp.IS_NULL, table="XXBT_PDKS_PER_DETAILS_V"),
            ],
            group_by=["city"],
            order_by=[
                OrderSpec(column="employee_count", direction=SortDirection.DESC),
            ],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="LOCATION",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="location_id",
                            right_table="LOCATION",
                            right_column="location_id",
                        ),
                    ],
                ),
            ],
        )

    return None


def _build_po_chain_joins(*, include_loc: bool = False, include_dist: bool = False, include_item: bool = False) -> list[JoinSpec]:
    """Build canonical PO join chain segments deterministically."""
    joins: list[JoinSpec] = [
        JoinSpec(
            left_table="PO_HEADERS_ALL",
            right_table="PO_LINES_ALL",
            join_type=JoinType.INNER,
            on=[
                JoinCondition(
                    left_table="PO_HEADERS_ALL",
                    left_column="po_header_id",
                    right_table="PO_LINES_ALL",
                    right_column="po_header_id",
                ),
            ],
        ),
    ]

    if include_loc or include_dist:
        joins.append(
            JoinSpec(
                left_table="PO_LINES_ALL",
                right_table="PO_LINE_LOCATIONS_ALL",
                join_type=JoinType.INNER,
                on=[
                    JoinCondition(
                        left_table="PO_LINES_ALL",
                        left_column="po_line_id",
                        right_table="PO_LINE_LOCATIONS_ALL",
                        right_column="po_line_id",
                    ),
                ],
            ),
        )

    if include_dist:
        joins.append(
            JoinSpec(
                left_table="PO_LINE_LOCATIONS_ALL",
                right_table="PO_DISTRIBUTIONS_ALL",
                join_type=JoinType.INNER,
                on=[
                    JoinCondition(
                        left_table="PO_LINE_LOCATIONS_ALL",
                        left_column="line_location_id",
                        right_table="PO_DISTRIBUTIONS_ALL",
                        right_column="line_location_id",
                    ),
                ],
            ),
        )

    if include_item:
        joins.append(
            JoinSpec(
                left_table="PO_LINES_ALL",
                right_table="MTL_SYSTEM_ITEMS_B",
                join_type=JoinType.INNER,
                on=[
                    JoinCondition(
                        left_table="PO_LINES_ALL",
                        left_column="item_id",
                        right_table="MTL_SYSTEM_ITEMS_B",
                        right_column="inventory_item_id",
                    ),
                ],
            ),
        )

    return joins


def _try_po_plan(norm: str, words: set[str]) -> QueryPlan | None:
    """Attempt to build deterministic PO-domain plans.

    Returns ``None`` when the query does not look PO-related.
    """
    has_vendor = _has_substr(norm, _PO_VENDOR_KW)
    has_line = _has_substr(norm, _PO_LINE_KW)
    has_item = _has_substr(norm, _PO_ITEM_KW)
    has_dist = _has_substr(norm, _PO_DIST_KW)
    has_pending = _has_substr(norm, _PO_PENDING_KW)
    has_unapproved = _has_substr(norm, _PO_UNAPPROVED_KW)
    has_unclosed = _has_substr(norm, _PO_UNCLOSED_KW)
    has_last_30d = _has_substr(norm, _PO_LAST_30D_KW)
    has_count = _has_any(words, {"sayısı", "sayisi", "kaç", "kac", "adet", "dağılım", "dagilim"})
    has_amount = any(k in norm for k in ("tutar", "maliyet", "amount", "toplam"))

    has_po_token = bool(_RE_PO_TOKEN.search(norm))
    po_signal = _has_substr(norm, _PO_DOMAIN_KW) or has_po_token

    # Implicit PO signals for short queries that don't explicitly mention PO.
    if not po_signal and ((has_pending and has_line) or (has_dist and has_amount)):
        po_signal = True

    if not po_signal:
        return None

    # 1) Son 30 günde açılan PO'lar
    if has_last_30d:
        return QueryPlan(
            intent="Son 30 günde açılan satın alma siparişlerini göster",
            table="PO_HEADERS_ALL",
            select_columns=["po_header_id", "vendor_id", "creation_date", "authorization_status", "currency_code"],
            filters=[
                FilterSpec(column="creation_date", op=FilterOp.GTE, value="__RELATIVE_DATE_LAST_30_DAYS__", table="PO_HEADERS_ALL"),
            ],
            order_by=[OrderSpec(column="creation_date", direction=SortDirection.DESC, table="PO_HEADERS_ALL")],
            limit=100,
        )

    # 2) Tedarikçiye göre PO sayısı
    if has_vendor and has_count:
        return QueryPlan(
            intent="Tedarikçiye göre PO sayısını göster",
            table="PO_HEADERS_ALL",
            select_columns=["vendor_id"],
            aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="*", alias="po_count", table="PO_HEADERS_ALL")],
            group_by=["vendor_id"],
            order_by=[OrderSpec(column="po_count", direction=SortDirection.DESC)],
            limit=100,
        )

    # 3) Dağıtım bazında tutar analizi
    # Primary table is PO_DISTRIBUTIONS_ALL so column validation passes.
    if has_dist and has_amount:
        return QueryPlan(
            intent="Dağıtım bazında sipariş tutar analizini göster",
            table="PO_DISTRIBUTIONS_ALL",
            select_columns=["po_distribution_id", "quantity_ordered", "unit_price"],
            order_by=[
                OrderSpec(column="quantity_ordered", direction=SortDirection.DESC, table="PO_DISTRIBUTIONS_ALL"),
            ],
            limit=100,
        )

    # 4) Ürün bazında PO satırları
    if has_item and (has_line or has_count):
        return QueryPlan(
            intent="Ürün bazında PO satır sayısını göster",
            table="PO_HEADERS_ALL",
            select_columns=["segment1", "description"],
            aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="*", alias="line_count", table="PO_LINES_ALL")],
            group_by=["segment1", "description"],
            order_by=[OrderSpec(column="line_count", direction=SortDirection.DESC)],
            joins=_build_po_chain_joins(include_item=True),
            limit=100,
        )

    # 5) Teslim bekleyen satırlar
    if has_pending and has_line:
        return QueryPlan(
            intent="Teslim bekleyen PO satırlarını göster",
            table="PO_HEADERS_ALL",
            select_columns=["line_num", "item_description", "quantity", "quantity_received"],
            filters=[
                FilterSpec(column="quantity_received", op=FilterOp.LT, value="__COLUMN_REF__quantity", table="PO_LINE_LOCATIONS_ALL"),
            ],
            order_by=[OrderSpec(column="quantity", direction=SortDirection.DESC, table="PO_LINES_ALL")],
            joins=_build_po_chain_joins(include_loc=True),
            limit=100,
        )

    # 6) Kalem bazında sipariş miktarı
    if has_line and any(k in norm for k in ("miktar", "adet", "quantity")):
        return QueryPlan(
            intent="Kalem bazında sipariş miktarını göster",
            table="PO_HEADERS_ALL",
            select_columns=["line_num", "item_description"],
            aggregations=[AggregationSpec(function=AggregateFn.SUM, column="quantity", alias="total_qty", table="PO_LINES_ALL")],
            group_by=["line_num", "item_description"],
            order_by=[OrderSpec(column="total_qty", direction=SortDirection.DESC)],
            joins=_build_po_chain_joins(),
            limit=100,
        )

    # 7) Onaysız / bekleyen PO'lar
    if has_unapproved:
        return QueryPlan(
            intent="Onaysız veya bekleyen PO'ları listele",
            table="PO_HEADERS_ALL",
            select_columns=["po_header_id", "vendor_id", "creation_date", "authorization_status"],
            filters=[
                FilterSpec(column="authorization_status", op=FilterOp.NEQ, value="APPROVED", table="PO_HEADERS_ALL"),
            ],
            order_by=[OrderSpec(column="creation_date", direction=SortDirection.DESC, table="PO_HEADERS_ALL")],
            limit=100,
        )

    # 8) Kapatılmamış / açık PO'lar
    if has_unclosed:
        return QueryPlan(
            intent="Kapatılmamış açık PO'ları listele",
            table="PO_HEADERS_ALL",
            select_columns=["po_header_id", "vendor_id", "creation_date", "authorization_status"],
            filters=[
                FilterSpec(column="authorization_status", op=FilterOp.NEQ, value="CLOSED", table="PO_HEADERS_ALL"),
            ],
            order_by=[OrderSpec(column="creation_date", direction=SortDirection.DESC, table="PO_HEADERS_ALL")],
            limit=100,
        )

    # 9) Açık satınalma siparişleri (default PO list intent)
    if any(k in norm for k in ("açık", "acik", "listele", "göster", "goster", "getir")):
        return QueryPlan(
            intent="Açık satın alma siparişlerini listele",
            table="PO_HEADERS_ALL",
            select_columns=["po_header_id", "vendor_id", "creation_date", "authorization_status", "currency_code"],
            filters=[
                FilterSpec(column="authorization_status", op=FilterOp.NEQ, value="CLOSED", table="PO_HEADERS_ALL"),
            ],
            order_by=[OrderSpec(column="creation_date", direction=SortDirection.DESC, table="PO_HEADERS_ALL")],
            limit=100,
        )

    # PO domain detected but intent narrowness not strict enough: return
    # a deterministic safe listing instead of unnecessary clarification.
    return QueryPlan(
        intent="Satın alma siparişlerini listele",
        table="PO_HEADERS_ALL",
        select_columns=["po_header_id", "vendor_id", "creation_date", "authorization_status"],
        order_by=[OrderSpec(column="creation_date", direction=SortDirection.DESC, table="PO_HEADERS_ALL")],
        limit=100,
    )


# ===================================================================
# Rule-pipeline builder
# ===================================================================


def _build_plan_from_rules(user_msg: str) -> QueryPlan:
    """Deterministic rule pipeline.

    Steps:
      1. Normalize text
      2. Detect aggregate dimension  → aggregation + group_by
      3. Detect projection hints     → select_columns
      4. Detect status filters        → active / terminated
      5. Detect temporal filters      → hire_date / tenure
      6. Detect employment filters    → payroll / intern
      7. Detect contact filters       → email / phone
      8. Detect salary intent
      9. Detect sort intent
      10. Build QueryPlan or fallback to clarification
    """
    norm = _normalize(user_msg)
    words = _tokens(norm)

    # ── 0) PO-domain detection (Sprint 6) ─────────────────────
    po_plan = _try_po_plan(norm, words)
    if po_plan is not None:
        return po_plan

    # ── 1) Multi-table detection (Sprint 5) ────────────────────
    # Try multi-table patterns first; if matched, return immediately.
    multi_plan = _try_multi_table_plan(norm, words)
    if multi_plan is not None:
        return multi_plan

    # Accumulators
    select_columns: list[str] = []
    filters: list[FilterSpec] = []
    aggregations: list[AggregationSpec] = []
    group_by: list[str] = []
    order_by: list[OrderSpec] = []
    intent_parts: list[str] = []
    matched = False  # at least one rule fired

    # ── 1) Aggregate dimension ─────────────────────────────────
    # Use substring matching for AGG keywords because Turkish suffixes
    # (sayılarını, dağılımını) prevent exact token matches.
    # Dimensional keywords alone (departmanındaki, pozisyonundaki) are NOT
    # treated as agg signals — they need an explicit aggregation keyword.
    has_agg_signal = any(kw in norm for kw in _AGG_KW)
    agg_dimension: str | None = None
    if has_agg_signal:
        for kw, col in _ORG_DIMENSION_MAP.items():
            if kw in norm:
                agg_dimension = col
                break

    if has_agg_signal and agg_dimension:
        aggregations.append(
            AggregationSpec(function=AggregateFn.COUNT, column="*", alias="sayi"),
        )
        group_by.append(agg_dimension)
        dim_label = agg_dimension.replace("_ADI", "").replace("_", " ").lower()
        intent_parts.append(f"{dim_label} bazında sayı")
        matched = True
    elif has_agg_signal and not agg_dimension:
        # Generic count without dimension — still a valid plan
        aggregations.append(
            AggregationSpec(function=AggregateFn.COUNT, column="*", alias="sayi"),
        )
        intent_parts.append("Toplam sayı")
        matched = True

    # ── 2) Projection hints (ad-soyad / sicil) ────────────────
    if _RE_NAME_PROJECTION.search(norm) and not aggregations:
        select_columns = ["SICIL_NO", "AD", "SOYAD"]
        intent_parts.append("Ad soyad / sicil listeleme")
        matched = True

    # ── 3) Active / terminated status ──────────────────────────
    if _has_any(words, _ACTIVE_KW) or "aktif" in norm:
        filters.append(FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL))
        intent_parts.append("Aktif çalışanlar")
        matched = True

    if _has_any(words, _TERMINATED_KW) or _has_substr(norm, _TERMINATED_KW):
        filters.append(FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NOT_NULL))
        intent_parts.append("Ayrılan çalışanlar")
        matched = True

    # ── 4) Temporal filters ────────────────────────────────────
    if _RE_LAST_1_YEAR.search(norm):
        filters.append(
            FilterSpec(
                column="ISE_GIRIS_TARIHI",
                op=FilterOp.GTE,
                value="__RELATIVE_DATE_LAST_1_YEAR__",
            ),
        )
        intent_parts.append("Son 1 yılda işe başlayan")
        matched = True

    if _RE_TENURE_10.search(norm):
        filters.append(
            FilterSpec(
                column="ISE_GIRIS_TARIHI",
                op=FilterOp.LTE,
                value="__RELATIVE_DATE_10_YEARS_AGO__",
            ),
        )
        intent_parts.append("10 yıldan uzun süredir çalışan")
        matched = True

    # Last 6 months — maps to ISE_GIRIS_TARIHI as best available proxy.
    # "terfi" (promotion) has no dedicated column; temporal signal is preserved.
    if _RE_LAST_6M.search(norm):
        filters.append(
            FilterSpec(
                column="ISE_GIRIS_TARIHI",
                op=FilterOp.GTE,
                value="__RELATIVE_DATE_LAST_6_MONTHS__",
            ),
        )
        intent_parts.append("Son 6 ayda işe giriş / değişim")
        matched = True

    # Specific hire year: "2024 yılında işe giren"
    _year_m = _RE_YEAR_HIRE.search(norm)
    if _year_m:
        _yr = int(_year_m.group(1))
        filters.append(FilterSpec(column="ISE_GIRIS_TARIHI", op=FilterOp.GTE, value=f"{_yr}-01-01"))
        filters.append(FilterSpec(column="ISE_GIRIS_TARIHI", op=FilterOp.LT,  value=f"{_yr + 1}-01-01"))
        intent_parts.append(f"{_yr} yılında işe giren")
        matched = True

    # ── 5) Employment-type filters ─────────────────────────────
    if _has_any(words, _PAYROLL_KW) or "bordrolu" in norm:
        filters.append(
            FilterSpec(column="BORDROLU", op=FilterOp.EQ, value=1),
        )
        intent_parts.append("Bordrolu çalışanlar")
        matched = True

    if _has_any(words, _INTERN_KW) or _has_substr(norm, _INTERN_KW):
        filters.append(
            FilterSpec(column="STAJYER", op=FilterOp.EQ, value=1),
        )
        intent_parts.append("Stajyerler")
        matched = True

    # ── 6) Contact filters ─────────────────────────────────────
    if _has_substr(norm, _EMAIL_KW):
        # "eksik" / "olmayan" → IS_NULL; otherwise IS_NOT_NULL
        if "eksik" in norm or "olmayan" in norm or "yok" in norm:
            filters.append(FilterSpec(column="EMAIL", op=FilterOp.IS_NULL))
            intent_parts.append("Email eksik olanlar")
        else:
            filters.append(FilterSpec(column="EMAIL", op=FilterOp.IS_NOT_NULL))
            intent_parts.append("Email olanlar")
        matched = True

    if _has_substr(norm, _PHONE_KW):
        # "eksik" / "olmayan" → IS_NULL; otherwise IS_NOT_NULL
        if "eksik" in norm or "olmayan" in norm or "yok" in norm:
            filters.append(FilterSpec(column="DAHILI", op=FilterOp.IS_NULL))
            intent_parts.append("Telefon dahili eksik")
        else:
            filters.append(FilterSpec(column="DAHILI", op=FilterOp.IS_NOT_NULL))
            intent_parts.append("Telefon dahili olanlar")
        matched = True

    # ── 7) Salary intent ───────────────────────────────────────
    # NOTE: XXBT_PDKS_PER_DETAILS_V has no salary column. Show BORDROLU flag instead.
    if _has_any(words, _SALARY_KW):
        if not select_columns:
            select_columns = ["SICIL_NO", "AD", "SOYAD", "BORDROLU"]
        elif "BORDROLU" not in select_columns:
            select_columns.append("BORDROLU")
        intent_parts.append("Maaş/bordro bilgisi")
        matched = True

    # ── 7b) TC / kimlik intent ──────────────────────────────
    # NOTE: TC_NO is a restricted column; the planner returns it, validation rejects.
    if _has_any(words, _TC_KW):
        if not select_columns:
            select_columns = ["SICIL_NO", "AD", "SOYAD", "TC_NO"]
        elif "TC_NO" not in select_columns:
            select_columns.append("TC_NO")
        intent_parts.append("TC/kimlik bilgisi")
        matched = True

    # ── 8) Sort intent ─────────────────────────────────────────
    if _RE_SORT.search(norm):
        order_by.append(OrderSpec(column="SOYAD", direction=SortDirection.ASC))
        intent_parts.append("Sıralı liste")
        matched = True

    # ── 9) Generic listing catch-all ───────────────────────────
    # If some person/list keyword present but no rule fired yet,
    # treat as generic employee listing.
    # ASCII variant "calisan" is included for users who type without Turkish chars.
    if not matched:
        person_signal = _has_any(words, _PERSON_KW) or _has_substr(
            norm, {"çalışan", "personel", "calisan"},
        )
        verb_signal = _has_any(words, _LIST_VERBS) or _has_substr(
            norm, _LIST_VERBS,
        )
        if person_signal or verb_signal:
            if not select_columns:
                select_columns = ["SICIL_NO", "AD", "SOYAD", "BIRIM_ADI"]
            intent_parts.append("Çalışanları listele")
            matched = True

    # ── 10) Fallback: clarification ────────────────────────────
    if not matched:
        return QueryPlan(
            intent="Belirsiz sorgu",
            table="XXBT_PDKS_PER_DETAILS_V",
            needs_clarification=True,
            clarification_message=(
                "Hangi bilgileri görmek istediğinizi belirtir misiniz?"
            ),
        )

    # ── Assemble final plan ────────────────────────────────────
    # If no explicit projection was set and we have aggregations, keep
    # select_columns empty (aggregation query).
    # If no explicit projection and no aggregation, set a sensible default.
    if not select_columns and not aggregations:
        select_columns = ["SICIL_NO", "AD", "SOYAD"]

    intent = " + ".join(intent_parts) if intent_parts else "Çalışan sorgusu"

    return QueryPlan(
        intent=intent,
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=select_columns,
        filters=filters,
        aggregations=aggregations,
        group_by=group_by,
        order_by=order_by,
    )


# ===================================================================
# Provider
# ===================================================================


class MockLLMProvider(LLMProvider):
    """Deterministic mock LLM – no network, no randomness.

    For **planner** calls (``generate_structured``), the provider extracts
    the user message from the prompt (after ``Kullanıcı sorusu:``) and
    passes it through a keyword/regex rule pipeline to produce a
    ``QueryPlan``.  Only truly ambiguous queries trigger clarification.

    For **narrator** calls (``generate_text``), it pattern-matches on the
    execution summary embedded in the prompt to produce a deterministic
    Turkish narration.
    """

    def __init__(self) -> None:
        self.last_structured_response_text: str | None = None
        self.last_structured_parse_error: str | None = None
        self.last_text_response_text: str | None = None

    # -- Structured output (planner) -----------------------------------------

    async def generate_structured(
        self, prompt: str, response_model: type[T],
    ) -> T:
        user_msg = self._extract_user_message(prompt)
        plan = _build_plan_from_rules(user_msg)
        self.last_structured_response_text = plan.model_dump_json(indent=2)
        self.last_structured_parse_error = None
        return plan  # type: ignore[return-value]

    # -- Free-form text (narrator) -------------------------------------------

    async def generate_text(self, prompt: str) -> str:
        folded = casefold_tr(prompt)
        summary_folded = folded.split("sonuç özeti:")[-1] if "sonuç özeti:" in folded else folded
        summary_folded = summary_folded.split("yanıtını ver:")[0]

        # Empty result
        if "satır sayısı: 0" in summary_folded or "satır_sayısı=0" in summary_folded:
            self.last_text_response_text = "Aradığınız kriterlere uygun kayıt bulunamadı."
            return self.last_text_response_text

        # Success with row count – check BEFORE error patterns because
        # the narrator system template contains words like "Kısıtlı" in
        # its instructions which would otherwise false-match.
        match = re.search(r"satır sayısı:\s*(\d+)", summary_folded)
        if not match:
            match = re.search(r"satır_sayısı=(\d+)", summary_folded)
        if match:
            self.last_text_response_text = f"{match.group(1)} kayıt bulundu."
            return self.last_text_response_text

        # Clarification needed — checked before restriction errors to avoid
        # column descriptions (containing "kısıtlı") triggering wrong branch.
        if "açıklama gerekli" in summary_folded or "clarification" in summary_folded or "lütfen belirtin" in summary_folded:
            self.last_text_response_text = "Hangi alanları görmek istediğinizi belirtir misiniz?"
            return self.last_text_response_text

        # Restricted-column validation error
        if "kısıtlı" in summary_folded or "erişime kapalı" in summary_folded:
            self.last_text_response_text = (
                "İstenen alan erişime kapalı olduğu için sorgu çalıştırılamadı."
            )
            return self.last_text_response_text

        # General validation error
        if "doğrulama hatası" in summary_folded:
            self.last_text_response_text = "Sorgunuzda bir doğrulama hatası oluştu."
            return self.last_text_response_text

        # Clarification needed (fallback)
        if "açıklama" in summary_folded and "gerekl" in summary_folded:
            self.last_text_response_text = "Hangi alanları görmek istediğinizi belirtir misiniz?"
            return self.last_text_response_text

        # Execution error
        if "çalıştırma hatası" in summary_folded:
            self.last_text_response_text = "Sorgu çalıştırılırken bir hata oluştu."
            return self.last_text_response_text

        self.last_text_response_text = "Sorgu başarıyla çalıştırıldı."
        return self.last_text_response_text

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _extract_user_message(prompt: str) -> str:
        """Extract the raw user message from a planner prompt.

        The planner prompt ends with ``Kullanıcı sorusu: <message>``.
        Matching only that portion avoids false positives from the example
        queries embedded in the system template.
        """
        marker = "Kullanıcı sorusu: "
        idx = prompt.rfind(marker)
        if idx >= 0:
            return prompt[idx + len(marker) :].strip()
        return prompt
