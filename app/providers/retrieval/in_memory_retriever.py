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

import asyncio
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
_ROOT_TABLE_BOOST: int = 14
_ENTITY_SEED_BOOST: int = 10
_MATCHING_MODULE_BOOST: int = 8
_MISMATCH_MODULE_DIVISOR: int = 6

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
        self._task_state: dict[int, dict[str, object] | None] = {}
        self._fallback_state: dict[str, object] | None = None

    @property
    def last_retrieval_diagnostics(self) -> dict[str, object] | None:
        task = asyncio.current_task()
        if task is not None and id(task) in self._task_state:
            return self._task_state[id(task)]
        return self._fallback_state

    def _set_last_retrieval_diagnostics(self, payload: dict[str, object] | None) -> None:
        self._fallback_state = payload
        task = asyncio.current_task()
        if task is not None:
            self._task_state[id(task)] = payload
            if len(self._task_state) > 2048:
                self._task_state.clear()

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
        primary_module = query_understanding.primary_module() if query_understanding is not None else None
        multi_entity = bool(query_understanding and query_understanding.multi_entity_flag)
        root_table_name: str | None = None

        if reg is not None and resolved_entities:
            for entity_id in resolved_entities:
                entity = reg.get_entity(entity_id)
                if entity is None:
                    continue
                if root_table_name is None:
                    root_table_name = entity.root_table.upper()
                for tbl_name in entity.default_tables:
                    entity_seeded_names.add(tbl_name.upper())
                # Graph expansion: approved edges only
                for edge in reg.get_relationships_for_entity(entity_id, approved_only=True):
                    entity_seeded_names.add(edge.target_table.upper())
                    entity_seeded_names.add(edge.source_table.upper())

        if root_table_name is None and entity_seeded_names:
            root_table_name = next(iter(entity_seeded_names), None)

        candidate_records: list[dict[str, object]] = []

        # ------------------------------------------------------------------
        # Scoring
        # ------------------------------------------------------------------
        for table in snapshot.tables:
            raw_score = self._score(table, user_query)
            adjusted_score = raw_score
            table_module = _table_module(table)
            reasons: list[str] = []

            if entity_seeded_names:
                # Entity-seed boost: tables in the seeded set get a strong boost
                if table.name.upper() in entity_seeded_names:
                    adjusted_score = int(adjusted_score * 2.0) + _ENTITY_SEED_BOOST
                    reasons.append("entity_seed")
                else:
                    # Suppress non-seeded tables (they survive only if they score
                    # very highly on their own keyword match)
                    adjusted_score = adjusted_score // 4
                    reasons.append("non_seed_suppressed")
            else:
                # Fallback: module-pattern-based boost/suppress (legacy HR/PO)
                if primary_module and table_module:
                    if table_module == primary_module and adjusted_score > 0:
                        adjusted_score = int(adjusted_score * 2.0) + _MATCHING_MODULE_BOOST
                        reasons.append("same_domain_boost")
                    elif not multi_entity:
                        adjusted_score = max(0, adjusted_score // _MISMATCH_MODULE_DIVISOR)
                        reasons.append("cross_domain_suppressed")

            if root_table_name is not None and table.name.upper() == root_table_name:
                adjusted_score += _ROOT_TABLE_BOOST
                reasons.append("root_table")

            if raw_score > 0 and not reasons:
                reasons.append("keyword_match")

            candidate_records.append({
                "table": table,
                "raw_score": raw_score,
                "score": adjusted_score,
                "module": table_module,
                "reason": ",".join(reasons) or "no_signal",
            })

        candidate_records.sort(key=lambda record: int(record["score"]), reverse=True)

        # Apply minimum score threshold (not just > 0)
        selected_records = [
            record for record in candidate_records[:top_k]
            if int(record["score"]) >= _MIN_RETRIEVAL_SCORE
        ]
        selected = [record["table"] for record in selected_records]

        if not selected:
            # Fallback: no match at all → return first top_k tables
            fallback = snapshot.tables[:top_k]
            self._set_last_retrieval_diagnostics({
                "dominant_domain_match": None,
                "root_table_name": root_table_name,
                "root_table_confidence": "low",
                "noisy_context_count": 0,
                "dropped_candidates": [f"{record['table'].name}:below_threshold" for record in candidate_records[top_k:]],
                "kept_candidates_reason": {table.name: "fallback" for table in fallback},
            })
            return CatalogSnapshot(
                tables=fallback,
                relationships=self._filter_relationships(snapshot, fallback),
            )

        # Log rejected candidates for traceability
        rejected = [
            (int(record["score"]), record["table"].name) for record in candidate_records
            if 0 < int(record["score"]) < _MIN_RETRIEVAL_SCORE
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
                selected = self._expand_related(
                    snapshot,
                    selected,
                    top_k,
                    primary_module=primary_module,
                    entity_seeded_names=entity_seeded_names,
                )

        selected_names = {table.name for table in selected}
        kept_candidates_reason = {
            record["table"].name: str(record["reason"])
            for record in candidate_records
            if record["table"].name in selected_names
        }
        expanded_tables = [table.name for table in selected if table.name not in kept_candidates_reason]
        for table_name in expanded_tables:
            kept_candidates_reason[table_name] = "relation_expansion"

        noisy_context_count = 0
        if primary_module:
            noisy_context_count = sum(
                1
                for table in selected
                if _table_module(table) is not None and _table_module(table) != primary_module
            )

        dominant_domain_match: bool | None = None
        if primary_module:
            same_domain = sum(1 for table in selected if _table_module(table) == primary_module)
            dominant_domain_match = same_domain >= max(1, len(selected) - noisy_context_count)

        root_table_confidence = "low"
        if root_table_name and selected and selected[0].name.upper() == root_table_name:
            root_table_confidence = "high"
        elif root_table_name and any(table.name.upper() == root_table_name for table in selected):
            root_table_confidence = "medium"

        dropped_candidates = []
        for record in candidate_records:
            table = record["table"]
            if table.name in selected_names:
                continue
            dropped_candidates.append(f"{table.name}:{record['reason']}")

        self._set_last_retrieval_diagnostics({
            "dominant_domain_match": dominant_domain_match,
            "root_table_name": root_table_name,
            "root_table_confidence": root_table_confidence,
            "noisy_context_count": noisy_context_count,
            "dropped_candidates": dropped_candidates,
            "kept_candidates_reason": kept_candidates_reason,
        })

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
        *,
        primary_module: str | None = None,
        entity_seeded_names: set[str] | None = None,
    ) -> list[TableMetadata]:
        """Add FK-related tables that aren't already selected (up to top_k)."""
        selected_names = {t.name.upper() for t in selected}
        all_tables = {t.name.upper(): t for t in snapshot.tables}
        entity_seeded_names = entity_seeded_names or set()

        to_add: list[TableMetadata] = []
        for table in selected:
            for rel in snapshot.get_relationships_for(table.name):
                other = (
                    rel.to_table.upper()
                    if rel.from_table.upper() == table.name.upper()
                    else rel.from_table.upper()
                )
                if other not in selected_names and other in all_tables:
                    candidate = all_tables[other]
                    candidate_module = _table_module(candidate)
                    if primary_module and candidate_module and candidate_module != primary_module and other not in entity_seeded_names:
                        continue
                    to_add.append(candidate)
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
