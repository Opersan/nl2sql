"""Tests for schema retrieval service and in-memory retriever."""

from __future__ import annotations

import pytest

from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    ForeignKeyMetadata,
    TableMetadata,
)
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.providers.retrieval.in_memory_retriever import InMemoryRetriever, _tokenize
from app.services.schema_retrieval_service import SchemaRetrievalService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def small_catalog_provider() -> InMemoryCatalogProvider:
    """Default in-memory provider (single employee table, < threshold)."""
    return InMemoryCatalogProvider()


@pytest.fixture
def retriever(small_catalog_provider: InMemoryCatalogProvider) -> InMemoryRetriever:
    return InMemoryRetriever(small_catalog_provider)


@pytest.fixture
def retrieval_service(retriever: InMemoryRetriever) -> SchemaRetrievalService:
    return SchemaRetrievalService(retriever)


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------


class TestTokenization:
    def test_basic_split(self) -> None:
        assert _tokenize("hello world") == ["hello", "world"]

    def test_punctuation_removed(self) -> None:
        # "listele" is a stop token, so only domain words survive
        assert _tokenize("maaş, listele!") == ["maaş"]

    def test_empty_string(self) -> None:
        assert _tokenize("") == []
        assert _tokenize("   ") == []

    def test_mixed_case_folded(self) -> None:
        tokens = _tokenize("Employee SALARY")
        assert all(t == t.lower() or "ı" in t for t in tokens)


# ---------------------------------------------------------------------------
# Small catalog — now filters instead of returning everything
# ---------------------------------------------------------------------------


class TestSmallCatalogRetrieval:
    """Even small catalogs go through scoring now."""

    @pytest.mark.asyncio
    async def test_matching_query_returns_match(
        self, retriever: InMemoryRetriever,
    ) -> None:
        """A query that matches the employee table should return it."""
        snapshot = await retriever.retrieve("employee listele")

        assert len(snapshot.tables) >= 1
        assert snapshot.tables[0].name == "XXBT_PDKS_PER_DETAILS_V"

    @pytest.mark.asyncio
    async def test_unrelated_query_returns_fallback_not_full_catalog(
        self, retriever: InMemoryRetriever,
    ) -> None:
        """Unrelated query should NOT return the full catalog anymore.
        It returns first top_k tables as fallback."""
        snapshot = await retriever.retrieve("xyz bilinmeyen sorgu", top_k=1)

        # Fallback: first top_k tables from catalog (1 in this case)
        assert len(snapshot.tables) <= 1

    @pytest.mark.asyncio
    async def test_small_catalog_still_filters_by_score(
        self, retriever: InMemoryRetriever,
    ) -> None:
        """Even with < 10 tables, scoring applies — not unconditional return."""
        snapshot = await retriever.retrieve("employee salary bilgileri")

        # Should match employee (XXBT view), via scoring
        assert len(snapshot.tables) >= 1
        table_names = [t.name for t in snapshot.tables]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestRetrieverScoring:
    def test_table_name_match_scores_highest(
        self, retriever: InMemoryRetriever,
    ) -> None:
        table = TableMetadata(name="XXBT_PDKS_PER_DETAILS_V", columns=[])
        score = retriever._score(table, "XXBT_PDKS_PER_DETAILS_V")
        assert score > 0

    def test_alias_match_scores(
        self, retriever: InMemoryRetriever,
    ) -> None:
        table = TableMetadata(
            name="XXBT_PDKS_PER_DETAILS_V",
            aliases=["personnel"],
            columns=[],
        )
        score = retriever._score(table, "personnel listele")
        assert score > 0

    def test_column_name_match_scores(
        self, retriever: InMemoryRetriever,
    ) -> None:
        table = TableMetadata(
            name="XXBT_PDKS_PER_DETAILS_V",
            columns=[ColumnMetadata(name="salary", data_type=ColumnType.NUMBER)],
        )
        score = retriever._score(table, "salary göster")
        assert score > 0

    def test_no_match_scores_zero(
        self, retriever: InMemoryRetriever,
    ) -> None:
        table = TableMetadata(name="other_table", columns=[])
        score = retriever._score(table, "çalışanlar")
        assert score == 0

    def test_punctuation_in_query_handled(
        self, retriever: InMemoryRetriever,
    ) -> None:
        # Table has 'employee' as an alias; punctuation in the query should be
        # stripped so the alias still matches.
        table = TableMetadata(name="XXBT_PDKS_PER_DETAILS_V", aliases=["employee"], columns=[])
        score = retriever._score(table, "employee?")
        assert score > 0

    def test_empty_query_scores_zero(
        self, retriever: InMemoryRetriever,
    ) -> None:
        table = TableMetadata(name="XXBT_PDKS_PER_DETAILS_V", columns=[])
        score = retriever._score(table, "")
        assert score == 0


