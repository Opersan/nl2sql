"""Document retrieval service.

Provides a high-level API that the planner uses to obtain relevant
document context (schema prose + few-shot examples) for a user query.
Delegates to a pluggable ``DocumentRetriever``.

This service mirrors ``SchemaRetrievalService`` for the document layer of
the hybrid architecture.
"""

from __future__ import annotations

import asyncio
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
        self._task_state: dict[int, dict[str, object] | None] = {}
        self._fallback_state: dict[str, object] | None = None

    @property
    def last_retrieval_diagnostics(self) -> dict[str, object] | None:
        task = asyncio.current_task()
        if task is not None and id(task) in self._task_state:
            return self._task_state[id(task)]
        return self._fallback_state

    def _set_last_retrieval_diagnostics(self, payload: dict[str, object] | None) -> None:
        self._fallback_state = payload
        task = asyncio.current_task()
        if task is not None:
            self._task_state[id(task)] = payload
            if len(self._task_state) > 2048:
                self._task_state.clear()

    async def retrieve_context(
        self,
        user_query: str,
        *,
        top_k_docs: int | None = None,
        top_k_examples: int | None = None,
        query_understanding: "QueryUnderstanding | None" = None,
        retrieved_tables: list[str] | None = None,
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

        rerank_result, diagnostics = self._rerank_documents(
            user_query,
            result,
            k_docs=k_docs,
            k_examples=k_examples,
            query_understanding=query_understanding,
            retrieved_tables=retrieved_tables or [],
        )
        result = rerank_result
        self._set_last_retrieval_diagnostics(diagnostics)

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

    @staticmethod
    def _infer_item_module(table_name: str | None, modules: set[str]) -> str | None:
        if table_name:
            tn = casefold_tr(table_name)
            for module, patterns in _MODULE_TABLE_PATTERNS.items():
                if any(pattern in tn for pattern in patterns):
                    return module
        for module in modules:
            return module
        return None

    @classmethod
    def _rerank_documents(
        cls,
        user_query: str,
        result: DocumentRetrievalResult,
        *,
        k_docs: int,
        k_examples: int,
        query_understanding: "QueryUnderstanding | None",
        retrieved_tables: list[str],
    ) -> tuple[DocumentRetrievalResult, dict[str, object]]:
        base_diag = getattr(result, "diagnostics", None)
        _ = base_diag
        folded = casefold_tr(user_query)
        primary_module = query_understanding.primary_module() if query_understanding is not None else None
        retrieved_table_set = {table.upper() for table in retrieved_tables}
        root_table_name = retrieved_tables[0].upper() if retrieved_tables else None

        kept_candidates_reason: dict[str, str] = {}
        dropped_candidates: list[str] = []
        noisy_context_count = 0

        def _doc_priority(doc: SchemaDocument) -> tuple[int, int, int, int]:
            nonlocal noisy_context_count
            module = doc.module or cls._infer_item_module(doc.table_name, {casefold_tr(tag).upper() for tag in doc.tags})
            table_name = (doc.table_name or "").upper()
            same_table = int(bool(table_name and table_name in retrieved_table_set))
            root_match = int(bool(root_table_name and table_name == root_table_name))
            module_match = int(bool(primary_module and module == primary_module))
            cross_domain = int(bool(primary_module and module and module != primary_module))
            doc_type_rank = 1 if doc.doc_type.value in {"table", "column", "relationship"} else 0
            if cross_domain and not same_table:
                dropped_candidates.append(f"{doc.doc_id}:cross_domain_doc")
                return (-1, -1, -1, -1)
            reason_bits = []
            if root_match:
                reason_bits.append("root_table_doc")
            if same_table:
                reason_bits.append("retrieved_table_match")
            if module_match:
                reason_bits.append("same_domain_doc")
            kept_candidates_reason[doc.doc_id] = ",".join(reason_bits) or "keyword_doc"
            if cross_domain:
                noisy_context_count += 1
            return (root_match, same_table, module_match, doc_type_rank - cross_domain)

        def _example_priority(example: ExampleDocument) -> tuple[int, int, int, int]:
            nonlocal noisy_context_count
            table_set = {table.upper() for table in example.tables}
            example_modules = {
                inferred
                for table in example.tables
                for inferred in [cls._infer_item_module(table, set())]
                if inferred is not None
            }
            root_match = int(bool(root_table_name and root_table_name in table_set))
            table_overlap = len(table_set & retrieved_table_set)
            module_match = int(bool(primary_module and primary_module in example_modules))
            cross_domain = int(bool(primary_module and example_modules and primary_module not in example_modules and not table_overlap))
            canonical_shape = int(any(tag in {"join", "multi_table", "aggregation"} for tag in map(casefold_tr, example.tags)))
            if cross_domain:
                dropped_candidates.append(f"{example.doc_id}:cross_domain_example")
                return (-1, -1, -1, -1)
            reason_bits = []
            if root_match:
                reason_bits.append("root_table_example")
            if table_overlap:
                reason_bits.append("retrieved_table_overlap")
            if module_match:
                reason_bits.append("same_domain_example")
            if canonical_shape:
                reason_bits.append("canonical_shape")
            kept_candidates_reason[example.doc_id] = ",".join(reason_bits) or "keyword_example"
            if primary_module and example_modules and primary_module not in example_modules:
                noisy_context_count += 1
            return (root_match, table_overlap, module_match, canonical_shape)

        scored_docs: list[tuple[tuple[int, int, int, int], SchemaDocument]] = []
        for doc in result.schema_docs:
            priority = _doc_priority(doc)
            if priority[0] >= 0:
                scored_docs.append((priority, doc))
        scored_docs.sort(key=lambda item: item[0], reverse=True)

        scored_examples: list[tuple[tuple[int, int, int, int], ExampleDocument]] = []
        for example in result.examples:
            priority = _example_priority(example)
            if priority[0] >= 0:
                scored_examples.append((priority, example))
        scored_examples.sort(key=lambda item: item[0], reverse=True)

        docs = [doc for _, doc in scored_docs]
        examples = [example for _, example in scored_examples]

        reranked = DocumentRetrievalResult(
            schema_docs=docs[:k_docs],
            examples=examples[:k_examples],
        )

        if primary_module == "PO":
            reranked = cls._semantic_rerank(user_query, reranked, k_docs=k_docs)

        return reranked, {
            "noisy_context_count": noisy_context_count,
            "dropped_candidates": dropped_candidates,
            "kept_candidates_reason": kept_candidates_reason,
        }
