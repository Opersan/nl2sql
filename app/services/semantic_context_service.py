"""Runtime semantic context retrieval for planner prompt injection.

This service gathers compact, query-relevant semantic facts from the
existing ``SemanticFoundationRegistry`` and formats them for inclusion
in the planner prompt as a distinct *Semantic Grounding* section.

Design principles
-----------------
* **Deterministic** — no LLM calls; pure index lookups.
* **Selective** — retrieves only items relevant to the current query
  using signals from ``QueryUnderstanding`` and the retrieved
  ``CatalogSnapshot``.
* **Budget-aware** — each subsection is capped; a global char budget
  is enforced via ``max_total_chars``.
* **Traceable** — returns a structured ``SemanticRetrievalTrace`` that
  the planner trace can embed verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.domain.catalog_models import CatalogSnapshot
from app.semantic.models import (
    FlexfieldDefinition,
    GlossaryEntry,
    LookupType,
    MetricDefinition,
    RelationshipEdge,
    SemanticEntity,
)

logger = get_logger(__name__)

# ── Budget defaults ────────────────────────────────────────────────────────
DEFAULT_MAX_TOTAL_CHARS: int = 3000
"""Hard cap for the entire semantic grounding block.

With a 262k context window, 3000 chars (~750 tokens) is comfortably
within budget while keeping the semantic section focused.
"""

_MAX_ENTITIES: int = 5
_MAX_GLOSSARY: int = 12
_MAX_METRICS: int = 10
_MAX_RELATIONSHIPS: int = 8
_MAX_LOOKUPS: int = 10
_MAX_FLEXFIELDS: int = 4


# ── Result dataclasses ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class SemanticContextBundle:
    """Prompt-ready semantic context matched for the current query."""

    matched_entities: list[SemanticEntity] = field(default_factory=list)
    matched_glossary: list[GlossaryEntry] = field(default_factory=list)
    matched_metrics: list[MetricDefinition] = field(default_factory=list)
    matched_relationships: list[RelationshipEdge] = field(default_factory=list)
    matched_lookups: list[LookupType] = field(default_factory=list)
    matched_flexfields: list[FlexfieldDefinition] = field(default_factory=list)

    @property
    def total_matches(self) -> int:
        return (
            len(self.matched_entities)
            + len(self.matched_glossary)
            + len(self.matched_metrics)
            + len(self.matched_relationships)
            + len(self.matched_lookups)
            + len(self.matched_flexfields)
        )

    @property
    def is_empty(self) -> bool:
        return self.total_matches == 0


@dataclass(frozen=True)
class SemanticRetrievalTrace:
    """Structured trace payload for the semantic retrieval stage."""

    semantic_retrieval_used: bool = False
    semantic_retrieval_source: str = "none"
    semantic_retrieval_skip_reason: str | None = None
    # Match counts
    matched_entity_count: int = 0
    matched_glossary_count: int = 0
    matched_metric_count: int = 0
    matched_relationship_count: int = 0
    matched_lookup_count: int = 0
    matched_flexfield_count: int = 0
    semantic_matches_total: int = 0
    # Matched ids/names
    matched_entity_ids: list[str] = field(default_factory=list)
    matched_glossary_terms: list[str] = field(default_factory=list)
    matched_metric_names: list[str] = field(default_factory=list)
    matched_relationship_ids: list[str] = field(default_factory=list)
    matched_lookup_names: list[str] = field(default_factory=list)
    matched_flexfield_names: list[str] = field(default_factory=list)
    # Prompt section
    semantic_prompt_chars: int = 0
    semantic_prompt_section_text: str = ""
    # Budget
    semantic_budget_applied: bool = False
    semantic_items_trimmed: int = 0
    semantic_trim_reason: str | None = None

    def as_trace_dict(self) -> dict[str, Any]:
        return {
            "semantic_retrieval_used": self.semantic_retrieval_used,
            "semantic_retrieval_source": self.semantic_retrieval_source,
            "semantic_retrieval_skip_reason": self.semantic_retrieval_skip_reason,
            "matched_entity_count": self.matched_entity_count,
            "matched_glossary_count": self.matched_glossary_count,
            "matched_metric_count": self.matched_metric_count,
            "matched_relationship_count": self.matched_relationship_count,
            "matched_lookup_count": self.matched_lookup_count,
            "matched_flexfield_count": self.matched_flexfield_count,
            "semantic_matches_total": self.semantic_matches_total,
            "matched_entity_ids": list(self.matched_entity_ids),
            "matched_glossary_terms": list(self.matched_glossary_terms),
            "matched_metric_names": list(self.matched_metric_names),
            "matched_relationship_ids": list(self.matched_relationship_ids),
            "matched_lookup_names": list(self.matched_lookup_names),
            "matched_flexfield_names": list(self.matched_flexfield_names),
            "semantic_prompt_chars": self.semantic_prompt_chars,
            "semantic_prompt_section_text": self.semantic_prompt_section_text,
            "semantic_budget_applied": self.semantic_budget_applied,
            "semantic_items_trimmed": self.semantic_items_trimmed,
            "semantic_trim_reason": self.semantic_trim_reason,
        }


# ── Retrieval logic ────────────────────────────────────────────────────────

def retrieve_semantic_context(
    *,
    query_understanding: Any,
    retrieved_snapshot: CatalogSnapshot,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> tuple[SemanticContextBundle, SemanticRetrievalTrace]:
    """Retrieve query-relevant semantic context from the registry.

    Parameters
    ----------
    query_understanding:
        ``QueryUnderstanding`` instance with resolved entities, detected
        metrics, extracted filters, etc.
    retrieved_snapshot:
        The catalog snapshot selected for this query (used to scope
        relationships, lookups, flexfields to retrieved tables).
    max_total_chars:
        Character budget for the rendered semantic grounding block.

    Returns
    -------
    A ``(bundle, trace)`` pair.  *bundle* carries the matched semantic
    objects; *trace* carries structured audit metadata.
    """
    try:
        from app.semantic.registry import get_registry
        registry = get_registry()
    except Exception as exc:
        logger.warning(
            "[semantic-context] registry unavailable: %s — skipping",
            exc,
        )
        return (
            SemanticContextBundle(),
            SemanticRetrievalTrace(
                semantic_retrieval_skip_reason=f"registry_unavailable: {exc}",
            ),
        )

    # Collect table names from the retrieved snapshot for scoping
    retrieved_table_names = {t.name.upper() for t in retrieved_snapshot.tables}
    if not retrieved_table_names:
        return (
            SemanticContextBundle(),
            SemanticRetrievalTrace(
                semantic_retrieval_skip_reason="no_tables_retrieved",
            ),
        )

    # ── 1. Entities ────────────────────────────────────────────────────
    resolved_entity_ids: list[str] = list(
        getattr(query_understanding, "resolved_entities", []) or []
    )
    # Also match entities by retrieved table names
    seen_entity_ids: set[str] = set(resolved_entity_ids)
    for tbl_name in retrieved_table_names:
        ent = registry.get_entity_by_table(tbl_name)
        if ent and ent.entity_id not in seen_entity_ids:
            resolved_entity_ids.append(ent.entity_id)
            seen_entity_ids.add(ent.entity_id)

    matched_entities: list[SemanticEntity] = []
    for eid in resolved_entity_ids[:_MAX_ENTITIES]:
        ent = registry.get_entity(eid)
        if ent:
            matched_entities.append(ent)

    # ── 2. Glossary ────────────────────────────────────────────────────
    # Gather glossary terms from query understanding + registry term scan
    normed = getattr(query_understanding, "normalized_question", "") or ""
    matched_glossary: list[GlossaryEntry] = []
    seen_glossary: set[str] = set()

    # Phrase scan on normalized question
    phrase_hits = registry.resolve_phrases_in_text(normed)
    for entry in phrase_hits:
        key = f"{entry.normalized}:{entry.canonical}"
        if key not in seen_glossary:
            seen_glossary.add(key)
            matched_glossary.append(entry)

    # Token scan — check each extracted filter dimension + detected entities
    filter_dimensions: list[str] = []
    for f in getattr(query_understanding, "extracted_filters", []) or []:
        dim = f.get("dimension", "")
        val = f.get("value", "")
        if dim:
            filter_dimensions.append(dim)
        if val:
            filter_dimensions.append(val)

    for token in filter_dimensions:
        from app.utils.turkish import casefold_tr
        normed_token = casefold_tr(token)
        hits = registry.resolve_term(normed_token)
        for entry in hits:
            key = f"{entry.normalized}:{entry.canonical}"
            if key not in seen_glossary:
                seen_glossary.add(key)
                matched_glossary.append(entry)

    matched_glossary = matched_glossary[:_MAX_GLOSSARY]

    # ── 3. Metrics ─────────────────────────────────────────────────────
    detected_metric_ids: list[str] = list(
        getattr(query_understanding, "detected_metrics", []) or []
    )
    matched_metrics: list[MetricDefinition] = []
    seen_metric_ids: set[str] = set()

    # Direct detected metrics
    for mid in detected_metric_ids:
        m = registry.get_metric(mid)
        if m and m.metric_id not in seen_metric_ids:
            seen_metric_ids.add(m.metric_id)
            matched_metrics.append(m)

    # Metric alias scan from normalized question tokens
    agg_hints = getattr(query_understanding, "extracted_aggregation_hints", []) or []
    for hint in agg_hints:
        from app.utils.turkish import casefold_tr
        m = registry.get_metric_by_alias(casefold_tr(hint))
        if m and m.metric_id not in seen_metric_ids:
            seen_metric_ids.add(m.metric_id)
            matched_metrics.append(m)

    # Also match metrics whose entity_id matches any resolved entity
    if matched_entities:
        entity_ids = {e.entity_id for e in matched_entities}
        for m in registry.get_all_metrics():
            if m.entity_id in entity_ids and m.metric_id not in seen_metric_ids:
                seen_metric_ids.add(m.metric_id)
                matched_metrics.append(m)

    matched_metrics = matched_metrics[:_MAX_METRICS]

    # ── 4. Relationships ───────────────────────────────────────────────
    matched_relationships: list[RelationshipEdge] = []
    seen_rel_ids: set[str] = set()

    for ent in matched_entities:
        edges = registry.get_relationships_for_entity(
            ent.entity_id, approved_only=True,
        )
        for edge in edges:
            # Only include if both tables are in the retrieved snapshot
            if (
                edge.edge_id not in seen_rel_ids
                and edge.source_table.upper() in retrieved_table_names
                and edge.target_table.upper() in retrieved_table_names
            ):
                seen_rel_ids.add(edge.edge_id)
                matched_relationships.append(edge)

    matched_relationships = matched_relationships[:_MAX_RELATIONSHIPS]

    # ── 5. Lookups ─────────────────────────────────────────────────────
    matched_lookups: list[LookupType] = []
    seen_lookup_keys: set[str] = set()

    # Match lookups relevant to filter columns in retrieved tables
    for f in getattr(query_understanding, "extracted_filters", []) or []:
        col_hint = f.get("column_hint", "")
        if col_hint:
            for tbl_name in retrieved_table_names:
                lkps = registry.get_lookups_for_column(col_hint, table_name=tbl_name)
                for lkp in lkps:
                    key = f"{lkp.lookup_type}:{lkp.raw_value}"
                    if key not in seen_lookup_keys:
                        seen_lookup_keys.add(key)
                        matched_lookups.append(lkp)

    # Also check lookup_hints from QU
    for hint in getattr(query_understanding, "lookup_hints", []) or []:
        lt = hint.get("lookup_type", "")
        if lt:
            for lkp in registry.get_lookup(lt):
                key = f"{lkp.lookup_type}:{lkp.raw_value}"
                if key not in seen_lookup_keys:
                    seen_lookup_keys.add(key)
                    matched_lookups.append(lkp)

    matched_lookups = matched_lookups[:_MAX_LOOKUPS]

    # ── 6. Flexfields ──────────────────────────────────────────────────
    matched_flexfields: list[FlexfieldDefinition] = []
    for ff in registry.get_all_flexfields():
        if ff.table and ff.table.upper() in retrieved_table_names:
            matched_flexfields.append(ff)

    matched_flexfields = matched_flexfields[:_MAX_FLEXFIELDS]

    # ── Build bundle ───────────────────────────────────────────────────
    bundle = SemanticContextBundle(
        matched_entities=matched_entities,
        matched_glossary=matched_glossary,
        matched_metrics=matched_metrics,
        matched_relationships=matched_relationships,
        matched_lookups=matched_lookups,
        matched_flexfields=matched_flexfields,
    )

    # ── Render prompt block & apply budget ─────────────────────────────
    raw_text = build_semantic_grounding_block(bundle)
    items_trimmed = 0
    trim_reason: str | None = None
    budget_applied = False

    if len(raw_text) > max_total_chars and max_total_chars > 0:
        budget_applied = True
        # Progressively trim: flexfields → lookups → glossary → metrics
        trimmed_bundle = bundle
        for trim_target, trim_label in [
            ("flexfields", "drop_flexfields"),
            ("lookups", "drop_lookups"),
            ("glossary", "trim_glossary"),
        ]:
            if len(raw_text) <= max_total_chars:
                break
            if trim_target == "flexfields" and trimmed_bundle.matched_flexfields:
                items_trimmed += len(trimmed_bundle.matched_flexfields)
                trimmed_bundle = SemanticContextBundle(
                    matched_entities=trimmed_bundle.matched_entities,
                    matched_glossary=trimmed_bundle.matched_glossary,
                    matched_metrics=trimmed_bundle.matched_metrics,
                    matched_relationships=trimmed_bundle.matched_relationships,
                    matched_lookups=trimmed_bundle.matched_lookups,
                    matched_flexfields=[],
                )
                trim_reason = trim_label
            elif trim_target == "lookups" and trimmed_bundle.matched_lookups:
                items_trimmed += len(trimmed_bundle.matched_lookups)
                trimmed_bundle = SemanticContextBundle(
                    matched_entities=trimmed_bundle.matched_entities,
                    matched_glossary=trimmed_bundle.matched_glossary,
                    matched_metrics=trimmed_bundle.matched_metrics,
                    matched_relationships=trimmed_bundle.matched_relationships,
                    matched_lookups=[],
                    matched_flexfields=trimmed_bundle.matched_flexfields,
                )
                trim_reason = trim_label
            elif trim_target == "glossary" and len(trimmed_bundle.matched_glossary) > 3:
                items_trimmed += len(trimmed_bundle.matched_glossary) - 3
                trimmed_bundle = SemanticContextBundle(
                    matched_entities=trimmed_bundle.matched_entities,
                    matched_glossary=trimmed_bundle.matched_glossary[:3],
                    matched_metrics=trimmed_bundle.matched_metrics,
                    matched_relationships=trimmed_bundle.matched_relationships,
                    matched_lookups=trimmed_bundle.matched_lookups,
                    matched_flexfields=trimmed_bundle.matched_flexfields,
                )
                trim_reason = trim_label
            raw_text = build_semantic_grounding_block(trimmed_bundle)
        bundle = trimmed_bundle

        # Final hard truncate if still over budget
        if len(raw_text) > max_total_chars:
            items_trimmed += 1
            trim_reason = "hard_truncate"
            raw_text = raw_text[:max_total_chars - 3] + "..."

    # ── Build trace ────────────────────────────────────────────────────
    trace = SemanticRetrievalTrace(
        semantic_retrieval_used=True,
        semantic_retrieval_source="SemanticFoundationRegistry",
        matched_entity_count=len(bundle.matched_entities),
        matched_glossary_count=len(bundle.matched_glossary),
        matched_metric_count=len(bundle.matched_metrics),
        matched_relationship_count=len(bundle.matched_relationships),
        matched_lookup_count=len(bundle.matched_lookups),
        matched_flexfield_count=len(bundle.matched_flexfields),
        semantic_matches_total=bundle.total_matches,
        matched_entity_ids=[e.entity_id for e in bundle.matched_entities],
        matched_glossary_terms=[g.raw_term for g in bundle.matched_glossary],
        matched_metric_names=[m.name for m in bundle.matched_metrics],
        matched_relationship_ids=[r.edge_id for r in bundle.matched_relationships],
        matched_lookup_names=[lk.lookup_type for lk in bundle.matched_lookups],
        matched_flexfield_names=[ff.name for ff in bundle.matched_flexfields],
        semantic_prompt_chars=len(raw_text),
        semantic_prompt_section_text=raw_text,
        semantic_budget_applied=budget_applied,
        semantic_items_trimmed=items_trimmed,
        semantic_trim_reason=trim_reason,
    )

    logger.info(
        "[semantic-context] matched %d item(s): entities=%d glossary=%d "
        "metrics=%d relationships=%d lookups=%d flexfields=%d chars=%d",
        bundle.total_matches,
        len(bundle.matched_entities),
        len(bundle.matched_glossary),
        len(bundle.matched_metrics),
        len(bundle.matched_relationships),
        len(bundle.matched_lookups),
        len(bundle.matched_flexfields),
        len(raw_text),
    )

    return bundle, trace


# ── Prompt block rendering ─────────────────────────────────────────────────

def build_semantic_grounding_block(bundle: SemanticContextBundle) -> str:
    """Render a compact, human-readable semantic grounding prompt section.

    Returns an empty string when the bundle is empty.
    """
    if bundle.is_empty:
        return ""

    lines: list[str] = [
        "Anlamsal bağlam (Semantic Grounding):",
        "  ⚠ Bu bölümdeki metrik ve semantic adlar mantıksal grounding içindir; "
        "fiziksel kolon referansı değildir. Fiziksel kolon seçimi yalnız yapısal "
        "katalogdan (Tablo Detayları) yapılmalıdır.",
    ]

    # Entities
    if bundle.matched_entities:
        lines.append("  İş varlıkları:")
        for e in bundle.matched_entities:
            tables = ", ".join(
                [e.root_table] + [t for t in (e.default_tables or []) if t != e.root_table]
            )
            filters = ", ".join(e.likely_filters[:5]) if e.likely_filters else "-"
            lines.append(
                f"    - {e.display_name} ({e.entity_id}): "
                f"modül={e.module}, tablolar=[{tables}], "
                f"filtreler=[{filters}]"
            )

    # Glossary
    if bundle.matched_glossary:
        lines.append("  Terim eşlemeleri:")
        for g in bundle.matched_glossary:
            lines.append(f"    - \"{g.raw_term}\" → {g.canonical} ({g.match_type})")

    # Metrics
    if bundle.matched_metrics:
        lines.append("  İş metrikleri:")
        for m in bundle.matched_metrics:
            desc = f": {m.description}" if m.description else ""
            lines.append(
                f"    - {m.name} ({m.metric_id}): "
                f"ifade={m.expression}, tablo={m.table or '-'}{desc}"
            )

    # Relationships (curated, not raw FK)
    if bundle.matched_relationships:
        lines.append("  Onaylı join ilişkileri (semantic, catalog FK'dan ayrı):")
        for r in bundle.matched_relationships:
            join_keys = ", ".join(
                f"{p.source_column}={p.target_column}" for p in r.join_keys
            )
            notes = f" — {r.notes}" if r.notes else ""
            lines.append(
                f"    - {r.source_table} → {r.target_table} "
                f"[{join_keys}] ({r.join_direction}){notes}"
            )

    # Lookups
    if bundle.matched_lookups:
        lines.append("  Lookup değerleri:")
        for lk in bundle.matched_lookups:
            meaning = lk.meaning or lk.decoded_value or "-"
            lines.append(
                f"    - {lk.lookup_type}: {lk.raw_value} → {meaning}"
            )

    # Flexfields
    if bundle.matched_flexfields:
        lines.append("  Flexfield tanımları:")
        for ff in bundle.matched_flexfields:
            desc = f": {ff.description}" if ff.description else ""
            lines.append(
                f"    - {ff.name} ({ff.flexfield_id}): "
                f"tablo={ff.table}, segment={ff.segment_column}{desc}"
            )

    return "\n".join(lines)
