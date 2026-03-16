"""In-memory schema retriever using keyword / alias matching.

This is the Sprint 3 Phase 1 retriever.  It performs case-insensitive
(Turkish-aware) keyword matching against table names, aliases, column
names, column aliases, and descriptions to find the most relevant tables
for a given user query.

Relation-aware expansion (Sprint 5)
====================================
After scoring, if the top-scored table has FK relationships defined in
the catalog snapshot, related tables are automatically included when the
query tokens hint at cross-table concepts (e.g. "departman bazında
çalışan sayısı" → EMPLOYEE + DEPARTMENT).

Scoring and filtering
=====================
Every table in the catalog is scored against the query.  Only tables
with ``score > 0`` are returned, capped at *top_k*.  When **no** table
scores above zero the retriever returns the first *top_k* tables from
the catalog (ordered by definition order) so that the LLM still receives
some context — but it never silently returns the entire catalog.

This implementation is intentionally simple.  It will be superseded by
BM25 or vector-similarity retrieval in later phases, but the
``SchemaRetriever`` interface and the ``CatalogSnapshot`` return type
remain stable.
"""

from __future__ import annotations

import re

from app.domain.catalog_models import CatalogSnapshot, TableMetadata
from app.providers.catalog.base import CatalogProvider
from app.providers.retrieval.base import SchemaRetriever
from app.utils.turkish import casefold_tr

# Pre-compiled pattern for punctuation removal during tokenization.
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split and drop empty tokens."""
    folded = casefold_tr(text)
    cleaned = _PUNCT_RE.sub(" ", folded)
    return [t for t in cleaned.split() if t]


class InMemoryRetriever(SchemaRetriever):
    """Keyword-based retriever backed by a ``CatalogProvider``."""

    def __init__(self, catalog_provider: CatalogProvider) -> None:
        self._provider = catalog_provider

    async def retrieve(
        self,
        user_query: str,
        *,
        top_k: int = 5,
    ) -> CatalogSnapshot:
        snapshot = await self._provider.get_snapshot()

        # Score every table
        scored = [
            (self._score(table, user_query), table)
            for table in snapshot.tables
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)

        # Only keep tables with score > 0, capped at top_k
        selected = [table for score, table in scored[:top_k] if score > 0]

        if not selected:
            # Fallback: no match at all → return first top_k tables (never the
            # full catalog) so the LLM gets *some* schema context.
            fallback = snapshot.tables[:top_k]
            return CatalogSnapshot(
                tables=fallback,
                relationships=self._filter_relationships(snapshot, fallback),
            )

        # --- Relation-aware expansion (Sprint 5) -------------------------
        # If the snapshot has relationship metadata, pull in related tables
        # that scored > 0 or are directly FK-related to top-scored tables.
        if snapshot.relationships:
            selected = self._expand_related(snapshot, selected, top_k)

        return CatalogSnapshot(
            tables=selected,
            relationships=self._filter_relationships(snapshot, selected),
        )

    # -- Relation-aware expansion ----------------------------------------

    @staticmethod
    def _expand_related(
        snapshot: CatalogSnapshot,
        selected: list[TableMetadata],
        top_k: int,
    ) -> list[TableMetadata]:
        """Add FK-related tables that aren't already selected (up to top_k)."""
        selected_names = {t.name.upper() for t in selected}
        all_tables = {t.name.upper(): t for t in snapshot.tables}

        to_add: list[TableMetadata] = []
        for table in selected:
            for rel in snapshot.get_relationships_for(table.name):
                # The "other" side of the relationship
                other = (
                    rel.to_table.upper()
                    if rel.from_table.upper() == table.name.upper()
                    else rel.from_table.upper()
                )
                if other not in selected_names and other in all_tables:
                    to_add.append(all_tables[other])
                    selected_names.add(other)

        result = selected + to_add
        return result[:top_k]

    @staticmethod
    def _filter_relationships(
        snapshot: CatalogSnapshot,
        selected: list[TableMetadata],
    ) -> list:
        """Return only relationships whose both tables are in *selected*."""
        names = {t.name.upper() for t in selected}
        return [
            r
            for r in snapshot.relationships
            if r.from_table.upper() in names and r.to_table.upper() in names
        ]

    # -- Scoring --------------------------------------------------------

    @staticmethod
    def _score(table: TableMetadata, query: str) -> int:
        """Score a table's relevance to *query* by keyword hits.

        Scoring heuristic (intentionally simple):
        * Table name match: +10
        * Table alias match: +8
        * Table description match: +5
        * Column name match: +3
        * Column alias match: +2
        * Column description match: +1
        """
        tokens = _tokenize(query)
        if not tokens:
            return 0

        score = 0

        folded_name = casefold_tr(table.name)
        folded_aliases = [casefold_tr(a) for a in table.aliases]
        folded_desc = casefold_tr(table.description) if table.description else ""

        for token in tokens:
            if token in folded_name:
                score += 10
            for alias in folded_aliases:
                if token in alias:
                    score += 8
            if token in folded_desc:
                score += 5

            for col in table.columns:
                col_name = casefold_tr(col.name)
                col_aliases = [casefold_tr(a) for a in col.aliases]
                col_desc = casefold_tr(col.description) if col.description else ""

                if token in col_name:
                    score += 3
                for ca in col_aliases:
                    if token in ca:
                        score += 2
                if token in col_desc:
                    score += 1

        return score
