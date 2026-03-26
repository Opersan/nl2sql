"""Semantic schema retriever using embedding cosine similarity.

Phase 3 retriever — uses dense table-level embeddings to find the most
semantically relevant tables for a user query.

Architecture
------------
* ``CatalogEmbeddingIndexer`` owns the embedding matrix and cache lifecycle.
* ``EmbeddingRetriever`` asks the indexer for the matrix, embeds the query,
  does a dot-product (all rows are L2-normalised so dot == cosine), and
  returns the top-K tables as a ``CatalogSnapshot``.
* Falls back to returning the first ``top_k`` tables when the index is not
  ready (same behaviour as ``InMemoryRetriever``'s zero-score fallback).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.domain.catalog_models import CatalogSnapshot, RelationshipMetadata
from app.providers.catalog.base import CatalogProvider
from app.providers.embedding.base import EmbeddingProvider
from app.providers.retrieval.base import SchemaRetriever
from app.providers.retrieval.gating_policy import (
    is_high_confidence_single_domain,
    table_module_from_registry,
)
from app.services.catalog_embedding_indexer import CatalogEmbeddingIndexer

if TYPE_CHECKING:
    from app.services.query_understanding import QueryUnderstanding

logger = get_logger(__name__)


class EmbeddingRetriever(SchemaRetriever):
    """Dense-vector semantic retriever backed by a ``CatalogEmbeddingIndexer``."""

    def __init__(
        self,
        catalog_provider: CatalogProvider,
        indexer: CatalogEmbeddingIndexer,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._catalog = catalog_provider
        self._indexer = indexer
        self._emb = embedding_provider

    async def retrieve(
        self,
        user_query: str,
        *,
        top_k: int = 5,
        query_understanding: "QueryUnderstanding | None" = None,
    ) -> CatalogSnapshot:
        snapshot = await self._catalog.get_snapshot()
        ready = await self._indexer.ensure_built()

        if not ready or self._indexer.get_matrix() is None:
            logger.warning(
                "[embedding-retriever] index not ready — returning first %d tables",
                top_k,
            )
            fallback = snapshot.tables[:top_k]
            return CatalogSnapshot(
                tables=fallback,
                relationships=self._filter_relationships(snapshot, fallback),
            )

        try:
            import numpy as np
        except ImportError:
            fallback = snapshot.tables[:top_k]
            return CatalogSnapshot(
                tables=fallback,
                relationships=self._filter_relationships(snapshot, fallback),
            )

        # Embed the query
        try:
            q_vecs = await self._emb.embed_texts([user_query])
        except Exception:
            logger.exception("[embedding-retriever] query embedding failed — fallback")
            fallback = snapshot.tables[:top_k]
            return CatalogSnapshot(
                tables=fallback,
                relationships=self._filter_relationships(snapshot, fallback),
            )

        q_vec = np.array(q_vecs[0], dtype=np.float32)
        norm = np.linalg.norm(q_vec)
        if norm > 0:
            q_vec = q_vec / norm

        matrix = self._indexer.get_matrix()  # (n_tables, dim)
        scores = matrix @ q_vec  # (n_tables,)

        # Map indexer table names → snapshot TableMetadata objects
        name_to_table = {t.name: t for t in snapshot.tables}
        ranked = sorted(
            zip(scores, self._indexer.table_names),
            key=lambda x: x[0],
            reverse=True,
        )

        # ------------------------------------------------------------------
        # Domain-aware post-filtering for high-confidence single-domain queries
        # ------------------------------------------------------------------
        # When QueryUnderstanding signals high-confidence single-domain intent,
        # suppress tables whose registry module conflicts with the primary module.
        # Table→module resolution uses SemanticFoundationRegistry (no substring
        # heuristics).  Tables not in the registry are kept (fail-open).
        should_gate, _primary_module = is_high_confidence_single_domain(query_understanding)
        if should_gate and _primary_module is not None:
            gated = [
                (s, n)
                for s, n in ranked
                if table_module_from_registry(n) in (_primary_module, None)
            ]
            if gated:  # guard: never empty the result entirely
                ranked = gated
                logger.debug(
                    "[embedding-retriever] domain-gating active module=%s "
                    "candidates_after=%d",
                    _primary_module, len(ranked),
                )

        selected = []
        for _score, name in ranked[:top_k]:
            table = name_to_table.get(name)
            if table is not None:
                selected.append(table)

        if not selected:
            selected = snapshot.tables[:top_k]

        return CatalogSnapshot(
            tables=selected,
            relationships=self._filter_relationships(snapshot, selected),
        )

    @staticmethod
    def _filter_relationships(
        snapshot: CatalogSnapshot,
        tables: list,
    ) -> list[RelationshipMetadata]:
        names = {t.name for t in tables}
        return [
            r
            for r in snapshot.relationships
            if r.from_table in names and r.to_table in names
        ]
