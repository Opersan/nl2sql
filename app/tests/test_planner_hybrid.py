"""Tests for PlannerService with hybrid document retrieval."""

from __future__ import annotations

import pytest

from app.domain.catalog_models import CatalogSnapshot
from app.domain.query_plan import QueryPlan
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.providers.documents.models import (
    DocType,
    DocumentCorpus,
    ExampleDocument,
    SchemaDocument,
)
from app.providers.llm.mock_llm import MockLLMProvider
from app.providers.llm.prompts import (
    DEFAULT_DOC_CONTENT_CHARS,
    DEFAULT_EXPLANATION_CHARS,
    DEFAULT_MAX_EXAMPLES,
    DEFAULT_MAX_SCHEMA_DOCS,
    _truncate,
    build_example_plan_hint,
    build_examples_block,
    build_hybrid_planner_prompt,
    build_planner_prompt,
    build_schema_docs_block,
)
from app.providers.retrieval.in_memory_doc_retriever import InMemoryDocumentRetriever
from app.services.catalog_service import CatalogService
from app.services.document_retrieval_service import DocumentRetrievalService
from app.services.planner_service import PlannerService


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
                content="HR modülü personel tablosu",
                table_name="XXBT_PDKS_PER_DETAILS_V",
                tags=["personel"],
            ),
        ],
        examples=[
            ExampleDocument(
                doc_id="ex1",
                question="Aktif çalışanları listele",
                sql="SELECT reg_no FROM employee WHERE quit_date IS NULL",
                tables=["XXBT_PDKS_PER_DETAILS_V"],
                explanation="quit_date NULL = aktif",
            ),
        ],
        source="test",
    )


@pytest.fixture
def planner_without_docs() -> PlannerService:
    """Planner without document retrieval (Sprint 2 behaviour)."""
    llm = MockLLMProvider()
    catalog = CatalogService(InMemoryCatalogProvider())
    return PlannerService(llm, catalog)


@pytest.fixture
def planner_with_docs() -> PlannerService:
    """Planner with document retrieval (hybrid mode)."""
    llm = MockLLMProvider()
    catalog = CatalogService(InMemoryCatalogProvider())
    corpus = _build_corpus()
    retriever = InMemoryDocumentRetriever(corpus)
    doc_retrieval = DocumentRetrievalService(retriever)
    return PlannerService(llm, catalog, doc_retrieval=doc_retrieval)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlannerWithoutDocs:
    @pytest.mark.asyncio
    async def test_plan_works_without_doc_retrieval(
        self, planner_without_docs: PlannerService,
    ) -> None:
        """Planner should work exactly as before when no doc retrieval."""
        plan = await planner_without_docs.plan("Aktif çalışanları listele")
        assert isinstance(plan, QueryPlan)
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"


class TestPlannerWithDocs:
    @pytest.mark.asyncio
    async def test_plan_with_doc_retrieval(
        self, planner_with_docs: PlannerService,
    ) -> None:
        """Planner should produce a valid plan even with doc retrieval enabled."""
        plan = await planner_with_docs.plan("Aktif çalışanları listele")
        assert isinstance(plan, QueryPlan)
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"

    @pytest.mark.asyncio
    async def test_plan_normalization_still_works(
        self, planner_with_docs: PlannerService,
    ) -> None:
        """Post-plan normalization should still apply with hybrid mode."""
        plan = await planner_with_docs.plan("Birim bazında çalışan sayısı")
        assert isinstance(plan, QueryPlan)
        assert plan.limit <= 1000  # max_row_limit default


class TestPlannerDocRetrievalInjection:
    def test_doc_retrieval_is_optional(self) -> None:
        """Constructor should accept doc_retrieval=None without error."""
        llm = MockLLMProvider()
        catalog = CatalogService(InMemoryCatalogProvider())
        planner = PlannerService(llm, catalog, doc_retrieval=None)
        assert planner._doc_retrieval is None

    def test_doc_retrieval_is_set(self) -> None:
        """Constructor should store doc_retrieval when provided."""
        llm = MockLLMProvider()
        catalog = CatalogService(InMemoryCatalogProvider())
        corpus = _build_corpus()
        retriever = InMemoryDocumentRetriever(corpus)
        doc_retrieval = DocumentRetrievalService(retriever)
        planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)
        assert planner._doc_retrieval is not None


# ---------------------------------------------------------------------------
# _build_prompt path tests
# ---------------------------------------------------------------------------


