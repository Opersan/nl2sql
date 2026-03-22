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
Every table in the catalog is scored against the query.  Tables must meet
a ``minimum_score`` threshold to be included.  When provided, a
``QueryUnderstanding`` object is used to:
- boost tables matching the detected module
- suppress tables from unrelated modules
- control FK expansion based on detected entity scope.

This implementation is intentionally simple.  It will be superseded by
BM25 or vector-similarity retrieval in later phases, but the
``SchemaRetriever`` interface and the ``CatalogSnapshot`` return type
remain stable.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.domain.catalog_models import CatalogSnapshot, TableMetadata
from app.providers.catalog.base import CatalogProvider
from app.providers.retrieval.base import SchemaRetriever
from app.core.logging import get_logger
from app.utils.turkish import casefold_tr

if TYPE_CHECKING:
    from app.semantic.registry import SemanticFoundationRegistry
    from app.services.query_understanding import QueryUnderstanding

logger = get_logger(__name__)

# Pre-compiled pattern for punctuation removal during tokenization.
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Minimum token length to be considered for scoring (filters noise like "da", "ki", "ve")
_MIN_TOKEN_LEN: int = 3

# Minimum retrieval score for a table to be included
_MIN_RETRIEVAL_SCORE: int = 3

# Turkish stop-words / suffixes that should never drive table scoring
_STOP_TOKENS: frozenset[str] = frozenset({
    "ve", "ile", "icin", "bir", "bu", "su", "da", "de", "mi", "mu",
    "ne", "ki", "var", "yok", "hem", "ya", "ama", "tum", "tüm",
    "getir", "goster", "listele", "ver", "bul", "say", "hesapla",
    "olan", "daki", "deki", "olan", "giren", "cikan",
})

# Module → table-name patterns for entity-aware scoring.
# DEMOTED to weak fallback: only used when no entity seeds are found via the
# SemanticFoundationRegistry.  New modules (AP, AR, GL, INV) are handled
# entirely by the registry; this dict covers only the legacy HR/PO patterns.
_MODULE_TABLE_PATTERNS: dict[str, list[str]] = {
    "HR": ["xxbt_pdks", "per_", "employee", "person", "hr_"],
    "PO": ["po_", "mtl_system_items", "purchase"],
}


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split and drop short/stop tokens."""
    folded = casefold_tr(text)
    cleaned = _PUNCT_RE.sub(" ", folded)
    return [
        t for t in cleaned.split()
        if t and len(t) >= _MIN_TOKEN_LEN and t not in _STOP_TOKENS
    ]


def _table_module(table: TableMetadata) -> str | None:
    """Infer a module label from a table name using naming conventions."""
    name_lower = table.name.lower()
    for module, patterns in _MODULE_TABLE_PATTERNS.items():
        if any(p in name_lower for p in patterns):
            return module
    return None


class InMemoryRetriever(SchemaRetriever):
    """Keyword-based retriever backed by a ``CatalogProvider``.

    When a ``SemanticFoundationRegistry`` is injected and the incoming
    ``QueryUnderstanding`` has non-empty ``resolved_entities``, the retriever
    uses *entity-first seeding*: candidate tables are pre-seeded from the
    entity’s ``default_tables`` and then expanded via approved relationship
    edges.  The classic keyword score is used only for ranking within the
    candidate set and as a fallback when no entity seeds exist.
    """

    def __init__(
        self,
        catalog_provider: CatalogProvider,
        *,
        semantic_registry: "SemanticFoundationRegistry | None" = None,
    ) -> None:
        self._provider = catalog_provider
        self._semantic_registry = semantic_registry

    async def retrieve(
        self,
        user_query: str,
        *,
        top_k: int = 5,
        query_understanding: "QueryUnderstanding | None" = None,
    ) -> CatalogSnapshot:
        snapshot = await self._provider.get_snapshot()

        # ------------------------------------------------------------------
        # Entity-first seeding (Phase 4)
        # ------------------------------------------------------------------
        # When resolved_entities is non-empty and a registry is available,
        # pre-seed candidate tables from entity.default_tables and expand
        # only via approved relationship edges.  The keyword scorer is then
        # applied only within the candidate set.
        entity_seeded_names: set[str] = set()
        reg = self._semantic_registry
        if (
            reg is None
            and query_understanding is not None
            and getattr(query_understanding, "resolved_entities", [])
        ):
            try:
                from app.semantic.registry import get_registry as _get_registry
                reg = _get_registry()
            except Exception:  # pragma: no cover
                reg = None

        resolved_entities = getattr(query_understanding, "resolved_entities", []) if query_understanding else []

        if reg is not None and resolved_entities:
            for entity_id in resolved_entities:
                entity = reg.get_entity(entity_id)
                if entity is None:
                    continue
                for tbl_name in entity.default_tables:
                    entity_seeded_names.add(tbl_name.upper())
                # Graph expansion: approved edges only
                for edge in reg.get_relationships_for_entity(entity_id, approved_only=True):
                    entity_seeded_names.add(edge.target_table.upper())
                    entity_seeded_names.add(edge.source_table.upper())

        all_table_map = {t.name.upper(): t for t in snapshot.tables}

        # ------------------------------------------------------------------
        # Scoring
        # ------------------------------------------------------------------
        qu_modules: set[str] = set()
        if query_understanding is not None:
            qu_modules = set(query_understanding.inferred_modules)

        scored: list[tuple[int, TableMetadata]] = []
        for table in snapshot.tables:
            raw_score = self._score(table, user_query)

            if entity_seeded_names:
                # Entity-seed boost: tables in the seeded set get a strong boost
                if table.name.upper() in entity_seeded_names:
                    raw_score = int(raw_score * 1.5) + 10
                else:
                    # Suppress non-seeded tables (they survive only if they score
                    # very highly on their own keyword match)
                    raw_score = raw_score // 4
            else:
                # Fallback: module-pattern-based boost/suppress (legacy HR/PO)
                tbl_module = _table_module(table)
                if qu_modules and tbl_module:
                    if tbl_module in qu_modules:
                        raw_score = int(raw_score * 1.5) + 5
                    elif not (query_understanding and query_understanding.multi_entity_flag):
                        raw_score = max(0, raw_score // 3)

            scored.append((raw_score, table))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        # Apply minimum score threshold (not just > 0)
        selected = [
            table for score, table in scored[:top_k]
            if score >= _MIN_RETRIEVAL_SCORE
        ]

        if not selected:
            # Fallback: no match at all → return first top_k tables
            fallback = snapshot.tables[:top_k]
            return CatalogSnapshot(
                tables=fallback,
                relationships=self._filter_relationships(snapshot, fallback),
            )

        # Log rejected candidates for traceability
        rejected = [
            (score, table.name) for score, table in scored
            if 0 < score < _MIN_RETRIEVAL_SCORE
        ]
        if rejected:
            logger.debug(
                "[retriever] Rejected %d table(s) below threshold %d: %s",
                len(rejected),
                _MIN_RETRIEVAL_SCORE,
                [(name, sc) for sc, name in rejected],
            )

        # --- Relation-aware expansion (Sprint 5) -------------------------
        # Controlled: only expand when multi-entity or cross-domain
        if snapshot.relationships:
            should_expand = (
                query_understanding is not None
                and (
                    query_understanding.multi_entity_flag
                    or query_understanding.requires_cross_domain_reasoning
                    or len(selected) > 1
                )
            ) or (query_understanding is None)  # backward compat: expand if no QU

            if should_expand:
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

        Scoring heuristic:
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
