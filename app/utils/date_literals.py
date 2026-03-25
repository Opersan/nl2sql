from __future__ import annotations

from datetime import date, datetime, timedelta
import re

_COLUMN_REF_PREFIX = "__COLUMN_REF__"
_EXPR_PREFIX = "__EXPR__"

_RELATIVE_DATE_EXPR_RE = re.compile(
    r"^(?:TRUNC\(\s*SYSDATE\s*\)|SYSDATE|CURRENT_DATE)\s*([+-])\s*(\d+)\s*$",
    re.IGNORECASE,
)
_NATURAL_RELATIVE_DATE_EXPR_RE = re.compile(
    r"^(?:NOW|TODAY|CURRENT_DATE|SYSDATE)\s*([+-])\s*(\d+)\s*(DAY|DAYS|WEEK|WEEKS)$",
    re.IGNORECASE,
)
_TRUNC_SYSDATE_RE = re.compile(r"^TRUNC\(\s*SYSDATE\s*\)$", re.IGNORECASE)
_TRUNC_FMT_RE = re.compile(
    r"^TRUNC\(\s*SYSDATE\s*,\s*'(?P<fmt>IW|MM|YYYY)'\s*\)$",
    re.IGNORECASE,
)

# Turkish-first runtime normalization for common planner/user date drift.
_YMD_SEPARATED_RE = re.compile(r"^(\d{4})[-/.](\d{2})[-/.](\d{2})$")
_DMY_SEPARATED_RE = re.compile(r"^(\d{2})[-/.](\d{2})[-/.](\d{4})$")


def _week_start(anchor: date) -> date:
    return anchor - timedelta(days=anchor.weekday())


def _resolve_relative_date_token(raw: str, *, anchor: date) -> date | None:
    token = raw.strip().upper()
    week_start = _week_start(anchor)
    sentinels: dict[str, date] = {
        "__RELATIVE_DATE_LAST_30_DAYS__": anchor - timedelta(days=30),
        "__RELATIVE_DATE_LAST_6_MONTHS__": anchor - timedelta(days=183),
        "__RELATIVE_DATE_LAST_1_YEAR__": anchor - timedelta(days=365),
        "__RELATIVE_DATE_1_YEAR__": anchor - timedelta(days=365),
        "__RELATIVE_DATE_10_YEARS_AGO__": anchor - timedelta(days=3650),
        "TODAY": anchor,
        "NOW": anchor,
        "CURRENT_DATE": anchor,
        "SYSDATE": anchor,
        "TRUNC(SYSDATE)": anchor,
        "CURRENT_WEEK_START": week_start,
        "THIS_WEEK_START": week_start,
        "THIS_WEEK_END": week_start + timedelta(days=7),
        "CURRENT_WEEK_END": week_start + timedelta(days=7),
        "CURRENT_MONTH_START": anchor.replace(day=1),
        "CURRENT_YEAR_START": anchor.replace(month=1, day=1),
        "BU_HAFTA": week_start,
        "THIS_WEEK": week_start,
        "CURRENT_WEEK": week_start,
    }
    return sentinels.get(token)


def _resolve_relative_date_expr(raw: str, *, anchor: date) -> date | None:
    token = raw.strip()
    if _TRUNC_SYSDATE_RE.match(token):
        return anchor

    trunc_fmt = _TRUNC_FMT_RE.match(token)
    if trunc_fmt:
        fmt = trunc_fmt.group("fmt").upper()
        if fmt == "IW":
            return _week_start(anchor)
        if fmt == "MM":
            return anchor.replace(day=1)
        if fmt == "YYYY":
            return anchor.replace(month=1, day=1)

    relative = _RELATIVE_DATE_EXPR_RE.match(token)
    if relative:
        operator = relative.group(1)
        days = int(relative.group(2))
        if operator == "+":
            return anchor + timedelta(days=days)
        return anchor - timedelta(days=days)

    natural_relative = _NATURAL_RELATIVE_DATE_EXPR_RE.match(token)
    if natural_relative:
        operator = natural_relative.group(1)
        amount = int(natural_relative.group(2))
        unit = natural_relative.group(3).upper()
        delta_days = amount * 7 if unit.startswith("WEEK") else amount
        if operator == "+":
            return anchor + timedelta(days=delta_days)
        return anchor - timedelta(days=delta_days)

    return None


def _parse_literal_date(raw: str) -> date | None:
    token = raw.strip()
    if not token:
        return None

    normalized = token.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass

    ymd = _YMD_SEPARATED_RE.match(token)
    if ymd:
        try:
            return date(int(ymd.group(1)), int(ymd.group(2)), int(ymd.group(3)))
        except ValueError:
            return None

    dmy = _DMY_SEPARATED_RE.match(token)
    if dmy:
        try:
            return date(int(dmy.group(3)), int(dmy.group(2)), int(dmy.group(1)))
        except ValueError:
            return None

    return None


def is_supported_oracle_date_expression(raw: str) -> bool:
    anchor = date.today()
    return _resolve_relative_date_expr(raw, anchor=anchor) is not None


def coerce_runtime_date_value(
    value: object,
    *,
    anchor: date | None = None,
) -> tuple[object, bool]:
    reference_date = anchor or date.today()

    if isinstance(value, datetime):
        return value.date(), True
    if isinstance(value, date):
        return value, True
    if not isinstance(value, str):
        return value, False

    raw = value.strip()
    if not raw:
        return value, False
    if raw.startswith(_COLUMN_REF_PREFIX):
        return value, True
    if raw.startswith(_EXPR_PREFIX):
        expr = raw[len(_EXPR_PREFIX):].strip()
        return value, is_supported_oracle_date_expression(expr)

    relative_token = _resolve_relative_date_token(raw, anchor=reference_date)
    if relative_token is not None:
        return relative_token, True

    relative_expr = _resolve_relative_date_expr(raw, anchor=reference_date)
    if relative_expr is not None:
        return relative_expr, True

    literal = _parse_literal_date(raw)
    if literal is not None:
        return literal, True

    return value, False