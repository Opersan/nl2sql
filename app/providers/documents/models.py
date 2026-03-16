"""Document corpus models for the hybrid retrieval layer.

These models represent *unstructured* or *semi-structured* documents that
complement the structured ``CatalogSnapshot``.  The two layers serve
different purposes:

Structured layer (CatalogSnapshot)
    Source-of-truth for validation, SQL compilation and execution.
    Populated via ``MetadataLoader`` → ``MetadataIngestionService``.

Document layer (DocumentCorpus)
    Retrieval-ready corpus used to enrich LLM context with:
    * schema prose descriptions (``table``, ``column``, ``relationship``,
      ``glossary``)
    * few-shot SQL examples (``example``)

The planner will eventually consume *both* layers:

1. Classify intent / domain
2. Retrieve relevant structured schema → ``CatalogSnapshot``
3. Retrieve relevant documents / examples → ``list[SchemaDocument]``
4. Produce ``QueryPlan``

Document types
==============
* **SchemaDocument** — a prose chunk describing a table, column,
  relationship, business term, or anything that helps the LLM understand
  the data model.  Identified by ``doc_type``.
* **ExampleDocument** — a natural-language question paired with its gold
  SQL and optional explanation.  Used for few-shot retrieval.
* **DocumentCorpus** — container holding both document kinds, produced by
  a ``DocumentLoader``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DocType(str, Enum):
    """Classification of schema documents."""

    TABLE = "table"
    COLUMN = "column"
    RELATIONSHIP = "relationship"
    GLOSSARY = "glossary"
    EXAMPLE = "example"


# ---------------------------------------------------------------------------
# Schema document
# ---------------------------------------------------------------------------


class SchemaDocument(BaseModel):
    """A prose chunk describing part of the data model.

    Each document carries enough metadata to support filtered retrieval
    (e.g. "give me all documents about the employee table").
    """

    doc_id: str = Field(..., min_length=1)
    doc_type: DocType
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    table_name: str | None = None
    column_name: str | None = None
    module: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Example document (few-shot)
# ---------------------------------------------------------------------------


class ExampleDocument(BaseModel):
    """A natural-language ↔ SQL example for few-shot retrieval.

    Fields
    ------
    question:
        The natural-language question (Turkish or English).
    sql:
        The gold-standard SQL that answers the question.
    tables:
        Table names referenced by the SQL (for retrieval filtering).
    explanation:
        Optional Turkish prose explaining the SQL logic.
    difficulty:
        Free-form difficulty tag (``easy``, ``medium``, ``hard``).
    tags:
        Arbitrary retrieval tags.

    Corpus evolution note
    ---------------------
    The planner prompt no longer exposes ``sql`` directly; it shows only
    plan-shape hints via ``build_example_plan_hint``.  The ``sql`` field
    is retained for:

    * offline evaluation & gold-reference regression tests
    * future migration tooling that derives a ``plan_hint`` /
      ``query_plan_shape`` field automatically

    A future sprint may add an optional ``plan_hint`` field.  All changes
    will be backward-compatible.
    """

    doc_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    sql: str = Field(..., min_length=1)
    tables: list[str] = Field(default_factory=list)
    explanation: str | None = None
    difficulty: str = "medium"
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Corpus container
# ---------------------------------------------------------------------------


class DocumentCorpus(BaseModel):
    """Container produced by a ``DocumentLoader``.

    Holds both schema documents and few-shot examples so that a single
    load call returns the full retrieval corpus.
    """

    schema_docs: list[SchemaDocument] = Field(default_factory=list)
    examples: list[ExampleDocument] = Field(default_factory=list)
    source: str = "unknown"
    version: str | None = None
