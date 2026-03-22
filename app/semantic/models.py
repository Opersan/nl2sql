"""Pydantic models for the semantic foundation data layer.

Six layers:
  1. GlossaryEntry       — term → canonical (entity_id or filter/metric alias)
  2. SemanticEntity      — business entity metadata for QU + retrieval
  3. RelationshipEdge    — typed join edges between entities/tables
  4. MetricDefinition    — named aggregation metrics with aliases
  5. LookupType          — EBS lookup code → human-readable meaning
  6. FlexfieldDefinition — descriptive/key flexfield segment metadata

All models are strict: unknown fields raise ValidationError so data
files are validated at load time rather than silently ignored.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class GlossaryEntry(BaseModel, extra="forbid"):
    """A single term-to-semantic-canonical mapping."""

    raw_term: str
    language: Literal["tr", "en"]
    normalized: str  # diacritic-stripped + casefold version of raw_term
    canonical: str   # entity_id  OR  "filter:<code>"  OR  "metric:<id>"
    match_type: Literal["exact", "phrase", "filter_alias", "metric_alias"]
    confidence: float = Field(ge=0.0, le=1.0)
    domain: str      # "HR" | "PO" | "AP" | "AR" | "GL" | "INV" | "cross"
    source: Literal["curated", "inferred", "etrm_curated"]


class SemanticEntity(BaseModel, extra="forbid"):
    """Lightweight entity record for query-understanding and retrieval seeding.

    Complements ``BusinessEntitySemantic`` in ``app.domain.semantic_models``
    which carries the full intent_rules + intent_defaults used by the
    planner normalization pass.
    """

    entity_id: str
    display_name: str          # shorthand for detected_entities (e.g. "employee")
    module: str                # "HR" | "PO" | "AP" | "AR" | "GL" | "INV"
    root_table: str
    default_tables: list[str] = Field(default_factory=list)

    # For intent_guard filter coverage analysis
    likely_filters: list[str] = Field(default_factory=list)
    likely_identifiers: list[str] = Field(default_factory=list)

    # signal_code → list of normalized keywords that indicate this signal
    filter_signal_keywords: dict[str, list[str]] = Field(default_factory=dict)

    # Normalized terms used for phrase-level entity scoring
    keywords: list[str] = Field(default_factory=list)

    default_intent: str = "generic"
    status_filter_column: str | None = None
    time_column: str | None = None


class JoinKeyPair(BaseModel, extra="forbid"):
    """A single column-pair in a relationship join condition."""

    source_column: str
    target_column: str


class RelationshipEdge(BaseModel, extra="forbid"):
    """A directed (or bidirectional) join edge between two entity tables."""

    edge_id: str
    source_entity: str
    target_entity: str
    source_table: str
    target_table: str
    join_keys: list[JoinKeyPair] = Field(default_factory=list)
    join_direction: Literal["source_to_target", "target_to_source", "bidirectional"] = (
        "source_to_target"
    )
    trust_level: Literal["high", "medium", "low"] = "medium"
    source_of_truth: Literal["etrm_curated", "curated", "inferred"] = "curated"
    notes: str | None = None
    approved_for_planner: bool = True
    multi_org_aware: bool = False

    @model_validator(mode="after")
    def _require_join_keys(self) -> "RelationshipEdge":
        if not self.join_keys:
            raise ValueError(
                f"RelationshipEdge '{self.edge_id}' must have at least one join_key pair."
            )
        return self


class MetricDefinition(BaseModel, extra="forbid"):
    """A named aggregation metric with resolved aliases."""

    metric_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    entity_id: str
    expression: str | None = None
    table: str | None = None
    column: str | None = None
    function: str | None = None
    description: str | None = None
    domain: str


class LookupType(BaseModel, extra="forbid"):
    """An EBS lookup code with its Turkish/English meaning."""

    lookup_type: str
    meaning: str       # Turkish human-readable label
    decoded_value: str # English label
    raw_value: str     # actual DB value
    domain: str
    table_ref: str | None = None
    notes: str | None = None


class FlexfieldDefinition(BaseModel, extra="forbid"):
    """Descriptive or key flexfield segment definition."""

    flexfield_id: str
    name: str
    application: str
    table: str
    segment_column: str
    value_set: str | None = None
    description: str | None = None
    module: str
    notes: str | None = None


class SemanticFoundation(BaseModel):
    """Container for the full 6-layer semantic foundation."""

    glossary: list[GlossaryEntry] = Field(default_factory=list)
    entities: list[SemanticEntity] = Field(default_factory=list)
    relationships: list[RelationshipEdge] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)
    lookups: list[LookupType] = Field(default_factory=list)
    flexfields: list[FlexfieldDefinition] = Field(default_factory=list)
