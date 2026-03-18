"""Hybrid schema retriever using Reciprocal Rank Fusion.

Phase 4 retriever — fuses keyword scores and semantic scores via RRF
(Reciprocal Rank Fusion) to get the benefits of both retrieval strategies.

RRF formula
-----------
    score(d) = alpha * 1/(rank_keyword + k) + (1-alpha) * 1/(rank_semantic + k)

where k=60 is the standard RRF constant that dampens rank importance for
very high-ranked results.

The fused ranking is computed over ``top_k * 2`` candidates from each
retriever to ensure enough overlap for fusion, then the final top-K are
returned.
"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.domain.catalog_models import CatalogSnapshot, RelationshipMetadata
from app.providers.retrieval.base import SchemaRetriever

logger = get_logger(__name__)

_RRF_K: int = 60  # Standard RRF damping constant


class HybridRetriever(SchemaRetriever):
    """RRF-fused retriever combining keyword and semantic back-ends."""

    def __init__(
        self,
        keyword: SchemaRetriever,
        semantic: SchemaRetriever,
        *,
        alpha: float = 0.5,
    ) -> None:
        """
        Parameters
        ----------
        keyword:
            Keyword-based retriever (InMemoryRetriever).
        semantic:
            Dense embedding retriever (EmbeddingRetriever).
        alpha:
            Weight for keyword scores.  ``1-alpha`` goes to semantic.
            alpha=1.0 → pure keyword; alpha=0.0 → pure semantic.
        """
        self._keyword = keyword
        self._semantic = semantic
        self._alpha = alpha

    async def retrieve(
        self,
        user_query: str,
        *,
        top_k: int = 5,
    ) -> CatalogSnapshot:
        # Retrieve candidates from both back-ends in parallel
        candidate_k = min(top_k * 2, 20)
        kw_snap, sem_snap = await asyncio.gather(
            self._keyword.retrieve(user_query, top_k=candidate_k),
            self._semantic.retrieve(user_query, top_k=candidate_k),
        )

        # Build a combined table registry (name → TableMetadata)
        name_to_table = {t.name: t for t in kw_snap.tables}
        for t in sem_snap.tables:
            name_to_table.setdefault(t.name, t)

        # Accumulate RRF scores
        scores: dict[str, float] = {}
        for rank, table in enumerate(kw_snap.tables):
            scores[table.name] = scores.get(table.name, 0.0) + self._alpha / (
                rank + _RRF_K
            )
        for rank, table in enumerate(sem_snap.tables):
            scores[table.name] = scores.get(table.name, 0.0) + (
                1.0 - self._alpha
            ) / (rank + _RRF_K)

        # Sort by fused score, return top-K
        ranked_names = sorted(scores, key=lambda n: scores[n], reverse=True)
        selected = [
            name_to_table[n] for n in ranked_names[:top_k] if n in name_to_table
        ]

        if not selected:
            selected = kw_snap.tables[:top_k]

        # Collect relationships across both snapshots, filter to selected set
        all_rels = list(kw_snap.relationships) + [
            r for r in sem_snap.relationships if r not in kw_snap.relationships
        ]
        selected_names = {t.name for t in selected}
        rels: list[RelationshipMetadata] = [
            r
            for r in all_rels
            if r.from_table in selected_names and r.to_table in selected_names
        ]

        logger.debug(
            "[hybrid-retriever] top=%d kw=%d sem=%d fused=%d (alpha=%.2f)",
            top_k,
            len(kw_snap.tables),
            len(sem_snap.tables),
            len(selected),
            self._alpha,
        )
        return CatalogSnapshot(tables=selected, relationships=rels)
