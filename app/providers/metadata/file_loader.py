"""File-based metadata loaders (JSON & CSV).

These loaders read schema definitions from local files and produce a
``MetadataBundle``.  They are intended for development, testing, and
bootstrapping scenarios where Oracle data-dictionary access is not yet
available.

Expected file formats
=====================

JSON (single file)
------------------
.. code-block:: json

    {
      "tables": [
        {
          "name": "XXBT_PDKS_PER_DETAILS_V",
          "schema_name": "HR",
          "description": "Ana personel tablosu",
          "aliases": ["employees", "personnel"],
          "primary_key": ["reg_no"],
          "columns": [
            {
              "name": "reg_no",
              "data_type": "INTEGER",
              "nullable": false,
              "restricted": false,
              "description": "Sicil numarası",
              "aliases": ["sicil_no"]
            }
          ]
        }
      ],
      "relationships": [],
      "source": "hr_export_v1",
      "version": "1.0"
    }

CSV – Normalized layout (preferred)
------------------------------------
Directory structure::

    metadata_dir/
      tables.csv          <- table-level info
      columns.csv         <- all columns across all tables
      relationships.csv   <- FK / relationship definitions (optional)
      synonyms.csv        <- table synonym mappings (optional)

CSV – Legacy layout (backward compatible)
------------------------------------------
Directory structure::

    metadata_dir/
      _tables.csv          <- table-level info (name, description, aliases, pk)
      employee.csv         <- column-level info for 'employee'
      department.csv       <- column-level info for 'department'

Detection priority: if ``tables.csv`` exists the normalized layout is
used; otherwise the loader falls back to the legacy ``_tables.csv``
layout.
"""

from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path

