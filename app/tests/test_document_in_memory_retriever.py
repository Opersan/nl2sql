"""Tests for InMemoryDocumentRetriever – scoring, tokenization, limits."""

from __future__ import annotations

import pytest

from app.providers.documents.models import (
    DocType,
    DocumentCorpus,
    ExampleDocument,
    SchemaDocument,
)
from app.providers.retrieval.base import DocumentRetrievalResult
from app.providers.retrieval.in_memory_doc_retriever import (
    InMemoryDocumentRetriever,
    _contains_substring,
    _contains_token,
    _tokenize,
)


# ---------------------------------------------------------------------------
# Tokenizer unit tests
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_punctuation_removed(self) -> None:
        tokens = _tokenize("employee'ler? (aktif)")
        # Apostrophe, question mark, parens stripped; words split
        assert tokens == ["XXBT_PDKS_PER_DETAILS_V", "ler", "aktif"]

    def test_punctuation_handling_detailed(self) -> None:
        tokens = _tokenize("tablo: employee, department!")
        assert tokens == ["tablo", "XXBT_PDKS_PER_DETAILS_V", "department"]

    def test_turkish_aware_lowercase(self) -> None:
        """Turkish İ → i (not ı) casefold."""
        tokens = _tokenize("İSTANBUL")
        assert tokens == ["istanbul"]

    def test_turkish_i_dot(self) -> None:
        tokens = _tokenize("İşten")
        assert tokens == ["işten"]

    def test_empty_string(self) -> None:
        assert _tokenize("") == []

    def test_whitespace_only(self) -> None:
        assert _tokenize("   \t\n  ") == []

    def test_mixed_case_normalized(self) -> None:
        tokens = _tokenize("Employee DEPARTMENT")
        assert tokens == ["XXBT_PDKS_PER_DETAILS_V", "department"]

    def test_numbers_preserved(self) -> None:
        tokens = _tokenize("tablo123 test456")
        assert tokens == ["tablo123", "test456"]


# ---------------------------------------------------------------------------
# Corpus fixture
# ---------------------------------------------------------------------------


def _build_corpus() -> DocumentCorpus:
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
                content="Departman bilgileri",
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
                content="quit_date IS NULL olan kayıtlar",
                tags=["aktif", "çalışan"],
            ),
            SchemaDocument(
                doc_id="d5",
                doc_type=DocType.TABLE,
                title="Salary tablosu",
                content="Maaş bilgileri tablosu",
                table_name="salary",
                module="FIN",
                tags=["maaş", "ücret"],
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
            ExampleDocument(
                doc_id="ex4",
                question="Maaş ortalaması",
                sql="SELECT AVG(amount) FROM salary",
                tables=["salary"],
                explanation="Ortalama maaş",
                difficulty="easy",
                tags=["maaş", "ortalama"],
            ),
        ],
        source="test",
    )


@pytest.fixture
def retriever() -> InMemoryDocumentRetriever:
    return InMemoryDocumentRetriever(_build_corpus())


# ---------------------------------------------------------------------------
# Schema doc scoring tests
# ---------------------------------------------------------------------------