# ---------------------------------------------------------------------------
# No-match fallback
# ---------------------------------------------------------------------------


class TestNoMatchFallback:
    """When no table scores > 0, retriever returns first top_k tables as fallback."""

    @pytest.mark.asyncio
    async def test_no_match_returns_limited_fallback(
        self, retriever: InMemoryRetriever,
    ) -> None:
        snapshot = await retriever.retrieve("xyzzy garble nonsense", top_k=2)

        # Should return at most top_k tables, not the entire catalog
        assert len(snapshot.tables) <= 2

    @pytest.mark.asyncio
    async def test_no_match_does_not_return_full_catalog(self) -> None:
        """With many tables, a non-matching query must not return all of them."""
        from unittest.mock import AsyncMock

        # Build a fake provider with 20 empty tables
        tables = [TableMetadata(name=f"table_{i}", columns=[]) for i in range(20)]
        provider = AsyncMock()
        provider.get_snapshot = AsyncMock(return_value=CatalogSnapshot(tables=tables))

        retriever = InMemoryRetriever(provider)
        snapshot = await retriever.retrieve("xyzzy garble nonsense", top_k=5)

        assert len(snapshot.tables) == 5  # top_k fallback, not 20


# ---------------------------------------------------------------------------
# SchemaRetrievalService
# ---------------------------------------------------------------------------


class TestSchemaRetrievalService:
    @pytest.mark.asyncio
    async def test_retrieve_context_returns_snapshot(
        self, retrieval_service: SchemaRetrievalService,
    ) -> None:
        snapshot = await retrieval_service.retrieve_context("employee listele")

        assert isinstance(snapshot, CatalogSnapshot)
        assert len(snapshot.tables) >= 1


class TestRetrievalDiagnostics:
    @pytest.mark.asyncio
    async def test_same_domain_bias_suppresses_cross_domain_table(self) -> None:
        from unittest.mock import AsyncMock

        provider = AsyncMock()
        provider.get_snapshot = AsyncMock(
            return_value=CatalogSnapshot(
                tables=[
                    TableMetadata(
                        name="XXBT_PDKS_PER_DETAILS_V",
                        aliases=["employee"],
                        columns=[ColumnMetadata(name="REG_NO", data_type=ColumnType.VARCHAR)],
                    ),
                    TableMetadata(
                        name="PO_HEADERS_ALL",
                        aliases=["purchase"],
                        columns=[ColumnMetadata(name="SEGMENT1", data_type=ColumnType.VARCHAR)],
                    ),
                ]
            )
        )
        retriever = InMemoryRetriever(provider)

        from app.services.query_understanding import QueryUnderstanding

        snapshot = await retriever.retrieve(
            "employee purchase listesi",
            top_k=2,
            query_understanding=QueryUnderstanding(
                original_question="employee purchase listesi",
                normalized_question="employee purchase listesi",
                inferred_modules=["HR"],
            ),
        )

        assert [table.name for table in snapshot.tables] == ["XXBT_PDKS_PER_DETAILS_V"]
        diagnostics = retriever.last_retrieval_diagnostics
        assert diagnostics is not None
        assert diagnostics["dominant_domain_match"] is True
        assert diagnostics["noisy_context_count"] == 0
        assert diagnostics["kept_candidates_reason"]["XXBT_PDKS_PER_DETAILS_V"] == "same_domain_boost"
        assert "PO_HEADERS_ALL:cross_domain_suppressed" in diagnostics["dropped_candidates"]


