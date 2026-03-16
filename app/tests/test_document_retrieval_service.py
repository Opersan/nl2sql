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
