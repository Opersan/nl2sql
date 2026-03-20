"""Catalog domain models.

Represent database metadata: tables, columns, types and the overall catalog
snapshot that providers must deliver.

Multi-table support (Sprint 5)
==============================
* ``ForeignKeyMetadata`` — describes a FK relationship between two tables.
* ``RelationshipMetadata`` — semantic-level relationship summary used by
  the planner prompt (e.g. "EMPLOYEE → DEPARTMENT via unit_id").
* ``TableMetadata.foreign_keys`` — FK constraints defined on this table.
* ``CatalogSnapshot.relationships`` — cross-table relationship index.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.utils.turkish import casefold_tr


def _id_fold(s: str) -> str:
    """Case-fold for SQL identifiers — neutralise Turkish I ambiguity.

    ``casefold_tr`` maps uppercase ``I`` → ``ı`` (Turkish dotless-i) which is
    correct for Turkish prose but breaks SQL identifier matching where ``I``
    is the ASCII letter.  This helper normalises ``ı`` → ``i`` after folding
    so that e.g. ``PO_HEADER_ID`` and ``po_header_id`` compare equal.
    """
    return casefold_tr(s).replace("ı", "i")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ColumnType(str, Enum):
    """Logical column data types (DB-agnostic)."""

    VARCHAR = "VARCHAR"
    VARCHAR2 = "VARCHAR2"
    NVARCHAR2 = "NVARCHAR2"
    NUMBER = "NUMBER"
    INTEGER = "INTEGER"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    CLOB = "CLOB"
    BOOLEAN = "BOOLEAN"


# ---------------------------------------------------------------------------
# Column & Table metadata
# ---------------------------------------------------------------------------

class ColumnMetadata(BaseModel):
    """Metadata for a single table column."""

    name: str
    data_type: ColumnType
    nullable: bool = True
    restricted: bool = False
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)

    def matches(self, name_or_alias: str) -> bool:
        """Case-insensitive (Turkish-aware) match against name or aliases."""
        folded = _id_fold(name_or_alias)
        if _id_fold(self.name) == folded:
            return True
        return any(_id_fold(a) == folded for a in self.aliases)


# ---------------------------------------------------------------------------
# Foreign key & relationship metadata (Sprint 5)
# ---------------------------------------------------------------------------


class ForeignKeyMetadata(BaseModel):
    """A foreign-key relationship from this table to a referenced table.

    ``column`` is the FK column in the **source** table;
    ``referenced_table`` / ``referenced_column`` identify the target.
    """

    column: str
    referenced_table: str
    referenced_column: str
    description: str | None = None


class RelationshipMetadata(BaseModel):
    """Semantic-level relationship summary between two tables.

    Used by the planner prompt to inform the LLM about available JOINs.
    ``from_table`` / ``from_column`` ← → ``to_table`` / ``to_column``.
    """

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relationship_type: str = Field(
        default="many_to_one",
        description="Relationship cardinality: many_to_one, one_to_many, many_to_many",
    )
    description: str | None = None


class TableMetadata(BaseModel):
    """Metadata for a single database table."""

    name: str
    description: str | None = None
    columns: list[ColumnMetadata] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyMetadata] = Field(default_factory=list)

    # -- Helper methods -----------------------------------------------------

    def matches(self, name_or_alias: str) -> bool:
        """Case-insensitive (Turkish-aware) match against name or aliases."""
        folded = _id_fold(name_or_alias)
        if _id_fold(self.name) == folded:
            return True
        return any(_id_fold(a) == folded for a in self.aliases)

    def get_column(self, name_or_alias: str) -> ColumnMetadata | None:
        """Resolve a column by name or alias (case-insensitive)."""
        for col in self.columns:
            if col.matches(name_or_alias):
                return col
        return None

    def has_column(self, name_or_alias: str) -> bool:
        """Check whether a column exists (by name or alias)."""
        return self.get_column(name_or_alias) is not None

    def restricted_columns(self) -> list[ColumnMetadata]:
        """Return all columns marked as restricted."""
        return [c for c in self.columns if c.restricted]

    def restricted_column_names(self) -> set[str]:
        """Return the canonical names of restricted columns."""
        return {c.name for c in self.columns if c.restricted}

    def column_names(self) -> list[str]:
        """Return canonical column names in definition order."""
        return [c.name for c in self.columns]

    def resolve_column_name(self, name_or_alias: str) -> str | None:
        """Return the canonical column name for a name/alias, or None."""
        col = self.get_column(name_or_alias)
        return col.name if col else None


# ---------------------------------------------------------------------------
# Catalog snapshot
# ---------------------------------------------------------------------------

class CatalogSnapshot(BaseModel):
    """A point-in-time snapshot of the entire catalog."""

    tables: list[TableMetadata] = Field(default_factory=list)
    relationships: list[RelationshipMetadata] = Field(default_factory=list)

    def get_table(self, name_or_alias: str) -> TableMetadata | None:
        """Resolve a table by name or alias."""
        for tbl in self.tables:
            if tbl.matches(name_or_alias):
                return tbl
        return None

    def table_names(self) -> list[str]:
        """Return canonical table names."""
        return [t.name for t in self.tables]

    def search_tables(self, query: str) -> list[TableMetadata]:
        """Return tables whose name, alias or description contains *query*."""
        folded = _id_fold(query)
        results: list[TableMetadata] = []
        for tbl in self.tables:
            if folded in _id_fold(tbl.name):
                results.append(tbl)
                continue
            if any(folded in _id_fold(a) for a in tbl.aliases):
                results.append(tbl)
                continue
            if tbl.description and folded in _id_fold(tbl.description):
                results.append(tbl)
        return results

    def get_relationships_for(self, table_name: str) -> list[RelationshipMetadata]:
        """Return all relationships involving *table_name* (case-insensitive)."""
        folded = _id_fold(table_name)
        return [
            r for r in self.relationships
            if _id_fold(r.from_table) == folded or _id_fold(r.to_table) == folded
        ]

    def get_join_path(
        self, from_table: str, to_table: str,
    ) -> RelationshipMetadata | None:
        """Find a direct relationship between two tables."""
        f = _id_fold(from_table)
        t = _id_fold(to_table)
        for r in self.relationships:
            rf = _id_fold(r.from_table)
            rt = _id_fold(r.to_table)
            if (rf == f and rt == t) or (rf == t and rt == f):
                return r
        return None
