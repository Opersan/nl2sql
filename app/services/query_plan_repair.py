"""Query Plan Repair Engine.

This layer operates on a fully-parsed ``QueryPlan`` (post-Pydantic validation)
*between* the Plan Normalizer and Semantic Normalization stages in the planner
pipeline.

Repair types
============
C — Qualified-column stripping: ``TABLE.COLUMN`` → bare ``COLUMN`` in plain
    string lists (select_columns, group_by); moves qualifier into the dedicated
    ``table`` field for FilterSpec / AggregationSpec / OrderSpec.
E — Anchor-table repair: if ``plan.table`` is a *child* table in the semantic
    registry, redirect to the canonical ``root_table`` so that the downstream
    semantic normalizer can build correct join paths.
F — Clarification rescue: suppress a ``needs_clarification=True`` plan when the
    registry has a *stable* intent that matches the user message.
D — Group-by auto fill: when aggregations exist and ``group_by`` is empty, fill
    ``group_by`` from the non-aggregate ``select_columns``.
G — Degenerate-plan guard: if the plan has no ``table`` but registry keywords
    match the user message, inject the matching entity's ``root_table``.

All repairs are *additive* — no existing behaviour is removed.  Each change is
recorded in the ``RepairResult`` audit trail returned alongside the repaired plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.domain.query_plan import (
    AggregationSpec,
    FilterSpec,
    OrderSpec,
    QueryPlan,
)
from app.services.semantic_planning import _load_registry

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Audit-trail types
# ---------------------------------------------------------------------------

@dataclass
class RepairAction:
    """Records one atomic repair applied to a plan."""

    repair_type: str
    """One of: C_qualified_column, E_anchor_table, F_clarification_rescue,
    D_group_by_fill, G_degenerate_plan."""

    description: str
    """Human-readable description of what was changed and why."""

    field_path: str
    """Dot-path to the affected field, e.g. ``select_columns[2]``."""

    original_value: Any
    """Value before repair."""

    repaired_value: Any
    """Value after repair."""


@dataclass
class RepairResult:
    """Aggregated audit report from one :meth:`QueryPlanRepairEngine.repair` call."""

    repair_applied: bool = False
    actions: list[RepairAction] = field(default_factory=list)

    @property
    def repaired_fields_count(self) -> int:
        """Number of individual field changes recorded."""
        return len(self.actions)

    def record(self, action: RepairAction) -> None:
        """Append *action* and mark the result as having applied at least one repair."""
        self.actions.append(action)
        self.repair_applied = True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Matches "TABLE.COLUMN" patterns (both segments must be plain identifiers).
_QUALIFIED_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$')

# Heuristic: presence of these characters/patterns signals an expression.
_EXPRESSION_RE = re.compile(r'[(\s+\-*/]')


def _split_qualifier(name: str) -> tuple[str, str | None]:
    """Return ``(column, table_upper)`` if *name* is ``TABLE.COLUMN``, else ``(name, None)``."""
    m = _QUALIFIED_RE.match(name.strip())
    if m:
        return m.group(2), m.group(1).upper()
    return name, None


def _is_expression(name: str) -> bool:
    """Return True when *name* looks like a SQL expression rather than a column name."""
    return bool(_EXPRESSION_RE.search(name))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class QueryPlanRepairEngine:
    """Post-parse, pre-semantic repair pass applied to every ``QueryPlan``.

    Usage::

        engine = QueryPlanRepairEngine()
        repaired_plan, audit = engine.repair(raw_plan, user_message)
    """

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def repair(
        self,
        plan: QueryPlan,
        user_message: str = "",
    ) -> tuple[QueryPlan, RepairResult]:
        """Apply all repair passes and return ``(repaired_plan, audit)``.

        Passes run in dependency order so that later passes see already-
        corrected field values:

        1. C — strip qualified columns (must run before D / anchor checks
           use column names).
        2. E — anchor-table redirect (changes ``plan.table``).
        3. F — clarification rescue (may clear ``needs_clarification``).
        4. D — group-by auto fill (depends on aggregations & select_columns).
        5. G — degenerate-plan guard (last resort when table is still None).
        """
        result = RepairResult()

        plan = self._repair_qualified_columns(plan, result)
        plan = self._repair_anchor_table(plan, user_message, result)
        plan = self._repair_clarification_rescue(plan, user_message, result)
        plan = self._repair_group_by_fill(plan, result)
        plan = self._repair_degenerate_plan(plan, user_message, result)

        if result.repair_applied:
            logger.info(
                "QueryPlanRepairEngine: %d repair(s) applied — types=[%s]",
                result.repaired_fields_count,
                ", ".join(a.repair_type for a in result.actions),
            )
        return plan, result

    # ------------------------------------------------------------------
    # Pass C — qualified-column stripping
    # ------------------------------------------------------------------

    def _repair_qualified_columns(
        self, plan: QueryPlan, result: RepairResult
    ) -> QueryPlan:
        """Strip ``TABLE.COLUMN`` qualifiers from plain-string column lists.

        * ``select_columns`` / ``group_by``: bare column name kept, qualifier
          discarded (the SQL compiler re-qualifies using table metadata).
        * ``order_by``: column stripped, qualifier moved to ``OrderSpec.table``
          if not already set.
        * ``filters``: column stripped, qualifier moved to
          ``FilterSpec.table`` if not already set.
        * ``aggregations``: column stripped, qualifier moved to
          ``AggregationSpec.table`` if not already set.

        Expression-like values (containing ``(``, spaces, operators) are
        skipped unconditionally.
        """
        mutations: dict[str, Any] = {}

        # -- select_columns -----------------------------------------------
        new_select: list[str] = []
        for i, col in enumerate(plan.select_columns):
            if _is_expression(col):
                new_select.append(col)
                continue
            bare, tbl = _split_qualifier(col)
            if tbl is not None:
                result.record(RepairAction(
                    repair_type="C_qualified_column",
                    description="Stripped table qualifier from select_columns",
                    field_path=f"select_columns[{i}]",
                    original_value=col,
                    repaired_value=bare,
                ))
                new_select.append(bare)
            else:
                new_select.append(col)
        if new_select != list(plan.select_columns):
            mutations["select_columns"] = new_select

        # -- group_by -----------------------------------------------------
        new_group: list[str] = []
        for i, col in enumerate(plan.group_by):
            if _is_expression(col):
                new_group.append(col)
                continue
            bare, tbl = _split_qualifier(col)
            if tbl is not None:
                result.record(RepairAction(
                    repair_type="C_qualified_column",
                    description="Stripped table qualifier from group_by",
                    field_path=f"group_by[{i}]",
                    original_value=col,
                    repaired_value=bare,
                ))
                new_group.append(bare)
            else:
                new_group.append(col)
        if new_group != list(plan.group_by):
            mutations["group_by"] = new_group

        # -- order_by -----------------------------------------------------
        new_order: list[OrderSpec] = []
        changed_order = False
        for i, spec in enumerate(plan.order_by):
            if _is_expression(spec.column):
                new_order.append(spec)
                continue
            col, tbl = _split_qualifier(spec.column)
            if tbl is not None:
                result.record(RepairAction(
                    repair_type="C_qualified_column",
                    description="Stripped table qualifier from order_by column",
                    field_path=f"order_by[{i}].column",
                    original_value=spec.column,
                    repaired_value=col,
                ))
                new_order.append(spec.model_copy(update={
                    "column": col,
                    "table": spec.table or tbl,
                }))
                changed_order = True
            else:
                new_order.append(spec)
        if changed_order:
            mutations["order_by"] = new_order

        # -- filters ------------------------------------------------------
        new_filters: list[FilterSpec] = []
        changed_filters = False
        for i, spec in enumerate(plan.filters):
            if _is_expression(spec.column):
                new_filters.append(spec)
                continue
            col, tbl = _split_qualifier(spec.column)
            if tbl is not None and spec.table is None:
                result.record(RepairAction(
                    repair_type="C_qualified_column",
                    description="Stripped table qualifier from filter column",
                    field_path=f"filters[{i}].column",
                    original_value=spec.column,
                    repaired_value=col,
                ))
                new_filters.append(spec.model_copy(update={"column": col, "table": tbl}))
                changed_filters = True
            else:
                new_filters.append(spec)
        if changed_filters:
            mutations["filters"] = new_filters

        # -- aggregations -------------------------------------------------
        new_aggs: list[AggregationSpec] = []
        changed_aggs = False
        for i, spec in enumerate(plan.aggregations):
            if _is_expression(spec.column):
                new_aggs.append(spec)
                continue
            col, tbl = _split_qualifier(spec.column)
            if tbl is not None and spec.table is None:
                result.record(RepairAction(
                    repair_type="C_qualified_column",
                    description="Stripped table qualifier from aggregation column",
                    field_path=f"aggregations[{i}].column",
                    original_value=spec.column,
                    repaired_value=col,
                ))
                new_aggs.append(spec.model_copy(update={"column": col, "table": tbl}))
                changed_aggs = True
            else:
                new_aggs.append(spec)
        if changed_aggs:
            mutations["aggregations"] = new_aggs

        if mutations:
            return plan.model_copy(update=mutations)
        return plan

    # ------------------------------------------------------------------
    # Pass E — anchor-table repair
    # ------------------------------------------------------------------

    def _repair_anchor_table(
        self, plan: QueryPlan, user_message: str, result: RepairResult
    ) -> QueryPlan:
        """Redirect a child-table anchor to the canonical entity root table.

        When the LLM picks a child table (e.g. ``PO_LINES_ALL``) as the
        base table, the semantic normalizer cannot apply canonical join
        paths correctly.  This pass detects child-anchor cases via the
        registry and rewrites ``plan.table`` to ``root_table`` before
        semantic normalization runs.

        The pass is skipped for clarification plans (``needs_clarification=True``).
        """
        if not plan.table or plan.needs_clarification:
            return plan

        try:
            registry = _load_registry()
        except Exception:
            return plan

        table_upper = plan.table.upper()
        for entity in registry.entities:
            child_upper = {t.upper() for t in entity.child_tables}
            if table_upper in child_upper:
                new_table = entity.root_table
                result.record(RepairAction(
                    repair_type="E_anchor_table",
                    description=(
                        f"Redirected child-table anchor '{plan.table}' "
                        f"→ root_table '{new_table}' "
                        f"(entity '{entity.entity_id}')"
                    ),
                    field_path="table",
                    original_value=plan.table,
                    repaired_value=new_table,
                ))
                logger.info(
                    "RepairEngine[E]: '%s' → '%s' (entity=%s)",
                    plan.table, new_table, entity.entity_id,
                )
                return plan.model_copy(update={"table": new_table})

        return plan

    # ------------------------------------------------------------------
    # Pass F — clarification rescue
    # ------------------------------------------------------------------

    def _repair_clarification_rescue(
        self, plan: QueryPlan, user_message: str, result: RepairResult
    ) -> QueryPlan:
        """Suppress a spurious clarification when the registry has a stable intent.

        The LLM sometimes returns ``needs_clarification=True`` for queries that
        are straightforwardly answerable.  When the semantic registry has a
        *stable* intent matching both the user message keywords and the
        candidate/plan tables, we:

        1. Clear ``needs_clarification`` and ``clarification_message``.
        2. Inject ``semantic_intent`` so downstream semantic normalization
           applies the correct canonical plan shape.
        3. Set ``table`` to the entity's ``root_table`` if not already set.
        """
        if not plan.needs_clarification:
            return plan

        try:
            registry = _load_registry()
        except Exception:
            return plan

        candidate_upper = {(plan.table or "").upper()}
        for t in plan.candidate_tables:
            candidate_upper.add(t.upper())

        msg_lower = user_message.lower()

        for entity in registry.entities:
            entity_tables_upper = {entity.root_table.upper()} | {
                t.upper() for t in entity.child_tables
            }
            table_match = bool(candidate_upper & entity_tables_upper)
            keyword_match = any(kw.lower() in msg_lower for kw in entity.keywords)

            if not (table_match or keyword_match):
                continue

            # Find the first stable intent defined for this entity.
            for intent_name, intent_def in entity.intent_defaults.items():
                if intent_def.stable:
                    result.record(RepairAction(
                        repair_type="F_clarification_rescue",
                        description=(
                            f"Suppressed spurious clarification; "
                            f"injected stable intent '{intent_name}' "
                            f"for entity '{entity.entity_id}'"
                        ),
                        field_path="needs_clarification",
                        original_value=True,
                        repaired_value=False,
                    ))
                    logger.info(
                        "RepairEngine[F]: clarification rescued → intent=%s entity=%s",
                        intent_name, entity.entity_id,
                    )
                    return plan.model_copy(update={
                        "needs_clarification": False,
                        "clarification_message": None,
                        "semantic_intent": intent_name,
                        "table": plan.table or entity.root_table,
                    })

        return plan

    # ------------------------------------------------------------------
    # Pass D — group-by auto fill
    # ------------------------------------------------------------------

    def _repair_group_by_fill(
        self, plan: QueryPlan, result: RepairResult
    ) -> QueryPlan:
        """Auto-fill ``group_by`` from non-aggregate ``select_columns``.

        When the plan has aggregations but an empty ``group_by``, the SQL
        compiler produces invalid SQL (missing GROUP BY).  This pass fills
        ``group_by`` with the select columns that are *not* themselves
        aggregate column references, matching the standard SQL requirement.

        The pass is skipped when ``group_by`` is already populated or when
        there are no aggregations.
        """
        if not plan.aggregations or plan.group_by or not plan.select_columns:
            return plan

        agg_cols_upper = {a.column.upper() for a in plan.aggregations}
        fill_cols = [
            c for c in plan.select_columns
            if not _is_expression(c) and c.upper() not in agg_cols_upper
        ]
        if not fill_cols:
            return plan

        result.record(RepairAction(
            repair_type="D_group_by_fill",
            description=(
                f"Auto-filled group_by from select_columns: {fill_cols}"
            ),
            field_path="group_by",
            original_value=[],
            repaired_value=fill_cols,
        ))
        logger.info(
            "RepairEngine[D]: filled group_by=%s from select_columns", fill_cols
        )
        return plan.model_copy(update={"group_by": fill_cols})

    # ------------------------------------------------------------------
    # Pass G — degenerate-plan guard
    # ------------------------------------------------------------------

    def _repair_degenerate_plan(
        self, plan: QueryPlan, user_message: str, result: RepairResult
    ) -> QueryPlan:
        """Inject a root table when the plan has none but registry keywords match.

        A degenerate plan (``table=None``, no clarification) will fail the
        catalog lookup downstream.  When the semantic registry has an entity
        whose keywords appear in the user message, we inject ``root_table``
        so that the plan can proceed through semantic normalization.
        """
        if plan.table or plan.needs_clarification:
            return plan

        # Also try candidate_tables for an entity hit.
        cand_upper = {t.upper() for t in plan.candidate_tables}

        try:
            registry = _load_registry()
        except Exception:
            return plan

        msg_lower = user_message.lower()
        for entity in registry.entities:
            entity_tables_upper = {entity.root_table.upper()} | {
                t.upper() for t in entity.child_tables
            }
            keyword_match = any(kw.lower() in msg_lower for kw in entity.keywords)
            table_match = bool(cand_upper & entity_tables_upper)

            if keyword_match or table_match:
                new_table = entity.root_table
                result.record(RepairAction(
                    repair_type="G_degenerate_plan",
                    description=(
                        f"Injected table='{new_table}' for entity "
                        f"'{entity.entity_id}' (degenerate plan without table)"
                    ),
                    field_path="table",
                    original_value=None,
                    repaired_value=new_table,
                ))
                logger.info(
                    "RepairEngine[G]: injected table=%s for entity=%s",
                    new_table, entity.entity_id,
                )
                return plan.model_copy(update={"table": new_table})

        return plan
