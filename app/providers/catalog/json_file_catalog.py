"""JSON-file-backed catalog provider.

Loads catalog metadata from a JSON file whose path is given at construction
time (typically set via ``settings.metadata_source_path``).

JSON format
-----------
::

    {
      "tables": [
        {
          "name": str,
          "description": str | null,
          "aliases": [str, ...],
          "primary_key": [str, ...],
          "foreign_keys": [
            {
              "column": str,
              "referenced_table": str,
              "referenced_column": str,
              "description": str | null
            }
          ],
          "columns": [
            {
              "name": str,
              "data_type": str,
              "nullable": bool,
              "restricted": bool,
              "description": str | null,
              "aliases": [str, ...]
            }
          ]
        }
      ]
    }

Supported ``data_type`` values mirror ``ColumnType``: ``NUMBER``,
``VARCHAR``, ``VARCHAR2``, ``NVARCHAR2``, ``INTEGER``, ``DATE``,
``TIMESTAMP``, ``CLOB``, ``BOOLEAN``.  Unknown values fall back to
``VARCHAR`` with a debug-level warning.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.logging import get_logger
from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    ForeignKeyMetadata,
    RelationshipMetadata,
    TableMetadata,
)
from app.providers.catalog.base import CatalogProvider

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_column_type(raw: str) -> ColumnType:
    """Convert a raw *data_type* string to ``ColumnType``, defaulting to VARCHAR."""
    try:
        return ColumnType(raw.strip().upper())
    except ValueError:
        logger.debug("[json-catalog] unknown column type %r — defaulting to VARCHAR", raw)
        return ColumnType.VARCHAR


def _parse_column(raw: dict) -> ColumnMetadata:
    return ColumnMetadata(
        name=raw["name"],
        data_type=_parse_column_type(raw.get("data_type", "VARCHAR")),
        nullable=raw.get("nullable", True),
        restricted=raw.get("restricted", False),
        description=raw.get("description"),
        aliases=raw.get("aliases") or [],
    )


def _parse_fk(raw: dict) -> ForeignKeyMetadata:
    return ForeignKeyMetadata(
        column=raw["column"],
        referenced_table=raw["referenced_table"],
        referenced_column=raw["referenced_column"],
        description=raw.get("description"),
    )


def _parse_table(raw: dict) -> TableMetadata:
    columns = [_parse_column(c) for c in raw.get("columns") or []]
    fks = [_parse_fk(fk) for fk in raw.get("foreign_keys") or []]
    return TableMetadata(
        name=raw["name"],
        description=raw.get("description"),
        aliases=raw.get("aliases") or [],
        primary_key=raw.get("primary_key") or [],
        foreign_keys=fks,
        columns=columns,
    )


def _build_relationships(tables: list[TableMetadata]) -> list[RelationshipMetadata]:
    """Derive ``RelationshipMetadata`` from all FK definitions in *tables*."""
    rels: list[RelationshipMetadata] = []
    for tbl in tables:
        for fk in tbl.foreign_keys:
            rels.append(RelationshipMetadata(
                from_table=tbl.name,
                from_column=fk.column,
                to_table=fk.referenced_table,
                to_column=fk.referenced_column,
            ))
    return rels


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_catalog_from_json(path: Path) -> CatalogSnapshot:
    """Parse *path* and return a ``CatalogSnapshot``.

    Raises ``ValueError`` when the file is missing the top-level ``tables``
    key.  Individual malformed table entries are skipped with a warning so
    one bad record cannot break the entire catalog.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_tables = payload.get("tables")
    if raw_tables is None:
        raise ValueError(
            f"[json-catalog] JSON file missing top-level 'tables' key: {path}"
        )

    tables: list[TableMetadata] = []
    for i, raw in enumerate(raw_tables):
        try:
            tables.append(_parse_table(raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[json-catalog] skipping malformed table entry #%d in %s: %s",
                i,
                path.name,
                exc,
            )

    return CatalogSnapshot(
        tables=tables,
        relationships=_build_relationships(tables),
    )


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class JsonFileCatalogProvider(CatalogProvider):
    """Catalog provider backed by a JSON metadata file.

    Loads eagerly in ``__init__`` so ``_snapshot`` is synchronously
    available — consistent with ``InMemoryCatalogProvider``.

    Parameters
    ----------
    path:
        Absolute or relative path to the JSON catalog file.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._snapshot = load_catalog_from_json(path)
        n_tables = len(self._snapshot.tables)
        n_cols = sum(len(t.columns) for t in self._snapshot.tables)
        logger.info(
            "[json-catalog] loaded %s — %d table(s), %d column(s)",
            path.name,
            n_tables,
            n_cols,
        )

    # CatalogProvider interface ----------------------------------------

    async def get_snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    async def get_table(self, table_name: str) -> TableMetadata | None:
        return self._snapshot.get_table(table_name)

    async def search_tables(self, query: str) -> list[TableMetadata]:
        return self._snapshot.search_tables(query)
