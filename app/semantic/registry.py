"""Runtime typed accessor for the semantic foundation.

``SemanticFoundationRegistry`` builds O(1) in-memory indexes from the
loaded ``SemanticFoundation`` and exposes well-typed accessor methods
that replace hardcoded keyword sets throughout the codebase.

Singleton
---------
``get_registry()`` returns an application-level singleton.  It is safe
to call from any module; the underlying ``SemanticFoundation`` is loaded
exactly once via ``lru_cache``::

    from app.semantic.registry import get_registry

    registry = get_registry()
    entries  = registry.resolve_term("calisan")       # O(1) term lookup
    entity   = registry.get_entity("HR_EMPLOYEES")   # O(1) entity lookup

Phrase matching
---------------
``resolve_phrases_in_text(normed_text)`` performs O(n_phrases) substring
scans.  This is intentional: phrase entries in the glossary represent
multi-token patterns that cannot be pre-split into individual token keys.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.semantic.loader import get_semantic_foundation, load_semantic_foundation
from app.semantic.models import (
    FlexfieldDefinition,
    GlossaryEntry,
    LookupType,
    MetricDefinition,
    RelationshipEdge,
    SemanticEntity,
    SemanticFoundation,
)


class SemanticFoundationRegistry:
    """Runtime accessor with pre-built O(1) indexes over the semantic foundation.

    Parameters
    ----------
    foundation:
        A pre-loaded ``SemanticFoundation``.  In production, obtained via
        ``get_semantic_foundation()``.  In tests, pass a custom foundation
        to avoid touching the file system.
    """

    def __init__(self, foundation: SemanticFoundation) -> None:
        self._foundation = foundation

        # Index: normalized_term → list[GlossaryEntry]  (exact/metric/filter keys)
        self._term_index: dict[str, list[GlossaryEntry]] = {}
        # Phrase entries checked as substrings — kept in insertion order
        self._phrase_entries: list[tuple[str, GlossaryEntry]] = []

        for entry in foundation.glossary:
            if entry.match_type == "phrase":
                self._phrase_entries.append((entry.normalized, entry))
            else:
                self._term_index.setdefault(entry.normalized, []).append(entry)

        # Index: entity_id → SemanticEntity
        self._entity_index: dict[str, SemanticEntity] = {
            e.entity_id: e for e in foundation.entities
        }

        # Index: metric_id → MetricDefinition
        self._metric_index: dict[str, MetricDefinition] = {
            m.metric_id: m for m in foundation.metrics
        }

        # Index: lowercase alias → MetricDefinition
        self._metric_alias_index: dict[str, MetricDefinition] = {}
        for m in foundation.metrics:
            for alias in m.aliases:
                self._metric_alias_index[alias.lower()] = m

        # Index: entity_id → list[RelationshipEdge]  (source side only for
        # directed edges; bidirectional edges are indexed on both sides)
        self._entity_relationships: dict[str, list[RelationshipEdge]] = {}
        for edge in foundation.relationships:
            self._entity_relationships.setdefault(edge.source_entity, []).append(edge)
            if edge.join_direction == "bidirectional":
                self._entity_relationships.setdefault(
                    edge.target_entity, []
                ).append(edge)

        # Index: lookup_type → list[LookupType]
        self._lookup_index: dict[str, list[LookupType]] = {}
        for lkp in foundation.lookups:
            self._lookup_index.setdefault(lkp.lookup_type, []).append(lkp)

        # Index: flexfield_id → FlexfieldDefinition
        self._flexfield_index: dict[str, FlexfieldDefinition] = {
            f.flexfield_id: f for f in foundation.flexfields
        }

        # Flat lists (for iteration)
        self._all_entities: list[SemanticEntity] = list(foundation.entities)
        self._all_relationships: list[RelationshipEdge] = list(foundation.relationships)
        self._all_glossary_entries: list[GlossaryEntry] = list(foundation.glossary)

    # ------------------------------------------------------------------
    # Term resolution
    # ------------------------------------------------------------------

    def resolve_term(self, normalized_term: str) -> list[GlossaryEntry]:
        """Return all glossary entries matching *normalized_term* exactly (O(1)).

        Only matches ``exact``, ``filter_alias``, and ``metric_alias`` entries.
        Use ``resolve_phrases_in_text`` for phrase-type substring matching.
        """
        return self._term_index.get(normalized_term, [])

    def resolve_phrases_in_text(self, normed_text: str) -> list[GlossaryEntry]:
        """Return all phrase-type glossary entries whose normalized form is a
        substring of *normed_text*.

        Complexity: O(n_phrases).  Designed for single-query analysis passes.
        """
        return [entry for phrase, entry in self._phrase_entries if phrase in normed_text]

    def resolve_term_to_entity_ids(self, normalized_term: str) -> list[str]:
        """Return entity_id canonicals for exact/phrase matches of *normalized_term*."""
        return [
            e.canonical
            for e in self._term_index.get(normalized_term, [])
            if e.match_type in ("exact", "phrase")
            and not e.canonical.startswith("filter:")
            and not e.canonical.startswith("metric:")
        ]

    # ------------------------------------------------------------------
    # Entity access
    # ------------------------------------------------------------------

    def get_entity(self, entity_id: str) -> SemanticEntity | None:
        """Return the ``SemanticEntity`` for *entity_id*, or ``None``."""
        return self._entity_index.get(entity_id)

    def get_all_entities(self) -> list[SemanticEntity]:
        """Return all entities in definition order."""
        return self._all_entities

    def get_entity_by_table(self, table_name: str) -> SemanticEntity | None:
        """Return the first entity whose ``root_table`` or ``default_tables``
        contains *table_name* (case-insensitive)."""
        upper = table_name.upper()
        for entity in self._all_entities:
            tables = {entity.root_table.upper(), *(t.upper() for t in entity.default_tables)}
            if upper in tables:
                return entity
        return None

    # ------------------------------------------------------------------
    # Relationship graph
    # ------------------------------------------------------------------

    def get_relationships_for_entity(
        self,
        entity_id: str,
        *,
        approved_only: bool = True,
    ) -> list[RelationshipEdge]:
        """Return outgoing relationship edges for *entity_id*.

        Parameters
        ----------
        approved_only:
            When ``True`` (default), only edges with
            ``approved_for_planner=True`` are returned.
        """
        edges = self._entity_relationships.get(entity_id, [])
        if approved_only:
            return [e for e in edges if e.approved_for_planner]
        return list(edges)

    def get_all_relationships(self) -> list[RelationshipEdge]:
        return self._all_relationships

    # ------------------------------------------------------------------
    # Metric access
    # ------------------------------------------------------------------

    def get_all_metrics(self) -> list[MetricDefinition]:
        """Return all metric definitions in definition order."""
        return list(self._metric_index.values())

    def get_metric(self, metric_id: str) -> MetricDefinition | None:
        """Return the metric for *metric_id*, or ``None``."""
        return self._metric_index.get(metric_id)

    def get_metric_by_alias(self, alias: str) -> MetricDefinition | None:
        """Return the metric whose alias list contains *alias* (case-insensitive)."""
        return self._metric_alias_index.get(alias.lower())

    # ------------------------------------------------------------------
    # Lookup access
    # ------------------------------------------------------------------

    def get_lookup(self, lookup_type: str) -> list[LookupType]:
        """Return all lookup codes for *lookup_type*."""
        return self._lookup_index.get(lookup_type, [])

    # ------------------------------------------------------------------
    # Flexfield access
    # ------------------------------------------------------------------

    def get_flexfield(self, flexfield_id: str) -> FlexfieldDefinition | None:
        """Return the flexfield definition for *flexfield_id*, or ``None``."""
        return self._flexfield_index.get(flexfield_id)

    def get_all_flexfields(self) -> list[FlexfieldDefinition]:
        """Return all flexfield definitions in definition order."""
        return list(self._flexfield_index.values())

    def get_all_glossary_entries(self) -> list[GlossaryEntry]:
        """Return all glossary entries in definition order."""
        return self._all_glossary_entries

    # ------------------------------------------------------------------
    # Aggregate signal-keyword index (used by intent_guard)
    # ------------------------------------------------------------------

    def build_signal_keyword_index(self) -> dict[str, set[str]]:
        """Aggregate ``filter_signal_keywords`` across all entities.

        Returns a mapping of signal_code → set of normalized keyword strings
        assembled from every entity that defines that signal.
        """
        index: dict[str, set[str]] = {}
        for entity in self._all_entities:
            for signal_code, keywords in entity.filter_signal_keywords.items():
                index.setdefault(signal_code, set()).update(keywords)
        return index

    def build_dimension_column_index(self) -> dict[str, tuple[str, ...]]:
        """Build dimension → column-hint tuple from entities' likely_filters
        and likely_identifiers.

        Returns a mapping used by ``intent_guard.compute_filter_coverage``.
        """
        _DIM_PREFIXES: dict[str, tuple[str, ...]] = {
            "status": ("status", "authorization_status", "durum", "cikis_tarihi",
                       "quit_date", "bordrolu", "stajyer", "enabled_flag",
                       "payment_status_flag", "complete_flag"),
            "date": ("date", "tarih", "creation", "effective", "start", "end",
                     "invoice_date", "trx_date", "default_effective_date"),
            "org": ("location", "lokasyon", "birim", "departman", "organization",
                    "unvan", "org_id", "ledger_id", "bill_to_customer_id"),
        }
        # Augment with entity-specific columns
        augmented: dict[str, set[str]] = {k: set(v) for k, v in _DIM_PREFIXES.items()}
        for entity in self._all_entities:
            for col in entity.likely_filters:
                col_lower = col.lower()
                for dim, prefixes in _DIM_PREFIXES.items():
                    if any(col_lower.startswith(p) or p in col_lower for p in prefixes):
                        augmented[dim].add(col_lower)
        return {dim: tuple(sorted(cols)) for dim, cols in augmented.items()}


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_registry() -> SemanticFoundationRegistry:
    """Return the application-level ``SemanticFoundationRegistry`` singleton.

    Loaded once on first call; all subsequent calls return the same instance.
    """
    return SemanticFoundationRegistry(get_semantic_foundation())


def make_registry_from_dir(semantic_dir: Path) -> SemanticFoundationRegistry:
    """Create a ``SemanticFoundationRegistry`` from a custom directory.

    Use this in tests to load fixture data without affecting the
    application singleton cache.
    """
    foundation = load_semantic_foundation(semantic_dir=semantic_dir)
    return SemanticFoundationRegistry(foundation)
