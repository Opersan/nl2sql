"""Catalog service – thin layer on top of a CatalogProvider.

Provides convenient resolution helpers used by the validation service and
the SQL compiler.

Sprint 3 retrieval integration
==============================
When ``settings.enable_metadata_retrieval`` is ``True`` and a
``SchemaRetrievalService`` is injected, ``get_relevant_context()``
delegates to the retrieval service instead of returning the full snapshot.
Otherwise it falls back to the full dump (Sprint 1-2 behaviour).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings
from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    TableMetadata,
)
from app.providers.catalog.base import CatalogProvider
from app.services.schema_retrieval_service import SchemaRetrievalService

if TYPE_CHECKING:
    from app.services.query_understanding import QueryUnderstanding


class CatalogService:
    """High-level catalog operations."""

    def __init__(
        self,
        provider: CatalogProvider,
        *,
        retrieval: SchemaRetrievalService | None = None,
    ) -> None:
        self._provider = provider
        self._retrieval = retrieval

    async def get_snapshot(self) -> CatalogSnapshot:
        """Return the full catalog snapshot from the underlying provider."""
        return await self._provider.get_snapshot()

    async def get_relevant_context(
        self,
        user_message: str,
        *,
        query_understanding: "QueryUnderstanding | None" = None,
    ) -> CatalogSnapshot:
        """Return catalog context relevant to *user_message*.

        Behaviour
        ---------
        * When retrieval is enabled (``settings.enable_metadata_retrieval``
          and a ``SchemaRetrievalService`` is injected): delegates to the
          retrieval service which returns only the most relevant tables.
        * Otherwise: returns the **full** catalog snapshot (Sprint 1-2
          fallback).

        The return type stays ``CatalogSnapshot`` so that
        ``build_planner_prompt`` and downstream consumers do not need to
        change.
        """
        if settings.enable_metadata_retrieval and self._retrieval is not None:
            return await self._retrieval.retrieve_context(
                user_message, query_understanding=query_understanding,
            )
        return await self.get_snapshot()

    async def resolve_table(self, name_or_alias: str) -> TableMetadata | None:
        """Resolve a table by canonical name or alias (case-insensitive)."""
        return await self._provider.get_table(name_or_alias)

    async def resolve_column(
        self, table_name: str, column_or_alias: str
    ) -> ColumnMetadata | None:
        """Resolve a column inside a table (by name or alias)."""
        table = await self.resolve_table(table_name)
        if table is None:
            return None
        return table.get_column(column_or_alias)

    async def table_exists(self, table_name: str) -> bool:
        """Check whether *table_name* (or alias) is known."""
        return (await self.resolve_table(table_name)) is not None

    async def column_exists(self, table_name: str, column_name: str) -> bool:
        """Check whether a column exists in the given table."""
        col = await self.resolve_column(table_name, column_name)
        return col is not None

    async def get_restricted_columns(self, table_name: str) -> set[str]:
        """Return canonical names of restricted columns for *table_name*."""
        table = await self.resolve_table(table_name)
        if table is None:
            return set()
        return table.restricted_column_names()
