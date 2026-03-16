"""Semantic planning models.

Defines entity-centric metadata used by planner-side semantic normalization.
The layer is metadata-driven and prompt-agnostic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class CanonicalJoinStep(BaseModel):
    """A single directed step in a canonical join path."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str


class CanonicalJoinPath(BaseModel):
    """Named join path template for an entity."""

    path_id: str
    steps: list[CanonicalJoinStep] = Field(default_factory=list)


class IntentRule(BaseModel):
    """Ordered keyword-based rule for intent inference.

    A rule matches when:
    - ALL keywords in ``all_of`` are found in the (casefolded) message, AND
    - ANY keyword in ``any_of`` is found in the message  (or ``any_of`` is empty).

    At least one of ``all_of`` or ``any_of`` must be non-empty.
    """

    intent: str
    all_of: list[str] = Field(default_factory=list)
    any_of: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_condition(self) -> "IntentRule":
        if not self.all_of and not self.any_of:
            raise ValueError("IntentRule must have at least one condition in all_of or any_of.")
        return self


class RegistryAggregationSpec(BaseModel):
    """Lightweight aggregation spec stored in the semantic registry (strings only)."""

    function: str  # SUM | COUNT | AVG | MIN | MAX
    column: str
    table: str | None = None
    alias: str | None = None


class RegistryFilterSpec(BaseModel):
    """Lightweight filter spec stored in the semantic registry (strings only)."""

    column: str
    table: str | None = None
    op: str  # =  !=  <  <=  >  >=  IS_NULL  IS_NOT_NULL  …
    value: Any = None


class RegistryComputedMeasureSpec(BaseModel):
    """Lightweight computed measure spec stored in the semantic registry."""

    name: str
    expression_ref: str
    alias: str | None = None
    table: str | None = None


class IntentDefaults(BaseModel):
    """Canonical plan-shape overrides for a specific semantic intent.

    ``stable=True`` suppresses unstable clarification-only LLM responses for
    this intent and replaces them with the canonical plan shape.

    ``select_columns`` is applied only when the normalised plan has no
    aggregations (i.e. listing queries).  For aggregation queries the
    SELECT shape is already determined by ``group_by`` + ``aggregations``.
    """

    stable: bool = False
    select_columns: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregations: list[RegistryAggregationSpec] = Field(default_factory=list)
    filters: list[RegistryFilterSpec] = Field(default_factory=list)
    computed_measures: list[RegistryComputedMeasureSpec] = Field(default_factory=list)


class BusinessEntitySemantic(BaseModel):
    """Entity-level semantic registry record."""

    entity_id: str
    root_table: str
    child_tables: list[str] = Field(default_factory=list)
    join_paths: list[CanonicalJoinPath] = Field(default_factory=list)
    dimensions: dict[str, str] = Field(default_factory=dict)
    measures: dict[str, str] = Field(default_factory=dict)
    status_semantics: dict[str, str] = Field(default_factory=dict)
    time_semantics: dict[str, str] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    intent_rules: list[IntentRule] = Field(default_factory=list)
    intent_defaults: dict[str, IntentDefaults] = Field(default_factory=dict)
    default_intent: str = "generic"

    def get_join_path(self, path_id: str) -> CanonicalJoinPath | None:
        for p in self.join_paths:
            if p.path_id == path_id:
                return p
        return None


class SemanticRegistry(BaseModel):
    """Top-level semantic registry loaded from external metadata."""

    version: str = "1.0"
    entities: list[BusinessEntitySemantic] = Field(default_factory=list)
    intent_join_paths: dict[str, str] = Field(default_factory=dict)

    def get_entity(self, entity_id: str) -> BusinessEntitySemantic | None:
        for e in self.entities:
            if e.entity_id == entity_id:
                return e
        return None