class TestBuildPromptPath:
    @pytest.mark.asyncio
    async def test_build_prompt_uses_hybrid_when_doc_retrieval_injected(self) -> None:
        """When doc_retrieval is injected, _build_prompt should use hybrid prompt."""
        catalog = CatalogService(InMemoryCatalogProvider())
        corpus = _build_corpus()
        retriever = InMemoryDocumentRetriever(corpus)
        doc_retrieval = DocumentRetrievalService(retriever)
        llm = MockLLMProvider()
        planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)

        # Use "XXBT_PDKS_PER_DETAILS_V" to guarantee the schema doc (table_name=employee) is matched
        context = await catalog.get_relevant_context("employee aktif çalışanları listele")
        prompt = await planner._build_prompt("employee aktif çalışanları listele", context)

        # Hybrid prompt should contain the schema docs block header
        assert "Ek şema bilgileri:" in prompt
        # And the examples block header
        assert "Benzer sorgu örnekleri:" in prompt
        # System prompt must be present
        assert "Sen bir NL2SQL planner" in prompt
        # User message must be present
        assert "Kullanıcı sorusu: employee aktif çalışanları listele" in prompt

    @pytest.mark.asyncio
    async def test_hybrid_prompt_contains_schema_docs_block(self) -> None:
        """Hybrid prompt should include schema document content."""
        catalog = CatalogService(InMemoryCatalogProvider())
        corpus = _build_corpus()
        retriever = InMemoryDocumentRetriever(corpus)
        doc_retrieval = DocumentRetrievalService(retriever)
        llm = MockLLMProvider()
        planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)

        context = await catalog.get_relevant_context("XXBT_PDKS_PER_DETAILS_V")
        prompt = await planner._build_prompt("XXBT_PDKS_PER_DETAILS_V", context)

        assert "[table] Employee tablosu" in prompt

    @pytest.mark.asyncio
    async def test_hybrid_prompt_contains_examples_block(self) -> None:
        """Hybrid prompt should include plan hints, not raw SQL."""
        catalog = CatalogService(InMemoryCatalogProvider())
        corpus = _build_corpus()
        retriever = InMemoryDocumentRetriever(corpus)
        doc_retrieval = DocumentRetrievalService(retriever)
        llm = MockLLMProvider()
        planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)

        context = await catalog.get_relevant_context("Aktif çalışanları listele")
        prompt = await planner._build_prompt("Aktif çalışanları listele", context)

        assert "Örnek 1:" in prompt
        assert "Plan ipucu:" in prompt
        # SQL must NOT appear in the examples block
        assert "SELECT reg_no FROM employee" not in prompt

    @pytest.mark.asyncio
    async def test_build_prompt_uses_base_when_no_doc_retrieval(self) -> None:
        """Without doc_retrieval, _build_prompt should use base planner prompt."""
        catalog = CatalogService(InMemoryCatalogProvider())
        llm = MockLLMProvider()
        planner = PlannerService(llm, catalog, doc_retrieval=None)

        context = await catalog.get_relevant_context("Aktif çalışanlar")
        prompt = await planner._build_prompt("Aktif çalışanlar", context)

        # Base prompt should not contain hybrid sections
        assert "Ek şema bilgileri:" not in prompt
        assert "Benzer sorgu örnekleri:" not in prompt
        # But should contain the user message and system prompt
        assert "Kullanıcı sorusu: Aktif çalışanlar" in prompt
        assert "Sen bir NL2SQL planner" in prompt


# ---------------------------------------------------------------------------
# System prompt safety rules
# ---------------------------------------------------------------------------


class TestSystemPromptSafetyRules:
    def _build_prompt_text(self) -> str:
        from app.providers.llm.prompts import _PLANNER_SYSTEM
        return _PLANNER_SYSTEM

    def test_catalog_is_source_of_truth(self) -> None:
        prompt = self._build_prompt_text()
        assert "asıl referans kaynağıdır" in prompt

    def test_schema_docs_are_auxiliary(self) -> None:
        prompt = self._build_prompt_text()
        assert "yardımcı bağlam" in prompt
        assert "katalog geçerlidir" in prompt

    def test_examples_are_guide_only(self) -> None:
        prompt = self._build_prompt_text()
        assert "yalnızca rehber" in prompt

    def test_no_unretrieved_tables(self) -> None:
        prompt = self._build_prompt_text()
        assert "yapısal katalog bağlamında" in prompt

    def test_output_must_be_queryplan_json(self) -> None:
        prompt = self._build_prompt_text()
        assert "QueryPlan JSON" in prompt


