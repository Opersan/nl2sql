"""JSONL-based document corpus loader.

Reads a single ``.jsonl`` file where each line is a JSON object
representing either a **schema document** or a **few-shot example**.

Line format
===========
Every line must contain a ``doc_type`` field.  The loader routes each
line to ``SchemaDocument`` or ``ExampleDocument`` based on its value.

Schema document line (doc_type ∈ {table, column, relationship, glossary})
-------------------------------------------------------------------------
.. code-block:: json

    {
      "doc_type": "table",
      "doc_id": "employee_overview",
      "title": "Employee tablosu",
      "content": "HR modülündeki ana personel tablosu. ...",
      "table_name": "XXBT_PDKS_PER_DETAILS_V",
      "module": "HR",
      "tags": ["personel", "hr"]
    }

Example line (doc_type == "example")
------------------------------------
.. code-block:: json

    {
      "doc_type": "example",
      "doc_id": "ex_active_employees",
      "question": "Aktif çalışanları listele",
      "sql": "SELECT reg_no, first_name FROM employee WHERE quit_date IS NULL",
      "tables": ["XXBT_PDKS_PER_DETAILS_V"],
      "explanation": "quit_date NULL olanlar aktif çalışanlardır",
      "difficulty": "easy",
      "tags": ["aktif", "çalışan"]
    }

Strict mode (default)
=====================
When ``strict=True`` (the default), any malformed JSON line, unknown
``doc_type``, or invalid payload raises ``DocumentLoadError``.

When ``strict=False``, such lines are logged as warnings and skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.exceptions import DocumentLoadError
from app.core.logging import get_logger
from app.providers.documents.base import DocumentLoader
from app.providers.documents.models import (
    DocType,
    DocumentCorpus,
    ExampleDocument,
    SchemaDocument,
)

logger = get_logger(__name__)

# doc_type values that map to SchemaDocument
_SCHEMA_DOC_TYPES = frozenset({
    DocType.TABLE.value,
    DocType.COLUMN.value,
    DocType.RELATIONSHIP.value,
    DocType.GLOSSARY.value,
})


class JSONLDocumentLoader(DocumentLoader):
    """Load a document corpus from a single JSONL file.

    Parameters
    ----------
    strict:
        When ``True`` (default), malformed JSON, unknown ``doc_type``
        values, and invalid payloads raise ``DocumentLoadError``.
        When ``False``, such lines are logged as warnings and skipped.
    """

    def __init__(self, *, strict: bool = True) -> None:
        self._strict = strict

    async def load(self, source: Path | str) -> DocumentCorpus:
        path = Path(source)
        if not path.exists():
            raise DocumentLoadError(
                f"Document corpus file not found: {path}",
                detail=str(path.resolve()),
            )

        schema_docs: list[SchemaDocument] = []
        examples: list[ExampleDocument] = []
        line_no = 0

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DocumentLoadError(
                f"Failed to read document corpus: {exc}",
                detail=str(exc),
            ) from exc

        for raw_line in text.splitlines():
            line_no += 1
            stripped = raw_line.strip()
            if not stripped:
                continue

            # --- Parse JSON ---
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                msg = f"Malformed JSON at line {line_no}: {exc}"
                if self._strict:
                    raise DocumentLoadError(msg, detail=stripped) from exc
                logger.warning("Skipping %s", msg)
                continue

            doc_type = data.get("doc_type", "")

            # --- Route by doc_type ---
            if doc_type == DocType.EXAMPLE.value:
                try:
                    examples.append(ExampleDocument.model_validate(data))
                except Exception as exc:  # noqa: BLE001
                    msg = f"Invalid example payload at line {line_no}: {exc}"
                    if self._strict:
                        raise DocumentLoadError(msg, detail=str(data)) from exc
                    logger.warning("Skipping %s", msg)
            elif doc_type in _SCHEMA_DOC_TYPES:
                try:
                    schema_docs.append(SchemaDocument.model_validate(data))
                except Exception as exc:  # noqa: BLE001
                    msg = f"Invalid schema doc payload at line {line_no}: {exc}"
                    if self._strict:
                        raise DocumentLoadError(msg, detail=str(data)) from exc
                    logger.warning("Skipping %s", msg)
            else:
                msg = f"Unknown doc_type '{doc_type}' at line {line_no}"
                if self._strict:
                    raise DocumentLoadError(msg, detail=str(data))
                logger.warning("Skipping %s", msg)

        logger.info(
            "Loaded document corpus from %s: %d schema doc(s), %d example(s)",
            path.name,
            len(schema_docs),
            len(examples),
        )

        return DocumentCorpus(
            schema_docs=schema_docs,
            examples=examples,
            source=str(path),
        )
