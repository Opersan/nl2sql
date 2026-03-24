"""Tests for DocumentRetrievalService."""

from __future__ import annotations

import pytest

from app.providers.documents.models import (
    DocType,
    DocumentCorpus,
    ExampleDocument,
    SchemaDocument,
)
from app.providers.retrieval.base import DocumentRetrievalResult
from app.providers.retrieval.in_memory_doc_retriever import InMemoryDocumentRetriever
from app.services.document_retrieval_service import DocumentRetrievalService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_corpus() -> DocumentCorpus:
    return DocumentCorpus(
        schema_docs=[
            SchemaDocument(
                doc_id="d1",
                doc_type=DocType.TABLE,
                title="Employee tablosu",
                content="Personel tablosu",
                table_name="XXBT_PDKS_PER_DETAILS_V",
                tags=["personel"],
            ),
        ],
        examples=[
            ExampleDocument(
                doc_id="ex1",
                question="Aktif çalışanlar",
                sql="SELECT reg_no FROM employee WHERE quit_date IS NULL",
                tables=["XXBT_PDKS_PER_DETAILS_V"],
                tags=["aktif"],
            ),
        ],
        source="test",
    )


@pytest.fixture
def service() -> DocumentRetrievalService:
    corpus = _build_corpus()
    retriever = InMemoryDocumentRetriever(corpus)
    return DocumentRetrievalService(retriever)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDocumentRetrievalService:
    @pytest.mark.asyncio
    async def test_retrieve_context_returns_result(
        self, service: DocumentRetrievalService,
    ) -> None:
        result = await service.retrieve_context("XXBT_PDKS_PER_DETAILS_V")
        assert isinstance(result, DocumentRetrievalResult)

    @pytest.mark.asyncio
    async def test_retrieve_finds_relevant_docs(
        self, service: DocumentRetrievalService,
    ) -> None:
        result = await service.retrieve_context("XXBT_PDKS_PER_DETAILS_V")
        assert len(result.schema_docs) >= 1
        assert result.schema_docs[0].table_name == "XXBT_PDKS_PER_DETAILS_V"

    @pytest.mark.asyncio
    async def test_retrieve_finds_relevant_examples(
        self, service: DocumentRetrievalService,
    ) -> None:
        result = await service.retrieve_context("aktif çalışan")
        assert len(result.examples) >= 1

    @pytest.mark.asyncio
    async def test_top_k_override(
        self, service: DocumentRetrievalService,
    ) -> None:
        result = await service.retrieve_context(
            "XXBT_PDKS_PER_DETAILS_V", top_k_docs=1, top_k_examples=1,
        )
        assert len(result.schema_docs) <= 1
        assert len(result.examples) <= 1

    @pytest.mark.asyncio
    async def test_empty_query(
        self, service: DocumentRetrievalService,
    ) -> None:
        result = await service.retrieve_context("")
        assert result.schema_docs == []
        assert result.examples == []

    @pytest.mark.asyncio
    async def test_rerank_drops_cross_domain_context_without_duplicate_diagnostics(self) -> None:
        class _Retriever:
            async def retrieve(self, *args: object, **kwargs: object) -> DocumentRetrievalResult:
                return DocumentRetrievalResult(
                    schema_docs=[
                        SchemaDocument(
                            doc_id="hr-doc",
                            doc_type=DocType.TABLE,
                            title="Employee tablosu",
                            content="Personel kayıtları",
                            table_name="XXBT_PDKS_PER_DETAILS_V",
                            module="HR",
                            tags=["personel"],
                        ),
                        SchemaDocument(
                            doc_id="po-doc",
                            doc_type=DocType.TABLE,
                            title="PO header",
                            content="Satınalma başlıkları",
                            table_name="PO_HEADERS_ALL",
                            module="PO",
                            tags=["satınalma"],
                        ),
                    ],
                    examples=[
                        ExampleDocument(
                            doc_id="hr-ex",
                            question="Aktif çalışanlar",
                            sql="SELECT reg_no FROM xxbt_pdks_per_details_v WHERE quit_date IS NULL",
                            tables=["XXBT_PDKS_PER_DETAILS_V"],
                            tags=["aktif"],
                        ),
                        ExampleDocument(
                            doc_id="po-ex",
                            question="Açık satınalma siparişleri",
                            sql="SELECT segment1 FROM po_headers_all",
                            tables=["PO_HEADERS_ALL"],
                            tags=["satınalma"],
                        ),
                    ],
                )

        service = DocumentRetrievalService(_Retriever())

        from app.services.query_understanding import QueryUnderstanding

        result = await service.retrieve_context(
            "aktif çalışanları listele",
            query_understanding=QueryUnderstanding(
                original_question="aktif çalışanları listele",
                normalized_question="aktif calisanlari listele",
                inferred_modules=["HR"],
                multi_entity_flag=True,
            ),
            retrieved_tables=["XXBT_PDKS_PER_DETAILS_V"],
            top_k_docs=5,
            top_k_examples=5,
        )

        assert [doc.doc_id for doc in result.schema_docs] == ["hr-doc"]
        assert [example.doc_id for example in result.examples] == ["hr-ex"]

        diagnostics = service.last_retrieval_diagnostics
        assert diagnostics is not None
        assert diagnostics["dropped_candidates"] == [
            "po-doc:cross_domain_doc",
            "po-ex:cross_domain_example",
        ]
        assert diagnostics["kept_candidates_reason"]["hr-doc"] == "root_table_doc,retrieved_table_match,same_domain_doc"
        assert diagnostics["kept_candidates_reason"]["hr-ex"] == "root_table_example,retrieved_table_overlap,same_domain_example"
        assert diagnostics["noisy_context_count"] == 0