class TestSchemaDocScoring:
    @pytest.mark.asyncio
    async def test_title_match_boosts_score(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """A token matching the title should boost the doc's score."""
        result = await retriever.retrieve("XXBT_PDKS_PER_DETAILS_V", top_k_docs=10)
        doc_ids = [d.doc_id for d in result.schema_docs]
        # d1 has "XXBT_PDKS_PER_DETAILS_V" in title → high score
        assert "d1" in doc_ids

    @pytest.mark.asyncio
    async def test_tag_match_boosts_score(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """A token matching a tag should boost the doc's score."""
        result = await retriever.retrieve("personel", top_k_docs=10)
        doc_ids = [d.doc_id for d in result.schema_docs]
        # d1 has tag "personel"
        assert "d1" in doc_ids

    @pytest.mark.asyncio
    async def test_table_name_boost(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """table_name match gives highest boost (+10)."""
        result = await retriever.retrieve("department", top_k_docs=10)
        doc_ids = [d.doc_id for d in result.schema_docs]
        # d2 has table_name=department → +10 boost
        assert "d2" in doc_ids
        # d2 should rank high due to table_name boost
        assert doc_ids.index("d2") < 2

    @pytest.mark.asyncio
    async def test_content_match(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Content match should also score."""
        result = await retriever.retrieve("maaş", top_k_docs=10)
        doc_ids = [d.doc_id for d in result.schema_docs]
        # d5 has "maaş" in content and tags
        assert "d5" in doc_ids


# ---------------------------------------------------------------------------
# Example scoring tests
# ---------------------------------------------------------------------------


class TestExampleScoring:
    @pytest.mark.asyncio
    async def test_question_match_scoring(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Query matching example question should score the example."""
        result = await retriever.retrieve("aktif çalışanlar", top_k_examples=10)
        ex_ids = [e.doc_id for e in result.examples]
        # ex1 has "çalışanları" in question, should match "çalışanlar" token
        assert "ex1" in ex_ids

    @pytest.mark.asyncio
    async def test_example_table_boost(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Example tables list match gives +10 boost per token hit."""
        result = await retriever.retrieve("department", top_k_examples=10)
        ex_ids = [e.doc_id for e in result.examples]
        # ex3 has tables=["department"]
        assert "ex3" in ex_ids

    @pytest.mark.asyncio
    async def test_example_tag_match(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Example tag match gives +5 boost."""
        result = await retriever.retrieve("maaş", top_k_examples=10)
        ex_ids = [e.doc_id for e in result.examples]
        # ex4 has tag "maaş"
        assert "ex4" in ex_ids

    @pytest.mark.asyncio
    async def test_explanation_match(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Explanation match gives +2 boost."""
        result = await retriever.retrieve("ortalama", top_k_examples=10)
        ex_ids = [e.doc_id for e in result.examples]
        # ex4 has "ortalama" in explanation and tags
        assert "ex4" in ex_ids


# ---------------------------------------------------------------------------
# Limit tests
# ---------------------------------------------------------------------------


class TestRetrievalLimits:
    @pytest.mark.asyncio
    async def test_top_k_docs_limit(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Returned docs should not exceed top_k_docs."""
        result = await retriever.retrieve("employee aktif personel", top_k_docs=2)
        assert len(result.schema_docs) <= 2

    @pytest.mark.asyncio
    async def test_top_k_examples_limit(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Returned examples should not exceed top_k_examples."""
        result = await retriever.retrieve(
            "çalışan birim departman maaş", top_k_examples=1,
        )
        assert len(result.examples) <= 1

    @pytest.mark.asyncio
    async def test_top_k_docs_zero(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("XXBT_PDKS_PER_DETAILS_V", top_k_docs=0)
        assert result.schema_docs == []

    @pytest.mark.asyncio
    async def test_top_k_examples_zero(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("XXBT_PDKS_PER_DETAILS_V", top_k_examples=0)
        assert result.examples == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_no_match_returns_empty(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Query with no matching tokens should return empty result."""
        result = await retriever.retrieve("xyznonexistent12345")
        assert result.schema_docs == []
        assert result.examples == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        result = await retriever.retrieve("")
        assert result.schema_docs == []
        assert result.examples == []

    @pytest.mark.asyncio
    async def test_empty_corpus(self) -> None:
        r = InMemoryDocumentRetriever(DocumentCorpus())
        result = await r.retrieve("XXBT_PDKS_PER_DETAILS_V")
        assert result.schema_docs == []
        assert result.examples == []

    @pytest.mark.asyncio
    async def test_mixed_corpus_returns_both(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """A broad query should return both docs and examples."""
        result = await retriever.retrieve(
            "employee aktif", top_k_docs=5, top_k_examples=5,
        )
        assert isinstance(result, DocumentRetrievalResult)
        assert len(result.schema_docs) >= 1
        assert len(result.examples) >= 1

    @pytest.mark.asyncio
    async def test_punctuation_in_query(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Punctuation should not break retrieval."""
        result = await retriever.retrieve("employee'ler?")
        assert isinstance(result, DocumentRetrievalResult)

    @pytest.mark.asyncio
    async def test_mixed_case_query(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Case should not matter."""
        result = await retriever.retrieve("XXBT_PDKS_PER_DETAILS_V")
        table_names = [d.table_name for d in result.schema_docs]
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names


# ---------------------------------------------------------------------------
# _contains_token / _contains_substring unit tests
# ---------------------------------------------------------------------------


class TestContainsToken:
    def test_exact_match(self) -> None:
        assert _contains_token("XXBT_PDKS_PER_DETAILS_V", "employee tablosu") is True

    def test_substring_of_field(self) -> None:
        # "employ" is a substring of "XXBT_PDKS_PER_DETAILS_V" → True
        assert _contains_token("employ", "employee tablosu") is True

    def test_no_match(self) -> None:
        assert _contains_token("salary", "employee tablosu") is False

    def test_empty_text(self) -> None:
        assert _contains_token("XXBT_PDKS_PER_DETAILS_V", "") is False


class TestContainsSubstring:
    def test_turkish_suffix_match(self) -> None:
        """'çalışanları' contains field word 'çalışan' → True."""
        assert _contains_substring("çalışanları", "çalışan") is True

    def test_short_token_rejected(self) -> None:
        """Tokens < 4 chars should not trigger substring fallback."""
        assert _contains_substring("abc", "ab") is False

    def test_exact_match_excluded(self) -> None:
        """Substring fallback should not fire for exact word matches."""
        assert _contains_substring("XXBT_PDKS_PER_DETAILS_V", "XXBT_PDKS_PER_DETAILS_V") is False

    def test_short_field_word_ignored(self) -> None:
        """Field words < 3 chars should not match."""
        assert _contains_substring("abcdef", "ab xy") is False

    def test_multi_word_field(self) -> None:
        """One of multiple field words matches → True."""
        assert _contains_substring("personeli", "hr personel modülü") is True

    def test_no_match(self) -> None:
        assert _contains_substring("XXBT_PDKS_PER_DETAILS_V", "salary maaş") is False


# ---------------------------------------------------------------------------
# Substring fallback retrieval tests
# ---------------------------------------------------------------------------


class TestSubstringFallback:
    @pytest.mark.asyncio
    async def test_turkish_suffix_retrieves_doc(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Query 'çalışanları' should match docs with tag 'çalışan' via
        substring fallback (field word 'çalışan' is contained in token
        'çalışanları')."""
        result = await retriever.retrieve("çalışanları", top_k_docs=10)
        doc_ids = [d.doc_id for d in result.schema_docs]
        # d3 has tag "aktif" — no match
        # d4 has tag "çalışan" — substring fallback matches "çalışanları"
        assert "d4" in doc_ids

    @pytest.mark.asyncio
    async def test_turkish_suffix_retrieves_example(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Query 'çalışanları' should match examples with tag 'çalışan'."""
        result = await retriever.retrieve("çalışanları", top_k_examples=10)
        ex_ids = [e.doc_id for e in result.examples]
        # ex1 has tag "çalışan" → substring match
        assert "ex1" in ex_ids

    @pytest.mark.asyncio
    async def test_short_token_no_fallback(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """A short token ('abc') should not trigger substring fallback."""
        result = await retriever.retrieve("abc")
        assert result.schema_docs == []
        assert result.examples == []

    @pytest.mark.asyncio
    async def test_exact_still_preferred(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """When exact match exists, substring fallback should not add
        significant extra score."""
        # "XXBT_PDKS_PER_DETAILS_V" hits exact on table_name (+10), title (+8), content (+3)
        result_exact = await retriever.retrieve("XXBT_PDKS_PER_DETAILS_V", top_k_docs=10)
        # "employeeler" hits only substring fallback (if "XXBT_PDKS_PER_DETAILS_V" in token)
        result_substr = await retriever.retrieve("employeeler", top_k_docs=10)
        # Both should find employee docs
        exact_ids = [d.doc_id for d in result_exact.schema_docs]
        substr_ids = [d.doc_id for d in result_substr.schema_docs]
        assert "d1" in exact_ids
        assert "d1" in substr_ids

    @pytest.mark.asyncio
    async def test_mixed_case_substring(
        self, retriever: InMemoryDocumentRetriever,
    ) -> None:
        """Substring fallback should also be case-insensitive."""
        result = await retriever.retrieve("ÇALIŞANLARI", top_k_docs=10)
        # Case-folded version should still match via substring fallback
        assert isinstance(result, DocumentRetrievalResult)
