"""Tests for the canonical semantic repository and runtime projections.

Validates that:
- the canonical repository merges JSONL foundation data with the legacy
  planner registry overlay
- planner/runtime projections are derived from the same repository
- duplicate semantic fields are resolved deterministically
- planner-facing semantics stay available after the refactor
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.semantic_models import SemanticRegistry
from app.semantic.models import SemanticFoundation
from app.semantic.repository import (
    build_runtime_semantic_foundation,
    build_runtime_semantic_registry,
    load_semantic_repository,
)


def test_repository_merges_foundation_and_legacy_overlay() -> None:
    repository = load_semantic_repository()

    po = repository.get_entity("PO_PURCHASING")
    assert po is not None
    assert po.display_name == "purchase_order"
    assert po.module == "PO"
    assert po.root_table == "PO_HEADERS_ALL"
    assert "PO_LINES_ALL" in po.default_tables
    assert "PO_LINES_ALL" in po.child_tables
    assert po.join_paths
    assert "po_line_quantity" in po.intent_defaults
    assert "sipariş" in po.keywords


def test_runtime_projections_share_the_same_entity_ids() -> None:
    repository = load_semantic_repository()
    planner_registry = build_runtime_semantic_registry(repository)
    foundation = build_runtime_semantic_foundation(repository)

    planner_ids = {entity.entity_id for entity in planner_registry.entities}
    foundation_ids = {entity.entity_id for entity in foundation.entities}

    assert planner_ids == foundation_ids
    assert "PO_PURCHASING" in planner_ids
    assert "HR_EMPLOYEES" in planner_ids


def test_planner_projection_keeps_legacy_policy_and_alias_overlay() -> None:
    repository = load_semantic_repository()
    planner_registry = build_runtime_semantic_registry(repository)

    assert isinstance(planner_registry, SemanticRegistry)
    assert "password" in planner_registry.policy_rules.sensitive_intent_patterns
    assert planner_registry.column_aliases.global_aliases["email"] == "EMAIL"
    scoped = planner_registry.column_aliases.table_scoped["XXBT_PDKS_PER_DETAILS_V"]
    assert scoped["giris_tarihi"] == "ISE_GIRIS_TARIHI"


def test_foundation_projection_preserves_metrics_and_relationships() -> None:
    repository = load_semantic_repository()
    foundation = build_runtime_semantic_foundation(repository)

    assert isinstance(foundation, SemanticFoundation)
    assert any(metric.metric_id == "po_count" for metric in foundation.metrics)
    assert any(edge.source_entity == "PO_PURCHASING" for edge in foundation.relationships)


def test_duplicate_keywords_and_default_tables_are_deduplicated(tmp_path: Path) -> None:
    semantic_dir = tmp_path / "semantic"
    semantic_dir.mkdir()

    (semantic_dir / "glossary.jsonl").write_text(
        json.dumps({
            "raw_term": "sipariş",
            "language": "tr",
            "normalized": "siparis",
            "canonical": "PO_PURCHASING",
            "match_type": "phrase",
            "confidence": 0.95,
            "domain": "PO",
            "source": "curated",
        }) + "\n",
        encoding="utf-8",
    )
    (semantic_dir / "entities.jsonl").write_text(
        json.dumps({
            "entity_id": "PO_PURCHASING",
            "display_name": "purchase_order",
            "module": "PO",
            "root_table": "PO_HEADERS_ALL",
            "default_tables": ["PO_HEADERS_ALL", "PO_LINES_ALL"],
            "keywords": ["siparis", "vendor"],
        }) + "\n",
        encoding="utf-8",
    )
    (semantic_dir / "relationships.jsonl").write_text(
        json.dumps({
            "edge_id": "po-1",
            "source_entity": "PO_PURCHASING",
            "target_entity": "PO_PURCHASING",
            "source_table": "PO_HEADERS_ALL",
            "target_table": "PO_LINES_ALL",
            "join_keys": [{"source_column": "po_header_id", "target_column": "po_header_id"}],
        }) + "\n",
        encoding="utf-8",
    )
    (semantic_dir / "metrics.jsonl").write_text(
        json.dumps({
            "metric_id": "po_count",
            "name": "PO Count",
            "entity_id": "PO_PURCHASING",
            "domain": "PO",
        }) + "\n",
        encoding="utf-8",
    )
    (semantic_dir / "lookups.jsonl").write_text(
        json.dumps({
            "lookup_type": "PO_STATUS",
            "meaning": "Açık",
            "decoded_value": "Open",
            "raw_value": "OPEN",
            "domain": "PO",
        }) + "\n",
        encoding="utf-8",
    )
    (semantic_dir / "flexfields.jsonl").write_text(
        json.dumps({
            "flexfield_id": "PO_FF",
            "name": "PO FF",
            "application": "PO",
            "table": "PO_HEADERS_ALL",
            "segment_column": "ATTRIBUTE1",
            "module": "PO",
        }) + "\n",
        encoding="utf-8",
    )

    legacy_path = tmp_path / "semantic_registry.json"
    legacy_path.write_text(
        json.dumps({
            "entities": [{
                "entity_id": "PO_PURCHASING",
                "root_table": "PO_HEADERS_ALL",
                "child_tables": ["PO_LINES_ALL", "PO_LINES_ALL"],
                "keywords": ["vendor", "siparis", "siparis"],
                "join_paths": [],
                "intent_rules": [],
                "intent_defaults": {},
                "default_intent": "po_generic",
            }],
            "intent_join_paths": {},
        }),
        encoding="utf-8",
    )

    repository = load_semantic_repository(
        semantic_dir=semantic_dir,
        legacy_registry_path=legacy_path,
    )
    po = repository.get_entity("PO_PURCHASING")

    assert po is not None
    assert po.default_tables == ["PO_HEADERS_ALL", "PO_LINES_ALL"]
    assert po.child_tables == ["PO_LINES_ALL"]
    assert po.keywords == ["siparis", "vendor"]


def test_semantic_planning_load_registry_uses_repository_projection() -> None:
    from app.services.semantic_planning import _load_registry

    _load_registry.cache_clear()
    registry = _load_registry()

    assert isinstance(registry, SemanticRegistry)
    assert registry.get_entity("PO_PURCHASING") is not None
    assert registry.get_entity("PO_PURCHASING").join_paths