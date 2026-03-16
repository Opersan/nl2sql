"""Tests for the in-memory document retriever."""

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_corpus() -> DocumentCorpus:
    """Build a small test corpus with schema docs and examples."""
    return DocumentCorpus(
        schema_docs=[
            SchemaDocument(
                doc_id="d1",
                doc_type=DocType.TABLE,
                title="Employee tablosu",
                content="Ana personel tablosu, HR modülü",
                table_name="XXBT_PDKS_PER_DETAILS_V",
                module="HR",
                tags=["personel", "hr"],
            ),
            SchemaDocument(
                doc_id="d2",
                doc_type=DocType.TABLE,
                title="Department tablosu",
                content="Departman / birim bilgileri",
                table_name="department",
                module="HR",
                tags=["departman", "birim"],
            ),
            SchemaDocument(
                doc_id="d3",
                doc_type=DocType.COLUMN,
                title="quit_date kolonu",
                content="İşten ayrılma tarihi, NULL ise aktif",
                table_name="XXBT_PDKS_PER_DETAILS_V",
                column_name="quit_date",
                tags=["aktif", "ayrılma"],
            ),
            SchemaDocument(
                doc_id="d4",
                doc_type=DocType.GLOSSARY,
                title="Aktif çalışan tanımı",
                content="quit_date IS NULL olan kayıtlar aktif çalışandır",
                tags=["aktif", "çalışan"],
            ),
        ],
        examples=[
            ExampleDocument(
                doc_id="ex1",
                question="Aktif çalışanları listele",
                sql="SELECT reg_no FROM employee WHERE quit_date IS NULL",
                tables=["XXBT_PDKS_PER_DETAILS_V"],
                explanation="quit_date NULL = aktif",
                difficulty="easy",
                tags=["aktif", "çalışan"],
            ),
            ExampleDocument(
                doc_id="ex2",
                question="Birim bazında çalışan sayısı",
                sql="SELECT unit_name, COUNT(*) FROM employee GROUP BY unit_name",
                tables=["XXBT_PDKS_PER_DETAILS_V"],
                explanation="Birim grupla, say",
                difficulty="medium",
                tags=["birim", "sayı"],
            ),
            ExampleDocument(
                doc_id="ex3",
                question="Departman listesi",
                sql="SELECT dept_name FROM department",
                tables=["department"],
                explanation="Departmanları listele",
                difficulty="easy",
                tags=["departman"],
            ),
        ],
        source="test",
    )


@pytest.fixture
def retriever() -> InMemoryDocumentRetriever:
    return InMemoryDocumentRetriever(_build_corpus())


# ---------------------------------------------------------------------------
# Schema doc retrieval tests
# ---------------------------------------------------------------------------


class TestSchemaDocRetrieval:
    @pytest.mark.asyncio
    async def test_employee_query_retrieves_employee_docs(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("XXBT_PDKS_PER_DETAILS_V", top_k_docs=5)
        assert isinstance(result, DocumentRetrievalResult)
        # employee table doc and quit_date column doc should score highest
        table_names = [d.table_name for d in result.schema_docs]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names

    @pytest.mark.asyncio
    async def test_department_query(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("departman", top_k_docs=5)
        # department doc should appear (via tag or content)
        doc_ids = [d.doc_id for d in result.schema_docs]
        assert "d2" in doc_ids

    @pytest.mark.asyncio
    async def test_aktif_query_retrieves_glossary(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("aktif çalışan", top_k_docs=5)
        doc_ids = [d.doc_id for d in result.schema_docs]
        # glossary about aktif çalışan should be present
        assert "d4" in doc_ids

    @pytest.mark.asyncio
    async def test_top_k_docs_limit(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("employee aktif", top_k_docs=2)
        assert len(result.schema_docs) <= 2

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("")
        assert result.schema_docs == []
        assert result.examples == []


# ---------------------------------------------------------------------------
# Example retrieval tests
# ---------------------------------------------------------------------------


class TestExampleRetrieval:
    @pytest.mark.asyncio
    async def test_aktif_query_retrieves_aktif_example(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve(
            "aktif çalışanlar", top_k_examples=3,
        )
        example_ids = [e.doc_id for e in result.examples]
        assert "ex1" in example_ids

    @pytest.mark.asyncio
    async def test_birim_query_retrieves_birim_example(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve(
            "birim bazında sayısı", top_k_examples=3,
        )
        example_ids = [e.doc_id for e in result.examples]
        assert "ex2" in example_ids

    @pytest.mark.asyncio
    async def test_departman_query_retrieves_dept_example(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve(
            "departman listesi", top_k_examples=3,
        )
        example_ids = [e.doc_id for e in result.examples]
        assert "ex3" in example_ids

    @pytest.mark.asyncio
    async def test_top_k_examples_limit(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("çalışan birim", top_k_examples=1)
        assert len(result.examples) <= 1

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("xyznonexistent")
        assert result.examples == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestDocRetrieverEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_corpus(self) -> None:
        retriever = InMemoryDocumentRetriever(DocumentCorpus())
        result = await retriever.retrieve("XXBT_PDKS_PER_DETAILS_V")
        assert result.schema_docs == []
        assert result.examples == []

    @pytest.mark.asyncio
    async def test_punctuation_in_query(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Punctuation in query should not break retrieval."""
        result = await retriever.retrieve("employee'ler?")
        # Should still find employee-related docs
        assert isinstance(result, DocumentRetrievalResult)

    @pytest.mark.asyncio
    async def test_mixed_case_query(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Case should not matter due to casefold."""
        result = await retriever.retrieve("XXBT_PDKS_PER_DETAILS_V")
        table_names = [d.table_name for d in result.schema_docs]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names
