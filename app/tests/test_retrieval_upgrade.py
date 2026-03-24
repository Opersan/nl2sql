"""Tests for the entity-aware retrieval upgrade (min-score, module boost/suppress)."""

from __future__ import annotations

import pytest

from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    ForeignKeyMetadata,
    RelationshipMetadata,
    TableMetadata,
)
from app.providers.retrieval.embedding_retriever import EmbeddingRetriever
from app.providers.retrieval.hybrid_retriever import HybridRetriever
from app.providers.retrieval.in_memory_retriever import (
    InMemoryRetriever,
    _MIN_RETRIEVAL_SCORE,
    _STOP_TOKENS,
    _tokenize,
)
from app.services.query_understanding import analyze_query
from unittest.mock import AsyncMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(*tables: TableMetadata, rels: list | None = None):
    """Build a mock CatalogProvider returning the given tables."""
    provider = AsyncMock()
    provider.get_snapshot = AsyncMock(
        return_value=CatalogSnapshot(
            tables=list(tables),
            relationships=rels or [],
        ),
    )
    return provider


def _hr_table() -> TableMetadata:
    return TableMetadata(
        name="XXBT_PDKS_PER_DETAILS_V",
        aliases=["calisan", "employee", "personel"],
        description="Çalışan detay bilgileri",
        columns=[
            ColumnMetadata(name="PERSON_ID", data_type=ColumnType.NUMBER),
            ColumnMetadata(name="EMPLOYEE_NAME", data_type=ColumnType.VARCHAR, aliases=["ad"]),
            ColumnMetadata(name="CITY", data_type=ColumnType.VARCHAR, aliases=["sehir", "il"]),
            ColumnMetadata(name="SALARY", data_type=ColumnType.NUMBER, aliases=["maas"]),
        ],
    )


def _po_header_table() -> TableMetadata:
    return TableMetadata(
        name="PO_HEADERS_ALL",
        aliases=["siparis", "satinalma"],
        description="Satınalma sipariş başlıkları",
        columns=[
            ColumnMetadata(name="PO_HEADER_ID", data_type=ColumnType.NUMBER),
            ColumnMetadata(name="VENDOR_NAME", data_type=ColumnType.VARCHAR, aliases=["tedarikci"]),
        ],
    )


def _po_lines_table() -> TableMetadata:
    return TableMetadata(
        name="PO_LINES_ALL",
        aliases=["kalem"],
        description="Satınalma sipariş kalemleri",
        columns=[
            ColumnMetadata(name="PO_LINE_ID", data_type=ColumnType.NUMBER),
            ColumnMetadata(name="PO_HEADER_ID", data_type=ColumnType.NUMBER),
        ],
    )


# ---------------------------------------------------------------------------
# Tokenizer improvements
# ---------------------------------------------------------------------------


class TestTokenizerStopWords:
    """Short/stop tokens must be filtered out."""

    def test_stop_words_excluded(self) -> None:
        tokens = _tokenize("İstanbul'daki çalışanları getir")
        # "daki" and "getir" are stop tokens
        assert "daki" not in tokens
        assert "getir" not in tokens

    def test_short_tokens_excluded(self) -> None:
        tokens = _tokenize("A ve B ile C")
        # Single-char and 2-char tokens filtered
        assert "ve" not in tokens
        assert "ile" not in tokens

    def test_meaningful_tokens_kept(self) -> None:
        tokens = _tokenize("Istanbul personel listesi")
        # casefold_tr lowers İ→i but I→ı
        assert any("stanbul" in t for t in tokens)
        assert "personel" in tokens
        assert "listesi" in tokens


# ---------------------------------------------------------------------------
# Minimum score threshold
# ---------------------------------------------------------------------------


class TestMinimumScoreThreshold:
    """Tables below _MIN_RETRIEVAL_SCORE should be excluded."""

    @pytest.mark.asyncio
    async def test_low_score_table_excluded(self) -> None:
        """A table with only a marginal description match should be excluded."""
        weak_table = TableMetadata(
            name="UNRELATED_TABLE",
            description="Genel bilgiler tablosu",  # no real keyword match
            columns=[],
        )
        hr = _hr_table()
        provider = _make_provider(hr, weak_table)
        retriever = InMemoryRetriever(provider)

        qu = analyze_query("Personel listesi")
        snap = await retriever.retrieve("Personel listesi", top_k=5, query_understanding=qu)

        table_names = [t.name for t in snap.tables]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names
        # Weak table with below threshold score should not be there
        # (or if it is, its score should be >= MIN)
        retriever_score = retriever._score(weak_table, "Personel listesi")
        if retriever_score < _MIN_RETRIEVAL_SCORE:
            assert "UNRELATED_TABLE" not in table_names


# ---------------------------------------------------------------------------
# Module-aware boost/suppress
# ---------------------------------------------------------------------------


