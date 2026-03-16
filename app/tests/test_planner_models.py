"""Tests for QueryPlan and related domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
    SortDirection,
)


# ---------------------------------------------------------------------------
# QueryPlan defaults
# ---------------------------------------------------------------------------


class TestQueryPlanDefaults:
    def test_default_limit_is_100(self) -> None:
        plan = QueryPlan(intent="test query", table="XXBT_PDKS_PER_DETAILS_V", select_columns=["reg_no"])
        assert plan.limit == 100

    def test_limit_clamps_to_max_1000(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan(intent="test", table="t", select_columns=["a"], limit=1001)

    def test_limit_min_1(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan(intent="test", table="t", select_columns=["a"], limit=0)

    def test_empty_intent_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan(intent="", table="t", select_columns=["a"])

    def test_default_lists_are_empty(self) -> None:
        plan = QueryPlan(intent="x", table="t", select_columns=["a"])
        assert plan.filters == []
        assert plan.aggregations == []
        assert plan.group_by == []
        assert plan.order_by == []
        assert plan.candidate_tables == []

    def test_empty_table_string_rejected(self) -> None:
        """table="" should be rejected (use None instead)."""
        with pytest.raises(ValidationError):
            QueryPlan(intent="x", table="", select_columns=["a"])


# ---------------------------------------------------------------------------
# FilterSpec validation
# ---------------------------------------------------------------------------


class TestFilterSpec:
    def test_eq_requires_value(self) -> None:
        with pytest.raises(ValidationError):
            FilterSpec(column="x", op=FilterOp.EQ, value=None)

    def test_between_requires_two_values(self) -> None:
        with pytest.raises(ValidationError):
            FilterSpec(column="x", op=FilterOp.BETWEEN, value=[1])

    def test_between_accepts_two_values(self) -> None:
        f = FilterSpec(column="x", op=FilterOp.BETWEEN, value=[1, 10])
        assert f.value == [1, 10]

    def test_in_requires_non_empty_list(self) -> None:
        with pytest.raises(ValidationError):
            FilterSpec(column="x", op=FilterOp.IN, value=[])

    def test_in_accepts_list(self) -> None:
        f = FilterSpec(column="x", op=FilterOp.IN, value=["a", "b"])
        assert f.value == ["a", "b"]

    def test_is_null_ignores_value(self) -> None:
        f = FilterSpec(column="x", op=FilterOp.IS_NULL, value="anything")
        assert f.value is None

    def test_is_not_null_ignores_value(self) -> None:
        f = FilterSpec(column="x", op=FilterOp.IS_NOT_NULL)
        assert f.value is None

    def test_like_requires_value(self) -> None:
        f = FilterSpec(column="x", op=FilterOp.LIKE, value="%test%")
        assert f.value == "%test%"

    def test_empty_column_rejected(self) -> None:
        """FilterSpec with empty column string should be rejected."""
        with pytest.raises(ValidationError):
            FilterSpec(column="", op=FilterOp.EQ, value="x")


# ---------------------------------------------------------------------------
# Enum coverage
# ---------------------------------------------------------------------------


class TestEnums:
    def test_filter_op_values(self) -> None:
        assert FilterOp.EQ.value == "="
        assert FilterOp.NEQ.value == "!="
        assert FilterOp.BETWEEN.value == "BETWEEN"

    def test_sort_direction(self) -> None:
        assert SortDirection.ASC.value == "ASC"
        assert SortDirection.DESC.value == "DESC"

    def test_aggregate_fn(self) -> None:
        assert AggregateFn.COUNT.value == "COUNT"
        assert AggregateFn.AVG.value == "AVG"

    def test_invalid_filter_op_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FilterSpec(column="x", op="INVALID", value=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AggregationSpec
# ---------------------------------------------------------------------------


class TestAggregationSpec:
    def test_effective_alias_default(self) -> None:
        agg = AggregationSpec(function=AggregateFn.COUNT, column="reg_no")
        assert agg.effective_alias() == "COUNT_reg_no"

    def test_effective_alias_custom(self) -> None:
        agg = AggregationSpec(function=AggregateFn.SUM, column="salary", alias="total_salary")
        assert agg.effective_alias() == "total_salary"


# ---------------------------------------------------------------------------
# OrderSpec
# ---------------------------------------------------------------------------


class TestOrderSpec:
    def test_default_direction_asc(self) -> None:
        o = OrderSpec(column="reg_no")
        assert o.direction == SortDirection.ASC

    def test_empty_column_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrderSpec(column="")


# ---------------------------------------------------------------------------
# Non-empty list elements
# ---------------------------------------------------------------------------


class TestNonEmptyListElements:
    def test_empty_candidate_table_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan(intent="x", candidate_tables=[""], select_columns=["a"])

    def test_empty_select_column_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan(intent="x", table="t", select_columns=["a", ""])

    def test_empty_group_by_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan(
                intent="x", table="t", select_columns=["a"], group_by=[""]
            )


# ---------------------------------------------------------------------------
# Clarification consistency
# ---------------------------------------------------------------------------


class TestClarificationConsistency:
    def test_needs_clarification_without_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan(
                intent="x", table="t", select_columns=["a"],
                needs_clarification=True,
            )

    def test_message_without_flag_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan(
                intent="x", table="t", select_columns=["a"],
                clarification_message="Hangi birim?",
            )

    def test_valid_clarification_pair(self) -> None:
        plan = QueryPlan(
            intent="x", table="t", select_columns=["a"],
            needs_clarification=True,
            clarification_message="Hangi birim?",
        )
        assert plan.needs_clarification is True
        assert plan.clarification_message == "Hangi birim?"

    def test_empty_clarification_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan(
                intent="x", table="t", select_columns=["a"],
                needs_clarification=True,
                clarification_message="",
            )


# ---------------------------------------------------------------------------
# COUNT(*) semantics
# ---------------------------------------------------------------------------


class TestCountStar:
    def test_count_star_allowed(self) -> None:
        agg = AggregationSpec(function=AggregateFn.COUNT, column="*")
        assert agg.effective_alias() == "COUNT_all"

    def test_sum_star_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AggregationSpec(function=AggregateFn.SUM, column="*")

    def test_avg_star_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AggregationSpec(function=AggregateFn.AVG, column="*")

    def test_min_star_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AggregationSpec(function=AggregateFn.MIN, column="*")

    def test_max_star_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AggregationSpec(function=AggregateFn.MAX, column="*")

    def test_count_star_with_custom_alias(self) -> None:
        agg = AggregationSpec(
            function=AggregateFn.COUNT, column="*", alias="total"
        )
        assert agg.effective_alias() == "total"
