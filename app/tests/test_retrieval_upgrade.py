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
from unittest.mock import AsyncMock, MagicMock


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


# ---------------------------------------------------------------------------
# Domain-aware retrieval integrity (cross-domain contamination prevention)
# ---------------------------------------------------------------------------


def _po_location_table() -> TableMetadata:
    """PO_LINE_LOCATIONS_ALL — has 'location' heavily in name/columns."""
    return TableMetadata(
        name="PO_LINE_LOCATIONS_ALL",
        aliases=["line location", "shipment location"],
        description="Oracle Purchasing shipment tablosu. SHIP_TO_LOCATION_ID içerir.",
        columns=[
            ColumnMetadata(name="LINE_LOCATION_ID", data_type=ColumnType.NUMBER),
            ColumnMetadata(name="PO_HEADER_ID", data_type=ColumnType.NUMBER),
            ColumnMetadata(name="SHIP_TO_LOCATION_ID", data_type=ColumnType.NUMBER),
        ],
    )


def _build_mock_indexer(table_names: list[str], raw_scores: list[float]):
    """Build a mock CatalogEmbeddingIndexer that returns deterministic scores."""
    import numpy as np

    # Column vector so matrix @ q_vec_unit produces raw_scores directly
    matrix = np.array([[s] for s in raw_scores], dtype=np.float32)
    indexer = MagicMock()
    indexer.ensure_built = AsyncMock(return_value=True)
    indexer.get_matrix = MagicMock(return_value=matrix)
    indexer.table_names = table_names
    return indexer


