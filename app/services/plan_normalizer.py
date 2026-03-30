"""Pre-validation & post-validation normalization for QueryPlan dicts.

This module sits between the raw LLM JSON output and the Pydantic
``QueryPlan`` model.  It fixes common LLM contract violations so that
the structured plan can be parsed without errors.

Two-phase normalization
=======================
1. **Pre-parse** (``normalize_raw_plan``) — operates on ``dict`` *before*
   ``QueryPlan.model_validate()``.  Fixes enum variants, trims whitespace,
   etc.
2. **Post-parse** (``canonicalize_columns``) — operates on a valid
   ``QueryPlan`` *after* parsing.  Resolves column aliases → canonical
   names using ``TableMetadata``.

Both phases log what they changed so that evaluation tooling can count
normalization/canonicalization events.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from difflib import get_close_matches
from typing import Any

from app.core.logging import get_logger
from app.domain.catalog_models import TableMetadata
from app.domain.query_plan import (
    AggregateFn,
    FilterOp,
    QueryPlan,
    SortDirection,
)

logger = get_logger(__name__)

_PLAN_LIST_FIELD_ALIASES: tuple[str, ...] = ("column", "name", "field", "value")
_LIST_LIKE_PLAN_FIELDS: tuple[str, ...] = (
    "select_columns",
    "group_by",
    "candidate_tables",
)
_OBJECT_LIST_PLAN_FIELDS: tuple[str, ...] = (
    "filters",
    "aggregations",
    "order_by",
    "joins",
)


# -----------------------------------------------------------------------
# FilterOp alias map — LLM may produce any of these instead of the
# canonical enum value.  All keys are UPPER-CASED for lookup.
# -----------------------------------------------------------------------
_FILTER_OP_ALIASES: dict[str, str] = {
    # Equality
    "EQUALS": "=",
    "EQUAL": "=",
    "EQ": "=",
    "==": "=",
    # Not-equal
    "NOT_EQUAL": "!=",
    "NOT_EQUALS": "!=",
    "NEQ": "!=",
    "NE": "!=",
    "<>": "!=",
    # Less than
    "LESS_THAN": "<",
    "LT": "<",
    # Less than or equal
    "LESS_THAN_OR_EQUAL": "<=",
    "LESS_THAN_OR_EQUALS": "<=",
    "LTE": "<=",
    "LE": "<=",
    # Greater than
    "GREATER_THAN": ">",
    "GT": ">",
    # Greater than or equal
    "GREATER_THAN_OR_EQUAL": ">=",
    "GREATER_THAN_OR_EQUALS": ">=",
    "GTE": ">=",
    "GE": ">=",
    # Null checks
    "IS NULL": "IS_NULL",
    "ISNULL": "IS_NULL",
    "NULL": "IS_NULL",
    "IS NOT NULL": "IS_NOT_NULL",
    "IS_NOT": "IS_NOT_NULL",
    "IS NOT": "IS_NOT_NULL",
    "ISNOTNULL": "IS_NOT_NULL",
    "NOT_NULL": "IS_NOT_NULL",
    "NOTNULL": "IS_NOT_NULL",
    # Bare "IS" — LLM uses to mean "column exists / is not null"
    "IS": "IS_NOT_NULL",
    # LIKE variants
    "ILIKE": "LIKE",
    "LIKE_UPPER": "LIKE",
    "LIKE_LOWER": "LIKE",
    "CONTAINS": "LIKE",
    "STARTS_WITH": "LIKE",
    "ENDS_WITH": "LIKE",
    # Function-as-operator hallucinations (LLM uses UPPER/LOWER as filter op)
    # Best approximation: treat as case-insensitive substring match (LIKE)
    "UPPER": "LIKE",
    "LOWER": "LIKE",
}

# Ops that cannot be meaningfully normalized to a binary FilterOp.
# Filters using these ops are DROPPED with a warning rather than causing
# a Pydantic validation failure that rejects the entire plan.
_FILTER_OP_DROP: frozenset[str] = frozenset({
    "EXTRACT_YEAR",
    "EXTRACT_MONTH",
    "EXTRACT",
    "YEAR",
    "MONTH",
    "DATE_FORMAT",
    "YEAR_EQUALS",
    "TO_CHAR",
    "TO_DATE",
})

# Build reverse look-up set of valid canonical values for quick membership test.
_VALID_FILTER_OPS: set[str] = {op.value for op in FilterOp}

# ---------------------------------------------------------------------------
# Date value normalization — handles relative-date string bind values
# that Oracle cannot consume directly (ORA-01858 / ORA-01861).
# ---------------------------------------------------------------------------

# Compiled patterns ordered by specificity (most specific first).
_RELATIVE_DATE_PATTERNS: list[tuple[re.Pattern[str], Any]] = [
    # CURRENT_DATE - N days
    (
        re.compile(r'^current_date\s*[-–]\s*(\d+)$', re.I),
        lambda m: (date.today() - timedelta(days=int(m.group(1)))).isoformat(),
    ),
    # CURRENT_DATE + N days
    (
        re.compile(r'^current_date\s*[+]\s*(\d+)$', re.I),
        lambda m: (date.today() + timedelta(days=int(m.group(1)))).isoformat(),
    ),
    # SYSDATE - N
    (
        re.compile(r'^sysdate\s*[-–]\s*(\d+)$', re.I),
        lambda m: (date.today() - timedelta(days=int(m.group(1)))).isoformat(),
    ),
    # Named relative anchors
    (re.compile(r'^today$', re.I),                lambda _: date.today().isoformat()),
    (re.compile(r'^current_date$', re.I),         lambda _: date.today().isoformat()),
    (re.compile(r'^sysdate$', re.I),              lambda _: date.today().isoformat()),
    (re.compile(r'^trunc\(sysdate\)$', re.I),    lambda _: date.today().isoformat()),
    (re.compile(r'^current_week_start$', re.I),   lambda _: (date.today() - timedelta(days=date.today().weekday())).isoformat()),
    (re.compile(r'^current_month_start$', re.I),  lambda _: date.today().replace(day=1).isoformat()),
    (re.compile(r'^current_year_start$', re.I),   lambda _: date.today().replace(month=1, day=1).isoformat()),
    # SQL expressions that survived as string values (e.g. "CURRENT_DATE - 30")
    (
        re.compile(r'^CURRENT_DATE\s*-\s*(\d+)$'),
        lambda m: (date.today() - timedelta(days=int(m.group(1)))).isoformat(),
    ),
    # Oracle TRUNC(SYSDATE, 'fmt') expressions the LLM may emit as filter values.
    # Resolved to ISO dates so Oracle does not receive raw SQL function strings as binds.
    (
        re.compile(r"^trunc\s*\(\s*sysdate\s*,\s*'iw'\s*\)$", re.I),
        lambda _: (date.today() - timedelta(days=date.today().weekday())).isoformat(),
    ),
    (
        re.compile(r"^trunc\s*\(\s*sysdate\s*,\s*'mm'\s*\)$", re.I),
        lambda _: date.today().replace(day=1).isoformat(),
    ),
    (
        re.compile(r"^trunc\s*\(\s*sysdate\s*,\s*'yyyy'\s*\)$", re.I),
        lambda _: date.today().replace(month=1, day=1).isoformat(),
    ),
    # Natural-language week anchors (Turkish / English) the LLM may use
    (
        re.compile(r"^(bu_hafta|bu\s+hafta|this_week|current_week)$", re.I),
        lambda _: (date.today() - timedelta(days=date.today().weekday())).isoformat(),
    ),
]


def _normalize_date_value(value: object) -> object:
    """If *value* is a relative-date string, resolve it to an ISO date string.

    Returns the original value unchanged for anything that does not match
    a known relative-date pattern.
    """
    if not isinstance(value, str):
        return value
    v = value.strip()
    for pattern, resolver in _RELATIVE_DATE_PATTERNS:
        m = pattern.match(v)
        if m:
            resolved = resolver(m)
            logger.debug("Date value normalised: %r -> %r", value, resolved)
            return resolved
    return value


def _coerce_column_token(item: object) -> object:
    """Coerce known column-token drift into a plain string when safe."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in _PLAN_LIST_FIELD_ALIASES:
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return item


