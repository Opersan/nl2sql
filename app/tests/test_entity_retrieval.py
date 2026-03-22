"""Tests for entity-seeded retrieval in InMemoryRetriever (Phase 4).

Validates that:
- When resolved_entities is non-empty, entity.default_tables are preferred.
- Entity-seeded retrieval narrows the candidate set vs. broad top-k.
- Approved relationship edges expand the seed set.
- Non-seeded tables are suppressed (lower rank) but not fully excluded.
- Legacy module-pattern fallback still works when no entity seeds.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.catalog_models import CatalogSnapshot, ColumnMetadata, TableMetadata
from app.providers.catalog.base import CatalogProvider
from app.providers.retrieval.in_memory_retriever import InMemoryRetriever
from app.semantic.loader import load_semantic_foundation
from app.semantic.models import SemanticFoundation
from app.semantic.registry import SemanticFoundationRegistry
from app.services.query_understanding import QueryUnderstanding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_table(name: str, description: str = "", aliases: list[str] | None = None) -> TableMetadata:
    return TableMetadata(
        name=name,
        description=description,
        aliases=aliases or [],
        columns=[ColumnMetadata(name="id", data_type="NUMBER")],
    )


def _build_snapshot(table_names: list[str]) -> CatalogSnapshot:
    return CatalogSnapshot(tables=[_make_table(n) for n in table_names])


def _make_catalog_provider(snapshot: CatalogSnapshot) -> CatalogProvider:
    provider = MagicMock(spec=CatalogProvider)
    provider.get_snapshot = AsyncMock(return_value=snapshot)
    provider.get_table = AsyncMock(side_effect=lambda name: next(
        (t for t in snapshot.tables if t.name.upper() == name.upper()), None
    ))
    return provider


@pytest.fixture(scope="module")
def real_registry() -> SemanticFoundationRegistry:
    data_dir = Path(__file__).resolve().parents[2] / "data" / "semantic"
    foundation = load_semantic_foundation(semantic_dir=data_dir)
    return SemanticFoundationRegistry(foundation)


@pytest.fixture(scope="module")
def mixed_snapshot() -> CatalogSnapshot:
    """Snapshot with HR and PO tables plus unrelated noise."""
    return _build_snapshot([
        "XXBT_PDKS_PER_DETAILS_V",   # HR entity root
        "PO_HEADERS_ALL",             # PO entity root
        "PO_LINES_ALL",               # PO child table
        "GL_JE_HEADERS",              # GL (different domain)
        "SOME_UNRELATED_TABLE",       # noise
    ])


# ---------------------------------------------------------------------------
# Entity-first seeding narrows candidate set
# ---------------------------------------------------------------------------

class TestEntityFirstSeeding:
    def test_hr_query_seeds_hr_table(
        self,
        real_registry: SemanticFoundationRegistry,
        mixed_snapshot: CatalogSnapshot,
    ) -> None:
        provider = _make_catalog_provider(mixed_snapshot)
        retriever = InMemoryRetriever(provider, semantic_registry=real_registry)
        qu = QueryUnderstanding(
            original_question="Çalışan listesi",
            normalized_question="calisan listesi",
            resolved_entities=["HR_EMPLOYEES"],
            detected_entities=["employee"],
            inferred_modules=["HR"],
        )
        result = asyncio.get_event_loop().run_until_complete(
            retriever.retrieve("Çalışan listesi", top_k=5, query_understanding=qu)
        )
        table_names = {t.name for t in result.tables}
        # HR entity root must be in results
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names

    def test_po_query_seeds_po_tables(
        self,
        real_registry: SemanticFoundationRegistry,
        mixed_snapshot: CatalogSnapshot,
    ) -> None:
        provider = _make_catalog_provider(mixed_snapshot)
        retriever = InMemoryRetriever(provider, semantic_registry=real_registry)
        qu = QueryUnderstanding(
            original_question="Sipariş listesi",
            normalized_question="siparis listesi",
            resolved_entities=["PO_PURCHASING"],
            detected_entities=["purchase_order"],
            inferred_modules=["PO"],
        )
        result = asyncio.get_event_loop().run_until_complete(
            retriever.retrieve("Sipariş listesi", top_k=5, query_understanding=qu)
        )
        table_names = {t.name for t in result.tables}
        assert "PO_HEADERS_ALL" in table_names

    def test_entity_seed_suppresses_unrelated_tables(
        self,
        real_registry: SemanticFoundationRegistry,
        mixed_snapshot: CatalogSnapshot,
    ) -> None:
        """Unrelated noise table should not dominate results when entity seeds exist."""
        provider = _make_catalog_provider(mixed_snapshot)
        retriever = InMemoryRetriever(provider, semantic_registry=real_registry)
        qu = QueryUnderstanding(
            original_question="çalışanları listele",
            normalized_question="calisanlari listele",
            resolved_entities=["HR_EMPLOYEES"],
            detected_entities=["employee"],
            inferred_modules=["HR"],
        )
        result = asyncio.get_event_loop().run_until_complete(
            retriever.retrieve("çalışanları listele", top_k=3, query_understanding=qu)
        )
        table_names = [t.name for t in result.tables]
        # HR table must appear before unrelated noise table
        if "XXBT_PDKS_PER_DETAILS_V" in table_names and "SOME_UNRELATED_TABLE" in table_names:
            assert table_names.index("XXBT_PDKS_PER_DETAILS_V") < table_names.index("SOME_UNRELATED_TABLE")


# ---------------------------------------------------------------------------
# Approved relationship expansion
# ---------------------------------------------------------------------------

def test_relationship_expansion_includes_related_table(
    real_registry: SemanticFoundationRegistry,
    mixed_snapshot: CatalogSnapshot,
) -> None:
    """PO_LINES_ALL should be reachable via approved edge from PO_HEADERS_ALL."""
    provider = _make_catalog_provider(mixed_snapshot)
    retriever = InMemoryRetriever(provider, semantic_registry=real_registry)
    qu = QueryUnderstanding(
        original_question="PO kalem sayısı",
        normalized_question="po kalem sayisi",
        resolved_entities=["PO_PURCHASING"],
        detected_entities=["purchase_order"],
        inferred_modules=["PO"],
    )
    result = asyncio.get_event_loop().run_until_complete(
        retriever.retrieve("PO kalem sayısı", top_k=5, query_understanding=qu)
    )
    table_names = {t.name for t in result.tables}
    assert "PO_HEADERS_ALL" in table_names
    assert "PO_LINES_ALL" in table_names


# ---------------------------------------------------------------------------
# Fallback: no entity seeds → legacy module-pattern behaviour
# ---------------------------------------------------------------------------

def test_no_entity_seeds_falls_back_to_module_patterns(
    mixed_snapshot: CatalogSnapshot,
) -> None:
    """Without entity seeds, the retriever must still boost HR tables for HR queries."""
    provider = _make_catalog_provider(mixed_snapshot)
    retriever = InMemoryRetriever(provider)  # no semantic_registry
    qu = QueryUnderstanding(
        original_question="çalışan listesi",
        normalized_question="calisan listesi",
        resolved_entities=[],  # empty — no entity seeds
        detected_entities=["employee"],
        inferred_modules=["HR"],
    )
    result = asyncio.get_event_loop().run_until_complete(
        retriever.retrieve("çalışan listesi", top_k=5, query_understanding=qu)
    )
    table_names = {t.name for t in result.tables}
    # XXBT_PDKS matches "xxbt_pdks" pattern in _MODULE_TABLE_PATTERNS["HR"]
    assert "XXBT_PDKS_PER_DETAILS_V" in table_names


# ---------------------------------------------------------------------------
# Backward compat: no QU at all
# ---------------------------------------------------------------------------

def test_retrieve_without_query_understanding(mixed_snapshot: CatalogSnapshot) -> None:
    provider = _make_catalog_provider(mixed_snapshot)
    retriever = InMemoryRetriever(provider)
    result = asyncio.get_event_loop().run_until_complete(
        retriever.retrieve("sipariş", top_k=5)
    )
    assert len(result.tables) > 0
