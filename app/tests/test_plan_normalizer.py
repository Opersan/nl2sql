"""Tests for plan_normalizer — pre-parse normalization & column canonicalization.

Coverage targets:
* FilterOp alias → canonical mapping (all common LLM variants).
* AggregateFn alias → canonical mapping.
* SortDirection alias → canonical mapping.
* Whitespace trimming in scalar and list fields.
* Table name uppercasing.
* Column canonicalization via TableMetadata alias resolution.
* NormalizationStats accumulation.
* Edge cases: missing fields, empty lists, unknown ops left as-is.
"""

from __future__ import annotations

import pytest

from app.domain.catalog_models import ColumnMetadata, ColumnType, TableMetadata
from app.domain.query_plan import (
    AggregationSpec,
    AggregateFn,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
    SortDirection,
)
from app.services.plan_normalizer import (
    NormalizationStats,
    canonicalize_columns,
    normalize_raw_plan,
)


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


def _employee_table() -> TableMetadata:
    """Minimal EMPLOYEE table with a few aliased columns."""
    return TableMetadata(
        name="XXBT_PDKS_PER_DETAILS_V",
        description="Çalışan tablosu",
        columns=[
            ColumnMetadata(
                name="reg_no",
                data_type=ColumnType.VARCHAR,
                aliases=["sicil_no", "sicil", "registration_number"],
            ),
            ColumnMetadata(
                name="first_name",
                data_type=ColumnType.VARCHAR,
                aliases=["ad", "isim", "name"],
            ),
            ColumnMetadata(
                name="last_name",
                data_type=ColumnType.VARCHAR,
                aliases=["soyad", "soyisim", "surname"],
            ),
            ColumnMetadata(
                name="unit_name",
                data_type=ColumnType.VARCHAR,
                aliases=["birim", "departman", "department", "department_name"],
            ),
            ColumnMetadata(
                name="quit_date",
                data_type=ColumnType.DATE,
                aliases=["ayrilis_tarihi", "termination_date"],
            ),
            ColumnMetadata(
                name="start_date",
                data_type=ColumnType.DATE,
                aliases=["ise_giris_tarihi", "hire_date"],
            ),
            ColumnMetadata(
                name="title",
                data_type=ColumnType.VARCHAR,
                aliases=["unvan", "gorev"],
            ),
        ],
    )


# -----------------------------------------------------------------------
# Phase 1 — normalize_raw_plan
# -----------------------------------------------------------------------


class TestFilterOpNormalization:
    """FilterOp alias → canonical value."""

    @pytest.mark.parametrize(
        "raw_op,expected",
        [
            ("GREATER_THAN_OR_EQUAL", ">="),
            ("GREATER_THAN_OR_EQUALS", ">="),
            ("GTE", ">="),
            ("GE", ">="),
            ("GREATER_THAN", ">"),
            ("GT", ">"),
            ("LESS_THAN_OR_EQUAL", "<="),
            ("LESS_THAN_OR_EQUALS", "<="),
            ("LTE", "<="),
            ("LE", "<="),
            ("LESS_THAN", "<"),
            ("LT", "<"),
            ("EQUALS", "="),
            ("EQUAL", "="),
            ("EQ", "="),
            ("==", "="),
            ("NOT_EQUAL", "!="),
            ("NOT_EQUALS", "!="),
            ("NEQ", "!="),
            ("NE", "!="),
            ("<>", "!="),
            ("IS NULL", "IS_NULL"),
            ("ISNULL", "IS_NULL"),
            ("NULL", "IS_NULL"),
            ("IS NOT NULL", "IS_NOT_NULL"),
            ("ISNOTNULL", "IS_NOT_NULL"),
            ("NOT_NULL", "IS_NOT_NULL"),
            ("NOTNULL", "IS_NOT_NULL"),
        ],
    )
    def test_filter_op_alias_resolved(self, raw_op: str, expected: str) -> None:
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "filters": [{"column": "quit_date", "op": raw_op, "value": None}],
        }
        stats = NormalizationStats()
        result = normalize_raw_plan(raw, stats=stats)

        assert result["filters"][0]["op"] == expected
        assert stats.filter_op_normalized >= 1

    def test_canonical_ops_not_modified(self) -> None:
        """Already-canonical operators must pass through unchanged."""
        for op in ("=", "!=", "<", "<=", ">", ">=", "LIKE", "IN", "BETWEEN", "IS_NULL", "IS_NOT_NULL"):
            raw = {
                "intent": "test",
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "filters": [{"column": "col", "op": op, "value": "x"}],
            }
            stats = NormalizationStats()
            result = normalize_raw_plan(raw, stats=stats)
            assert result["filters"][0]["op"] == op
            assert stats.filter_op_normalized == 0

    def test_unknown_op_left_as_is(self) -> None:
        """An unknown op should be left for Pydantic to reject."""
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "filters": [{"column": "col", "op": "XYZZY", "value": 1}],
        }
        stats = NormalizationStats()
        result = normalize_raw_plan(raw, stats=stats)
        assert result["filters"][0]["op"] == "XYZZY"
        assert stats.filter_op_normalized == 0

    def test_not_equal_null_rewritten_to_is_not_null(self) -> None:
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "filters": [{"column": "item_id", "op": "!=", "value": None}],
        }
        result = normalize_raw_plan(raw)
        assert result["filters"][0]["op"] == "IS_NOT_NULL"
        assert result["filters"][0]["value"] is None

    def test_equal_null_string_rewritten_to_is_null(self) -> None:
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "filters": [{"column": "quit_date", "op": "=", "value": "NULL"}],
        }
        result = normalize_raw_plan(raw)
        assert result["filters"][0]["op"] == "IS_NULL"
        assert result["filters"][0]["value"] is None