def _normalize_relative_delta_value(value: object, op: object) -> object:
    """Resolve known structured relative-date values into ISO dates when safe."""
    if not isinstance(value, dict):
        return value
    if str(value.get("type", "")).strip().lower() != "date_delta":
        return value

    units = value.get("units")
    unit = str(value.get("unit", "")).strip().lower()
    if not isinstance(units, int) or units < 0:
        return value

    if unit in {"day", "days", "gun", "gün"}:
        delta = timedelta(days=units)
    elif unit in {"week", "weeks", "hafta"}:
        delta = timedelta(weeks=units)
    elif unit in {"month", "months", "ay"}:
        delta = timedelta(days=30 * units)
    elif unit in {"year", "years", "yil", "yıl"}:
        delta = timedelta(days=365 * units)
    else:
        return value

    op_token = op.value if isinstance(op, FilterOp) else str(op)
    today = date.today()
    if op_token in {">=", ">"}:
        return (today - delta).isoformat()
    if op_token in {"<=", "<"}:
        return (today + delta).isoformat()
    return value

# -----------------------------------------------------------------------
# AggregateFn / SortDirection alias maps
# -----------------------------------------------------------------------
_AGG_FN_ALIASES: dict[str, str] = {
    "COUNT": "COUNT",
    "SUM": "SUM",
    "AVG": "AVG",
    "AVERAGE": "AVG",
    "MIN": "MIN",
    "MINIMUM": "MIN",
    "MAX": "MAX",
    "MAXIMUM": "MAX",
}
_VALID_AGG_FNS: set[str] = {fn.value for fn in AggregateFn}

