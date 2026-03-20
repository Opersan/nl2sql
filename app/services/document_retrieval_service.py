"""Document retrieval service.

Provides a high-level API that the planner uses to obtain relevant
document context (schema prose + few-shot examples) for a user query.
Delegates to a pluggable ``DocumentRetriever``.

This service mirrors ``SchemaRetrievalService`` for the document layer of
the hybrid architecture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.documents.models import ExampleDocument, SchemaDocument
from app.providers.retrieval.base import DocumentRetriever, DocumentRetrievalResult
from app.utils.turkish import casefold_tr

if TYPE_CHECKING:
    from app.services.query_understanding import QueryUnderstanding

logger = get_logger(__name__)

# Table-name patterns per module — used for filtering docs/examples
_MODULE_TABLE_PATTERNS: dict[str, list[str]] = {
    "HR": ["xxbt_pdks", "per_", "employee", "person", "hr_"],
    "PO": ["po_", "mtl_system_items", "purchase"],
}


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
        query_understanding: "QueryUnderstanding | None" = None,
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
        query_understanding:
            Optional pre-pass analysis for module-aware filtering.

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

        # Module-aware filtering: suppress docs from unrelated modules
        qu_modules: set[str] = set()
        if query_understanding is not None:
            qu_modules = set(query_understanding.inferred_modules)

        if qu_modules and not (query_understanding and query_understanding.multi_entity_flag):
            result = self._filter_by_module(result, qu_modules)

        result = self._semantic_rerank(user_query, result, k_docs=k_docs)

        logger.info(
            "Retrieved %d doc(s) + %d example(s) for query: %.60s",
            len(result.schema_docs),
            len(result.examples),
            user_query,
        )
        return result

    @staticmethod
    def _doc_belongs_to_module(
        table_name: str | None, tags: set[str], modules: set[str],
    ) -> bool | None:
        """Check if a doc/example belongs to one of the given modules.

        Returns True = belongs, False = belongs to *another* module,
        None = cannot determine (no table info → keep it).
        """
        if not table_name:
            return None
        tn = table_name.lower()
        for module, patterns in _MODULE_TABLE_PATTERNS.items():
            if any(p in tn for p in patterns):
                return module in modules
        return None  # Unknown table → keep

    @staticmethod
    def _filter_by_module(
        result: DocumentRetrievalResult,
        modules: set[str],
    ) -> DocumentRetrievalResult:
        """Remove docs and examples that clearly belong to a different module."""
        filtered_docs: list[SchemaDocument] = []
        for d in result.schema_docs:
            tags = {casefold_tr(t) for t in d.tags}
            belongs = DocumentRetrievalService._doc_belongs_to_module(
                d.table_name, tags, modules,
            )
            if belongs is not False:
                filtered_docs.append(d)

        filtered_examples: list[ExampleDocument] = []
        for e in result.examples:
            tags = {casefold_tr(t) for t in e.tags}
            # Check all tables; if ANY table belongs to a different module, filter
            keep = True
            for tbl in e.tables:
                belongs = DocumentRetrievalService._doc_belongs_to_module(
                    tbl, tags, modules,
                )
                if belongs is False:
                    keep = False
                    break
            if keep:
                filtered_examples.append(e)

        return DocumentRetrievalResult(
            schema_docs=filtered_docs,
            examples=filtered_examples,
        )

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
