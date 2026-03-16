"""Metadata ingestion service.

Transforms raw metadata (``MetadataBundle``) from external sources into
the domain ``CatalogSnapshot`` used by the rest of the application.

Pipeline
========
1. A ``MetadataLoader`` reads the source and produces a ``MetadataBundle``.
2. This service transforms each ``RawTableDef`` / ``RawColumnDef`` into
   the corresponding ``TableMetadata`` / ``ColumnMetadata`` domain models.
3. The resulting ``CatalogSnapshot`` can be fed to a ``CatalogProvider``
   or directly to the ``CatalogService``.

The transformation handles data-type mapping, alias normalisation, and
restricted-flag propagation.  Relationships from the bundle are preserved
in ``last_relationships`` for future JOIN support (Sprint 3+).
"""

from __future__ import annotations

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
from app.providers.metadata.base import MetadataLoader
from app.providers.metadata.models import (
    MetadataBundle,
    RawColumnDef,
    RawRelationshipDef,
    RawTableDef,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Data-type mapping
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, ColumnType] = {
    "VARCHAR": ColumnType.VARCHAR,
    "VARCHAR2": ColumnType.VARCHAR,
    "NVARCHAR": ColumnType.VARCHAR,
    "NVARCHAR2": ColumnType.VARCHAR,
    "CHAR": ColumnType.VARCHAR,
    "NCHAR": ColumnType.VARCHAR,
    "TEXT": ColumnType.VARCHAR,
    "STRING": ColumnType.VARCHAR,
    "NUMBER": ColumnType.NUMBER,
    "NUMERIC": ColumnType.NUMBER,
    "DECIMAL": ColumnType.NUMBER,
    "FLOAT": ColumnType.NUMBER,
    "DOUBLE": ColumnType.NUMBER,
    "INTEGER": ColumnType.INTEGER,
    "INT": ColumnType.INTEGER,
    "SMALLINT": ColumnType.INTEGER,
    "BIGINT": ColumnType.INTEGER,
    "DATE": ColumnType.DATE,
    "TIMESTAMP": ColumnType.TIMESTAMP,
    "TIMESTAMP WITH TIME ZONE": ColumnType.TIMESTAMP,
    "TIMESTAMP WITH LOCAL TIME ZONE": ColumnType.TIMESTAMP,
    "CLOB": ColumnType.CLOB,
    "NCLOB": ColumnType.CLOB,
    "LONG": ColumnType.CLOB,
    "BOOLEAN": ColumnType.BOOLEAN,
    "BOOL": ColumnType.BOOLEAN,
}


def _map_column_type(raw_type: str) -> ColumnType:
    """Map a raw data-type string to a ``ColumnType`` enum value.

    Falls back to ``ColumnType.VARCHAR`` for unknown types and emits a
    warning so that the mapping table can be extended.
    """
    normalised = raw_type.strip().upper()
    # Handle Oracle NUMBER(p,s) style by stripping parenthesised parts
    base = normalised.split("(")[0].strip()
    result = _TYPE_MAP.get(base)
    if result is None:
        logger.warning("Unknown column data-type '%s' – falling back to VARCHAR", raw_type)
        return ColumnType.VARCHAR
    return result


def _trim_dedup(items: list[str]) -> list[str]:
    """Trim whitespace from each item and remove duplicates (order preserved)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        stripped = item.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            out.append(stripped)
    return out


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MetadataIngestionService:
    """Transform raw metadata into domain catalog models.

    Relationships from the last ingested bundle are kept in
    ``last_relationships`` for downstream consumers that need FK /
    relationship info (Sprint 3+ JOIN planner).
    """

    def __init__(self, loader: MetadataLoader) -> None:
        self._loader = loader
        # TODO: CatalogSnapshot does not carry relationships yet.
        # We preserve them here so that future JOIN-planning code can
        # access them via the service instance.
        self.last_relationships: list[RawRelationshipDef] = []

    async def ingest(self, source: Path | str) -> CatalogSnapshot:
        """Load metadata from *source* and return a ``CatalogSnapshot``.

        Steps
        -----
        1. Delegate to the configured ``MetadataLoader`` to get a
           ``MetadataBundle``.
        2. Transform each raw table/column into domain models.
        3. Return the assembled ``CatalogSnapshot``.

        After this call, ``self.last_relationships`` contains the
        relationship definitions from the ingested bundle.
        """
        bundle = await self._loader.load(source)
        return self.transform(bundle)

    def transform(self, bundle: MetadataBundle) -> CatalogSnapshot:
        """Transform a ``MetadataBundle`` into a ``CatalogSnapshot``."""
        tables = [self._transform_table(raw) for raw in bundle.tables]
        self.last_relationships = list(bundle.relationships)

        # Map raw relationships into domain RelationshipMetadata
        relationships = [
            RelationshipMetadata(
                from_table=r.from_table,
                from_column=r.from_column,
                to_table=r.to_table,
                to_column=r.to_column,
                relationship_type=r.relationship_type,
                description=r.description or "",
            )
            for r in bundle.relationships
        ]

        if relationships:
            logger.info(
                "Mapped %d relationship(s) into CatalogSnapshot",
                len(relationships),
            )
        logger.info(
            "Metadata ingested: %d table(s) from source '%s'",
            len(tables),
            bundle.source,
        )
        return CatalogSnapshot(tables=tables, relationships=relationships)

    def get_relationships(self) -> list[RawRelationshipDef]:
        """Return relationship definitions from the last ingested bundle.

        TODO: Move relationships into ``CatalogSnapshot`` once the domain
        model supports multi-table / JOIN planning.
        """
        return list(self.last_relationships)

    # -- Internal transforms ------------------------------------------------

    @staticmethod
    def _transform_table(raw: RawTableDef) -> TableMetadata:
        columns = [
            MetadataIngestionService._transform_column(rc)
            for rc in raw.columns
        ]
        # Merge synonyms into aliases (trim + dedup the combined list)
        merged_aliases = _trim_dedup(raw.aliases + raw.synonyms)

        # Map raw foreign-key definitions into domain ForeignKeyMetadata
        foreign_keys = [
            ForeignKeyMetadata(
                column=fk.column,
                referenced_table=fk.referenced_table,
                referenced_column=fk.referenced_column,
                description=fk.description or "",
            )
            for fk in raw.foreign_keys
        ]

        return TableMetadata(
            name=raw.name,
            description=raw.description,
            aliases=merged_aliases,
            primary_key=raw.primary_key,
            foreign_keys=foreign_keys,
            columns=columns,
        )

    @staticmethod
    def _transform_column(raw: RawColumnDef) -> ColumnMetadata:
        return ColumnMetadata(
            name=raw.name,
            data_type=_map_column_type(raw.data_type),
            nullable=raw.nullable,
            restricted=raw.restricted,
            description=raw.description,
            aliases=_trim_dedup(raw.aliases),
        )