_SORT_DIR_ALIASES: dict[str, str] = {
    "ASCENDING": "ASC",
    "DESCENDING": "DESC",
    "A": "ASC",
    "D": "DESC",
}
_VALID_SORT_DIRS: set[str] = {d.value for d in SortDirection}


# -----------------------------------------------------------------------
# Phase 1 — Pre-parse normalization (dict → dict)
# -----------------------------------------------------------------------


class NormalizationStats:
    """Accumulator for normalization events — used by eval tooling."""

    def __init__(self) -> None:
        self.filter_op_normalized: int = 0
        self.agg_fn_normalized: int = 0
        self.sort_dir_normalized: int = 0
        self.whitespace_trimmed: int = 0
        self.column_canonicalized: int = 0
        self.table_name_canonicalized: bool = False

    @property
    def total_normalizations(self) -> int:
        return (
            self.filter_op_normalized
            + self.agg_fn_normalized
            + self.sort_dir_normalized
            + self.whitespace_trimmed
        )

    @property
    def total_canonicalizations(self) -> int:
        return self.column_canonicalized + int(self.table_name_canonicalized)

    def as_dict(self) -> dict[str, Any]:
        return {
            "filter_op_normalized": self.filter_op_normalized,
            "agg_fn_normalized": self.agg_fn_normalized,
            "sort_dir_normalized": self.sort_dir_normalized,
            "whitespace_trimmed": self.whitespace_trimmed,
            "column_canonicalized": self.column_canonicalized,
            "table_name_canonicalized": self.table_name_canonicalized,
            "total_normalizations": self.total_normalizations,
            "total_canonicalizations": self.total_canonicalizations,
        }


