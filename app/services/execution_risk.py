from __future__ import annotations

from datetime import date
import hashlib
import re
from typing import Any

from app.domain.catalog_models import TableMetadata
from app.domain.execution_models import CompiledQuery
from app.domain.query_plan import FilterOp, QueryPlan

_DATE_LITERAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RELATIVE_EXPR_RE = re.compile(r"^(?:__EXPR__)?(?:TRUNC\(\s*SYSDATE\s*\)|SYSDATE|CURRENT_DATE)\s*-\s*\d+$", re.IGNORECASE)


_BLOCKING_FLAG_TO_REASON: dict[str, str] = {
    "oracle_date_type_error": "precheck_date_literal_invalid",
    "invalid_filter_value": "precheck_invalid_filter_value",
}


def _is_date_column(meta: TableMetadata, column_name: str) -> bool:
    col = meta.get_column(column_name)
    if col is None:
        return False
    return col.data_type.value in {"DATE", "TIMESTAMP"}


def _is_status_column(column_name: str) -> bool:
    c = column_name.lower()
    return "status" in c or "durum" in c


def assess_pre_execution_risk(plan: QueryPlan, table: TableMetadata) -> dict[str, Any]:
    flags: list[str] = []

    for f in plan.filters:
        value = f.value

        if f.op in {FilterOp.EQ, FilterOp.NEQ, FilterOp.GT, FilterOp.GTE, FilterOp.LT, FilterOp.LTE, FilterOp.LIKE}:
            if isinstance(value, str) and not value.strip():
                flags.append("invalid_filter_value")

        if f.op == FilterOp.BETWEEN and isinstance(value, list) and len(value) == 2:
            left, right = value[0], value[1]
            if isinstance(left, (int, float)) and isinstance(right, (int, float)) and left > right:
                flags.append("invalid_filter_value")

        if _is_date_column(table, f.column):
            if isinstance(value, str):
                v = value.strip()
                if v and not _DATE_LITERAL_RE.match(v) and not _RELATIVE_EXPR_RE.match(v):
                    flags.append("oracle_date_type_error")
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        v = item.strip()
                        if v and not _DATE_LITERAL_RE.match(v):
                            flags.append("oracle_date_type_error")
                            break

        if _is_status_column(f.column) and f.op == FilterOp.EQ and isinstance(value, str):
            if value.strip().lower() in {"pending", "bekleyen", "açık", "acik"}:
                flags.append("ambiguous_business_status")

    if not plan.filters and not plan.aggregations and plan.limit >= 100:
        flags.append("high_risk_but_executable")

    # deterministic order for trace stability
    uniq_flags = sorted(set(flags))
    blocking = [f for f in uniq_flags if f in _BLOCKING_FLAG_TO_REASON]

    should_execute = len(blocking) == 0
    return {
        "pre_execution_risk_flags": uniq_flags,
        "blocking_risk_flags": blocking,
        "execution_guard_reason": _BLOCKING_FLAG_TO_REASON.get(blocking[0]) if blocking else None,
        "execution_skipped_reason": _BLOCKING_FLAG_TO_REASON.get(blocking[0]) if blocking else None,
        "should_execute": should_execute,
    }


def sql_fingerprint(sql: str) -> str:
    normalized = " ".join(sql.split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def bind_summary(compiled: CompiledQuery) -> dict[str, Any]:
    values = list((compiled.params or {}).values())
    type_counts: dict[str, int] = {}
    for v in values:
        if isinstance(v, bool):
            k = "bool"
        elif isinstance(v, int):
            k = "int"
        elif isinstance(v, float):
            k = "float"
        elif isinstance(v, str):
            k = "str"
        elif isinstance(v, date):
            k = "date"
        elif isinstance(v, list):
            k = "list"
        else:
            k = "other"
        type_counts[k] = type_counts.get(k, 0) + 1

    return {
        "bind_count": len(values),
        "bind_type_counts": type_counts,
    }