class TestFocusPruning:
    def test_focus_pruning_preserves_explicit_secondary_columns_and_caps_noise(self) -> None:
        from app.services.planning_context_service import PlanningContextAssemblyService
        from app.services.query_understanding import QueryUnderstanding

        snapshot = CatalogSnapshot(
            tables=[
                TableMetadata(
                    name="XXBT_PDKS_PER_DETAILS_V",
                    columns=[
                        ColumnMetadata(name="PERSON_ID", data_type=ColumnType.NUMBER),
                        ColumnMetadata(name="AD", data_type=ColumnType.VARCHAR),
                    ],
                    primary_key=["PERSON_ID"],
                ),
                TableMetadata(
                    name="PO_HEADERS_ALL",
                    columns=[
                        ColumnMetadata(name="PO_HEADER_ID", data_type=ColumnType.NUMBER),
                        ColumnMetadata(name="PERSON_ID", data_type=ColumnType.NUMBER),
                        ColumnMetadata(name="STATUS", data_type=ColumnType.VARCHAR),
                        ColumnMetadata(name="SECRET_NOTE", data_type=ColumnType.VARCHAR, restricted=True),
                        ColumnMetadata(name="COL1", data_type=ColumnType.VARCHAR),
                        ColumnMetadata(name="COL2", data_type=ColumnType.VARCHAR),
                        ColumnMetadata(name="COL3", data_type=ColumnType.VARCHAR),
                        ColumnMetadata(name="COL4", data_type=ColumnType.VARCHAR),
                        ColumnMetadata(name="COL5", data_type=ColumnType.VARCHAR),
                        ColumnMetadata(name="COL6", data_type=ColumnType.VARCHAR),
                    ],
                    primary_key=["PO_HEADER_ID"],
                    foreign_keys=[
                        ForeignKeyMetadata(
                            column="PERSON_ID",
                            referenced_table="XXBT_PDKS_PER_DETAILS_V",
                            referenced_column="PERSON_ID",
                        )
                    ],
                ),
            ]
        )

        pruned = PlanningContextAssemblyService.apply_focus_pruning(
            {
                "XXBT_PDKS_PER_DETAILS_V": ["AD"],
                "PO_HEADERS_ALL": ["STATUS", "SECRET_NOTE", "COL1", "COL2", "COL3", "COL4", "COL5", "COL6"],
            },
            snapshot,
            QueryUnderstanding(
                original_question="çalışan statüsü",
                normalized_question="calisan statusu",
            ),
            root_table_name="XXBT_PDKS_PER_DETAILS_V",
        )

        assert pruned["XXBT_PDKS_PER_DETAILS_V"] == ["AD"]
        assert "STATUS" in pruned["PO_HEADERS_ALL"]
        assert "PO_HEADER_ID" in pruned["PO_HEADERS_ALL"]
        assert "PERSON_ID" in pruned["PO_HEADERS_ALL"]
        assert "SECRET_NOTE" not in pruned["PO_HEADERS_ALL"]
        assert len(pruned["PO_HEADERS_ALL"]) <= 8

    @pytest.mark.asyncio
    async def test_retrieve_with_custom_top_k(
        self, retrieval_service: SchemaRetrievalService,
    ) -> None:
        snapshot = await retrieval_service.retrieve_context(
            "XXBT_PDKS_PER_DETAILS_V",
            top_k=1,
        )
        assert isinstance(snapshot, CatalogSnapshot)
        assert len(snapshot.tables) >= 1


# ---------------------------------------------------------------------------
# Planner integration with retrieval
# ---------------------------------------------------------------------------


class TestPlannerWithRetrieval:
    """Verify planner works when CatalogService uses retrieval."""

    @pytest.mark.asyncio
    async def test_planner_with_retrieval_service(self) -> None:
        """Planner should produce valid plans when retrieval is enabled."""
        from app.providers.llm.mock_llm import MockLLMProvider
        from app.services.catalog_service import CatalogService
        from app.services.planner_service import PlannerService

        provider = InMemoryCatalogProvider()
        retriever = InMemoryRetriever(provider)
        retrieval = SchemaRetrievalService(retriever)
        catalog = CatalogService(provider, retrieval=retrieval)
        planner = PlannerService(MockLLMProvider(), catalog)

        plan = await planner.plan("Aktif çalışanları listele")

        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert plan.needs_clarification is False
        assert len(plan.select_columns) > 0