def normalize_raw_plan(
    raw: dict[str, Any],
    *,
    stats: NormalizationStats | None = None,
) -> dict[str, Any]:
    """Normalise a raw dict so that it is compatible with ``QueryPlan.model_validate``.

    This is **non-destructive**: a new dict is returned (shallow copy of
    top-level keys, deep copy of mutable sub-lists).

    Normalisation steps:
    1. Trim whitespace from string fields.
    2. Normalise ``FilterOp`` enum aliases.
    3. Normalise ``AggregateFn`` enum aliases.
    4. Normalise ``SortDirection`` enum aliases.
    5. Normalise table name casing (UPPER).
    """
    if stats is None:
        stats = NormalizationStats()

    out: dict[str, Any] = dict(raw)  # shallow copy

    # --- 0. Coerce common field-shape drift into QueryPlan-compatible lists ---
    for key in _LIST_LIKE_PLAN_FIELDS:
        value = out.get(key)
        if isinstance(value, (str, dict)):
            out[key] = [value]
            stats.whitespace_trimmed += 1

    for key in _OBJECT_LIST_PLAN_FIELDS:
        value = out.get(key)
        if isinstance(value, dict):
            out[key] = [value]
            stats.whitespace_trimmed += 1

    # --- 1. Trim whitespace from scalar string fields ---
    for key in ("intent", "table", "clarification_message"):
        val = out.get(key)
        if isinstance(val, str):
            stripped = val.strip()
            if stripped != val:
                stats.whitespace_trimmed += 1
                out[key] = stripped

    # --- Trim whitespace in list-of-string fields ---
    for key in ("select_columns", "group_by", "candidate_tables"):
        items = out.get(key)
        if isinstance(items, list):
            new_items: list[str] = []
            for item in items:
                coerced = _coerce_column_token(item)
                if isinstance(coerced, str):
                    stripped = coerced.strip()
                    if stripped != coerced:
                        stats.whitespace_trimmed += 1
                    new_items.append(stripped)
                else:
                    new_items.append(coerced)
            out[key] = new_items

    if out.get("clarification_message") and "needs_clarification" not in out:
        out["needs_clarification"] = True

    if out.get("needs_clarification") is True and not out.get("intent"):
        out["intent"] = "clarification_required"

    # --- 2. Normalise FilterOp in filters ---
    filters = out.get("filters")
    if isinstance(filters, list):
        new_filters: list[dict[str, Any]] = []
        for f in filters:
            if isinstance(f, dict):
                f = dict(f)  # shallow copy
                op = f.get("op")
                op_token: str | None = None
                if isinstance(op, str):
                    # Trim whitespace
                    op_clean = op.strip()
                    op_token = op_clean
                    if op_clean != op:
                        stats.whitespace_trimmed += 1
                    op_upper = op_clean.upper()

                    if op_clean not in _VALID_FILTER_OPS:
                        if op_upper in _FILTER_OP_DROP:
                            # Cannot normalize; drop entire filter with a warning.
                            logger.warning(
                                "FilterOp %r cannot be mapped to a QueryPlan op; "
                                "dropping filter for column %r.",
                                op_clean,
                                f.get("column"),
                            )
                            stats.filter_op_normalized += 1
                            f["_drop"] = True
                        else:
                            canonical = _FILTER_OP_ALIASES.get(op_upper)
                            if canonical is not None:
                                logger.debug(
                                    "FilterOp normalised: %r -> %r", op_clean, canonical
                                )
                                stats.filter_op_normalized += 1
                                f["op"] = canonical
                            else:
                                # Leave as-is — Pydantic will report the error
                                f["op"] = op_clean
                    else:
                        f["op"] = op_clean

                # Normalise NULL comparisons for equality operators.
                # LLMs often emit `= null` / `!= null` instead of IS_NULL variants.
                current_op = f.get("op")
                value = f.get("value")
                null_like = value is None or (
                    isinstance(value, str) and value.strip().upper() in {"NULL", "NONE"}
                )
                if op_token in {"=", "!="} and current_op in {"=", "!="} and null_like:
                    f["op"] = "IS_NULL" if current_op == "=" else "IS_NOT_NULL"
                    f["value"] = None
                    stats.filter_op_normalized += 1

                # Comparison ops (>=, <=, >, <) with null value are semantically
                # invalid — drop the filter rather than letting Pydantic raise.
                if not f.get("_drop"):
                    current_op_check = f.get("op")
                    val_check = f.get("value")
                    null_like_check = val_check is None or (
                        isinstance(val_check, str)
                        and val_check.strip().upper() in {"NULL", "NONE"}
                    )
                    if null_like_check and current_op_check in {">=", "<=", ">", "<"}:
                        logger.warning(
                            "Filter column=%r op=%r has null value — dropping "
                            "filter (cannot bind null for comparison op).",
                            f.get("column"),
                            current_op_check,
                        )
                        f["_drop"] = True
                        stats.filter_op_normalized += 1

                # Normalize relative-date string values
                if not f.get("_drop"):
                    val = f.get("value")
                    if isinstance(val, str):
                        norm_val = _normalize_date_value(val)
                        if norm_val is not val:
                            f["value"] = norm_val
                            stats.whitespace_trimmed += 1
                    elif isinstance(val, dict):
                        norm_val = _normalize_relative_delta_value(val, f.get("op"))
                        if norm_val is not val:
                            f["value"] = norm_val
                            stats.whitespace_trimmed += 1
                    elif isinstance(val, list):
                        new_val = [_normalize_date_value(v) for v in val]
                        if new_val != val:
                            f["value"] = new_val
                            stats.whitespace_trimmed += 1

                # Normalise Python bool to int — JSON `true`/`false` is parsed as
                # Python bool, which some Oracle driver versions reject as a parameter
                # type for NUMBER columns (causes ORA-01722 or ORA-06502).
                if not f.get("_drop"):
                    val = f.get("value")
                    if isinstance(val, bool):
                        f["value"] = int(val)
                        stats.whitespace_trimmed += 1
                    elif isinstance(val, list):
                        coerced = [int(v) if isinstance(v, bool) else v for v in val]
                        if coerced != val:
                            f["value"] = coerced
                            stats.whitespace_trimmed += 1

                # Trim column name whitespace
                col = f.get("column")
                if isinstance(col, str):
                    stripped = col.strip()
                    if stripped != col:
                        stats.whitespace_trimmed += 1
                        f["column"] = stripped

                new_filters.append(f)
            else:
                new_filters.append(f)
        # Drop filters marked for removal (unmappable ops)
        out["filters"] = [f for f in new_filters if not f.get("_drop")]

    # --- 3. Normalise AggregateFn in aggregations ---
    aggregations = out.get("aggregations")
    if isinstance(aggregations, list):
        new_aggs: list[dict[str, Any]] = []
        for agg in aggregations:
            if isinstance(agg, dict):
                agg = dict(agg)
                fn = agg.get("function")
                if isinstance(fn, str):
                    fn_clean = fn.strip().upper()
                    if fn_clean not in _VALID_AGG_FNS:
                        canonical = _AGG_FN_ALIASES.get(fn_clean)
                        if canonical is not None:
                            logger.debug(
                                "AggregateFn normalised: %r -> %r", fn, canonical
                            )
                            stats.agg_fn_normalized += 1
                            agg["function"] = canonical
                        else:
                            agg["function"] = fn_clean
                    else:
                        agg["function"] = fn_clean

                # Trim column whitespace
                col = agg.get("column")
                if isinstance(col, str):
                    stripped = col.strip()
                    if stripped != col:
                        stats.whitespace_trimmed += 1
                        agg["column"] = stripped
                new_aggs.append(agg)
            else:
                new_aggs.append(agg)
        out["aggregations"] = new_aggs

    # --- 4. Normalise SortDirection in order_by ---
    order_by = out.get("order_by")
    if isinstance(order_by, list):
        new_orders: list[dict[str, Any]] = []
        for ob in order_by:
            if isinstance(ob, dict):
                ob = dict(ob)
                direction = ob.get("direction")
                if isinstance(direction, str):
                    dir_clean = direction.strip().upper()
                    if dir_clean not in _VALID_SORT_DIRS:
                        canonical = _SORT_DIR_ALIASES.get(dir_clean)
                        if canonical is not None:
                            logger.debug(
                                "SortDirection normalised: %r -> %r",
                                direction,
                                canonical,
                            )
                            stats.sort_dir_normalized += 1
                            ob["direction"] = canonical
                        else:
                            ob["direction"] = dir_clean
                    else:
                        ob["direction"] = dir_clean

                col = ob.get("column")
                if isinstance(col, str):
                    stripped = col.strip()
                    if stripped != col:
                        stats.whitespace_trimmed += 1
                        ob["column"] = stripped
                new_orders.append(ob)
            else:
                new_orders.append(ob)
        out["order_by"] = new_orders

    # --- 5. Table name uppercasing ---
    table = out.get("table")
    if isinstance(table, str):
        upper_table = table.strip().upper()
        if upper_table != table:
            logger.debug("Table name normalised: %r -> %r", table, upper_table)
            out["table"] = upper_table

    # Upper-case candidate_tables
    cands = out.get("candidate_tables")
    if isinstance(cands, list):
        out["candidate_tables"] = [
            c.upper() if isinstance(c, str) else c for c in cands
        ]

    # --- 6. Normalise joins (Sprint 5 multi-table) ---
    joins = out.get("joins")
    if isinstance(joins, list):
        new_joins: list[dict[str, Any]] = []
        for j in joins:
            if isinstance(j, dict):
                j = dict(j)
                # Upper-case table names
                for tbl_key in ("left_table", "right_table"):
                    tv = j.get(tbl_key)
                    if isinstance(tv, str):
                        j[tbl_key] = tv.strip().upper()
                # Normalise join_type
                jt = j.get("join_type")
                if isinstance(jt, str):
                    j["join_type"] = jt.strip().upper()
                # Upper-case table names in ON conditions
                on_list = j.get("on")
                if isinstance(on_list, list):
                    new_on: list[dict[str, Any]] = []
                    for cond in on_list:
                        if isinstance(cond, dict):
                            cond = dict(cond)
                            for k in ("left_table", "right_table"):
                                cv = cond.get(k)
                                if isinstance(cv, str):
                                    cond[k] = cv.strip().upper()
                            for k in ("left_column", "right_column"):
                                cv = cond.get(k)
                                if isinstance(cv, str):
                                    stripped = cv.strip()
                                    if stripped != cv:
                                        stats.whitespace_trimmed += 1
                                    cond[k] = stripped
                            new_on.append(cond)
                        else:
                            new_on.append(cond)
                    j["on"] = new_on
                new_joins.append(j)
            else:
                new_joins.append(j)
        out["joins"] = new_joins

    # --- 8. Auto-fill group_by when aggregations + select_columns exist but group_by is empty.
    # LLMs frequently forget GROUP BY when they include dimension columns alongside aggregates.
    aggs_raw = out.get("aggregations")
    sel_cols_raw = out.get("select_columns")
    grp_raw = out.get("group_by")
    if (
        isinstance(aggs_raw, list) and len(aggs_raw) > 0
        and isinstance(sel_cols_raw, list) and len(sel_cols_raw) > 0
        and (not grp_raw or len(grp_raw) == 0)
    ):
        logger.debug(
            "Auto-filling group_by from select_columns for aggregate query: %s",
            sel_cols_raw,
        )
        out["group_by"] = list(sel_cols_raw)
        stats.whitespace_trimmed += 1

    # --- 7. Upper-case table refs in filters/aggregations/order_by ---
    for list_key in ("filters", "aggregations", "order_by"):
        items = out.get(list_key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    tv = item.get("table")
                    if isinstance(tv, str):
                        item["table"] = tv.strip().upper()

    if stats.total_normalizations > 0:
        logger.info(
            "Pre-parse normalization applied %d fix(es): "
            "filter_op=%d, agg_fn=%d, sort_dir=%d, whitespace=%d",
            stats.total_normalizations,
            stats.filter_op_normalized,
            stats.agg_fn_normalized,
            stats.sort_dir_normalized,
            stats.whitespace_trimmed,
        )

    return out


# -----------------------------------------------------------------------
# Phase 2 — Post-parse column canonicalization (QueryPlan → QueryPlan)
# -----------------------------------------------------------------------


def canonicalize_columns(
    plan: QueryPlan,
    table_meta: TableMetadata | None,
    *,
    stats: NormalizationStats | None = None,
    table_meta_map: dict[str, TableMetadata] | None = None,
) -> QueryPlan:
    """Resolve column aliases to canonical names using *table_meta*.

    For multi-table plans, *table_meta_map* (table_name → TableMetadata)
    is used to resolve columns with explicit ``table`` qualifiers.
    The *table_meta* parameter is always used as the fallback for columns
    without a table qualifier.

    Returns a new ``QueryPlan`` with canonical column names in:
    * ``select_columns``
    * ``filters[].column``
    * ``aggregations[].column``
    * ``group_by``
    * ``order_by[].column``

    Columns that cannot be resolved are left as-is — the downstream
    ``ValidationService`` will report them as unknown.
    """
    if table_meta is None or plan.needs_clarification:
        return plan

    if stats is None:
        stats = NormalizationStats()

    if table_meta_map is None:
        table_meta_map = {}

    def _fuzzy_resolve(col: str, meta: TableMetadata) -> str | None:
        """Return the closest column name within edit-distance ≈ 2, or None."""
        col_upper = col.upper()
        names = meta.column_names()
        matches = get_close_matches(col_upper, [n.upper() for n in names], n=1, cutoff=0.8)
        if matches:
            idx = next(i for i, n in enumerate(names) if n.upper() == matches[0])
            return names[idx]
        return None

    def _resolve(col: str, table_name: str | None = None) -> str:
        """Resolve a column name, optionally scoped to a specific table."""
        if table_name:
            meta = table_meta_map.get(table_name.upper())
            if meta:
                canonical = meta.resolve_column_name(col)
                if canonical:
                    return canonical

        # For multi-table plans without explicit qualifier, try unique match
        # across all known joined tables before falling back to base table.
        if plan.is_multi_table and table_meta_map:
            hits: list[str] = []
            for meta in table_meta_map.values():
                canonical = meta.resolve_column_name(col)
                if canonical:
                    hits.append(canonical)
            uniq = list(dict.fromkeys(hits))
            if len(uniq) == 1:
                return uniq[0]

        # Fallback to primary table
        canonical = table_meta.resolve_column_name(col)  # type: ignore[union-attr]
        if canonical:
            return canonical

        # Fuzzy fallback — handle LLM hallucinated column names (e.g. AGENCY_ID → AGENT_ID)
        if table_name:
            meta = table_meta_map.get(table_name.upper())
            if meta:
                fuzzy = _fuzzy_resolve(col, meta)
                if fuzzy:
                    logger.info("Fuzzy column match: %r -> %r (table=%s)", col, fuzzy, table_name)
                    return fuzzy
        fuzzy = _fuzzy_resolve(col, table_meta)  # type: ignore[arg-type]
        if fuzzy:
            logger.info("Fuzzy column match: %r -> %r", col, fuzzy)
            return fuzzy

        return col

    mutations: dict[str, Any] = {}

    # -- select_columns --
    new_select: list[str] = []
    for col in plan.select_columns:
        resolved = _resolve(col)
        if resolved != col:
            logger.debug("Column canonicalised: %r -> %r (select)", col, resolved)
            stats.column_canonicalized += 1
        new_select.append(resolved)
    if new_select != list(plan.select_columns):
        mutations["select_columns"] = new_select

    # -- filters --
    from app.domain.query_plan import FilterSpec

    new_filters: list[FilterSpec] = []
    filters_changed = False
    for f in plan.filters:
        resolved = _resolve(f.column, f.table)
        if resolved != f.column:
            logger.debug("Column canonicalised: %r -> %r (filter)", f.column, resolved)
            stats.column_canonicalized += 1
            new_filters.append(
                FilterSpec(column=resolved, op=f.op, value=f.value, table=f.table)
            )
            filters_changed = True
        else:
            new_filters.append(f)
    if filters_changed:
        mutations["filters"] = new_filters

    # -- aggregations --
    from app.domain.query_plan import AggregationSpec, STAR_COLUMN

    new_aggs: list[AggregationSpec] = []
    aggs_changed = False
    for agg in plan.aggregations:
        if agg.column == STAR_COLUMN:
            new_aggs.append(agg)
            continue
        resolved = _resolve(agg.column, agg.table)
        if resolved != agg.column:
            logger.debug(
                "Column canonicalised: %r -> %r (aggregation)", agg.column, resolved
            )
            stats.column_canonicalized += 1
            new_aggs.append(
                AggregationSpec(
                    function=agg.function,
                    column=resolved,
                    alias=agg.alias,
                    table=agg.table,
                )
            )
            aggs_changed = True
        else:
            new_aggs.append(agg)
    if aggs_changed:
        mutations["aggregations"] = new_aggs

    # -- group_by --
    new_group_by: list[str] = []
    for col in plan.group_by:
        resolved = _resolve(col)
        if resolved != col:
            logger.debug("Column canonicalised: %r -> %r (group_by)", col, resolved)
            stats.column_canonicalized += 1
        new_group_by.append(resolved)
    if new_group_by != list(plan.group_by):
        mutations["group_by"] = new_group_by

    # -- order_by --
    from app.domain.query_plan import OrderSpec

    new_order_by: list[OrderSpec] = []
    order_changed = False
    for ob in plan.order_by:
        resolved = _resolve(ob.column, ob.table)
        if resolved != ob.column:
            logger.debug(
                "Column canonicalised: %r -> %r (order_by)", ob.column, resolved
            )
            stats.column_canonicalized += 1
            new_order_by.append(
                OrderSpec(column=resolved, direction=ob.direction, table=ob.table)
            )
            order_changed = True
        else:
            new_order_by.append(ob)
    if order_changed:
        mutations["order_by"] = new_order_by

    if stats.column_canonicalized > 0:
        logger.info(
            "Column canonicalization applied %d fix(es).",
            stats.column_canonicalized,
        )

    if mutations:
        return plan.model_copy(update=mutations)
    return plan