class TestAggregateFnNormalization:
    """AggregateFn alias → canonical value."""

    @pytest.mark.parametrize(
        "raw_fn,expected",
        [
            ("AVERAGE", "AVG"),
            ("MINIMUM", "MIN"),
            ("MAXIMUM", "MAX"),
            ("count", "COUNT"),
            ("sum", "SUM"),
        ],
    )
    def test_agg_fn_alias_resolved(self, raw_fn: str, expected: str) -> None:
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "aggregations": [{"function": raw_fn, "column": "*"}],
        }
        stats = NormalizationStats()
        result = normalize_raw_plan(raw, stats=stats)
        assert result["aggregations"][0]["function"] == expected

    def test_canonical_fns_not_modified(self) -> None:
        for fn in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
            raw = {
                "intent": "test",
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "aggregations": [{"function": fn, "column": "*"}],
            }
            stats = NormalizationStats()
            result = normalize_raw_plan(raw, stats=stats)
            assert result["aggregations"][0]["function"] == fn
            assert stats.agg_fn_normalized == 0


class TestSortDirectionNormalization:
    """SortDirection alias → canonical value."""

    @pytest.mark.parametrize(
        "raw_dir,expected",
        [
            ("ASCENDING", "ASC"),
            ("DESCENDING", "DESC"),
            ("A", "ASC"),
            ("D", "DESC"),
            ("asc", "ASC"),
            ("desc", "DESC"),
        ],
    )
    def test_sort_dir_alias_resolved(self, raw_dir: str, expected: str) -> None:
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "order_by": [{"column": "reg_no", "direction": raw_dir}],
        }
        stats = NormalizationStats()
        result = normalize_raw_plan(raw, stats=stats)
        assert result["order_by"][0]["direction"] == expected


class TestWhitespaceNormalization:
    """Whitespace trimming in various fields."""

    def test_intent_trimmed(self) -> None:
        raw = {"intent": "  test intent  ", "table": "XXBT_PDKS_PER_DETAILS_V"}
        stats = NormalizationStats()
        result = normalize_raw_plan(raw, stats=stats)
        assert result["intent"] == "test intent"
        assert stats.whitespace_trimmed >= 1

    def test_select_columns_trimmed(self) -> None:
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "select_columns": [" reg_no ", "first_name"],
        }
        stats = NormalizationStats()
        result = normalize_raw_plan(raw, stats=stats)
        assert result["select_columns"] == ["reg_no", "first_name"]

    def test_filter_column_trimmed(self) -> None:
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "filters": [{"column": " quit_date ", "op": "IS_NULL"}],
        }
        stats = NormalizationStats()
        result = normalize_raw_plan(raw, stats=stats)
        assert result["filters"][0]["column"] == "quit_date"

    def test_filter_op_whitespace_trimmed(self) -> None:
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "filters": [{"column": "col", "op": " = ", "value": 1}],
        }
        stats = NormalizationStats()
        result = normalize_raw_plan(raw, stats=stats)
        assert result["filters"][0]["op"] == "="

    def test_select_columns_object_list_flattened(self) -> None:
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "select_columns": [
                {"column": "reg_no", "table": "XXBT_PDKS_PER_DETAILS_V"},
                {"name": "first_name"},
            ],
        }
        result = normalize_raw_plan(raw)
        assert result["select_columns"] == ["reg_no", "first_name"]