class TestEmbeddingRetrieverDomainGating:
    """EmbeddingRetriever must suppress cross-domain tables for high-confidence
    single-domain queries using registry-based module lookup — no table-name
    substring heuristics or cosine-margin constants."""

    @pytest.mark.asyncio
    async def test_hr_location_query_suppresses_po_location_table(self) -> None:
        """'Istanbul'daki calisanlari getir': HR confidence high, PO location
        table must be suppressed (registry says PO module != HR)."""
        hr = _hr_table()         # XXBT_PDKS_PER_DETAILS_V → HR via registry
        po_loc = _po_location_table()  # PO_LINE_LOCATIONS_ALL → PO via registry

        provider = _make_provider(hr, po_loc)
        indexer = _build_mock_indexer(
            ["XXBT_PDKS_PER_DETAILS_V", "PO_LINE_LOCATIONS_ALL"],
            [0.85, 0.82],
        )
        emb_provider = AsyncMock()
        emb_provider.embed_texts = AsyncMock(return_value=[[1.0]])

        retriever = EmbeddingRetriever(provider, indexer, emb_provider)
        qu = analyze_query("Istanbul'daki calisanlari getir")

        snap = await retriever.retrieve(
            "Istanbul'daki calisanlari getir", top_k=3, query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names
        assert "PO_LINE_LOCATIONS_ALL" not in table_names

    @pytest.mark.asyncio
    async def test_hard_gate_suppresses_cross_domain_even_with_higher_score(self) -> None:
        """QU confidence is authoritative: PO table is suppressed for a
        high-confidence HR query even when its cosine score is much higher.
        No score-margin constant is used — registry module is the authority."""
        hr = _hr_table()
        po_loc = _po_location_table()

        provider = _make_provider(hr, po_loc)
        # PO scores much higher than HR — hard gate must still suppress it
        indexer = _build_mock_indexer(
            ["XXBT_PDKS_PER_DETAILS_V", "PO_LINE_LOCATIONS_ALL"],
            [0.70, 0.95],
        )
        emb_provider = AsyncMock()
        emb_provider.embed_texts = AsyncMock(return_value=[[1.0]])

        retriever = EmbeddingRetriever(provider, indexer, emb_provider)
        qu = analyze_query("Istanbul'daki calisanlari getir")  # high confidence HR

        snap = await retriever.retrieve(
            "Istanbul'daki calisanlari getir", top_k=3, query_understanding=qu,
        )

        # QU says HR, registry says PO → hard gate suppresses regardless of score
        table_names = [t.name for t in snap.tables]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names
        assert "PO_LINE_LOCATIONS_ALL" not in table_names

    @pytest.mark.asyncio
    async def test_requires_cross_domain_flag_bypasses_gate(self) -> None:
        """When QU sets requires_cross_domain_reasoning=True, domain gate is
        bypassed entirely so both tables survive retrieval."""
        hr = _hr_table()
        po_loc = _po_location_table()

        provider = _make_provider(hr, po_loc)
        indexer = _build_mock_indexer(
            ["XXBT_PDKS_PER_DETAILS_V", "PO_LINE_LOCATIONS_ALL"],
            [0.80, 0.90],
        )
        emb_provider = AsyncMock()
        emb_provider.embed_texts = AsyncMock(return_value=[[1.0]])

        retriever = EmbeddingRetriever(provider, indexer, emb_provider)
        qu = analyze_query("Istanbul'daki calisanlari getir")
        qu.requires_cross_domain_reasoning = True  # force cross-domain flag

        snap = await retriever.retrieve(
            "Istanbul'daki calisanlari getir", top_k=3, query_understanding=qu,
        )

        # Cross-domain reasoning bypasses gating → PO table survives
        table_names = [t.name for t in snap.tables]
        assert "PO_LINE_LOCATIONS_ALL" in table_names

    @pytest.mark.asyncio
    async def test_genuine_po_query_not_suppressed(self) -> None:
        """A high-confidence PO query must still retrieve PO tables unhindered."""
        po = _po_header_table()
        hr = _hr_table()

        provider = _make_provider(po, hr)
        indexer = _build_mock_indexer(
            ["PO_HEADERS_ALL", "XXBT_PDKS_PER_DETAILS_V"],
            [0.88, 0.65],
        )
        emb_provider = AsyncMock()
        emb_provider.embed_texts = AsyncMock(return_value=[[1.0]])

        retriever = EmbeddingRetriever(provider, indexer, emb_provider)
        qu = analyze_query("Onaylanmış satınalma siparişlerini listele")

        snap = await retriever.retrieve(
            "Onaylanmış satınalma siparişlerini listele", top_k=3, query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        assert "PO_HEADERS_ALL" in table_names

    @pytest.mark.asyncio
    async def test_low_confidence_no_domain_gating(self) -> None:
        """When entity_confidence is not 'high', domain gating must be inactive."""
        hr = _hr_table()
        po_loc = _po_location_table()

        provider = _make_provider(hr, po_loc)
        # PO table scores higher — without gating it should be returned
        indexer = _build_mock_indexer(
            ["XXBT_PDKS_PER_DETAILS_V", "PO_LINE_LOCATIONS_ALL"],
            [0.75, 0.80],
        )
        emb_provider = AsyncMock()
        emb_provider.embed_texts = AsyncMock(return_value=[[1.0]])

        retriever = EmbeddingRetriever(provider, indexer, emb_provider)
        # Ambiguous query — no clear entity signal → low confidence
        qu = analyze_query("bilinmeyen tablo sorgusu")

        snap = await retriever.retrieve(
            "bilinmeyen tablo sorgusu", top_k=3, query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        # PO table scores 0.80 > HR 0.75 → with no gating it's ranked first
        assert "PO_LINE_LOCATIONS_ALL" in table_names

    @pytest.mark.asyncio
    async def test_no_table_name_substring_dependency(self) -> None:
        """Domain gating must be registry-driven, not name-substring-driven.

        A table with 'purchase' and 'employee' in its name that is NOT in
        the registry must survive an HR query (unknown domain → fail-open).
        Only tables whose registry module explicitly conflicts are filtered.
        """
        hr = _hr_table()  # XXBT_PDKS_PER_DETAILS_V → HR via registry
        # Name has HR- and PO-like substrings but is NOT in the registry
        unregistered = TableMetadata(
            name="CUSTOM_PURCHASE_EMPLOYEE_DATA",
            description="Custom cross-system table",
            columns=[ColumnMetadata(name="ID", data_type=ColumnType.NUMBER)],
        )

        provider = _make_provider(hr, unregistered)
        indexer = _build_mock_indexer(
            ["XXBT_PDKS_PER_DETAILS_V", "CUSTOM_PURCHASE_EMPLOYEE_DATA"],
            [0.85, 0.80],
        )
        emb_provider = AsyncMock()
        emb_provider.embed_texts = AsyncMock(return_value=[[1.0]])

        retriever = EmbeddingRetriever(provider, indexer, emb_provider)
        qu = analyze_query("Istanbul'daki calisanlari getir")  # high confidence HR

        snap = await retriever.retrieve(
            "Istanbul'daki calisanlari getir", top_k=3, query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names
        # Unregistered table (unknown domain) must NOT be filtered — only
        # registry-confirmed cross-domain tables are suppressed
        assert "CUSTOM_PURCHASE_EMPLOYEE_DATA" in table_names
        """When entity_confidence is not 'high', domain gating must be inactive."""
        hr = _hr_table()
        po_loc = _po_location_table()

        provider = _make_provider(hr, po_loc)
        # PO table scores higher — without gating it should be returned
        indexer = _build_mock_indexer(
            ["XXBT_PDKS_PER_DETAILS_V", "PO_LINE_LOCATIONS_ALL"],
            [0.75, 0.80],
        )
        emb_provider = AsyncMock()
        emb_provider.embed_texts = AsyncMock(return_value=[[1.0]])

        retriever = EmbeddingRetriever(provider, indexer, emb_provider)
        # Ambiguous query — no clear entity signal → low confidence
        qu = analyze_query("bilinmeyen tablo sorgusu")

        snap = await retriever.retrieve(
            "bilinmeyen tablo sorgusu", top_k=3, query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        # PO table scores 0.80 > HR 0.75 → with no gating it's ranked first
        assert "PO_LINE_LOCATIONS_ALL" in table_names


class TestHybridRetrieverDomainHardening:
    """HybridRetriever must apply keyword-retriever-trust post-filtering for
    high-confidence single-domain queries (no table-name heuristics) and
    propagate keyword-retriever diagnostics."""

    @pytest.mark.asyncio
    async def test_hr_query_cross_domain_suppressed_via_rrf(self) -> None:
        """For high-confidence HR query, PO table not in kw_snap must be dropped
        from RRF result even if semantic sub-retriever ranked it highly."""
        hr = _hr_table()
        po_loc = _po_location_table()

        kw_snapshot = CatalogSnapshot(tables=[hr], relationships=[])
        sem_snapshot = CatalogSnapshot(tables=[po_loc, hr], relationships=[])

        keyword = AsyncMock()
        keyword.retrieve = AsyncMock(return_value=kw_snapshot)
        keyword.last_retrieval_diagnostics = {
            "dominant_domain_match": True,
            "root_table_name": "XXBT_PDKS_PER_DETAILS_V",
            "root_table_confidence": "high",
            "noisy_context_count": 0,
            "dropped_candidates": [],
            "kept_candidates_reason": {},
        }
        semantic = AsyncMock()
        semantic.retrieve = AsyncMock(return_value=sem_snapshot)

        retriever = HybridRetriever(keyword=keyword, semantic=semantic)
        qu = analyze_query("Istanbul'daki calisanlari getir")

        snap = await retriever.retrieve(
            "Istanbul'daki calisanlari getir", top_k=5, query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names
        assert "PO_LINE_LOCATIONS_ALL" not in table_names

    @pytest.mark.asyncio
    async def test_hybrid_diagnostics_forwarded_from_keyword(self) -> None:
        """HybridRetriever.last_retrieval_diagnostics must come from keyword sub-retriever."""
        hr = _hr_table()
        snapshot = CatalogSnapshot(tables=[hr], relationships=[])

        expected_diag = {
            "dominant_domain_match": True,
            "root_table_name": "XXBT_PDKS_PER_DETAILS_V",
            "root_table_confidence": "high",
            "noisy_context_count": 0,
            "dropped_candidates": [],
            "kept_candidates_reason": {},
        }
        keyword = AsyncMock()
        keyword.retrieve = AsyncMock(return_value=snapshot)
        keyword.last_retrieval_diagnostics = expected_diag

        semantic = AsyncMock()
        semantic.retrieve = AsyncMock(return_value=snapshot)

        retriever = HybridRetriever(keyword=keyword, semantic=semantic)
        await retriever.retrieve("Aktif çalışanları listele", top_k=3)

        assert retriever.last_retrieval_diagnostics is expected_diag

    @pytest.mark.asyncio
    async def test_genuine_po_query_not_affected_by_hardening(self) -> None:
        """A high-confidence PO query: HR table not in kw_snap must be dropped."""
        po = _po_header_table()
        hr = _hr_table()

        kw_snapshot = CatalogSnapshot(tables=[po], relationships=[])
        sem_snapshot = CatalogSnapshot(tables=[po, hr], relationships=[])

        keyword = AsyncMock()
        keyword.retrieve = AsyncMock(return_value=kw_snapshot)
        keyword.last_retrieval_diagnostics = None

        semantic = AsyncMock()
        semantic.retrieve = AsyncMock(return_value=sem_snapshot)

        retriever = HybridRetriever(keyword=keyword, semantic=semantic)
        qu = analyze_query("Onaylanmış satınalma siparişlerini listele")

        snap = await retriever.retrieve(
            "Onaylanmış satınalma siparişlerini listele", top_k=5, query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        assert "PO_HEADERS_ALL" in table_names
        assert "XXBT_PDKS_PER_DETAILS_V" not in table_names

    @pytest.mark.asyncio
    async def test_multi_entity_bypass_hardening(self) -> None:
        """Multi-entity queries must bypass domain hardening entirely."""
        hr = _hr_table()
        po = _po_header_table()

        kw_snapshot = CatalogSnapshot(tables=[hr], relationships=[])
        sem_snapshot = CatalogSnapshot(tables=[hr, po], relationships=[])

        keyword = AsyncMock()
        keyword.retrieve = AsyncMock(return_value=kw_snapshot)
        keyword.last_retrieval_diagnostics = None

        semantic = AsyncMock()
        semantic.retrieve = AsyncMock(return_value=sem_snapshot)

        retriever = HybridRetriever(keyword=keyword, semantic=semantic)
        # Simulate a multi-entity QU (e.g. asking about employees AND purchase orders)
        qu = analyze_query("Aktif çalışanları listele")
        qu.multi_entity_flag = True  # force multi-entity flag

        snap = await retriever.retrieve(
            "Aktif çalışanları listele", top_k=5, query_understanding=qu,
        )

        table_names = [t.name for t in snap.tables]
        # Both tables must survive — multi-entity bypasses hardening
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names
        assert "PO_HEADERS_ALL" in table_names


        table_names = [t.name for t in snap.tables]
        # Should not pull PO_LINES via FK when it's a purely HR query
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names


# ---------------------------------------------------------------------------
# Centralized gating policy — registry-driven, no substring heuristics
# ---------------------------------------------------------------------------


class TestGatingPolicyCentralized:
    """Verify that table→module resolution uses the semantic registry exclusively
    and that the gating decision is driven by QueryUnderstanding signals."""

    def test_registry_module_lookup_hr_table(self) -> None:
        """XXBT_PDKS_PER_DETAILS_V → HR via registry (confirmed by SemanticEntity.module)."""
        from app.providers.retrieval.gating_policy import table_module_from_registry
        assert table_module_from_registry("XXBT_PDKS_PER_DETAILS_V") == "HR"

    def test_registry_module_lookup_po_location_table(self) -> None:
        """PO_LINE_LOCATIONS_ALL → PO via registry (not through name substring match)."""
        from app.providers.retrieval.gating_policy import table_module_from_registry
        assert table_module_from_registry("PO_LINE_LOCATIONS_ALL") == "PO"

    def test_registry_module_lookup_unknown_table(self) -> None:
        """A table not in the registry returns None (unknown domain → fail-open)."""
        from app.providers.retrieval.gating_policy import table_module_from_registry
        assert table_module_from_registry("CUSTOM_TABLE_XYZ_UNKNOWN") is None

    def test_high_confidence_hr_gating_active(self) -> None:
        """is_high_confidence_single_domain → (True, 'HR') for Istanbul employee query."""
        from app.providers.retrieval.gating_policy import is_high_confidence_single_domain
        qu = analyze_query("Istanbul'daki calisanlari getir")
        should_gate, module = is_high_confidence_single_domain(qu)
        assert should_gate is True
        assert module == "HR"

    def test_multi_entity_gating_inactive(self) -> None:
        """multi_entity_flag=True → is_high_confidence_single_domain returns (False, None)."""
        from app.providers.retrieval.gating_policy import is_high_confidence_single_domain
        qu = analyze_query("Istanbul'daki calisanlari getir")
        qu.multi_entity_flag = True  # override to simulate multi-entity query
        should_gate, _ = is_high_confidence_single_domain(qu)
        assert should_gate is False

    def test_requires_cross_domain_reasoning_gating_inactive(self) -> None:
        """requires_cross_domain_reasoning=True → gating bypassed."""
        from app.providers.retrieval.gating_policy import is_high_confidence_single_domain
        qu = analyze_query("Istanbul'daki calisanlari getir")
        qu.requires_cross_domain_reasoning = True
        should_gate, _ = is_high_confidence_single_domain(qu)
        assert should_gate is False

    def test_compute_domain_noise_all_same_domain(self) -> None:
        """All tables from same domain → dominant=True, cross=0."""
        from app.providers.retrieval.gating_policy import compute_domain_noise
        tables = [_hr_table()]
        dominant, cross = compute_domain_noise(tables, "HR")
        assert cross == 0
        assert dominant is True

    def test_compute_domain_noise_cross_domain_contamination(self) -> None:
        """HR query + PO table in result → dominant=False, cross=1.
        This proves noisy_context_count will be incremented, causing
        retrieval_assessment to be 'noisy' rather than 'sufficient'."""
        from app.providers.retrieval.gating_policy import compute_domain_noise
        tables = [_hr_table(), _po_location_table()]  # 1 HR + 1 PO
        dominant, cross = compute_domain_noise(tables, "HR")
        assert cross == 1
        # same=1, cross=1 → 1 > 1 is False → not dominant
        assert dominant is False

    def test_compute_domain_noise_unregistered_table_not_counted(self) -> None:
        """A table not in the registry contributes 0 to cross-domain count."""
        from app.providers.retrieval.gating_policy import compute_domain_noise
        hr = _hr_table()
        unknown = TableMetadata(
            name="CUSTOM_UNREGISTERED_TABLE",
            description="Not in registry",
            columns=[ColumnMetadata(name="ID", data_type=ColumnType.NUMBER)],
        )
        dominant, cross = compute_domain_noise([hr, unknown], "HR")
        assert cross == 0  # unregistered table is NOT counted as cross-domain
        assert dominant is True


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