# ---------------------------------------------------------------------------
# Integration test: full pipeline with document retrieval
# ---------------------------------------------------------------------------


class TestPlannerIntegration:
    """End-to-end integration: DocumentRetrievalService + InMemoryDocumentRetriever
    + PlannerService wired together.  Verifies that the hybrid pipeline
    produces a valid QueryPlan when schema docs and examples are present."""

    @staticmethod
    def _build_rich_corpus() -> DocumentCorpus:
        return DocumentCorpus(
            schema_docs=[
                SchemaDocument(
                    doc_id="d1",
                    doc_type=DocType.TABLE,
                    title="Employee tablosu",
                    content="HR modülü personel tablosu",
                    table_name="XXBT_PDKS_PER_DETAILS_V",
                    tags=["personel", "hr"],
                ),
                SchemaDocument(
                    doc_id="d2",
                    doc_type=DocType.GLOSSARY,
                    title="Aktif çalışan tanımı",
                    content="quit_date IS NULL olan personel aktif sayılır",
                    tags=["aktif", "çalışan"],
                ),
            ],
            examples=[
                ExampleDocument(
                    doc_id="ex1",
                    question="Aktif çalışanları listele",
                    sql="SELECT reg_no, first_name FROM employee WHERE quit_date IS NULL",
                    tables=["XXBT_PDKS_PER_DETAILS_V"],
                    explanation="quit_date NULL = aktif çalışan",
                    tags=["aktif"],
                ),
                ExampleDocument(
                    doc_id="ex2",
                    question="Birim bazında çalışan sayısı",
                    sql="SELECT unit_name, COUNT(*) FROM employee GROUP BY unit_name",
                    tables=["XXBT_PDKS_PER_DETAILS_V"],
                    explanation="Birim bazında gruplama",
                    tags=["birim", "sayı"],
                ),
            ],
            source="integration-test",
        )

    @pytest.mark.asyncio
    async def test_full_pipeline_produces_query_plan(self) -> None:
        """With a rich corpus, PlannerService should still produce a QueryPlan."""
        llm = MockLLMProvider()
        catalog = CatalogService(InMemoryCatalogProvider())
        corpus = self._build_rich_corpus()
        retriever = InMemoryDocumentRetriever(corpus)
        doc_retrieval = DocumentRetrievalService(retriever)
        planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)

        plan = await planner.plan("Aktif çalışanları listele")

        assert isinstance(plan, QueryPlan)
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert plan.needs_clarification is False

    @pytest.mark.asyncio
    async def test_aggregate_plan_with_hybrid_retrieval(self) -> None:
        """Aggregate question should still produce correct plan shape."""
        llm = MockLLMProvider()
        catalog = CatalogService(InMemoryCatalogProvider())
        corpus = self._build_rich_corpus()
        retriever = InMemoryDocumentRetriever(corpus)
        doc_retrieval = DocumentRetrievalService(retriever)
        planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)

        plan = await planner.plan("Birim bazında çalışan sayısı")

        assert isinstance(plan, QueryPlan)
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert len(plan.aggregations) >= 1

    @pytest.mark.asyncio
    async def test_output_is_always_query_plan_not_sql(self) -> None:
        """Even with examples containing SQL, output must be QueryPlan."""
        llm = MockLLMProvider()
        catalog = CatalogService(InMemoryCatalogProvider())
        corpus = self._build_rich_corpus()
        retriever = InMemoryDocumentRetriever(corpus)
        doc_retrieval = DocumentRetrievalService(retriever)
        planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)

        plan = await planner.plan("Aktif çalışan sayısını göster")

        # Must be QueryPlan, never raw SQL
        assert isinstance(plan, QueryPlan)
        assert hasattr(plan, "intent")
        assert hasattr(plan, "table")

    @pytest.mark.asyncio
    async def test_unmatched_query_still_produces_plan(self) -> None:
        """A query with no matching docs/examples should still succeed."""
        llm = MockLLMProvider()
        catalog = CatalogService(InMemoryCatalogProvider())
        corpus = self._build_rich_corpus()
        retriever = InMemoryDocumentRetriever(corpus)
        doc_retrieval = DocumentRetrievalService(retriever)
        planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)

        # MockLLMProvider handles "Maaş bilgileri" as clarification
        plan = await planner.plan("Maaş bilgilerini göster")

        assert isinstance(plan, QueryPlan)


# ---------------------------------------------------------------------------
# Prompt content hardening tests
# ---------------------------------------------------------------------------


