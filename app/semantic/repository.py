"""Canonical semantic repository and runtime projections.

This module defines the single semantic source-of-truth used at runtime.
It merges the authoring-layer semantic foundation (``data/semantic/*.jsonl``)
with the legacy planner registry overlay (``data/semantic_registry.json``)
into one canonical in-memory repository.

Two runtime projections are produced from that repository:

* ``SemanticRegistry``               — planner / normalization projection
* ``SemanticFoundation``             — query-understanding / retrieval projection

The legacy JSON registry is kept only as a compatibility overlay while
planner-only semantics (intent defaults, column aliases, policy rules,
join-path templates) are migrated into the canonical authoring layer.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.domain.semantic_models import (
    BusinessEntitySemantic,
    CanonicalJoinPath,
    ColumnAliases,
    PolicyRules,
    SemanticRegistry,
)
from app.semantic.loader import load_semantic_foundation
from app.semantic.models import (
    FlexfieldDefinition,
    GlossaryEntry,
    LookupType,
    MetricDefinition,
    RelationshipEdge,
    SemanticEntity,
    SemanticFoundation,
)

logger = get_logger(__name__)

_DEFAULT_SEMANTIC_DIR = Path(__file__).resolve().parents[2] / "data" / "semantic"
_DEFAULT_LEGACY_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "semantic_registry.json"


def _dedup_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        token = item.strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _infer_module(entity_id: str, root_table: str) -> str:
    if entity_id and "_" in entity_id:
        return entity_id.split("_", 1)[0]
    table_upper = root_table.upper()
    if table_upper.startswith("PO_"):
        return "PO"
    if table_upper.startswith("AP_"):
        return "AP"
    if table_upper.startswith("RA_") or table_upper.startswith("HZ_"):
        return "AR"
    if table_upper.startswith("GL_"):
        return "GL"
    if table_upper.startswith("MTL_"):
        return "INV"
    return "cross"


def _infer_display_name(entity_id: str, root_table: str) -> str:
    if entity_id:
        return entity_id.lower()
    return root_table.lower()


class CanonicalSemanticEntity(BaseModel):
    """Merged semantic entity record used by the canonical repository."""

    entity_id: str
    display_name: str
    module: str
    root_table: str
    default_tables: list[str] = Field(default_factory=list)
    likely_filters: list[str] = Field(default_factory=list)
    likely_identifiers: list[str] = Field(default_factory=list)
    filter_signal_keywords: dict[str, list[str]] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    default_intent: str = "generic"
    status_filter_column: str | None = None
    time_column: str | None = None

    child_tables: list[str] = Field(default_factory=list)
    join_paths: list[CanonicalJoinPath] = Field(default_factory=list)
    dimensions: dict[str, str] = Field(default_factory=dict)
    measures: dict[str, str] = Field(default_factory=dict)
    status_semantics: dict[str, str] = Field(default_factory=dict)
    time_semantics: dict[str, str] = Field(default_factory=dict)
    intent_rules: list = Field(default_factory=list)
    intent_defaults: dict[str, object] = Field(default_factory=dict)

    def to_foundation_entity(self) -> SemanticEntity:
        return SemanticEntity(
            entity_id=self.entity_id,
            display_name=self.display_name,
            module=self.module,
            root_table=self.root_table,
            default_tables=list(self.default_tables),
            likely_filters=list(self.likely_filters),
            likely_identifiers=list(self.likely_identifiers),
            filter_signal_keywords={k: list(v) for k, v in self.filter_signal_keywords.items()},
            keywords=list(self.keywords),
            default_intent=self.default_intent,
            status_filter_column=self.status_filter_column,
            time_column=self.time_column,
        )

    def to_business_entity_semantic(self) -> BusinessEntitySemantic:
        return BusinessEntitySemantic(
            entity_id=self.entity_id,
            root_table=self.root_table,
            child_tables=list(self.child_tables),
            join_paths=list(self.join_paths),
            dimensions=dict(self.dimensions),
            measures=dict(self.measures),
            status_semantics=dict(self.status_semantics),
            time_semantics=dict(self.time_semantics),
            keywords=list(self.keywords),
            intent_rules=list(self.intent_rules),
            intent_defaults=dict(self.intent_defaults),
            default_intent=self.default_intent,
        )


class SemanticRepository(BaseModel):
    """Canonical semantic authoring repository.

    This is the authoritative in-memory semantic model.  All runtime
    registries are projections derived from this repository.
    """

    glossary: list[GlossaryEntry] = Field(default_factory=list)
    entities: list[CanonicalSemanticEntity] = Field(default_factory=list)
    relationships: list[RelationshipEdge] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)
    lookups: list[LookupType] = Field(default_factory=list)
    flexfields: list[FlexfieldDefinition] = Field(default_factory=list)
    intent_join_paths: dict[str, str] = Field(default_factory=dict)
    policy_rules: PolicyRules = Field(default_factory=PolicyRules)
    column_aliases: ColumnAliases = Field(default_factory=ColumnAliases)
    compatibility_sources: list[str] = Field(default_factory=list)

    def get_entity(self, entity_id: str) -> CanonicalSemanticEntity | None:
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        return None

    def to_foundation(self) -> SemanticFoundation:
        return SemanticFoundation(
            glossary=list(self.glossary),
            entities=[entity.to_foundation_entity() for entity in self.entities],
            relationships=list(self.relationships),
            metrics=list(self.metrics),
            lookups=list(self.lookups),
            flexfields=list(self.flexfields),
        )

    def to_planner_registry(self) -> SemanticRegistry:
        return SemanticRegistry(
            version="2.0",
            entities=[entity.to_business_entity_semantic() for entity in self.entities],
            intent_join_paths=dict(self.intent_join_paths),
            policy_rules=self.policy_rules,
            column_aliases=self.column_aliases,
        )


def _safe_load_legacy_registry(path: Path) -> SemanticRegistry:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Semantic registry file not found: %s", path)
        return SemanticRegistry()
    except Exception as exc:
        logger.warning("Semantic registry load failed (%s): %s", path, exc)
        return SemanticRegistry()

    try:
        return SemanticRegistry.model_validate(payload)
    except Exception as exc:
        logger.warning("Semantic registry validation failed (%s): %s", path, exc)
        return SemanticRegistry()


def _merge_entity(
    foundation_entity: SemanticEntity | None,
    legacy_entity: BusinessEntitySemantic | None,
) -> CanonicalSemanticEntity:
    if foundation_entity is None and legacy_entity is None:
        raise ValueError("At least one semantic entity source must be provided.")

    entity_id = (
        foundation_entity.entity_id
        if foundation_entity is not None
        else legacy_entity.entity_id
    )
    root_table = (
        foundation_entity.root_table
        if foundation_entity is not None
        else legacy_entity.root_table
    )

    default_tables = list(foundation_entity.default_tables) if foundation_entity is not None else []
    if not default_tables:
        default_tables = [root_table]
    if legacy_entity is not None:
        default_tables.extend([root_table, *legacy_entity.child_tables])
    default_tables = _dedup_preserve_order(default_tables)

    child_tables = list(legacy_entity.child_tables) if legacy_entity is not None else []
    if not child_tables:
        child_tables = [table for table in default_tables if table != root_table]
    child_tables = _dedup_preserve_order(child_tables)

    keywords: list[str] = []
    if foundation_entity is not None:
        keywords.extend(foundation_entity.keywords)
    if legacy_entity is not None:
        keywords.extend(legacy_entity.keywords)

    default_intent = "generic"
    if foundation_entity is not None and foundation_entity.default_intent:
        default_intent = foundation_entity.default_intent
    if default_intent == "generic" and legacy_entity is not None and legacy_entity.default_intent:
        default_intent = legacy_entity.default_intent

    return CanonicalSemanticEntity(
        entity_id=entity_id,
        display_name=(
            foundation_entity.display_name
            if foundation_entity is not None
            else _infer_display_name(entity_id, root_table)
        ),
        module=(
            foundation_entity.module
            if foundation_entity is not None
            else _infer_module(entity_id, root_table)
        ),
        root_table=root_table,
        default_tables=default_tables,
        likely_filters=(list(foundation_entity.likely_filters) if foundation_entity is not None else []),
        likely_identifiers=(list(foundation_entity.likely_identifiers) if foundation_entity is not None else []),
        filter_signal_keywords=(
            {name: list(values) for name, values in foundation_entity.filter_signal_keywords.items()}
            if foundation_entity is not None
            else {}
        ),
        keywords=_dedup_preserve_order(keywords),
        default_intent=default_intent,
        status_filter_column=(
            foundation_entity.status_filter_column if foundation_entity is not None else None
        ),
        time_column=(
            foundation_entity.time_column if foundation_entity is not None else None
        ),
        child_tables=child_tables,
        join_paths=(list(legacy_entity.join_paths) if legacy_entity is not None else []),
        dimensions=(dict(legacy_entity.dimensions) if legacy_entity is not None else {}),
        measures=(dict(legacy_entity.measures) if legacy_entity is not None else {}),
        status_semantics=(dict(legacy_entity.status_semantics) if legacy_entity is not None else {}),
        time_semantics=(dict(legacy_entity.time_semantics) if legacy_entity is not None else {}),
        intent_rules=(list(legacy_entity.intent_rules) if legacy_entity is not None else []),
        intent_defaults=(dict(legacy_entity.intent_defaults) if legacy_entity is not None else {}),
    )


def _merge_entities(
    foundation: SemanticFoundation,
    legacy_registry: SemanticRegistry,
) -> list[CanonicalSemanticEntity]:
    foundation_map = {entity.entity_id: entity for entity in foundation.entities}
    legacy_map = {entity.entity_id: entity for entity in legacy_registry.entities}
    ordered_ids: list[str] = []

    for entity in foundation.entities:
        ordered_ids.append(entity.entity_id)
    for entity in legacy_registry.entities:
        if entity.entity_id not in foundation_map:
            ordered_ids.append(entity.entity_id)

    merged: list[CanonicalSemanticEntity] = []
    for entity_id in ordered_ids:
        merged.append(_merge_entity(foundation_map.get(entity_id), legacy_map.get(entity_id)))
    return merged


def load_semantic_repository(
    *,
    semantic_dir: Path | None = None,
    legacy_registry_path: Path | None = None,
) -> SemanticRepository:
    """Load the canonical semantic repository from authoring sources.

    Authoring sources
    -----------------
    * ``data/semantic/*.jsonl``     — canonical semantic foundation layer
    * ``data/semantic_registry.json`` — backward-compatible planner overlay
    """
    semantic_root = semantic_dir or _DEFAULT_SEMANTIC_DIR
    legacy_path = legacy_registry_path or _DEFAULT_LEGACY_REGISTRY_PATH

    foundation = load_semantic_foundation(semantic_dir=semantic_root)
    legacy_registry = _safe_load_legacy_registry(legacy_path)

    repository = SemanticRepository(
        glossary=list(foundation.glossary),
        entities=_merge_entities(foundation, legacy_registry),
        relationships=list(foundation.relationships),
        metrics=list(foundation.metrics),
        lookups=list(foundation.lookups),
        flexfields=list(foundation.flexfields),
        intent_join_paths=dict(legacy_registry.intent_join_paths),
        policy_rules=legacy_registry.policy_rules,
        column_aliases=legacy_registry.column_aliases,
        compatibility_sources=[str(semantic_root), str(legacy_path)],
    )
    logger.info(
        "[semantic-repository] loaded %d entities, %d glossary entries, %d metrics",
        len(repository.entities),
        len(repository.glossary),
        len(repository.metrics),
    )
    return repository


@lru_cache(maxsize=1)
def get_semantic_repository() -> SemanticRepository:
    """Return the application-level canonical semantic repository singleton."""
    return load_semantic_repository()


def build_runtime_semantic_registry(
    repository: SemanticRepository,
) -> SemanticRegistry:
    """Project the canonical repository into the planner runtime registry."""
    return repository.to_planner_registry()


def build_runtime_semantic_foundation(
    repository: SemanticRepository,
) -> SemanticFoundation:
    """Project the canonical repository into the foundation runtime model."""
    return repository.to_foundation()