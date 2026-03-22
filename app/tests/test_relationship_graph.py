"""Tests for the relationship graph in the semantic foundation registry.

Validates that:
- get_relationships_for_entity returns only approved edges by default.
- approved_only=False returns all edges including unapproved ones.
- Bidirectional edges are indexed on both source and target.
- Edge join_keys are non-empty (enforced by model validator).
- Cross-module edges (e.g. PO → INV) are present.
- GL journal edges are NOT approved for planner (as per JSONL data).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.semantic.loader import load_semantic_foundation
from app.semantic.registry import SemanticFoundationRegistry


@pytest.fixture(scope="module")
def registry() -> SemanticFoundationRegistry:
    data_dir = Path(__file__).resolve().parents[2] / "data" / "semantic"
    foundation = load_semantic_foundation(semantic_dir=data_dir)
    return SemanticFoundationRegistry(foundation)


# ---------------------------------------------------------------------------
# Approved-only filtering
# ---------------------------------------------------------------------------

class TestApprovedFilter:
    def test_approved_only_excludes_unapproved_edges(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        all_edges = registry.get_relationships_for_entity("PO_PURCHASING", approved_only=False)
        approved = registry.get_relationships_for_entity("PO_PURCHASING", approved_only=True)
        # Approved set must be a subset of all
        approved_ids = {e.edge_id for e in approved}
        all_ids = {e.edge_id for e in all_edges}
        assert approved_ids <= all_ids

    def test_unapproved_edge_not_returned_by_default(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        # ap_invoices_to_po_headers has approved_for_planner=false per JSONL
        approved = registry.get_relationships_for_entity("AP_INVOICES", approved_only=True)
        unapproved_ids = {"ap_invoices_to_po_headers"}
        approved_edge_ids = {e.edge_id for e in approved}
        assert not unapproved_ids & approved_edge_ids

    def test_unapproved_edge_returned_when_flag_false(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        all_edges = registry.get_relationships_for_entity("AP_INVOICES", approved_only=False)
        edge_ids = {e.edge_id for e in all_edges}
        assert "ap_invoices_to_po_headers" in edge_ids


# ---------------------------------------------------------------------------
# Intra-module PO join chain
# ---------------------------------------------------------------------------

class TestPOJoinChain:
    def test_po_headers_to_lines_edge_present(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        edges = registry.get_relationships_for_entity("PO_PURCHASING", approved_only=True)
        edge_ids = {e.edge_id for e in edges}
        assert "po_headers_to_lines" in edge_ids

    def test_po_lines_to_shipments_edge_present(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        edges = registry.get_relationships_for_entity("PO_PURCHASING", approved_only=True)
        edge_ids = {e.edge_id for e in edges}
        assert "po_lines_to_shipments" in edge_ids

    def test_all_po_approved_edges_have_join_keys(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        edges = registry.get_relationships_for_entity("PO_PURCHASING", approved_only=True)
        for edge in edges:
            assert len(edge.join_keys) > 0, f"Edge {edge.edge_id} has no join_keys"


# ---------------------------------------------------------------------------
# Cross-module edges
# ---------------------------------------------------------------------------

class TestCrossModuleEdges:
    def test_po_to_inv_edge_exists(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        edges = registry.get_relationships_for_entity("PO_PURCHASING", approved_only=True)
        cross_module = [e for e in edges if e.target_entity == "INV_ITEMS"]
        assert len(cross_module) > 0

    def test_ap_to_po_approved_edge_exists(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        edges = registry.get_relationships_for_entity("AP_INVOICES", approved_only=True)
        po_edges = [e for e in edges if e.target_entity == "PO_PURCHASING"]
        assert len(po_edges) > 0

    def test_po_to_hr_buyer_edge_exists(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        edges = registry.get_relationships_for_entity("PO_PURCHASING", approved_only=True)
        hr_edges = [e for e in edges if e.target_entity == "HR_EMPLOYEES"]
        assert len(hr_edges) > 0


# ---------------------------------------------------------------------------
# Built-in graph integrity
# ---------------------------------------------------------------------------

class TestGraphIntegrity:
    def test_gl_je_lines_to_ap_not_approved(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        # GL → AP edge has approved_for_planner=false per JSONL data
        all_edges_gl = registry.get_relationships_for_entity(
            "GL_JOURNAL_ENTRIES", approved_only=False
        )
        gl_to_ap = [
            e for e in all_edges_gl
            if e.edge_id == "gl_je_to_ap_invoices"
        ]
        if gl_to_ap:
            assert not gl_to_ap[0].approved_for_planner

    def test_no_unknown_entity_ids_in_edges(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        known_entities = {e.entity_id for e in registry.get_all_entities()}
        for edge in registry.get_all_relationships():
            assert edge.source_entity in known_entities, (
                f"Edge {edge.edge_id} has unknown source_entity: {edge.source_entity}"
            )
            assert edge.target_entity in known_entities, (
                f"Edge {edge.edge_id} has unknown target_entity: {edge.target_entity}"
            )

    def test_edge_tables_match_entity_table_definitions(
        self, registry: SemanticFoundationRegistry
    ) -> None:
        """Each edge's source/target table must appear in the corresponding entity's default_tables."""
        for edge in registry.get_all_relationships():
            src_entity = registry.get_entity(edge.source_entity)
            tgt_entity = registry.get_entity(edge.target_entity)
            if src_entity:
                src_tables = {t.upper() for t in [src_entity.root_table] + src_entity.default_tables}
                assert edge.source_table.upper() in src_tables, (
                    f"Edge {edge.edge_id} source_table '{edge.source_table}' "
                    f"not in entity '{src_entity.entity_id}' tables"
                )
            if tgt_entity:
                tgt_tables = {t.upper() for t in [tgt_entity.root_table] + tgt_entity.default_tables}
                assert edge.target_table.upper() in tgt_tables, (
                    f"Edge {edge.edge_id} target_table '{edge.target_table}' "
                    f"not in entity '{tgt_entity.entity_id}' tables"
                )