from app.core.exceptions import MetadataLoadError
from app.core.logging import get_logger
from app.providers.metadata.base import MetadataLoader
from app.providers.metadata.models import (
    MetadataBundle,
    RawColumnDef,
    RawRelationshipDef,
    RawTableDef,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# JSON loader
# ---------------------------------------------------------------------------


class JSONMetadataLoader(MetadataLoader):
    """Load metadata from a single JSON file."""

    async def load(self, source: Path | str) -> MetadataBundle:
        path = Path(source)
        if not path.exists():
            raise MetadataLoadError(
                f"Metadata file not found: {path}",
                detail=str(path.resolve()),
            )
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (json.JSONDecodeError, OSError) as exc:
            raise MetadataLoadError(
                f"Failed to read metadata JSON: {exc}",
                detail=str(exc),
            ) from exc

        return MetadataBundle.model_validate(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_pipe_list(value: str) -> list[str]:
    """Split a pipe-separated string, stripping whitespace."""
    if not value or not value.strip():
        return []
    return [v.strip() for v in value.split("|") if v.strip()]


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "evet")


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV file and return a list of dicts (one per row)."""
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(StringIO(text))
    return list(reader)


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


class CSVMetadataLoader(MetadataLoader):
    """Load metadata from a directory of CSV files.

    Supports two layouts:

    1. **Normalized** (``tables.csv`` + ``columns.csv`` + optional
       ``relationships.csv`` / ``synonyms.csv``).
    2. **Legacy** (``_tables.csv`` + one ``<table>.csv`` per table).

    The loader auto-detects which layout is present by checking for
    ``tables.csv`` first.
    """

    async def load(self, source: Path | str) -> MetadataBundle:
        directory = Path(source)
        if not directory.is_dir():
            raise MetadataLoadError(
                f"Metadata directory not found: {directory}",
                detail=str(directory.resolve()),
            )

        # Decide layout
        if (directory / "tables.csv").exists():
            return self._load_normalized(directory)
        if (directory / "_tables.csv").exists():
            return self._load_legacy(directory)

        raise MetadataLoadError(
            f"No tables.csv or _tables.csv found in {directory}",
            detail="The directory must contain tables.csv (normalized) or _tables.csv (legacy).",
        )

    # -- Normalized layout --------------------------------------------------

    def _load_normalized(self, directory: Path) -> MetadataBundle:
        """Load using the normalized CSV layout."""
        try:
            tables = self._parse_tables_csv(directory / "tables.csv")
        except (KeyError, OSError) as exc:
            raise MetadataLoadError(
                f"Failed to parse tables.csv: {exc}",
                detail=str(exc),
            ) from exc

        # columns.csv — all columns in one file with a table_name column
        columns_file = directory / "columns.csv"
        if columns_file.exists():
            try:
                self._apply_columns_csv(tables, columns_file)
            except (KeyError, OSError) as exc:
                raise MetadataLoadError(
                    f"Failed to parse columns.csv: {exc}",
                    detail=str(exc),
                ) from exc
        else:
            logger.warning("columns.csv not found in %s – tables will have no columns", directory)

        # relationships.csv (optional)
        relationships: list[RawRelationshipDef] = []
        rel_file = directory / "relationships.csv"
        if rel_file.exists():
            try:
                relationships = self._parse_relationships_csv(rel_file)
            except (KeyError, OSError) as exc:
                logger.warning("Failed to parse relationships.csv: %s", exc)

        # synonyms.csv (optional)
        syn_file = directory / "synonyms.csv"
        if syn_file.exists():
            try:
                self._apply_synonyms_csv(tables, syn_file)
            except (KeyError, OSError) as exc:
                logger.warning("Failed to parse synonyms.csv: %s", exc)

        return MetadataBundle(
            tables=list(tables.values()),
            relationships=relationships,
            source=str(directory),
        )

    @staticmethod
    def _parse_tables_csv(path: Path) -> dict[str, RawTableDef]:
        """Parse ``tables.csv`` and return a name→RawTableDef mapping."""
        result: dict[str, RawTableDef] = {}
        for row in _read_csv(path):
            name = row["name"]
            result[name] = RawTableDef(
                name=name,
                schema_name=row.get("schema_name") or None,
                description=row.get("description") or None,
                aliases=_parse_pipe_list(row.get("aliases", "")),
                primary_key=_parse_pipe_list(row.get("primary_key", "")),
                object_type=row.get("object_type") or None,
                module=row.get("module") or None,
                synonyms=_parse_pipe_list(row.get("synonyms", "")),
            )
        return result

    @staticmethod
    def _apply_columns_csv(
        tables: dict[str, RawTableDef],
        path: Path,
    ) -> None:
        """Parse ``columns.csv`` and attach columns to their tables."""
        for row in _read_csv(path):
            table_name = row["table_name"]
            if table_name not in tables:
                logger.warning(
                    "columns.csv references unknown table '%s' – skipping row",
                    table_name,
                )
                continue
            tables[table_name].columns.append(
                RawColumnDef(
                    name=row["column_name"],
                    data_type=row.get("data_type", "VARCHAR").upper(),
                    nullable=_parse_bool(row.get("nullable", "true")),
                    restricted=_parse_bool(row.get("restricted", "false")),
                    description=row.get("description") or None,
                    aliases=_parse_pipe_list(row.get("aliases", "")),
                    example_values=_parse_pipe_list(row.get("example_values", "")),
                    semantic_tags=_parse_pipe_list(row.get("semantic_tags", "")),
                    business_name=row.get("business_name") or None,
                )
            )

    @staticmethod
    def _parse_relationships_csv(path: Path) -> list[RawRelationshipDef]:
        """Parse ``relationships.csv``."""
        rels: list[RawRelationshipDef] = []
        for row in _read_csv(path):
            rels.append(
                RawRelationshipDef(
                    from_table=row["from_table"],
                    from_column=row["from_column"],
                    to_table=row["to_table"],
                    to_column=row["to_column"],
                    relationship_type=row.get("relationship_type", "many_to_one"),
                    constraint_name=row.get("constraint_name") or None,
                    description=row.get("description") or None,
                )
            )
        return rels

    @staticmethod
    def _apply_synonyms_csv(
        tables: dict[str, RawTableDef],
        path: Path,
    ) -> None:
        """Parse ``synonyms.csv`` and merge synonyms into tables."""
        for row in _read_csv(path):
            table_name = row["table_name"]
            synonym = row["synonym"]
            if table_name in tables:
                tables[table_name].synonyms.append(synonym)
            else:
                logger.warning(
                    "synonyms.csv references unknown table '%s' – skipping",
                    table_name,
                )

    # -- Legacy layout ------------------------------------------------------

    def _load_legacy(self, directory: Path) -> MetadataBundle:
        """Load using the legacy ``_tables.csv`` + per-table CSV layout."""
        tables_file = directory / "_tables.csv"

        tables: list[RawTableDef] = []
        try:
            for table_row in _read_csv(tables_file):
                table_name = table_row["name"]
                table_def = RawTableDef(
                    name=table_name,
                    schema_name=table_row.get("schema_name") or None,
                    description=table_row.get("description") or None,
                    aliases=_parse_pipe_list(table_row.get("aliases", "")),
                    primary_key=_parse_pipe_list(table_row.get("primary_key", "")),
                    object_type=table_row.get("object_type") or None,
                    module=table_row.get("module") or None,
                    synonyms=_parse_pipe_list(table_row.get("synonyms", "")),
                    columns=self._load_legacy_columns(directory, table_name),
                )
                tables.append(table_def)
        except (KeyError, OSError) as exc:
            raise MetadataLoadError(
                f"Failed to parse CSV metadata: {exc}",
                detail=str(exc),
            ) from exc

        return MetadataBundle(
            tables=tables,
            relationships=[],
            source=str(directory),
        )

    @staticmethod
    def _load_legacy_columns(directory: Path, table_name: str) -> list[RawColumnDef]:
        """Load column definitions from ``<table_name>.csv`` (legacy layout)."""
        col_file = directory / f"{table_name}.csv"
        if not col_file.exists():
            logger.warning(
                "Column file '%s.csv' not found in %s – table will have no columns",
                table_name,
                directory,
            )
            return []

        columns: list[RawColumnDef] = []
        for row in _read_csv(col_file):
            columns.append(
                RawColumnDef(
                    name=row["column_name"],
                    data_type=row.get("data_type", "VARCHAR").upper(),
                    nullable=_parse_bool(row.get("nullable", "true")),
                    restricted=_parse_bool(row.get("restricted", "false")),
                    description=row.get("description") or None,
                    aliases=_parse_pipe_list(row.get("aliases", "")),
                    example_values=_parse_pipe_list(row.get("example_values", "")),
                    semantic_tags=_parse_pipe_list(row.get("semantic_tags", "")),
                    business_name=row.get("business_name") or None,
                )
            )
        return columns
