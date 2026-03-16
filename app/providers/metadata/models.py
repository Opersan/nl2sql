"""Metadata ingestion models.

These models represent the *raw* metadata coming from external sources
(CSV files, JSON exports, Oracle data-dictionary queries) **before** it
is transformed into the domain ``CatalogSnapshot`` used by the rest of
the application.

Separation rationale
====================
Keeping ingestion models separate from ``catalog_models`` allows the raw
schema description format to evolve independently of the domain layer.
The ``MetadataIngestionService`` is responsible for the transformation.

Supported source formats (Sprint 3 skeleton)
=============================================
* **JSON** – a single file containing a list of ``RawTableDef`` objects.
* **CSV**  – one CSV per table with columns: ``column_name``, ``data_type``,
  ``nullable``, ``restricted``, ``description``, ``aliases`` (pipe-separated).

Both loaders produce the same ``RawTableDef`` / ``RawColumnDef`` models.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RawColumnDef(BaseModel):
    """A column definition as it arrives from the metadata source."""

    name: str
    data_type: str = "VARCHAR"
    nullable: bool = True
    restricted: bool = False
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    example_values: list[str] = Field(default_factory=list)
    semantic_tags: list[str] = Field(default_factory=list)
    business_name: str | None = None


class RawForeignKeyDef(BaseModel):
    """A foreign-key column constraint on a table."""

    column: str
    referenced_table: str
    referenced_column: str
    description: str | None = None


class RawTableDef(BaseModel):
    """A table definition as it arrives from the metadata source."""

    name: str
    schema_name: str | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[RawForeignKeyDef] = Field(default_factory=list)
    columns: list[RawColumnDef] = Field(default_factory=list)
    object_type: str | None = None  # TABLE / VIEW
    module: str | None = None
    synonyms: list[str] = Field(default_factory=list)


class RawRelationshipDef(BaseModel):
    """A foreign-key / relationship descriptor (Sprint 3+ multi-table support).

    Not used by the current single-table pipeline but included in the
    ingestion schema so that metadata files can already declare
    relationships for future JOIN support.
    """

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relationship_type: str = "many_to_one"  # many_to_one | one_to_many | one_to_one
    constraint_name: str | None = None
    description: str | None = None


class MetadataBundle(BaseModel):
    """Complete metadata payload produced by a metadata loader."""

    tables: list[RawTableDef] = Field(default_factory=list)
    relationships: list[RawRelationshipDef] = Field(default_factory=list)
    source: str = "unknown"
    version: str | None = None
