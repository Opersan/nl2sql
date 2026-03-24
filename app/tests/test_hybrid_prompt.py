"""Tests for the hybrid planner prompt builder and document formatting."""

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
    build_examples_block,
    build_hybrid_planner_prompt,
    build_planner_prompt,
    build_schema_docs_block,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _snapshot() -> CatalogSnapshot:
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
        ]
    )


def _schema_docs() -> list[SchemaDocument]:
    return [
        SchemaDocument(
            doc_id="d1",
            doc_type=DocType.TABLE,
            title="Employee tablosu",
            content="Ana personel tablosu",
            table_name="XXBT_PDKS_PER_DETAILS_V",
        ),
        SchemaDocument(
            doc_id="d2",
            doc_type=DocType.GLOSSARY,
            title="Aktif çalışan",
            content="quit_date IS NULL olan kayıtlar",
        ),
    ]


def _examples() -> list[ExampleDocument]:
    return [
        ExampleDocument(
            doc_id="ex1",
            question="Aktif çalışanları listele",
            sql="SELECT reg_no FROM employee WHERE quit_date IS NULL",
            tables=["XXBT_PDKS_PER_DETAILS_V"],
            explanation="quit_date NULL = aktif",
        ),
    ]


# ---------------------------------------------------------------------------
# build_examples_block
# ---------------------------------------------------------------------------


class TestBuildExamplesBlock:
    def test_empty_examples(self) -> None:
        assert build_examples_block([]) == ""

    def test_single_example(self) -> None:
        block = build_examples_block(_examples(), max_examples=5)
        assert "Benzer sorgu örnekleri:" in block
        assert "Örnek 1:" in block
        assert "Aktif çalışanları listele" in block
        # SQL must NOT appear; plan hint must appear
        assert "SELECT reg_no" not in block
        assert "Plan ipucu:" in block
        assert "quit_date NULL" in block

    def test_multiple_examples(self) -> None:
        exs = _examples() + [
            ExampleDocument(
                doc_id="ex2",
                question="Toplam çalışan",
                sql="SELECT COUNT(*) FROM employee",
            ),
        ]
        block = build_examples_block(exs, max_examples=5)
        assert "Örnek 1:" in block
        assert "Örnek 2:" in block
        assert "Toplam çalışan" in block

    def test_example_without_explanation(self) -> None:
        ex = ExampleDocument(
            doc_id="ex_no_exp",
            question="Test",
            sql="SELECT 1",
        )
        block = build_examples_block([ex], max_examples=5)
        assert "Açıklama" not in block


# ---------------------------------------------------------------------------
# build_schema_docs_block
# ---------------------------------------------------------------------------


class TestBuildSchemaDocsBlock:
    def test_empty_docs(self) -> None:
        assert build_schema_docs_block([]) == ""

    def test_single_doc(self) -> None:
        block = build_schema_docs_block(_schema_docs()[:1])
        assert "Ek şema bilgileri:" in block
        assert "[table]" in block
        assert "Employee tablosu" in block
        assert "(tablo: XXBT_PDKS_PER_DETAILS_V)" in block

    def test_glossary_doc(self) -> None:
        block = build_schema_docs_block(_schema_docs()[1:])
        assert "[glossary]" in block
        assert "Aktif çalışan" in block

    def test_doc_without_table_name(self) -> None:
        doc = SchemaDocument(
            doc_id="g",
            doc_type=DocType.GLOSSARY,
            title="Term",
            content="Desc",
        )
        block = build_schema_docs_block([doc])
        assert "(tablo:" not in block


# ---------------------------------------------------------------------------
# build_hybrid_planner_prompt
# ---------------------------------------------------------------------------


class TestBuildHybridPlannerPrompt:
    def test_without_docs_same_as_base(self) -> None:
        """When no docs/examples provided, hybrid == base prompt content."""
        base = build_planner_prompt("Aktif çalışanlar", _snapshot())
        hybrid = build_hybrid_planner_prompt("Aktif çalışanlar", _snapshot())
        # Hybrid sections may join differently, but key elements match
        assert "Kullanıcı sorusu: Aktif çalışanlar" in hybrid
        assert "XXBT_PDKS_PER_DETAILS_V" in hybrid

    def test_with_examples_only(self) -> None:
        prompt = build_hybrid_planner_prompt(
            "Aktif çalışanlar",
            _snapshot(),
            examples=_examples(),
        )
        assert "Benzer sorgu örnekleri:" in prompt
        assert "Ek şema bilgileri:" not in prompt

    def test_with_docs_only(self) -> None:
        prompt = build_hybrid_planner_prompt(
            "Aktif çalışanlar",
            _snapshot(),
            schema_docs=_schema_docs(),
        )
        assert "Ek şema bilgileri:" in prompt
        assert "Benzer sorgu örnekleri:" not in prompt

    def test_with_both_docs_and_examples(self) -> None:
        prompt = build_hybrid_planner_prompt(
            "Aktif çalışanlar",
            _snapshot(),
            schema_docs=_schema_docs(),
            examples=_examples(),
        )
        assert "Ek şema bilgileri:" in prompt
        assert "Benzer sorgu örnekleri:" in prompt
        assert "Kullanıcı sorusu: Aktif çalışanlar" in prompt
        # System prompt should be present
        assert "Sen bir NL2SQL planner" in prompt

    def test_user_message_at_end(self) -> None:
        """User message should always be at the end of the prompt."""
        prompt = build_hybrid_planner_prompt(
            "Test sorusu",
            _snapshot(),
            schema_docs=_schema_docs(),
            examples=_examples(),
        )
        lines = prompt.strip().split("\n")
        assert "Kullanıcı sorusu: Test sorusu" in lines[-1]