class TestExamplesBlockExcludesSQL:
    """Examples block must never expose raw SQL to the planner."""

    def test_examples_block_omits_sql_keyword(self) -> None:
        ex = ExampleDocument(
            doc_id="ex1",
            question="Aktif çalışanları listele",
            sql="SELECT reg_no FROM employee WHERE quit_date IS NULL",
            tables=["XXBT_PDKS_PER_DETAILS_V"],
            explanation="quit_date NULL = aktif",
        )
        block = build_examples_block([ex], max_examples=5)
        assert "SELECT " not in block
        assert "SQL:" not in block

    def test_examples_block_shows_plan_hint(self) -> None:
        ex = ExampleDocument(
            doc_id="ex1",
            question="Birim bazında sayı",
            sql="SELECT unit_name, COUNT(*) FROM employee GROUP BY unit_name",
            tables=["XXBT_PDKS_PER_DETAILS_V"],
            explanation="Grupla ve say",
        )
        block = build_examples_block([ex], max_examples=5)
        assert "Plan ipucu:" in block
        assert "aggregation" in block
        assert "group_by" in block


class TestBuildExamplePlanHint:
    """Test build_example_plan_hint heuristic detection."""

    def test_simple_select(self) -> None:
        ex = ExampleDocument(
            doc_id="e1", question="Q", sql="SELECT name FROM t",
        )
        assert build_example_plan_hint(ex) == "simple_select"

    def test_aggregation(self) -> None:
        ex = ExampleDocument(
            doc_id="e1", question="Q", sql="SELECT COUNT(*) FROM t",
        )
        hint = build_example_plan_hint(ex)
        assert "aggregation" in hint

    def test_group_by(self) -> None:
        ex = ExampleDocument(
            doc_id="e1", question="Q",
            sql="SELECT dept, COUNT(*) FROM t GROUP BY dept",
        )
        hint = build_example_plan_hint(ex)
        assert "aggregation" in hint
        assert "group_by" in hint

    def test_order_by(self) -> None:
        ex = ExampleDocument(
            doc_id="e1", question="Q",
            sql="SELECT name FROM t ORDER BY name ASC",
        )
        hint = build_example_plan_hint(ex)
        assert "order_by" in hint

    def test_null_filter(self) -> None:
        ex = ExampleDocument(
            doc_id="e1", question="Q",
            sql="SELECT name FROM t WHERE quit_date IS NULL",
        )
        hint = build_example_plan_hint(ex)
        assert "null_filter" in hint

    def test_in_filter(self) -> None:
        ex = ExampleDocument(
            doc_id="e1", question="Q",
            sql="SELECT name FROM t WHERE dept IN ('A','B')",
        )
        hint = build_example_plan_hint(ex)
        assert "in_filter" in hint

    def test_between_filter(self) -> None:
        ex = ExampleDocument(
            doc_id="e1", question="Q",
            sql="SELECT name FROM t WHERE salary BETWEEN 1000 AND 5000",
        )
        hint = build_example_plan_hint(ex)
        assert "between_filter" in hint

    def test_combined_hints(self) -> None:
        ex = ExampleDocument(
            doc_id="e1", question="Q",
            sql="SELECT dept, AVG(sal) FROM t WHERE quit_date IS NULL GROUP BY dept ORDER BY dept",
        )
        hint = build_example_plan_hint(ex)
        assert "aggregation" in hint
        assert "group_by" in hint
        assert "order_by" in hint
        assert "null_filter" in hint


