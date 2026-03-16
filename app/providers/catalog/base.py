"""Abstract base class for catalog providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.catalog_models import CatalogSnapshot, TableMetadata


class CatalogProvider(ABC):
    """Contract that every catalog data source must fulfil."""

    @abstractmethod
    async def get_snapshot(self) -> CatalogSnapshot:
        """Return the full catalog snapshot."""
        ...

    @abstractmethod
    async def get_table(self, table_name: str) -> TableMetadata | None:
        """Return metadata for a single table (by name or alias), or None."""
        ...

    @abstractmethod
    async def search_tables(self, query: str) -> list[TableMetadata]:
        """Search tables by keyword (name / alias / description)."""
        ...
