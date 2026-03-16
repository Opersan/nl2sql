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

from app.domain.catalog_models import CatalogSnapshot
from app.domain.semantic_models import BusinessEntitySemantic, SemanticRegistry


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
) -> None:
    """Raise :exc:`RegistryValidationError` if *registry* has catalog inconsistencies.

    This is the strict-mode entry point.  For fail-open startup logging use
    :func:`validate_registry_against_catalog` directly.
    """
    issues = validate_registry_against_catalog(registry, snapshot)
    if issues:
        raise RegistryValidationError(issues)