class TestPromptTruncation:
    """Prompt size control: truncation must work correctly."""

    def test_truncate_short_text_unchanged(self) -> None:
        assert _truncate("kısa metin", 100) == "kısa metin"

    def test_truncate_long_text_trimmed(self) -> None:
        result = _truncate("A" * 600, 500)
        assert len(result) == 503  # 500 + "..."
        assert result.endswith("...")

    def test_truncate_empty_string(self) -> None:
        assert _truncate("", 100) == ""

    def test_truncate_none_safe(self) -> None:
        # _truncate treats falsy input as empty
        assert _truncate("", 50) == ""

    def test_docs_block_truncates_long_content(self) -> None:
        doc = SchemaDocument(
            doc_id="d1",
            doc_type=DocType.TABLE,
            title="Test tablo",
            content="X" * 1000,
            table_name="test",
        )
        block = build_schema_docs_block([doc], max_content_chars=50)
        # Content should be truncated
        assert "..." in block
        assert "X" * 51 not in block

    def test_examples_block_truncates_long_explanation(self) -> None:
        ex = ExampleDocument(
            doc_id="ex1",
            question="Test soru",
            sql="SELECT 1 FROM t",
            explanation="Y" * 500,
        )
        block = build_examples_block([ex], max_examples=5, max_explanation_chars=50)
        assert "..." in block
        assert "Y" * 51 not in block

    def test_docs_block_respects_max_docs(self) -> None:
        docs = [
            SchemaDocument(
                doc_id=f"d{i}",
                doc_type=DocType.TABLE,
                title=f"Tablo {i}",
                content=f"İçerik {i}",
                table_name=f"t{i}",
            )
            for i in range(10)
        ]
        block = build_schema_docs_block(docs, max_docs=3)
        assert block.count("[table]") == 3

    def test_examples_block_respects_max_examples(self) -> None:
        examples = [
            ExampleDocument(
                doc_id=f"ex{i}",
                question=f"Soru {i}",
                sql=f"SELECT {i} FROM t",
            )
            for i in range(10)
        ]
        block = build_examples_block(examples, max_examples=2)
        assert block.count("Örnek ") == 2


class TestPromptSizeConstants:
    """Prompt size constants must exist with expected defaults."""

    def test_default_max_schema_docs(self) -> None:
        assert DEFAULT_MAX_SCHEMA_DOCS == 4

    def test_default_max_examples(self) -> None:
        assert DEFAULT_MAX_EXAMPLES == 2

    def test_default_doc_content_chars(self) -> None:
        assert DEFAULT_DOC_CONTENT_CHARS == 500

    def test_default_explanation_chars(self) -> None:
        assert DEFAULT_EXPLANATION_CHARS == 250


# ---------------------------------------------------------------------------
# Planner integration: budget guard + degrade mode
# ---------------------------------------------------------------------------


class TestPlannerBudgetIntegration:
    """Verify PlannerService produces a valid QueryPlan even when the
    budget guard kicks in or document retrieval is unavailable."""

    @staticmethod
    def _build_planner_with_docs() -> PlannerService:
        llm = MockLLMProvider()
        catalog = CatalogService(InMemoryCatalogProvider())
        corpus = DocumentCorpus(
            schema_docs=[
                SchemaDocument(
                    doc_id="d1",
                    doc_type=DocType.TABLE,
                    title="Employee tablosu",
                    content="HR modülü personel tablosu " + "A" * 500,
                    table_name="XXBT_PDKS_PER_DETAILS_V",
                    tags=["personel"],
                ),
            ],
            examples=[
                ExampleDocument(
                    doc_id="ex1",
                    question="Aktif çalışanları listele",
                    sql="SELECT reg_no FROM employee WHERE quit_date IS NULL",
                    tables=["XXBT_PDKS_PER_DETAILS_V"],
                    explanation="quit_date NULL = aktif " + "B" * 400,
                ),
            ],
            source="budget-test",
        )
        retriever = InMemoryDocumentRetriever(corpus)
        doc_retrieval = DocumentRetrievalService(retriever)
        return PlannerService(llm, catalog, doc_retrieval=doc_retrieval)

    @pytest.mark.asyncio
    async def test_plan_with_budget_guard(self) -> None:
        """Budget guard active → PlannerService still returns QueryPlan."""
        planner = self._build_planner_with_docs()
        plan = await planner.plan("Aktif çalışanları listele")
        assert isinstance(plan, QueryPlan)
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"

    @pytest.mark.asyncio
    async def test_plan_without_doc_retrieval(self) -> None:
        """No doc retrieval → planner uses base prompt → QueryPlan OK."""
        llm = MockLLMProvider()
        catalog = CatalogService(InMemoryCatalogProvider())
        planner = PlannerService(llm, catalog, doc_retrieval=None)
        plan = await planner.plan("Aktif çalışanları listele")
        assert isinstance(plan, QueryPlan)
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"

    @pytest.mark.asyncio
    async def test_degrade_mode_no_corpus(self) -> None:
        """Simulates fail-open: doc_retrieval=None after corpus load failure."""
        llm = MockLLMProvider()
        catalog = CatalogService(InMemoryCatalogProvider())
        # doc_retrieval=None mimics the fail-open path
        planner = PlannerService(llm, catalog, doc_retrieval=None)
        plan = await planner.plan("Birim bazında çalışan sayısı")
        assert isinstance(plan, QueryPlan)
        assert plan.table == "XXBT_PDKS_PER_DETAILS_V"
        assert len(plan.aggregations) >= 1
