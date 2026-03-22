"""Startup-time consistency check: semantic registry ↔ catalog metadata.

Validates that every table, column, and join key referenced in the semantic
registry actually exists in the provided :class:`~app.domain.catalog_models.CatalogSnapshot`.

Design notes
------------
* The validator is a **pure function** — no I/O, no side-effects.
* Skeleton / draft entities whose ``root_table`` is absent from the catalog
  are still checked; their issues are included in the returned list.
* Special values that are intentionally not catalog columns are skipped:
    - ``"*"``                — wildcard aggregate (e.g. ``COUNT(*)``)
    - ``"__COLUMN_REF__…"``  — runtime column references in filter values
    - ``expression_ref``     — named expression references (not raw columns)
"""

from __future__ import annotations

from dataclasses import dataclass

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.catalog_models import CatalogSnapshot
from app.domain.semantic_models import BusinessEntitySemantic, SemanticRegistry

if TYPE_CHECKING:
    from app.semantic.registry import SemanticFoundationRegistry


# ---------------------------------------------------------------------------
# Issue + error types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegistryIssue:
    """A single consistency problem found between the registry and the catalog."""

    entity_id: str
    location: str   # human-readable path to the offending field
    detail: str     # what exactly is missing / wrong

    def __str__(self) -> str:
        return f"[{self.entity_id}] {self.location}: {self.detail}"


