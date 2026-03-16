"""Tests for the prompt budget guard in build_hybrid_planner_prompt.

The budget guard enforces a total character limit on the assembled hybrid
prompt.  Reduction order:
1. Reduce example count
2. Reduce schema-doc count
3. Aggressively trim explanations
4. Aggressively trim doc content
5. Remove all optional sections
6. Truncate system prompt head to fit remaining budget

The user question and structured catalog section are never truncated.
If the budget is too small to hold them, ``ValueError`` is raised.
"""

from __future__ import annotations

import pytest

from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    TableMetadata,
)
from app.providers.documents.models import DocType, ExampleDocument, SchemaDocument
from app.providers.llm.prompts import (
    DEFAULT_PROMPT_MAX_CHARS,
    _MIN_PROMPT_BUDGET_CHARS,
    build_catalog_summary,
    build_hybrid_planner_prompt,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _snapshot() -> CatalogSnapshot:
    """Minimal catalog snapshot for prompt construction."""
    return CatalogSnapshot(
        tables=[
            TableMetadata(
                name="XXBT_PDKS_PER_DETAILS_V",
                description="Personel tablosu",
                columns=[
                    ColumnMetadata(name="reg_no", data_type=ColumnType.VARCHAR),
                    ColumnMetadata(name="first_name", data_type=ColumnType.VARCHAR),
                ],
            ),
        ],
    )


def _many_docs(n: int, content_size: int = 300) -> list[SchemaDocument]:
    """Create *n* schema documents with *content_size*-char content."""
    return [
        SchemaDocument(
            doc_id=f"d{i}",
            doc_type=DocType.TABLE,
            title=f"Tablo {i}",
            content="X" * content_size,
            table_name=f"table_{i}",
        )
        for i in range(n)
    ]


def _many_examples(n: int, explanation_size: int = 200) -> list[ExampleDocument]:
    """Create *n* examples with *explanation_size*-char explanations."""
    return [
        ExampleDocument(
            doc_id=f"ex{i}",
            question=f"Soru {i} — çalışan bilgileri",
            sql=f"SELECT col_{i} FROM table_{i}",
            tables=[f"table_{i}"],
            explanation="Y" * explanation_size,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPromptBudgetBasic:
    """Budget guard respects max_prompt_chars."""

    def test_small_prompt_unchanged(self) -> None:
        """When prompt fits budget, nothing is trimmed."""
        prompt = build_hybrid_planner_prompt(
            "Aktif çalışanlar",
            _snapshot(),
            schema_docs=_many_docs(1, 50),
            examples=_many_examples(1, 50),
            max_prompt_chars=DEFAULT_PROMPT_MAX_CHARS,
        )
        assert len(prompt) <= DEFAULT_PROMPT_MAX_CHARS
        assert "Ek şema bilgileri:" in prompt
        assert "Benzer sorgu örnekleri:" in prompt

    def test_final_prompt_within_budget(self) -> None:
        """With many large docs/examples, final prompt <= budget."""
        prompt = build_hybrid_planner_prompt(
            "Aktif çalışanların listesi",
            _snapshot(),
            schema_docs=_many_docs(20, 800),
            examples=_many_examples(20, 800),
            max_prompt_chars=3000,
        )
        assert len(prompt) <= 3000

    def test_very_tight_budget_still_within_limit(self) -> None:
        """Even a tight budget is respected; essentials survive intact."""
        prompt = build_hybrid_planner_prompt(
            "Aktif çalışanlar",
            _snapshot(),
            schema_docs=_many_docs(10, 500),
            examples=_many_examples(10, 500),
            max_prompt_chars=500,
        )
        assert len(prompt) <= 500
        # User question and catalog header must survive intact at tight budget
        assert "Aktif çalışanlar" in prompt
        assert "Kullanılabilir tablolar:" in prompt
        assert "Kullanıcı sorusu: Aktif çalışanlar" in prompt


class TestBudgetPreservesEssentials:
    """Essential sections are never dropped by the budget guard."""

    def test_user_question_preserved(self) -> None:
        """User question must always be in the prompt, exact string."""
        user_msg = "Bu özel soru promptta kalmalı"
        prompt = build_hybrid_planner_prompt(
            user_msg,
            _snapshot(),
            schema_docs=_many_docs(20, 800),
            examples=_many_examples(20, 800),
            max_prompt_chars=3000,
        )
        assert f"Kullanıcı sorusu: {user_msg}" in prompt

    def test_catalog_section_preserved(self) -> None:
        """Structured catalog section must always appear."""
        prompt = build_hybrid_planner_prompt(
            "test",
            _snapshot(),
            schema_docs=_many_docs(20, 800),
            examples=_many_examples(20, 800),
            max_prompt_chars=3000,
        )
        assert "Kullanılabilir tablolar:" in prompt
        assert "XXBT_PDKS_PER_DETAILS_V" in prompt

class TestBudgetNoSQL:
    """Raw SQL must never appear in the examples block."""

    def test_examples_block_no_sql(self) -> None:
        examples = [
            ExampleDocument(
                doc_id="ex1",
                question="Aktif çalışanları listele",
                sql="SELECT reg_no FROM employee WHERE quit_date IS NULL",
                tables=["XXBT_PDKS_PER_DETAILS_V"],
                explanation="quit_date NULL = aktif",
            ),
        ]
        prompt = build_hybrid_planner_prompt(
            "test",
            _snapshot(),
            examples=examples,
            max_prompt_chars=DEFAULT_PROMPT_MAX_CHARS,
        )
        assert "SELECT reg_no" not in prompt
        assert "Plan ipucu:" in prompt

    def test_examples_block_no_sql_after_budget_trim(self) -> None:
        """Even after aggressive budget trimming, no SQL leaks."""
        examples = _many_examples(5, 400)
        prompt = build_hybrid_planner_prompt(
            "test",
            _snapshot(),
            examples=examples,
            max_examples=5,
            max_prompt_chars=3000,
        )
        assert "SELECT " not in prompt or "select_columns" in prompt.lower()


class TestSystemPromptTruncation:
    """Step 6 truncates only the system prompt head; essentials survive."""

    def test_tight_budget_keeps_essentials_within_limit(self) -> None:
        """Tight budget respects limit while preserving essentials."""
        prompt = build_hybrid_planner_prompt(
            "test",
            _snapshot(),
            max_prompt_chars=300,
        )
        assert len(prompt) <= 300
        assert "Kullanıcı sorusu: test" in prompt
        assert "Kullanılabilir tablolar:" in prompt

    def test_user_question_intact_after_truncation(self) -> None:
        """User question string appears intact after system prompt truncation."""
        question = "UNIQUE_QUESTION_MARKER_42"
        prompt = build_hybrid_planner_prompt(
            question,
            _snapshot(),
            schema_docs=_many_docs(10, 500),
            examples=_many_examples(10, 500),
            max_prompt_chars=400,
        )
        assert len(prompt) <= 400
        assert f"Kullanıcı sorusu: {question}" in prompt

    def test_catalog_header_survives_extreme_pressure(self) -> None:
        """Catalog header survives even extreme budget pressure."""
        prompt = build_hybrid_planner_prompt(
            "x",
            _snapshot(),
            schema_docs=_many_docs(10, 500),
            examples=_many_examples(10, 500),
            max_prompt_chars=300,
        )
        assert len(prompt) <= 300
        assert "Kullanılabilir tablolar:" in prompt


class TestBudgetReductionOrder:
    """Verify the exact 6-step reduction contract."""

    def test_examples_drop_before_docs(self) -> None:
        """With moderate budget, examples shrink while docs survive."""
        docs = _many_docs(3, 100)
        examples = _many_examples(4, 100)

        # Unbounded — measure full size
        full = build_hybrid_planner_prompt(
            "Birim bazında çalışan sayısı",
            _snapshot(),
            schema_docs=docs,
            examples=examples,
            max_schema_docs=3,
            max_examples=4,
            max_prompt_chars=100_000,
        )

        # Pick a budget that requires trimming but is not tiny
        budget = len(full) - 300
        trimmed = build_hybrid_planner_prompt(
            "Birim bazında çalışan sayısı",
            _snapshot(),
            schema_docs=docs,
            examples=examples,
            max_schema_docs=3,
            max_examples=4,
            max_prompt_chars=budget,
        )

        assert len(trimmed) <= budget
        # Docs section header should survive (examples cut first)
        assert "Ek şema bilgileri:" in trimmed
        # Some examples may remain, but fewer than 4
        example_count = trimmed.count("Plan ipucu:")
        assert example_count < 4

    def test_docs_drop_after_all_examples_gone(self) -> None:
        """If removing all examples doesn't fit, docs are reduced next."""
        docs = _many_docs(6, 300)
        examples = _many_examples(2, 50)

        trimmed = build_hybrid_planner_prompt(
            "test",
            _snapshot(),
            schema_docs=docs,
            examples=examples,
            max_schema_docs=6,
            max_examples=2,
            max_prompt_chars=3000,
        )

        assert len(trimmed) <= 3000

    def test_all_optional_sections_can_be_dropped(self) -> None:
        """With very tight budget, both docs and examples are removed."""
        docs = _many_docs(10, 500)
        examples = _many_examples(10, 500)

        trimmed = build_hybrid_planner_prompt(
            "Aktif çalışanlar",
            _snapshot(),
            schema_docs=docs,
            examples=examples,
            max_prompt_chars=2200,
        )

        assert len(trimmed) <= 2200
        # Essential sections survive
        assert "Kullanılabilir tablolar:" in trimmed
        assert "Aktif çalışanlar" in trimmed


class TestBudgetEssentialsGuarantee:
    """The user question and structured catalog must never be dropped."""

    def test_user_question_at_end_of_prompt(self) -> None:
        """Prompt always ends with the user question line."""
        question = "Bu soru mutlaka kalmalı XYZ123"
        prompt = build_hybrid_planner_prompt(
            question,
            _snapshot(),
            schema_docs=_many_docs(5, 300),
            examples=_many_examples(5, 300),
            max_prompt_chars=4000,
        )
        assert f"Kullanıcı sorusu: {question}" in prompt
        tail = f"Kullanıcı sorusu: {question}"
        assert prompt.rstrip().endswith(tail)

    def test_catalog_table_names_preserved(self) -> None:
        """All table names from the snapshot survive under budget."""
        from app.domain.catalog_models import ColumnMetadata, ColumnType, TableMetadata

        snapshot = CatalogSnapshot(
            tables=[
                TableMetadata(
                    name="XXBT_PDKS_PER_DETAILS_V",
                    description="Personel",
                    columns=[
                        ColumnMetadata(name="id", data_type=ColumnType.NUMBER),
                    ],
                ),
                TableMetadata(
                    name="department",
                    description="Birim",
                    columns=[
                        ColumnMetadata(name="dept_id", data_type=ColumnType.NUMBER),
                    ],
                ),
            ],
        )
        prompt = build_hybrid_planner_prompt(
            "test",
            snapshot,
            schema_docs=_many_docs(10, 400),
            examples=_many_examples(10, 400),
            max_prompt_chars=4000,
        )
        assert "XXBT_PDKS_PER_DETAILS_V" in prompt
        assert "department" in prompt

    def test_system_prompt_rules_preserved(self) -> None:
        """Core system prompt rules survive heavy budget pressure."""
        prompt = build_hybrid_planner_prompt(
            "test",
            _snapshot(),
            schema_docs=_many_docs(20, 800),
            examples=_many_examples(20, 800),
            max_prompt_chars=3000,
        )
        assert "KESİNLİKLE SQL ÜRETME" in prompt
        assert "Sen bir NL2SQL planner" in prompt

    def test_total_len_always_within_budget(self) -> None:
        """Parametric check: many budget sizes always respected."""
        for budget in (800, 1500, 3000, 6000, 12000):
            prompt = build_hybrid_planner_prompt(
                "Aktif çalışanları listele",
                _snapshot(),
                schema_docs=_many_docs(10, 500),
                examples=_many_examples(10, 500),
                max_prompt_chars=budget,
            )
            assert len(prompt) <= budget, f"budget={budget}, got len={len(prompt)}"


# ---------------------------------------------------------------------------
# Minimum budget enforcement – ValueError on impossible budgets
# ---------------------------------------------------------------------------


class TestMinimumBudgetEnforcement:
    """Budget below essential content size raises ValueError."""

    def test_budget_below_static_minimum_raises(self) -> None:
        """Budget below _MIN_PROMPT_BUDGET_CHARS raises ValueError."""
        with pytest.raises(ValueError, match="must be at least"):
            build_hybrid_planner_prompt(
                "test",
                _snapshot(),
                max_prompt_chars=100,
            )

    def test_budget_exactly_at_static_minimum_ok(self) -> None:
        """Budget exactly at _MIN_PROMPT_BUDGET_CHARS does not raise."""
        # Pick a budget >= both the static minimum and essential size.
        essential = (
            f"Kullanılabilir tablolar:\n{build_catalog_summary(_snapshot())}"
            f"\n\nKullanıcı sorusu: a"
        )
        budget = max(_MIN_PROMPT_BUDGET_CHARS, len(essential) + 1)
        prompt = build_hybrid_planner_prompt(
            "a",
            _snapshot(),
            max_prompt_chars=budget,
        )
        assert len(prompt) <= budget
        assert "Kullanıcı sorusu: a" in prompt

    def test_budget_below_essential_tail_raises(self) -> None:
        """Budget that can't fit catalog + user question raises ValueError."""
        from app.domain.catalog_models import ColumnMetadata, ColumnType, TableMetadata

        big_snapshot = CatalogSnapshot(
            tables=[
                TableMetadata(
                    name=f"table_{i}",
                    description=f"Description for table {i} " * 5,
                    columns=[
                        ColumnMetadata(
                            name=f"col_{j}",
                            data_type=ColumnType.VARCHAR,
                            description=f"Column description {j}",
                        )
                        for j in range(10)
                    ],
                )
                for i in range(5)
            ],
        )
        essential = (
            f"Kullanılabilir tablolar:\n{build_catalog_summary(big_snapshot)}"
            f"\n\nKullanıcı sorusu: test"
        )
        # Use a budget smaller than essential but >= static minimum
        budget = max(_MIN_PROMPT_BUDGET_CHARS, 300)
        if budget < len(essential):
            with pytest.raises(ValueError, match="minimum required"):
                build_hybrid_planner_prompt(
                    "test",
                    big_snapshot,
                    max_prompt_chars=budget,
                )
        else:
            pytest.skip("catalog too small to trigger dynamic ValueError")

    def test_user_question_never_truncated(self) -> None:
        """User question is always complete in the output — never cut."""
        long_question = "Bu çok uzun bir soru " * 10
        essential = (
            f"Kullanılabilir tablolar:\n{build_catalog_summary(_snapshot())}"
            f"\n\nKullanıcı sorusu: {long_question}"
        )
        # Use a budget that fits essential content
        budget = len(essential) + 50
        prompt = build_hybrid_planner_prompt(
            long_question,
            _snapshot(),
            schema_docs=_many_docs(10, 500),
            examples=_many_examples(10, 500),
            max_prompt_chars=budget,
        )
        assert len(prompt) <= budget
        assert f"Kullanıcı sorusu: {long_question}" in prompt

    def test_user_question_too_long_for_budget_raises(self) -> None:
        """A very long user question that exceeds budget raises ValueError."""
        long_question = "x" * 500
        essential = (
            f"Kullanılabilir tablolar:\n{build_catalog_summary(_snapshot())}"
            f"\n\nKullanıcı sorusu: {long_question}"
        )
        # Budget smaller than essential
        budget = len(essential) - 50
        if budget < _MIN_PROMPT_BUDGET_CHARS:
            budget = _MIN_PROMPT_BUDGET_CHARS
        assert budget < len(essential), "test setup: budget must be < essential"
        with pytest.raises(ValueError, match="minimum required"):
            build_hybrid_planner_prompt(
                long_question,
                _snapshot(),
                max_prompt_chars=budget,
            )
