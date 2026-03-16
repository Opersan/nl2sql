"""Tests for the JSONL document loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.exceptions import DocumentLoadError
from app.providers.documents.jsonl_loader import JSONLDocumentLoader
from app.providers.documents.models import DocType, DocumentCorpus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def strict_loader() -> JSONLDocumentLoader:
    """Default loader — strict=True."""
    return JSONLDocumentLoader(strict=True)


@pytest.fixture
def lenient_loader() -> JSONLDocumentLoader:
    """Lenient loader — strict=False (warning + skip)."""
    return JSONLDocumentLoader(strict=False)


def _write_jsonl(path: Path, lines: list[dict]) -> Path:
    """Write a list of dicts as JSONL to *path*."""
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Happy-path tests (work in both strict and lenient modes)
# ---------------------------------------------------------------------------


class TestJSONLLoaderHappyPath:
    @pytest.mark.asyncio
    async def test_load_mixed_corpus(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """A mix of schema docs and examples should be correctly routed."""
        lines = [
            {
                "doc_type": "table",
                "doc_id": "emp_table",
                "title": "Employee tablosu",
                "content": "Ana personel tablosu",
                "table_name": "XXBT_PDKS_PER_DETAILS_V",
                "module": "HR",
                "tags": ["personel"],
            },
            {
                "doc_type": "column",
                "doc_id": "emp_col_regno",
                "title": "reg_no kolonu",
                "content": "Sicil numarası",
                "table_name": "XXBT_PDKS_PER_DETAILS_V",
                "column_name": "reg_no",
            },
            {
                "doc_type": "example",
                "doc_id": "ex_active",
                "question": "Aktif çalışanları listele",
                "sql": "SELECT reg_no FROM employee WHERE quit_date IS NULL",
                "tables": ["XXBT_PDKS_PER_DETAILS_V"],
                "explanation": "quit_date NULL = aktif",
                "difficulty": "easy",
                "tags": ["aktif"],
            },
        ]
        corpus_file = _write_jsonl(tmp_path / "corpus.jsonl", lines)
        corpus = await strict_loader.load(corpus_file)

        assert isinstance(corpus, DocumentCorpus)
        assert len(corpus.schema_docs) == 2
        assert len(corpus.examples) == 1
        assert corpus.schema_docs[0].doc_type == DocType.TABLE
        assert corpus.schema_docs[1].doc_type == DocType.COLUMN
        assert corpus.examples[0].question == "Aktif çalışanları listele"
        assert corpus.source == str(corpus_file)

    @pytest.mark.asyncio
    async def test_load_only_schema_docs(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        lines = [
            {
                "doc_type": "glossary",
                "doc_id": "g1",
                "title": "Aktif çalışan",
                "content": "quit_date IS NULL olan kayıtlar",
            },
        ]
        corpus = await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))
        assert len(corpus.schema_docs) == 1
        assert len(corpus.examples) == 0
        assert corpus.schema_docs[0].doc_type == DocType.GLOSSARY

    @pytest.mark.asyncio
    async def test_load_only_examples(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        lines = [
            {
                "doc_type": "example",
                "doc_id": "ex1",
                "question": "Toplam çalışan sayısı",
                "sql": "SELECT COUNT(*) FROM employee",
                "tables": ["XXBT_PDKS_PER_DETAILS_V"],
            },
            {
                "doc_type": "example",
                "doc_id": "ex2",
                "question": "Birim bazında çalışan sayısı",
                "sql": "SELECT unit_name, COUNT(*) FROM employee GROUP BY unit_name",
                "tables": ["XXBT_PDKS_PER_DETAILS_V"],
            },
        ]
        corpus = await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))
        assert len(corpus.schema_docs) == 0
        assert len(corpus.examples) == 2

    @pytest.mark.asyncio
    async def test_empty_file(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """An empty file produces an empty corpus without error."""
        (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
        corpus = await strict_loader.load(tmp_path / "empty.jsonl")
        assert len(corpus.schema_docs) == 0
        assert len(corpus.examples) == 0

    @pytest.mark.asyncio
    async def test_blank_lines_skipped(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        content = (
            '\n\n{"doc_type":"table","doc_id":"t","title":"T","content":"C"}\n\n'
        )
        (tmp_path / "b.jsonl").write_text(content, encoding="utf-8")
        corpus = await strict_loader.load(tmp_path / "b.jsonl")
        assert len(corpus.schema_docs) == 1

    @pytest.mark.asyncio
    async def test_relationship_doc_type(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        lines = [
            {
                "doc_type": "relationship",
                "doc_id": "r1",
                "title": "employee -> department",
                "content": "FK ilişkisi",
                "table_name": "XXBT_PDKS_PER_DETAILS_V",
            },
        ]
        corpus = await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))
        assert len(corpus.schema_docs) == 1
        assert corpus.schema_docs[0].doc_type == DocType.RELATIONSHIP


# ---------------------------------------------------------------------------
# Strict mode error tests
# ---------------------------------------------------------------------------


class TestStrictModeErrors:
    @pytest.mark.asyncio
    async def test_file_not_found(self, strict_loader: JSONLDocumentLoader) -> None:
        with pytest.raises(DocumentLoadError):
            await strict_loader.load("/nonexistent/path.jsonl")

    @pytest.mark.asyncio
    async def test_malformed_json_raises(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Strict mode: malformed JSON line raises DocumentLoadError."""
        content = (
            '{"doc_type":"table","doc_id":"t","title":"T","content":"C"}\n'
            "NOT VALID JSON\n"
        )
        (tmp_path / "m.jsonl").write_text(content, encoding="utf-8")
        with pytest.raises(DocumentLoadError, match="Malformed JSON"):
            await strict_loader.load(tmp_path / "m.jsonl")

    @pytest.mark.asyncio
    async def test_unknown_doc_type_raises(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Strict mode: unknown doc_type raises DocumentLoadError."""
        lines = [
            {"doc_type": "unknown_type", "doc_id": "x", "title": "X", "content": "Y"},
        ]
        with pytest.raises(DocumentLoadError, match="Unknown doc_type"):
            await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))

    @pytest.mark.asyncio
    async def test_invalid_schema_doc_payload_raises(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Strict mode: schema doc with missing required fields raises."""
        lines = [
            {"doc_type": "table"},  # missing doc_id, title, content
        ]
        with pytest.raises(DocumentLoadError, match="Invalid schema doc"):
            await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))

    @pytest.mark.asyncio
    async def test_invalid_example_payload_raises(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Strict mode: example with missing required fields raises."""
        lines = [
            {"doc_type": "example"},  # missing doc_id, question, sql
        ]
        with pytest.raises(DocumentLoadError, match="Invalid example"):
            await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))

    @pytest.mark.asyncio
    async def test_empty_doc_id_raises(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Strict mode: empty doc_id (min_length=1) raises."""
        lines = [
            {"doc_type": "table", "doc_id": "", "title": "T", "content": "C"},
        ]
        with pytest.raises(DocumentLoadError, match="Invalid schema doc"):
            await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))

    @pytest.mark.asyncio
    async def test_empty_title_raises(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Strict mode: empty title (min_length=1) raises."""
        lines = [
            {"doc_type": "table", "doc_id": "x", "title": "", "content": "C"},
        ]
        with pytest.raises(DocumentLoadError, match="Invalid schema doc"):
            await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))

    @pytest.mark.asyncio
    async def test_empty_content_raises(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Strict mode: empty content (min_length=1) raises."""
        lines = [
            {"doc_type": "table", "doc_id": "x", "title": "T", "content": ""},
        ]
        with pytest.raises(DocumentLoadError, match="Invalid schema doc"):
            await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))

    @pytest.mark.asyncio
    async def test_empty_question_raises(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Strict mode: example with empty question raises."""
        lines = [
            {"doc_type": "example", "doc_id": "e", "question": "", "sql": "SELECT 1"},
        ]
        with pytest.raises(DocumentLoadError, match="Invalid example"):
            await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))

    @pytest.mark.asyncio
    async def test_empty_sql_raises(
        self, strict_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Strict mode: example with empty sql raises."""
        lines = [
            {"doc_type": "example", "doc_id": "e", "question": "Q", "sql": ""},
        ]
        with pytest.raises(DocumentLoadError, match="Invalid example"):
            await strict_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))