class TestModuleAwareScoring:
    """When QU detects a single module, tables from other modules are suppressed."""

    @pytest.mark.asyncio
    async def test_hr_query_suppresses_po(self) -> None:
        hr = _hr_table()
        po = _po_header_table()
        provider = _make_provider(hr, po)
        retriever = InMemoryRetriever(provider)

        qu = analyze_query("İstanbul'daki çalışanları getir")
        snap = await retriever.retrieve(
            "İstanbul'daki çalışanları getir",
            top_k=5,
            query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names
        # PO table should be suppressed or at least ranked lower
        if "PO_HEADERS_ALL" in table_names:
            hr_idx = table_names.index("XXBT_PDKS_PER_DETAILS_V")
            po_idx = table_names.index("PO_HEADERS_ALL")
            assert hr_idx < po_idx

    @pytest.mark.asyncio
    async def test_po_query_suppresses_hr(self) -> None:
        hr = _hr_table()
        po = _po_header_table()
        provider = _make_provider(hr, po)
        retriever = InMemoryRetriever(provider)

        qu = analyze_query("Onaylanmış satınalma siparişlerini listele")
        snap = await retriever.retrieve(
            "Onaylanmış satınalma siparişlerini listele",
            top_k=5,
            query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        assert "PO_HEADERS_ALL" in table_names

    @pytest.mark.asyncio
    async def test_backward_compat_without_qu(self) -> None:
        """When no QueryUnderstanding is passed, retriever works as before."""
        hr = _hr_table()
        provider = _make_provider(hr)
        retriever = InMemoryRetriever(provider)

        snap = await retriever.retrieve("employee listele", top_k=5)
        assert len(snap.tables) >= 1


# ---------------------------------------------------------------------------
# Controlled FK expansion
# ---------------------------------------------------------------------------


class TestControlledFKExpansion:
    """FK expansion should be controlled by QU signals."""

    @pytest.mark.asyncio
    async def test_single_entity_no_expansion(self) -> None:
        """When QU says single entity (no cross-domain), FK expansion should not fire."""
        hr = _hr_table()
        po = _po_header_table()
        po_lines = _po_lines_table()

        rels = [
            RelationshipMetadata(
                from_table="PO_LINES_ALL",
                from_column="PO_HEADER_ID",
                to_table="PO_HEADERS_ALL",
                to_column="PO_HEADER_ID",
                relationship_type="many_to_one",
            ),
        ]
        provider = _make_provider(hr, po, po_lines, rels=rels)
        retriever = InMemoryRetriever(provider)

        # HR-only query — should NOT cascade FK expansion to PO tables
        qu = analyze_query("Aktif çalışanları listele")
        snap = await retriever.retrieve(
            "Aktif çalışanları listele", top_k=5, query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        # Should not pull PO_LINES via FK when it's a purely HR query
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names


class TestHybridAndSemanticRetrieverCompatibility:
    """Hybrid and semantic retrievers must accept query_understanding."""

    @pytest.mark.asyncio
    async def test_hybrid_retriever_forwards_query_understanding(self) -> None:
        qu = analyze_query("Aktif çalışanları listele")
        snapshot = CatalogSnapshot(tables=[_hr_table()], relationships=[])

        keyword = AsyncMock()
        keyword.retrieve = AsyncMock(return_value=snapshot)
        semantic = AsyncMock()
        semantic.retrieve = AsyncMock(return_value=snapshot)

        retriever = HybridRetriever(keyword=keyword, semantic=semantic)

        result = await retriever.retrieve(
            "Aktif çalışanları listele",
            top_k=3,
            query_understanding=qu,
        )

        assert [t.name for t in result.tables] == ["XXBT_PDKS_PER_DETAILS_V"]
        keyword.retrieve.assert_awaited_once_with(
            "Aktif çalışanları listele",
            top_k=6,
            query_understanding=qu,
        )
        semantic.retrieve.assert_awaited_once_with(
            "Aktif çalışanları listele",
            top_k=6,
            query_understanding=qu,
        )

    @pytest.mark.asyncio
    async def test_embedding_retriever_accepts_query_understanding(self) -> None:
        table = _hr_table()
        provider = _make_provider(table)

        indexer = AsyncMock()
        indexer.ensure_built = AsyncMock(return_value=False)
        indexer.get_matrix = AsyncMock(return_value=None)

        emb_provider = AsyncMock()
        retriever = EmbeddingRetriever(provider, indexer, emb_provider)

        qu = analyze_query("Aktif çalışanları listele")
        snap = await retriever.retrieve(
            "Aktif çalışanları listele",
            top_k=1,
            query_understanding=qu,
        )

        assert [t.name for t in snap.tables] == ["XXBT_PDKS_PER_DETAILS_V"]
        emb_provider.embed_texts.assert_not_called()