class TestTableNameUppercasing:
    """Table name should be uppercased."""

    def test_lowercase_table_uppercased(self) -> None:
        raw = {"intent": "test", "table": "XXBT_PDKS_PER_DETAILS_V"}
        result = normalize_raw_plan(raw)
        assert result["table"] == "XXBT_PDKS_PER_DETAILS_V"

    def test_mixed_case_table_uppercased(self) -> None:
        raw = {"intent": "test", "table": "XXBT_PDKS_PER_DETAILS_V"}
        result = normalize_raw_plan(raw)
        assert result["table"] == "XXBT_PDKS_PER_DETAILS_V"

    def test_already_upper_unchanged(self) -> None:
        raw = {"intent": "test", "table": "XXBT_PDKS_PER_DETAILS_V"}
        result = normalize_raw_plan(raw)
        assert result["table"] == "XXBT_PDKS_PER_DETAILS_V"

    def test_candidate_tables_uppercased(self) -> None:
        raw = {"intent": "test", "candidate_tables": ["XXBT_PDKS_PER_DETAILS_V", "Department"]}
        result = normalize_raw_plan(raw)
        assert result["candidate_tables"] == ["XXBT_PDKS_PER_DETAILS_V", "DEPARTMENT"]


class TestNormalizationStats:
    """Verify stats accumulation."""

    def test_stats_accumulate(self) -> None:
        raw = {
            "intent": "  test  ",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "filters": [
                {"column": " quit_date ", "op": "GREATER_THAN_OR_EQUAL", "value": "2024-01-01"},
                {"column": "start_date", "op": "LESS_THAN", "value": "2023-01-01"},
            ],
            "aggregations": [{"function": "AVERAGE", "column": "salary"}],
            "order_by": [{"column": "reg_no", "direction": "ASCENDING"}],
        }
        stats = NormalizationStats()
        normalize_raw_plan(raw, stats=stats)

        assert stats.filter_op_normalized == 2  # GTE + LT
        assert stats.agg_fn_normalized == 1  # AVERAGE
        assert stats.sort_dir_normalized == 1  # ASCENDING
        assert stats.whitespace_trimmed >= 2  # intent + filter column
        assert stats.total_normalizations > 0

    def test_as_dict(self) -> None:
        stats = NormalizationStats()
        stats.filter_op_normalized = 2
        stats.column_canonicalized = 3
        d = stats.as_dict()
        assert d["filter_op_normalized"] == 2
        assert d["column_canonicalized"] == 3
        assert d["total_normalizations"] == 2
        assert d["total_canonicalizations"] == 3


class TestNormalizedPlanParses:
    """End-to-end: LLM-like raw dict → normalize → QueryPlan.model_validate."""

    def test_greater_than_or_equal_parses(self) -> None:
        raw = {
            "intent": "Son 1 yılda işe başlayanlar",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "select_columns": ["reg_no", "first_name"],
            "filters": [
                {"column": "start_date", "op": "GREATER_THAN_OR_EQUAL", "value": "2024-01-01"},
                {"column": "quit_date", "op": "IS NULL"},
            ],
            "limit": 100,
            "needs_clarification": False,
        }
        normalised = normalize_raw_plan(raw)
        plan = QueryPlan.model_validate(normalised)

        assert plan.filters[0].op == FilterOp.GTE
        assert plan.filters[1].op == FilterOp.IS_NULL
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"

    def test_multiple_aliases_parse(self) -> None:
        raw = {
            "intent": "Departman bazlı sayım",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "aggregations": [{"function": "AVERAGE", "column": "start_date"}],
            "order_by": [{"column": "reg_no", "direction": "DESCENDING"}],
            "filters": [{"column": "quit_date", "op": "NOT_NULL"}],
            "needs_clarification": False,
        }
        normalised = normalize_raw_plan(raw)
        plan = QueryPlan.model_validate(normalised)

        assert plan.aggregations[0].function == AggregateFn.AVG
        assert plan.order_by[0].direction == SortDirection.DESC
        assert plan.filters[0].op == FilterOp.IS_NOT_NULL

    def test_structured_date_delta_value_parses(self) -> None:
        raw = {
            "intent": "Son 30 gunde girilenler",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "select_columns": ["reg_no"],
            "filters": [
                {
                    "column": "start_date",
                    "op": ">=",
                    "value": {"type": "date_delta", "units": 30, "unit": "day"},
                }
            ],
        }
        normalised = normalize_raw_plan(raw)
        plan = QueryPlan.model_validate(normalised)

        assert plan.filters[0].op == FilterOp.GTE
        assert isinstance(plan.filters[0].value, str)


# -----------------------------------------------------------------------
# Phase 2 — canonicalize_columns
# -----------------------------------------------------------------------