# ---------------------------------------------------------------------------
# Lenient (non-strict) mode tests
# ---------------------------------------------------------------------------


class TestLenientModeSkips:
    @pytest.mark.asyncio
    async def test_file_not_found_still_raises(
        self, lenient_loader: JSONLDocumentLoader,
    ) -> None:
        """File-not-found always raises, regardless of strict flag."""
        with pytest.raises(DocumentLoadError):
            await lenient_loader.load("/nonexistent/path.jsonl")

    @pytest.mark.asyncio
    async def test_malformed_json_skipped(
        self, lenient_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Non-strict: malformed JSON lines are skipped."""
        content = (
            '{"doc_type":"table","doc_id":"t","title":"T","content":"C"}\n'
            "NOT VALID JSON\n"
            '{"doc_type":"example","doc_id":"e","question":"Q","sql":"S"}\n'
        )
        (tmp_path / "m.jsonl").write_text(content, encoding="utf-8")
        corpus = await lenient_loader.load(tmp_path / "m.jsonl")
        assert len(corpus.schema_docs) == 1
        assert len(corpus.examples) == 1

    @pytest.mark.asyncio
    async def test_unknown_doc_type_skipped(
        self, lenient_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Non-strict: unknown doc_type lines are skipped."""
        lines = [
            {"doc_type": "unknown_type", "title": "X", "content": "Y"},
            {"doc_type": "table", "doc_id": "t", "title": "T", "content": "C"},
        ]
        corpus = await lenient_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))
        assert len(corpus.schema_docs) == 1
        assert len(corpus.examples) == 0

    @pytest.mark.asyncio
    async def test_invalid_schema_doc_skipped(
        self, lenient_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Non-strict: invalid schema doc payload is skipped."""
        lines = [
            {"doc_type": "table"},  # missing required fields
            {"doc_type": "table", "doc_id": "ok", "title": "Valid", "content": "Ok"},
        ]
        corpus = await lenient_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))
        assert len(corpus.schema_docs) == 1

    @pytest.mark.asyncio
    async def test_invalid_example_skipped(
        self, lenient_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Non-strict: invalid example payload is skipped."""
        lines = [
            {"doc_type": "example"},  # missing required fields
            {"doc_type": "example", "doc_id": "ok", "question": "Q", "sql": "SELECT 1"},
        ]
        corpus = await lenient_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))
        assert len(corpus.examples) == 1

    @pytest.mark.asyncio
    async def test_empty_doc_id_skipped(
        self, lenient_loader: JSONLDocumentLoader, tmp_path: Path,
    ) -> None:
        """Non-strict: empty doc_id is skipped, valid line passes."""
        lines = [
            {"doc_type": "table", "doc_id": "", "title": "T", "content": "C"},
            {"doc_type": "table", "doc_id": "ok", "title": "T2", "content": "C2"},
        ]
        corpus = await lenient_loader.load(_write_jsonl(tmp_path / "c.jsonl", lines))
        assert len(corpus.schema_docs) == 1
        assert corpus.schema_docs[0].doc_id == "ok"


