"""Unit tests for the semantic context retrieval service.

Covers:
- Deterministic entity matching (by table, by entity_id)
- Glossary phrase/term matching
- Metric matching (by metric_id, by alias, by entity cascade)
- Relationship scoping (only matching retrieved tables, approved_only)
- Lookup matching via filter column_hint and lookup_hints
- Flexfield matching by retrieved table names
- Budget trimming (progressive reduction)
- Prompt block rendering format
- Trace structure completeness
- Empty / no-table edge cases
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.domain.catalog_models import CatalogSnapshot, TableMetadata
from app.semantic.models import (
    FlexfieldDefinition,
    GlossaryEntry,
    JoinKeyPair,
    LookupType,
    MetricDefinition,
    RelationshipEdge,
    SemanticEntity,
    SemanticFoundation,
)
from app.semantic.registry import SemanticFoundationRegistry
from app.services.semantic_context_service import (
    SemanticContextBundle,
    SemanticRetrievalTrace,
    build_semantic_grounding_block,
    retrieve_semantic_context,
)


# ---------------------------------------------------------------------------
# Fixtures: tiny semantic foundation
# ---------------------------------------------------------------------------

def _make_foundation() -> SemanticFoundation:
    return SemanticFoundation(
        glossary=[
            GlossaryEntry(
                raw_term="çalışan",
                language="tr",
                normalized="calisan",
                canonical="HR_EMPLOYEES",
                match_type="exact",
                confidence=1.0,
                domain="HR",
                source="curated",
            ),
            GlossaryEntry(
                raw_term="satınalma siparişi",
                language="tr",
                normalized="satinalma siparisi",
                canonical="PO_HEADERS",
                match_type="phrase",
                confidence=1.0,
                domain="PO",
                source="curated",
            ),
        ],
        entities=[
            SemanticEntity(
                entity_id="HR_EMPLOYEES",
                display_name="Çalışan",
                module="HR",
                root_table="XXHR_EMPLOYEES_V",
                default_tables=["XXHR_EMPLOYEES_V"],
                likely_filters=["LOCATION_ADI", "UNVAN"],
                likely_identifiers=["EMPLOYEE_NUMBER"],
                keywords=["calisan", "personel"],
            ),
            SemanticEntity(
                entity_id="PO_HEADERS",
                display_name="Satınalma Siparişi",
                module="PO",
                root_table="PO_HEADERS_ALL",
                default_tables=["PO_HEADERS_ALL", "PO_LINES_ALL"],
                likely_filters=["AUTHORIZATION_STATUS"],
                likely_identifiers=["SEGMENT1"],
                keywords=["siparis", "po"],
            ),
        ],
        relationships=[
            RelationshipEdge(
                edge_id="PO_HEADER_TO_LINE",
                source_entity="PO_HEADERS",
                target_entity="PO_LINES",
                source_table="PO_HEADERS_ALL",
                target_table="PO_LINES_ALL",
                join_keys=[
                    JoinKeyPair(
                        source_column="PO_HEADER_ID",
                        target_column="PO_HEADER_ID",
                    ),
                ],
                join_direction="source_to_target",
                approved_for_planner=True,
            ),
            RelationshipEdge(
                edge_id="HR_TO_PO_UNAPPROVED",
                source_entity="HR_EMPLOYEES",
                target_entity="PO_HEADERS",
                source_table="XXHR_EMPLOYEES_V",
                target_table="PO_HEADERS_ALL",
                join_keys=[
                    JoinKeyPair(
                        source_column="PERSON_ID",
                        target_column="AGENT_ID",
                    ),
                ],
                join_direction="source_to_target",
                approved_for_planner=False,
            ),
        ],
        metrics=[
            MetricDefinition(
                metric_id="emp_count",
                name="Çalışan Sayısı",
                aliases=["calisan sayisi", "personel sayisi"],
                entity_id="HR_EMPLOYEES",
                expression="COUNT(*)",
                table="XXHR_EMPLOYEES_V",
                domain="HR",
                description="Toplam çalışan sayısı",
            ),
            MetricDefinition(
                metric_id="po_header_count",
                name="Sipariş Sayısı",
                aliases=["siparis sayisi"],
                entity_id="PO_HEADERS",
                expression="COUNT(*)",
                table="PO_HEADERS_ALL",
                domain="PO",
            ),
        ],
        lookups=[
            LookupType(
                lookup_type="PO_DOCUMENT_TYPE",
                meaning="Standart Satınalma Siparişi",
                decoded_value="Standard Purchase Order",
                raw_value="STANDARD",
                domain="PO",
                table_ref="PO_HEADERS_ALL.TYPE_LOOKUP_CODE",
            ),
        ],
        flexfields=[
            FlexfieldDefinition(
                flexfield_id="PO_HEADERS_DFF",
                name="PO Headers DFF",
                application="PO",
                table="PO_HEADERS_ALL",
                segment_column="ATTRIBUTE1",
                module="PO",
            ),
            FlexfieldDefinition(
                flexfield_id="HR_ASSIGNMENT_DFF",
                name="HR Assignment DFF",
                application="HR",
                table="XXHR_EMPLOYEES_V",
                segment_column="ATTRIBUTE2",
                module="HR",
            ),
        ],
    )


@dataclass
class FakeQueryUnderstanding:
    """Minimal fake for QueryUnderstanding with fields used by retrieval."""

    resolved_entities: list[str] = field(default_factory=list)
    detected_metrics: list[str] = field(default_factory=list)
    extracted_filters: list[dict[str, str]] = field(default_factory=list)
    extracted_aggregation_hints: list[str] = field(default_factory=list)
    lookup_hints: list[dict[str, str]] = field(default_factory=list)
    normalized_question: str = ""


def _snapshot(*table_names: str) -> CatalogSnapshot:
    tables = [TableMetadata(name=t, columns=[]) for t in table_names]
    return CatalogSnapshot(tables=tables, relationships=[])


@pytest.fixture()
def _patch_registry(monkeypatch: pytest.MonkeyPatch):
    """Patch get_registry to return a test foundation."""
    registry = SemanticFoundationRegistry(_make_foundation())

    import app.services.semantic_context_service as svc

    def _fake_get_registry():
        return registry

    monkeypatch.setattr(
        "app.semantic.registry.get_registry", _fake_get_registry,
    )
    return registry


# ---------------------------------------------------------------------------
# Entity matching
# ---------------------------------------------------------------------------


class TestEntityMatching:
    def test_match_by_resolved_entity_id(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["HR_EMPLOYEES"])
        snap = _snapshot("XXHR_EMPLOYEES_V")
        bundle, trace = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        assert len(bundle.matched_entities) == 1
        assert bundle.matched_entities[0].entity_id == "HR_EMPLOYEES"

    def test_match_by_retrieved_table(self, _patch_registry):
        qu = FakeQueryUnderstanding()
        snap = _snapshot("PO_HEADERS_ALL")
        bundle, trace = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        entity_ids = [e.entity_id for e in bundle.matched_entities]
        assert "PO_HEADERS" in entity_ids

    def test_dedup_entity_from_both_sources(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["HR_EMPLOYEES"])
        snap = _snapshot("XXHR_EMPLOYEES_V")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        # Should not duplicate — entity appears via both resolved_entities AND table match
        hr_matches = [e for e in bundle.matched_entities if e.entity_id == "HR_EMPLOYEES"]
        assert len(hr_matches) == 1


# ---------------------------------------------------------------------------
# Glossary matching
# ---------------------------------------------------------------------------


class TestGlossaryMatching:
    def test_phrase_scan_in_normalized_question(self, _patch_registry):
        qu = FakeQueryUnderstanding(
            normalized_question="satinalma siparisi listele",
        )
        snap = _snapshot("PO_HEADERS_ALL")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        terms = [g.raw_term for g in bundle.matched_glossary]
        assert "satınalma siparişi" in terms

    def test_filter_dimension_term_resolution(self, _patch_registry):
        qu = FakeQueryUnderstanding(
            extracted_filters=[{"dimension": "calisan", "value": ""}],
        )
        snap = _snapshot("XXHR_EMPLOYEES_V")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        terms = [g.raw_term for g in bundle.matched_glossary]
        assert "çalışan" in terms


# ---------------------------------------------------------------------------
# Metric matching
# ---------------------------------------------------------------------------


class TestMetricMatching:
    def test_match_by_detected_metric_id(self, _patch_registry):
        qu = FakeQueryUnderstanding(detected_metrics=["emp_count"])
        snap = _snapshot("XXHR_EMPLOYEES_V")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        assert any(m.metric_id == "emp_count" for m in bundle.matched_metrics)

    def test_match_by_aggregation_hint_alias(self, _patch_registry):
        qu = FakeQueryUnderstanding(
            extracted_aggregation_hints=["calisan sayisi"],
        )
        snap = _snapshot("XXHR_EMPLOYEES_V")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        assert any(m.metric_id == "emp_count" for m in bundle.matched_metrics)

    def test_cascade_metrics_from_entity(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["PO_HEADERS"])
        snap = _snapshot("PO_HEADERS_ALL")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        metric_ids = [m.metric_id for m in bundle.matched_metrics]
        assert "po_header_count" in metric_ids


# ---------------------------------------------------------------------------
# Relationship matching
# ---------------------------------------------------------------------------


class TestRelationshipMatching:
    def test_approved_relationships_only(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["HR_EMPLOYEES"])
        # Both tables in snapshot, but the relationship is unapproved
        snap = _snapshot("XXHR_EMPLOYEES_V", "PO_HEADERS_ALL")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        # HR_TO_PO_UNAPPROVED should be excluded
        edge_ids = [r.edge_id for r in bundle.matched_relationships]
        assert "HR_TO_PO_UNAPPROVED" not in edge_ids

    def test_relationship_requires_both_tables(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["PO_HEADERS"])
        # Only one table — relationship needs both
        snap = _snapshot("PO_HEADERS_ALL")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        # PO_HEADER_TO_LINE should be excluded — PO_LINES_ALL not in snapshot
        edge_ids = [r.edge_id for r in bundle.matched_relationships]
        assert "PO_HEADER_TO_LINE" not in edge_ids

    def test_relationship_included_when_both_tables_present(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["PO_HEADERS"])
        snap = _snapshot("PO_HEADERS_ALL", "PO_LINES_ALL")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        edge_ids = [r.edge_id for r in bundle.matched_relationships]
        assert "PO_HEADER_TO_LINE" in edge_ids


# ---------------------------------------------------------------------------
# Lookup matching
# ---------------------------------------------------------------------------


class TestLookupMatching:
    def test_lookup_via_column_hint(self, _patch_registry):
        qu = FakeQueryUnderstanding(
            extracted_filters=[{"column_hint": "TYPE_LOOKUP_CODE"}],
        )
        snap = _snapshot("PO_HEADERS_ALL")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        assert any(lk.lookup_type == "PO_DOCUMENT_TYPE" for lk in bundle.matched_lookups)

    def test_lookup_via_lookup_hints(self, _patch_registry):
        qu = FakeQueryUnderstanding(
            lookup_hints=[{"lookup_type": "PO_DOCUMENT_TYPE"}],
        )
        snap = _snapshot("PO_HEADERS_ALL")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        assert any(lk.lookup_type == "PO_DOCUMENT_TYPE" for lk in bundle.matched_lookups)


# ---------------------------------------------------------------------------
# Flexfield matching
# ---------------------------------------------------------------------------


class TestFlexfieldMatching:
    def test_flexfield_matched_by_table(self, _patch_registry):
        qu = FakeQueryUnderstanding()
        snap = _snapshot("PO_HEADERS_ALL")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        ff_ids = [f.flexfield_id for f in bundle.matched_flexfields]
        assert "PO_HEADERS_DFF" in ff_ids
        assert "HR_ASSIGNMENT_DFF" not in ff_ids

    def test_flexfield_not_matched_when_table_missing(self, _patch_registry):
        qu = FakeQueryUnderstanding()
        snap = _snapshot("PO_LINES_ALL")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        assert len(bundle.matched_flexfields) == 0


# ---------------------------------------------------------------------------
# Budget trimming
# ---------------------------------------------------------------------------


class TestBudgetTrimming:
    def test_small_budget_triggers_trimming(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["HR_EMPLOYEES", "PO_HEADERS"])
        snap = _snapshot("XXHR_EMPLOYEES_V", "PO_HEADERS_ALL", "PO_LINES_ALL")
        _, trace = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
            max_total_chars=100,
        )
        assert trace.semantic_budget_applied
        assert trace.semantic_items_trimmed > 0

    def test_zero_budget_means_no_limit(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["HR_EMPLOYEES"])
        snap = _snapshot("XXHR_EMPLOYEES_V")
        _, trace = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
            max_total_chars=0,
        )
        assert not trace.semantic_budget_applied


# ---------------------------------------------------------------------------
# Prompt block rendering
# ---------------------------------------------------------------------------


class TestPromptBlockRendering:
    def test_empty_bundle_renders_empty_string(self):
        bundle = SemanticContextBundle()
        assert build_semantic_grounding_block(bundle) == ""

    def test_rendered_block_starts_with_header(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["HR_EMPLOYEES"])
        snap = _snapshot("XXHR_EMPLOYEES_V")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        block = build_semantic_grounding_block(bundle)
        assert block.startswith("Anlamsal bağlam (Semantic Grounding):")

    def test_rendered_block_contains_entity_info(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["HR_EMPLOYEES"])
        snap = _snapshot("XXHR_EMPLOYEES_V")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        block = build_semantic_grounding_block(bundle)
        assert "HR_EMPLOYEES" in block
        assert "Çalışan" in block


# ---------------------------------------------------------------------------
# Trace structure
# ---------------------------------------------------------------------------


class TestTraceStructure:
    def test_trace_has_expected_keys(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["HR_EMPLOYEES"])
        snap = _snapshot("XXHR_EMPLOYEES_V")
        _, trace = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        d = trace.as_trace_dict()
        assert d["semantic_retrieval_used"] is True
        assert d["semantic_retrieval_source"] == "SemanticFoundationRegistry"
        assert "matched_entity_count" in d
        assert "matched_glossary_count" in d
        assert "matched_metric_count" in d
        assert "semantic_prompt_chars" in d
        assert isinstance(d["matched_entity_ids"], list)

    def test_trace_skip_when_no_tables(self, _patch_registry):
        qu = FakeQueryUnderstanding()
        snap = _snapshot()  # no tables
        _, trace = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        assert not trace.semantic_retrieval_used
        assert trace.semantic_retrieval_skip_reason == "no_tables_retrieved"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unknown_entity_id_is_skipped(self, _patch_registry):
        qu = FakeQueryUnderstanding(resolved_entities=["UNKNOWN_ENTITY"])
        snap = _snapshot("XXHR_EMPLOYEES_V")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        # Should not crash; HR_EMPLOYEES still matched via table
        assert any(e.entity_id == "HR_EMPLOYEES" for e in bundle.matched_entities)

    def test_unknown_metric_id_is_skipped(self, _patch_registry):
        qu = FakeQueryUnderstanding(detected_metrics=["nonexistent_metric"])
        snap = _snapshot("XXHR_EMPLOYEES_V")
        bundle, _ = retrieve_semantic_context(
            query_understanding=qu,
            retrieved_snapshot=snap,
        )
        # Should not crash; no metric matched by the invalid id
        # But emp_count may match via entity cascade
        assert bundle is not None