class TestCanonicalizeColumns:
    """Column alias → canonical name resolution via TableMetadata."""

    def _make_plan(self, **overrides: Any) -> QueryPlan:
        defaults = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "select_columns": [],
            "needs_clarification": False,
        }
        defaults.update(overrides)
        return QueryPlan.model_validate(defaults)

    def test_select_columns_canonicalized(self) -> None:
        plan = self._make_plan(
            select_columns=["department_name", "sicil_no", "first_name"],
        )
        table = _employee_table()
        stats = NormalizationStats()
        result = canonicalize_columns(plan, table, stats=stats)

        assert result.select_columns == ["unit_name", "reg_no", "first_name"]
        assert stats.column_canonicalized == 2

    def test_filter_columns_canonicalized(self) -> None:
        plan = self._make_plan(
            filters=[
                FilterSpec(column="termination_date", op=FilterOp.IS_NULL),
            ],
        )
        table = _employee_table()
        stats = NormalizationStats()
        result = canonicalize_columns(plan, table, stats=stats)

        assert result.filters[0].column == "quit_date"
        assert stats.column_canonicalized == 1

    def test_aggregation_columns_canonicalized(self) -> None:
        plan = self._make_plan(
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="departman"),
            ],
        )
        table = _employee_table()
        stats = NormalizationStats()
        result = canonicalize_columns(plan, table, stats=stats)

        assert result.aggregations[0].column == "unit_name"
        assert stats.column_canonicalized == 1

    def test_star_column_not_touched(self) -> None:
        plan = self._make_plan(
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*"),
            ],
        )
        table = _employee_table()
        stats = NormalizationStats()
        result = canonicalize_columns(plan, table, stats=stats)

        assert result.aggregations[0].column == "*"
        assert stats.column_canonicalized == 0

    def test_group_by_canonicalized(self) -> None:
        plan = self._make_plan(group_by=["department_name"])
        table = _employee_table()
        stats = NormalizationStats()
        result = canonicalize_columns(plan, table, stats=stats)

        assert result.group_by == ["unit_name"]
        assert stats.column_canonicalized == 1

    def test_order_by_canonicalized(self) -> None:
        plan = self._make_plan(
            order_by=[OrderSpec(column="sicil_no", direction=SortDirection.ASC)],
        )
        table = _employee_table()
        stats = NormalizationStats()
        result = canonicalize_columns(plan, table, stats=stats)

        assert result.order_by[0].column == "reg_no"
        assert stats.column_canonicalized == 1

    def test_unknown_column_left_as_is(self) -> None:
        """Unresolvable columns should be left for ValidationService to reject."""
        plan = self._make_plan(select_columns=["unknown_col", "reg_no"])
        table = _employee_table()
        stats = NormalizationStats()
        result = canonicalize_columns(plan, table, stats=stats)

        assert result.select_columns == ["unknown_col", "reg_no"]
        assert stats.column_canonicalized == 0

    def test_canonical_names_not_modified(self) -> None:
        plan = self._make_plan(
            select_columns=["reg_no", "first_name", "last_name"],
        )
        table = _employee_table()
        stats = NormalizationStats()
        result = canonicalize_columns(plan, table, stats=stats)

        assert result.select_columns == ["reg_no", "first_name", "last_name"]
        assert stats.column_canonicalized == 0

    def test_clarification_plan_skipped(self) -> None:
        plan = self._make_plan(
            needs_clarification=True,
            clarification_message="Hangi birim?",
        )
        table = _employee_table()
        stats = NormalizationStats()
        result = canonicalize_columns(plan, table, stats=stats)

        assert result is plan  # same object, not modified
        assert stats.column_canonicalized == 0

    def test_none_table_meta_skipped(self) -> None:
        plan = self._make_plan(select_columns=["department_name"])
        stats = NormalizationStats()
        result = canonicalize_columns(plan, None, stats=stats)

        assert result is plan
        assert stats.column_canonicalized == 0

    def test_combined_normalization_and_canonicalization(self) -> None:
        """End-to-end: raw dict with LLM quirks → normalize → parse → canonicalize."""
        raw = {
            "intent": "Birim bazında aktif çalışan sayısı",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "select_columns": ["department_name"],
            "aggregations": [{"function": "COUNT", "column": "*", "alias": "sayi"}],
            "group_by": ["department_name"],
            "filters": [
                {"column": "termination_date", "op": "IS NULL"},
            ],
            "needs_clarification": False,
        }
        # Phase 1: pre-parse normalization
        norm_stats = NormalizationStats()
        normalised = normalize_raw_plan(raw, stats=norm_stats)
        plan = QueryPlan.model_validate(normalised)

        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert plan.filters[0].op == FilterOp.IS_NULL

        # Phase 2: column canonicalization
        table = _employee_table()
        canon_stats = NormalizationStats()
        final = canonicalize_columns(plan, table, stats=canon_stats)

        assert final.select_columns == ["unit_name"]
        assert final.group_by == ["unit_name"]
        assert final.filters[0].column == "quit_date"
        assert canon_stats.column_canonicalized == 3  # department_name x2, termination_date x1