# ---------------------------------------------------------------------------
# Pydantic model required-field validation tests
# ---------------------------------------------------------------------------


class TestModelValidation:
    def test_schema_doc_requires_doc_id(self) -> None:
        """SchemaDocument.doc_id is required and min_length=1."""
        from pydantic import ValidationError
        from app.providers.documents.models import SchemaDocument, DocType

        with pytest.raises(ValidationError):
            SchemaDocument(doc_type=DocType.TABLE, title="T", content="C")  # no doc_id

    def test_schema_doc_rejects_empty_doc_id(self) -> None:
        from pydantic import ValidationError
        from app.providers.documents.models import SchemaDocument, DocType

        with pytest.raises(ValidationError):
            SchemaDocument(doc_id="", doc_type=DocType.TABLE, title="T", content="C")

    def test_schema_doc_rejects_empty_title(self) -> None:
        from pydantic import ValidationError
        from app.providers.documents.models import SchemaDocument, DocType

        with pytest.raises(ValidationError):
            SchemaDocument(doc_id="x", doc_type=DocType.TABLE, title="", content="C")

    def test_schema_doc_rejects_empty_content(self) -> None:
        from pydantic import ValidationError
        from app.providers.documents.models import SchemaDocument, DocType

        with pytest.raises(ValidationError):
            SchemaDocument(doc_id="x", doc_type=DocType.TABLE, title="T", content="")

    def test_example_doc_requires_doc_id(self) -> None:
        from pydantic import ValidationError
        from app.providers.documents.models import ExampleDocument

        with pytest.raises(ValidationError):
            ExampleDocument(question="Q", sql="S")  # no doc_id

    def test_example_doc_rejects_empty_doc_id(self) -> None:
        from pydantic import ValidationError
        from app.providers.documents.models import ExampleDocument

        with pytest.raises(ValidationError):
            ExampleDocument(doc_id="", question="Q", sql="S")

    def test_example_doc_rejects_empty_question(self) -> None:
        from pydantic import ValidationError
        from app.providers.documents.models import ExampleDocument

        with pytest.raises(ValidationError):
            ExampleDocument(doc_id="x", question="", sql="S")

    def test_example_doc_rejects_empty_sql(self) -> None:
        from pydantic import ValidationError
        from app.providers.documents.models import ExampleDocument

        with pytest.raises(ValidationError):
            ExampleDocument(doc_id="x", question="Q", sql="")

    def test_valid_schema_doc_ok(self) -> None:
        from app.providers.documents.models import SchemaDocument, DocType

        doc = SchemaDocument(
            doc_id="x", doc_type=DocType.TABLE, title="T", content="C",
        )
        assert doc.doc_id == "x"

    def test_valid_example_doc_ok(self) -> None:
        from app.providers.documents.models import ExampleDocument

        ex = ExampleDocument(doc_id="x", question="Q", sql="SELECT 1")
        assert ex.doc_id == "x"
