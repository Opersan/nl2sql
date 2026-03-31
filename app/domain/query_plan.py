"""Query plan domain models.

These models represent a structured, LLM-independent query plan that the
validation service, SQL compiler and executor work with.

Multi-table support (Sprint 5)
==============================
``QueryPlan`` supports optional ``joins`` that describe how the base
table relates to other tables.  All column references (``FilterSpec``,
``AggregationSpec``, ``OrderSpec``, ``select_columns``, ``group_by``)
may carry an optional ``table`` qualifier to disambiguate cross-table
references.  When ``table`` is ``None`` the column belongs to the base
table (backward compatible with single-table plans).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FilterOp(str, Enum):
    """Supported filter operations."""

    EQ = "="
    NEQ = "!="
    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="
    LIKE = "LIKE"
    IN = "IN"
    BETWEEN = "BETWEEN"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"


class SortDirection(str, Enum):
    """Sort direction for ORDER BY clauses."""

    ASC = "ASC"
    DESC = "DESC"


class AggregateFn(str, Enum):
    """Supported aggregate functions."""

    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


class JoinType(str, Enum):
    """Supported SQL JOIN types."""

    INNER = "INNER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

# Non-empty string constraint for use inside typed lists.
NonEmptyStr = Annotated[str, Field(min_length=1)]

# Scalar types that may appear as SQL filter values.
_ScalarValue = str | int | float | bool | date | datetime

# Union of all legal filter-value types.  Replaces the previous ``Any``.
FilterValue = _ScalarValue | list[_ScalarValue] | None

# Sentinel value for COUNT(*) -- the only aggregate that may omit a column.
STAR_COLUMN = "*"


# ---------------------------------------------------------------------------
# Join models (Sprint 5)
# ---------------------------------------------------------------------------

class JoinCondition(BaseModel):
    """A single ON-clause condition for a JOIN."""

    model_config = {"frozen": True}

    left_table: str = Field(..., min_length=1)
    left_column: str = Field(..., min_length=1)
    right_table: str = Field(..., min_length=1)
    right_column: str = Field(..., min_length=1)


class JoinSpec(BaseModel):
    """Describes a JOIN between two tables.

    ``left_table`` is typically the base (or a previously joined) table,
    ``right_table`` is the newly joined table.
    """

    model_config = {"frozen": True}

    left_table: str = Field(..., min_length=1)
    right_table: str = Field(..., min_length=1)
    join_type: JoinType = JoinType.INNER
    on: list[JoinCondition] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Table-qualified column reference (Sprint 5)
# ---------------------------------------------------------------------------

class QualifiedColumn(BaseModel):
    """A column reference optionally qualified with a table name.

    When ``table`` is ``None`` the column belongs to the base table of the
    query plan (backward compatible with Sprint 1-4 single-table plans).
    """

    model_config = {"frozen": True}

    column: str = Field(..., min_length=1)
    table: str | None = Field(default=None, min_length=1)

    def __str__(self) -> str:
        if self.table:
            return f"{self.table}.{self.column}"
        return self.column


# ---------------------------------------------------------------------------
# Spec models
# ---------------------------------------------------------------------------

class FilterSpec(BaseModel):
    """A single WHERE-clause filter."""

    model_config = {"frozen": True}

    column: str = Field(..., min_length=1)
    table: str | None = Field(default=None, min_length=1, description="Source table (None = base table).")
    op: FilterOp
    value: FilterValue = None

    @model_validator(mode="before")
    @classmethod
    def _validate_value(cls, data: Any) -> Any:
        if isinstance(data, dict):
            op_raw = data.get("op")
            value = data.get("value")

            # Normalise enum value for comparison
            if isinstance(op_raw, FilterOp):
                op = op_raw
            else:
                try:
                    op = FilterOp(op_raw)
                except (ValueError, KeyError):
                    return data  # let Pydantic's own enum validation report the error

            if op == FilterOp.BETWEEN:
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise ValueError(
                        "BETWEEN filter requires a list/tuple of exactly 2 values."
                    )
                if isinstance(value, tuple):
                    data["value"] = list(value)
            elif op == FilterOp.IN:
                if not isinstance(value, (list, tuple)) or len(value) == 0:
                    raise ValueError(
                        "IN filter requires a non-empty list/tuple of values."
                    )
                if isinstance(value, tuple):
                    data["value"] = list(value)
            elif op in (FilterOp.IS_NULL, FilterOp.IS_NOT_NULL):
                # value is irrelevant for NULL checks -- force to None.
                data["value"] = None
            else:
                if value is None:
                    raise ValueError(
                        f"Filter operation {op.value} requires a non-null value."
                    )
        return data


class AggregationSpec(BaseModel):
    """A single aggregate expression.

    ``column`` may be the sentinel ``"*"`` **only** when ``function`` is
    ``AggregateFn.COUNT``, which maps to SQL ``COUNT(*)``.  For every other
    aggregate function a real column name is required.
    """

    model_config = {"frozen": True}

    function: AggregateFn
    column: str = Field(..., min_length=1)
    table: str | None = Field(default=None, min_length=1, description="Source table (None = base table).")
    alias: str | None = None

    @model_validator(mode="after")
    def _validate_star_column(self) -> AggregationSpec:
        """Only COUNT may use the ``*`` column."""
        if self.column == STAR_COLUMN and self.function != AggregateFn.COUNT:
            raise ValueError(
                f"column='*' is only valid for COUNT, not {self.function.value}."
            )
        return self

    def effective_alias(self) -> str:
        """Return the alias to use in SELECT -- e.g. ``COUNT_reg_no``."""
        if self.alias:
            return self.alias
        if self.column == STAR_COLUMN:
            return f"{self.function.value}_all"
        return f"{self.function.value}_{self.column}"


class OrderSpec(BaseModel):
    """A single ORDER BY directive."""

    model_config = {"frozen": True}

    column: str = Field(..., min_length=1)
    table: str | None = Field(default=None, min_length=1, description="Source table (None = base table).")
    direction: SortDirection = SortDirection.ASC


class ComputedMeasureSpec(BaseModel):
    """A semantic/computed metric reference.

    ``expression_ref`` points to a canonical expression template defined by
    the semantic layer (e.g. ``PO_LINE_AMOUNT`` => ``quantity * unit_price``).
    """

    model_config = {"frozen": True}

    name: str = Field(..., min_length=1)
    expression_ref: str = Field(..., min_length=1)
    alias: str | None = None
    table: str | None = Field(default=None, min_length=1)


# ---------------------------------------------------------------------------
# QueryPlan
# ---------------------------------------------------------------------------

class QueryPlan(BaseModel):
    """Structured representation of a user query before SQL compilation.

    This model is the *contract* between the LLM planner and the
    deterministic services (validation, compilation, execution).

    Multi-table support (Sprint 5)
    ==============================
    * ``table`` is the **base table** (FROM clause).
    * ``joins`` describes additional tables joined to the base table.
    * Column references may include an optional ``table`` qualifier to
      disambiguate cross-table references (see ``QualifiedColumn``).
    * When ``joins`` is empty the plan is single-table (backward compat).
    """

    intent: str = Field(..., min_length=1, description="Natural-language intent summary.")
    table: str | None = Field(default=None, min_length=1)
    candidate_tables: list[NonEmptyStr] = Field(default_factory=list)
    joins: list[JoinSpec] = Field(default_factory=list)
    select_columns: list[NonEmptyStr] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    aggregations: list[AggregationSpec] = Field(default_factory=list)
    group_by: list[NonEmptyStr] = Field(default_factory=list)
    order_by: list[OrderSpec] = Field(default_factory=list)
    semantic_intent: str | None = Field(default=None, min_length=1)
    root_entity: str | None = Field(default=None, min_length=1)
    dimensions: list[NonEmptyStr] = Field(default_factory=list)
    measures: list[NonEmptyStr] = Field(default_factory=list)
    join_path_id: str | None = Field(default=None, min_length=1)
    computed_measures: list[ComputedMeasureSpec] = Field(default_factory=list)
    limit: int = Field(default=10000, ge=1, le=10000)
    needs_clarification: bool = False
    clarification_message: str | None = Field(default=None, min_length=1)
    clarification_missing_dimensions: list[str] = Field(default_factory=list)

    @property
    def is_multi_table(self) -> bool:
        """Return True when the plan involves JOINs."""
        return len(self.joins) > 0

    @property
    def all_tables(self) -> list[str]:
        """Return all table names referenced in the plan (base + joined)."""
        tables: list[str] = []
        if self.table:
            tables.append(self.table)
        for j in self.joins:
            if j.right_table not in tables:
                tables.append(j.right_table)
        return tables

    @model_validator(mode="after")
    def _check_clarification_consistency(self) -> QueryPlan:
        """Ensure *needs_clarification* and *clarification_message* agree."""
        if self.needs_clarification and not self.clarification_message:
            raise ValueError(
                "needs_clarification=True ise clarification_message boş olamaz."
            )
        if not self.needs_clarification and self.clarification_message is not None:
            raise ValueError(
                "needs_clarification=False iken clarification_message None olmalıdır."
            )
        return self
