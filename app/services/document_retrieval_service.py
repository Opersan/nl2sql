"""Document retrieval service.

Provides a high-level API that the planner uses to obtain relevant
document context (schema prose + few-shot examples) for a user query.
Delegates to a pluggable ``DocumentRetriever``.

This service mirrors ``SchemaRetrievalService`` for the document layer of
the hybrid architecture.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.documents.models import ExampleDocument, SchemaDocument
from app.providers.retrieval.base import DocumentRetriever, DocumentRetrievalResult
from app.utils.turkish import casefold_tr

logger = get_logger(__name__)


class DocumentRetrievalService:
    """Retrieve relevant documents and examples for a user query."""

    def __init__(self, retriever: DocumentRetriever) -> None:
        self._retriever = retriever

    async def retrieve_context(
        self,
        user_query: str,
        *,
        top_k_docs: int | None = None,
        top_k_examples: int | None = None,
    ) -> DocumentRetrievalResult:
        """Return document context relevant to *user_query*.

        Parameters
        ----------
        user_query:
            The natural-language question.
        top_k_docs:
            Max schema documents to retrieve.  Falls back to
            ``settings.retrieval_top_k`` when not provided.
        top_k_examples:
            Max examples to retrieve.  Falls back to
            ``settings.retrieval_top_k_examples`` when not provided.

        Returns
        -------
        A ``DocumentRetrievalResult`` with schema docs and examples.
        """
        k_docs = top_k_docs if top_k_docs is not None else settings.retrieval_top_k
        k_examples = (
            top_k_examples
            if top_k_examples is not None
            else settings.retrieval_top_k_examples
        )

        result = await self._retriever.retrieve(
            user_query,
            top_k_docs=k_docs,
            top_k_examples=k_examples,
        )

        result = self._semantic_rerank(user_query, result, k_docs=k_docs)

        logger.info(
            "Retrieved %d doc(s) + %d example(s) for query: %.60s",
            len(result.schema_docs),
            len(result.examples),
            user_query,
        )
        return result

    @staticmethod
    def _semantic_rerank(
        user_query: str,
        result: DocumentRetrievalResult,
        *,
        k_docs: int,
    ) -> DocumentRetrievalResult:
        """Semantic-first reranking for enterprise multi-table planning.

        Retrieval order for PO-like queries:
        1) root entity docs
        2) join-path docs
        3) metric docs
        4) up to 2 canonical examples
        """
        folded = casefold_tr(user_query)
        po_signal = any(
            k in folded
            for k in ("po", "satınalma", "satın alma", "sipariş", "tedarikçi", "kalem", "dağıtım", "ürün", "item")
        )
        if not po_signal:
            return result

        docs = result.schema_docs
        examples = result.examples

        root_docs: list[SchemaDocument] = []
        join_docs: list[SchemaDocument] = []
        metric_docs: list[SchemaDocument] = []
        other_docs: list[SchemaDocument] = []

        for d in docs:
            tags = {casefold_tr(t) for t in d.tags}
            title = casefold_tr(d.title)
            table_name = casefold_tr(d.table_name or "")

            if d.doc_type.value == "table" and table_name == "po_headers_all":
                root_docs.append(d)
            elif "join" in tags or "multi_table" in tags or "join" in title:
                join_docs.append(d)
            elif d.doc_type.value == "column" and (
                {"miktar", "tutar", "amount", "quantity", "aggregation"} & tags
                or any(x in title for x in ("miktar", "tutar", "quantity", "amount"))
            ):
                metric_docs.append(d)
            else:
                other_docs.append(d)

        ordered_docs = (root_docs + join_docs + metric_docs + other_docs)[:k_docs]

        canonical_examples: list[ExampleDocument] = []
        fallback_examples: list[ExampleDocument] = []
        for e in examples:
            tags = {casefold_tr(t) for t in e.tags}
            tables = {casefold_tr(t) for t in e.tables}
            if (
                ("join" in tags or "multi_table" in tags)
                and "po_headers_all" in tables
                and len(tables) >= 2
            ):
                canonical_examples.append(e)
            else:
                fallback_examples.append(e)

        ordered_examples = (canonical_examples[:2] + fallback_examples)[: max(2, len(examples))]

        return DocumentRetrievalResult(
            schema_docs=ordered_docs,
            examples=ordered_examples,
        )