class RegistryValidationError(Exception):
    """Raised by :func:`assert_registry_valid` when issues are found."""

    def __init__(self, issues: list[RegistryIssue]) -> None:
        self.issues = issues
        lines = "\n".join(f"  • {i}" for i in issues)
        super().__init__(
            f"Semantic registry validation failed ({len(issues)} issue(s)):\n{lines}"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _table_exists(name: str, snapshot: CatalogSnapshot) -> bool:
    return snapshot.get_table(name) is not None


def _column_in_table(col: str, table_name: str, snapshot: CatalogSnapshot) -> bool:
    tbl = snapshot.get_table(table_name)
    return tbl is not None and tbl.has_column(col)


def _column_in_entity(
    col: str,
    entity: BusinessEntitySemantic,
    snapshot: CatalogSnapshot,
) -> bool:
    """Return True if *col* exists in ANY of the entity's known tables."""
    return any(
        _column_in_table(col, t, snapshot)
        for t in [entity.root_table, *entity.child_tables]
    )


def _skip_col(col: str) -> bool:
    """Return True for wildcard or runtime-reference column names."""
    return col == "*" or col.upper().startswith("__")


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

def validate_registry_against_catalog(
    registry: SemanticRegistry,
    snapshot: CatalogSnapshot,
) -> list[RegistryIssue]:
    """Check *registry* for consistency against *snapshot*.

    Returns a (possibly empty) list of :class:`RegistryIssue` objects.
    The list is empty when everything is consistent.

    Checks performed (per entity)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    1. ``root_table`` exists in catalog.
    2. Every ``child_table`` exists in catalog.
    3. Every ``join_path`` step — left/right table AND column — exists in
       catalog.
    4. ``intent_defaults`` group-by columns exist in some entity table.
    5. ``intent_defaults`` aggregation table+column combinations exist.
    6. ``intent_defaults`` filter table+column combinations exist.
    """
    issues: list[RegistryIssue] = []

    for entity in registry.entities:
        eid = entity.entity_id

        # ------------------------------------------------------------------
        # 1. root_table
        # ------------------------------------------------------------------
        if not _table_exists(entity.root_table, snapshot):
            issues.append(RegistryIssue(
                entity_id=eid,
                location="root_table",
                detail=f"table '{entity.root_table}' not found in catalog",
            ))

        # ------------------------------------------------------------------
        # 2. child_tables
        # ------------------------------------------------------------------
        for child_tbl in entity.child_tables:
            if not _table_exists(child_tbl, snapshot):
                issues.append(RegistryIssue(
                    entity_id=eid,
                    location="child_tables",
                    detail=f"table '{child_tbl}' not found in catalog",
                ))

        # ------------------------------------------------------------------
        # 3. join_path steps — table and column existence
        # ------------------------------------------------------------------
        for jp in entity.join_paths:
            for step_idx, step in enumerate(jp.steps):
                loc = f"join_path:{jp.path_id} step[{step_idx}]"
                for side, tbl, col in (
                    ("left",  step.left_table,  step.left_column),
                    ("right", step.right_table, step.right_column),
                ):
                    if not _table_exists(tbl, snapshot):
                        issues.append(RegistryIssue(
                            entity_id=eid,
                            location=loc,
                            detail=f"{side} table '{tbl}' not found in catalog",
                        ))
                    elif not _column_in_table(col, tbl, snapshot):
                        issues.append(RegistryIssue(
                            entity_id=eid,
                            location=loc,
                            detail=f"{side} column '{col}' not found in table '{tbl}'",
                        ))

        # ------------------------------------------------------------------
        # 4–6. intent_defaults per-intent plan-shape fields
        # ------------------------------------------------------------------
        for intent_name, intent_def in entity.intent_defaults.items():

            # 4. group_by
            for col in intent_def.group_by:
                if not _skip_col(col) and not _column_in_entity(col, entity, snapshot):
                    issues.append(RegistryIssue(
                        entity_id=eid,
                        location=f"intent_defaults:{intent_name} group_by",
                        detail=f"column '{col}' not found in any entity table",
                    ))

            # 5. aggregations
            for idx, agg in enumerate(intent_def.aggregations):
                if _skip_col(agg.column):
                    continue
                loc = f"intent_defaults:{intent_name} aggregations[{idx}]"
                if agg.table is not None:
                    if not _table_exists(agg.table, snapshot):
                        issues.append(RegistryIssue(
                            entity_id=eid,
                            location=loc,
                            detail=f"table '{agg.table}' not found in catalog",
                        ))
                    elif not _column_in_table(agg.column, agg.table, snapshot):
                        issues.append(RegistryIssue(
                            entity_id=eid,
                            location=loc,
                            detail=f"column '{agg.column}' not found in table '{agg.table}'",
                        ))
                elif not _column_in_entity(agg.column, entity, snapshot):
                    issues.append(RegistryIssue(
                        entity_id=eid,
                        location=loc,
                        detail=f"column '{agg.column}' not found in any entity table",
                    ))

            # 6. filters
            for idx, flt in enumerate(intent_def.filters):
                if _skip_col(flt.column):
                    continue
                loc = f"intent_defaults:{intent_name} filters[{idx}]"
                if flt.table is not None:
                    if not _table_exists(flt.table, snapshot):
                        issues.append(RegistryIssue(
                            entity_id=eid,
                            location=loc,
                            detail=f"table '{flt.table}' not found in catalog",
                        ))
                    elif not _column_in_table(flt.column, flt.table, snapshot):
                        issues.append(RegistryIssue(
                            entity_id=eid,
                            location=loc,
                            detail=f"column '{flt.column}' not found in table '{flt.table}'",
                        ))
                elif not _column_in_entity(flt.column, entity, snapshot):
                    issues.append(RegistryIssue(
                        entity_id=eid,
                        location=loc,
                        detail=f"column '{flt.column}' not found in any entity table",
                    ))

    return issues


def assert_registry_valid(
    registry: SemanticRegistry,
    snapshot: CatalogSnapshot,
    *,
    foundation_registry: SemanticFoundationRegistry | None = None,
) -> None:
    """Raise :exc:`RegistryValidationError` if *registry* has catalog inconsistencies.

    When *foundation_registry* is provided, also validates the JSONL-based
    semantic foundation (entities, relationships, metrics, flexfields, glossary)
    against the catalog snapshot.

    This is the strict-mode entry point.  For fail-open startup logging use
    :func:`validate_registry_against_catalog` directly.
    """
    issues = validate_registry_against_catalog(registry, snapshot)
    if foundation_registry is not None:
        issues += validate_semantic_foundation_against_catalog(
            foundation_registry, snapshot
        )
    if issues:
        raise RegistryValidationError(issues)


# ---------------------------------------------------------------------------
# Foundation (JSONL) validator
# ---------------------------------------------------------------------------

def validate_semantic_foundation_against_catalog(
    foundation_registry: SemanticFoundationRegistry,
    snapshot: CatalogSnapshot,
) -> list[RegistryIssue]:
    """Validate the new JSONL-based semantic foundation against *snapshot*.

    Checks performed
    ~~~~~~~~~~~~~~~~
    1. Each ``SemanticEntity`` — ``root_table`` and every ``default_tables``
       entry must exist in the catalog.
    2. Each ``RelationshipEdge`` with ``approved_for_planner=True`` —
       ``source_table`` and ``target_table`` must exist; every join key's
       source/target column must exist in the respective table.
    3. Each ``MetricDefinition`` with ``table`` set — table must exist in
       catalog; if ``column`` is also set (and not ``"*"``), column must
       exist in that table.
    4. Each ``FlexfieldDefinition`` — ``table`` must exist; ``segment_column``
       must exist in that table.
    5. Glossary cross-reference — every ``canonical`` value that looks like an
       entity ID (no ``":"`` separator) must resolve to a known entity.

    Returns a (possibly empty) list of :class:`RegistryIssue` objects.
    """
    issues: list[RegistryIssue] = []

    # -----------------------------------------------------------------------
    # 1. SemanticEntity table existence
    # -----------------------------------------------------------------------
    for entity in foundation_registry.get_all_entities():
        eid = entity.entity_id
        for tbl in [entity.root_table, *entity.default_tables]:
            if not _table_exists(tbl, snapshot):
                issues.append(RegistryIssue(
                    entity_id=eid,
                    location="foundation:entity.default_tables",
                    detail=f"table '{tbl}' not found in catalog",
                ))

    # -----------------------------------------------------------------------
    # 2. RelationshipEdge column existence (approved edges only)
    # -----------------------------------------------------------------------
    for edge in foundation_registry.get_all_relationships():
        if not edge.approved_for_planner:
            continue
        eid = f"{edge.source_entity}→{edge.target_entity}"
        for side, tbl in (("source", edge.source_table), ("target", edge.target_table)):
            if not _table_exists(tbl, snapshot):
                issues.append(RegistryIssue(
                    entity_id=eid,
                    location=f"foundation:edge:{edge.edge_id}",
                    detail=f"{side} table '{tbl}' not found in catalog",
                ))
        for jk_idx, jk in enumerate(edge.join_keys):
            loc = f"foundation:edge:{edge.edge_id}.join_keys[{jk_idx}]"
            if not _skip_col(jk.source_column) and not _column_in_table(
                jk.source_column, edge.source_table, snapshot
            ):
                issues.append(RegistryIssue(
                    entity_id=eid,
                    location=loc,
                    detail=(
                        f"source column '{jk.source_column}' not found "
                        f"in table '{edge.source_table}'"
                    ),
                ))
            if not _skip_col(jk.target_column) and not _column_in_table(
                jk.target_column, edge.target_table, snapshot
            ):
                issues.append(RegistryIssue(
                    entity_id=eid,
                    location=loc,
                    detail=(
                        f"target column '{jk.target_column}' not found "
                        f"in table '{edge.target_table}'"
                    ),
                ))

    # -----------------------------------------------------------------------
    # 3. MetricDefinition table + column existence
    # -----------------------------------------------------------------------
    for metric in foundation_registry.get_all_metrics():
        if not metric.table:
            continue
        if not _table_exists(metric.table, snapshot):
            issues.append(RegistryIssue(
                entity_id=metric.metric_id,
                location="foundation:metric.table",
                detail=f"table '{metric.table}' not found in catalog",
            ))
        elif metric.column and not _skip_col(metric.column):
            if not _column_in_table(metric.column, metric.table, snapshot):
                issues.append(RegistryIssue(
                    entity_id=metric.metric_id,
                    location="foundation:metric.column",
                    detail=(
                        f"column '{metric.column}' not found in table '{metric.table}'"
                    ),
                ))

    # -----------------------------------------------------------------------
    # 4. FlexfieldDefinition table + segment_column existence
    # -----------------------------------------------------------------------
    for ff in foundation_registry.get_all_flexfields():
        if not _table_exists(ff.table, snapshot):
            issues.append(RegistryIssue(
                entity_id=ff.flexfield_id,
                location="foundation:flexfield.table",
                detail=f"table '{ff.table}' not found in catalog",
            ))
        elif not _skip_col(ff.segment_column) and not _column_in_table(
            ff.segment_column, ff.table, snapshot
        ):
            issues.append(RegistryIssue(
                entity_id=ff.flexfield_id,
                location="foundation:flexfield.segment_column",
                detail=(
                    f"column '{ff.segment_column}' not found in table '{ff.table}'"
                ),
            ))

    # -----------------------------------------------------------------------
    # 5. Glossary → entity cross-reference
    # -----------------------------------------------------------------------
    known_entity_ids = {e.entity_id for e in foundation_registry.get_all_entities()}
    for entry in foundation_registry.get_all_glossary_entries():
        canonical = entry.canonical
        if ":" in canonical:
            # "filter:{code}" or "metric:{id}" — not an entity_id, skip
            continue
        if canonical not in known_entity_ids:
            issues.append(RegistryIssue(
                entity_id=canonical,
                location=f"foundation:glossary.canonical (term='{entry.raw_term}')",
                detail=f"canonical entity_id '{canonical}' not found in entities.jsonl",
            ))

    return issues
