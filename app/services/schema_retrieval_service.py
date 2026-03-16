"""Schema retrieval service.

Provides a high-level API that the planner (and future services) use to
obtain relevant catalog context for a user query.  The service delegates
to a pluggable ``SchemaRetriever`` and can optionally apply post-retrieval
transformations (e.g. column pruning, description enrichment).

Sprint 3 wiring
================
* Sprint 1-2: ``CatalogService.get_relevant_context()`` returns the full
  snapshot.  It now delegates to this service when retrieval is enabled.
* Sprint 3+: swap ``InMemoryRetriever`` for a BM25 / vector retriever
  without touching the planner or prompt builder.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.catalog_models import CatalogSnapshot
from app.providers.retrieval.base import SchemaRetriever

logger = get_logger(__name__)


class SchemaRetrievalService:
    """Retrieve relevant schema context for a user query."""

    def __init__(self, retriever: SchemaRetriever) -> None:
        self._retriever = retriever

    async def retrieve_context(
        self,
        user_query: str,
        *,
        top_k: int | None = None,
    ) -> CatalogSnapshot:
        """Return catalog context relevant to *user_query*.

        Parameters
        ----------
        user_query:
            The natural-language question.
        top_k:
            Max tables to retrieve.  Falls back to
            ``settings.retrieval_top_k`` when not provided.

        Returns
        -------
        A ``CatalogSnapshot`` with only the most relevant tables.
        """
        k = top_k if top_k is not None else settings.retrieval_top_k
        snapshot = await self._retriever.retrieve(user_query, top_k=k)

        logger.info(
            "Retrieved %d table(s) for query (top_k=%d): %.60s",
            len(snapshot.tables),
            k,
            user_query,
        )
        return snapshot
