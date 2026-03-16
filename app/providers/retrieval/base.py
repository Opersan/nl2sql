"""Abstract bases for retrieval providers.

Two retriever contracts live here:

1. **SchemaRetriever** – returns a ``CatalogSnapshot`` subset (structured
   metadata layer).
2. **DocumentRetriever** – returns lists of ``SchemaDocument`` and/or
   ``ExampleDocument`` (document / few-shot layer).

Both are consumed by the hybrid planner pipeline:

    classify intent → retrieve schema → retrieve documents/examples → plan

Sprint 3 roadmap
================
* **Phase 1 (this skeleton):** keyword / alias matching for both layers.
* **Phase 2:** TF-IDF or BM25 over table/column descriptions.
* **Phase 3:** Vector-similarity search over embedded metadata.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.domain.catalog_models import CatalogSnapshot
from app.providers.documents.models import ExampleDocument, SchemaDocument


# ---------------------------------------------------------------------------
# Schema retriever (structured metadata)
# ---------------------------------------------------------------------------


class SchemaRetriever(ABC):
    """Contract for schema retrieval back-ends."""

    @abstractmethod
    async def retrieve(
        self,
        user_query: str,
        *,
        top_k: int = 5,
    ) -> CatalogSnapshot:
        """Return the most relevant catalog subset for *user_query*.

        Parameters
        ----------
        user_query:
            The natural-language question from the user.
        top_k:
            Maximum number of tables to include in the result.  Columns
            within each table are always returned in full (column-level
            pruning is a Phase 3 enhancement).

        Returns
        -------
        A ``CatalogSnapshot`` containing only the retrieved tables.
        """
        ...


# ---------------------------------------------------------------------------
# Document retriever result
# ---------------------------------------------------------------------------


@dataclass
class DocumentRetrievalResult:
    """Container for document retrieval output."""

    schema_docs: list[SchemaDocument] = field(default_factory=list)
    examples: list[ExampleDocument] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Document retriever (document / few-shot layer)
# ---------------------------------------------------------------------------


class DocumentRetriever(ABC):
    """Contract for document / example retrieval back-ends."""

    @abstractmethod
    async def retrieve(
        self,
        user_query: str,
        *,
        top_k_docs: int = 5,
        top_k_examples: int = 3,
    ) -> DocumentRetrievalResult:
        """Return the most relevant documents and examples for *user_query*.

        Parameters
        ----------
        user_query:
            The natural-language question from the user.
        top_k_docs:
            Maximum schema documents to return.
        top_k_examples:
            Maximum few-shot examples to return.

        Returns
        -------
        A ``DocumentRetrievalResult`` with schema docs and examples.
        """
        ...
