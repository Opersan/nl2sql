"""Unit tests for app.core.context_builder.ContextBuilder."""
from __future__ import annotations

import re
from datetime import date
from unittest.mock import patch

import pytest

from app.core.context_builder import ContextBuilder, SystemContext


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def _build() -> SystemContext:
    return ContextBuilder().build()


# ---------------------------------------------------------------------------
# field format tests
# ---------------------------------------------------------------------------


def test_current_date_format():
    ctx = _build()
    assert DATE_RE.match(ctx.current_date), f"Bad date format: {ctx.current_date}"


def test_current_timestamp_format():
    ctx = _build()
    assert TS_RE.match(ctx.current_timestamp), f"Bad timestamp format: {ctx.current_timestamp}"


def test_fiscal_year_start_format():
    ctx = _build()
    assert DATE_RE.match(ctx.fiscal_year_start), f"Bad fiscal_year_start: {ctx.fiscal_year_start}"


def test_all_required_fields_present():
    ctx = _build()
    d = ctx.as_dict()
    required = {
        "current_date",
        "current_timestamp",
        "timezone",
        "fiscal_year_start",
        "default_time_window",
        "week_start",
        "currency",
    }
    assert required <= set(d.keys()), f"Missing fields: {required - set(d.keys())}"


def test_no_null_values():
    ctx = _build()
    for key, value in ctx.as_dict().items():
        assert value is not None and value != "", f"Field {key!r} is empty/null"


# ---------------------------------------------------------------------------
# timezone test
# ---------------------------------------------------------------------------


def test_default_timezone_is_istanbul():
    ctx = _build()
    assert ctx.timezone == "Europe/Istanbul"


def test_timestamp_has_tz_offset():
    ctx = _build()
    # ISO timestamp must carry +HH:MM or -HH:MM
    assert "+" in ctx.current_timestamp or (
        ctx.current_timestamp.count("-") >= 3
    ), "Timestamp has no timezone offset"


# ---------------------------------------------------------------------------
# fiscal year auto-advance logic
# ---------------------------------------------------------------------------


def test_fiscal_year_start_never_in_future():
    ctx = _build()
    today = date.today()
    fy = date.fromisoformat(ctx.fiscal_year_start)
    assert fy <= today, f"fiscal_year_start {fy} is in the future"


def test_fiscal_year_start_within_last_year():
    ctx = _build()
    today = date.today()
    fy = date.fromisoformat(ctx.fiscal_year_start)
    delta = (today - fy).days
    assert delta <= 366, f"fiscal_year_start is more than a year old: {fy}"


# ---------------------------------------------------------------------------
# to_prompt_block
# ---------------------------------------------------------------------------


def test_to_prompt_block_contains_date():
    ctx = _build()
    block = ctx.to_prompt_block()
    assert ctx.current_date in block


def test_to_prompt_block_contains_currency():
    ctx = _build()
    block = ctx.to_prompt_block()
    assert ctx.currency in block


# ---------------------------------------------------------------------------
# determinism (two builds within same second share date/date fields)
# ---------------------------------------------------------------------------


def test_two_builds_share_same_date():
    ctx1 = _build()
    ctx2 = _build()
    assert ctx1.current_date == ctx2.current_date


# ---------------------------------------------------------------------------
# invalid timezone raises ValueError
# ---------------------------------------------------------------------------


def test_invalid_timezone_raises():
    with patch("app.core.context_builder.settings") as mock_settings:
        mock_settings.system_timezone = "Not/AReal/Zone"
        mock_settings.fiscal_year_start = "2026-01-01"
        mock_settings.default_time_window = "last_30_days"
        mock_settings.week_start = "monday"
        mock_settings.system_currency = "TRY"
        with pytest.raises(ValueError, match="Invalid IANA timezone"):
            ContextBuilder().build()


# ---------------------------------------------------------------------------
# as_dict round-trip
# ---------------------------------------------------------------------------


def test_as_dict_round_trip():
    ctx = _build()
    d = ctx.as_dict()
    assert d["timezone"] == ctx.timezone
    assert d["currency"] == ctx.currency
    assert d["week_start"] == ctx.week_start
