"""Query Plan Repair Engine.

Deterministic post-parse repair pass for QueryPlan.

Scope is intentionally narrow:
1) syntax-level normalization
2) semantic-registry-backed enforcement
3) limited clarification policy
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.core.logging import get_logger
from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    ComputedMeasureSpec,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
)
from app.domain.semantic_models import BusinessEntitySemantic, IntentDefaults, SemanticRegistry
from app.services.semantic_planning import _joins_from_path, _load_registry

logger = get_logger(__name__)


@dataclass
class RepairAction:
    """One atomic repair change applied to a plan field."""

    repair_type: str
    description: str
    field_path: str
    original_value: Any
    repaired_value: Any


@dataclass
class RepairResult:
    """Aggregated audit report from one QueryPlanRepairEngine.repair call."""

    repair_applied: bool = False
    actions: list[RepairAction] = field(default_factory=list)

    @property
    def repaired_fields_count(self) -> int:
        return len(self.actions)

    def record(self, action: RepairAction) -> None:
        self.actions.append(action)
        self.repair_applied = True


_QUALIFIED_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)$")
_EXPRESSION_RE = re.compile(r"[(\s+\-*/]")

_RELATIVE_DATE_PATTERNS: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"^current_date\s*[-]\s*(\d+)$", re.I), lambda m: (date.today() - timedelta(days=int(m.group(1)))).isoformat()),
    (re.compile(r"^current_date\s*[+]\s*(\d+)$", re.I), lambda m: (date.today() + timedelta(days=int(m.group(1)))).isoformat()),
    (re.compile(r"^sysdate\s*[-]\s*(\d+)$", re.I), lambda m: (date.today() - timedelta(days=int(m.group(1)))).isoformat()),
    (re.compile(r"^today$", re.I), lambda _: date.today().isoformat()),
    (re.compile(r"^current_date$", re.I), lambda _: date.today().isoformat()),
    (re.compile(r"^sysdate$", re.I), lambda _: date.today().isoformat()),
    (re.compile(r"^trunc\(sysdate\)$", re.I), lambda _: date.today().isoformat()),
]


def _split_qualifier(name: str) -> tuple[str, str | None]:
    m = _QUALIFIED_RE.match(name.strip())
    if m:
        return m.group(2), m.group(1).upper()
    return name, None


def _is_expression(name: str) -> bool:
    return bool(_EXPRESSION_RE.search(name))


def _normalize_date_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", raw)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    for pat, resolver in _RELATIVE_DATE_PATTERNS:
        mm = pat.match(raw)
        if mm:
            try:
                return resolver(mm)
            except Exception:
                return value
    return value


def _entity_table_set(entity: BusinessEntitySemantic) -> set[str]:
    return {entity.root_table.upper(), *{t.upper() for t in entity.child_tables}}


def _resolve_entity(plan: QueryPlan, registry: SemanticRegistry) -> BusinessEntitySemantic | None:
    if plan.root_entity:
        by_id = registry.get_entity(plan.root_entity)
        if by_id is not None:
            return by_id

    if plan.table:
        table_u = plan.table.upper()
        matches = [e for e in registry.entities if table_u in _entity_table_set(e)]
        if len(matches) == 1:
            return matches[0]

    if plan.candidate_tables:
        cand_u = {t.upper() for t in plan.candidate_tables}
        matches = [e for e in registry.entities if _entity_table_set(e) & cand_u]
        if len(matches) == 1:
            return matches[0]

    if plan.semantic_intent:
        intent = plan.semantic_intent
        matches = []
        for e in registry.entities:
            if intent in e.intent_defaults:
                matches.append(e)
                continue
            path_id = registry.intent_join_paths.get(intent)
            if path_id and e.get_join_path(path_id) is not None:
                matches.append(e)
        uniq = {m.entity_id: m for m in matches}
        if len(uniq) == 1:
            return next(iter(uniq.values()))

    return None


def _resolve_semantic_intent(
    plan: QueryPlan,
    entity: BusinessEntitySemantic,
    registry: SemanticRegistry,
) -> str | None:
    if plan.semantic_intent:
        path_id = registry.intent_join_paths.get(plan.semantic_intent)
        if plan.semantic_intent in entity.intent_defaults:
            return plan.semantic_intent
        if path_id and entity.get_join_path(path_id) is not None:
            return plan.semantic_intent

    if plan.join_path_id:
        candidates = [
            intent
            for intent, path_id in registry.intent_join_paths.items()
            if path_id == plan.join_path_id
        ]
        supported = [
            intent
            for intent in candidates
            if (intent in entity.intent_defaults) or (entity.get_join_path(plan.join_path_id) is not None)
        ]
        if len(supported) == 1:
            return supported[0]

    return None


def _defaults_table_owner(defaults: IntentDefaults) -> dict[str, str]:
    owner: dict[str, str] = {}
    for agg in defaults.aggregations:
        if agg.table:
            owner[agg.column.upper()] = agg.table
    for flt in defaults.filters:
        if flt.table:
            owner[flt.column.upper()] = flt.table
    return owner


class QueryPlanRepairEngine:
    """Deterministic post-parse repair pass for QueryPlan."""

    def repair(
        self,
        plan: QueryPlan,
        user_message: str = "",
    ) -> tuple[QueryPlan, RepairResult]:
        del user_message

        result = RepairResult()

        plan = self._normalize_syntax(plan, result)
        plan = self._resolve_filter_aliases(plan, result)
        plan = self._enforce_semantics(plan, result)
        plan = self._apply_clarification_policy(plan, result)

        if result.repair_applied:
            logger.info(
                "QueryPlanRepairEngine applied %d repair(s): %s",
                result.repaired_fields_count,
                ", ".join(a.repair_type for a in result.actions),
            )
        return plan, result

    def _resolve_filter_aliases(self, plan: QueryPlan, result: RepairResult) -> QueryPlan:
        """Pass J – remap aliased filter column names to canonical names via registry."""
        registry = _load_registry()
        global_aliases: dict[str, str] = getattr(registry.column_aliases, "global_aliases", {})
        if not global_aliases:
            return plan

        new_filters = list(plan.filters)
        changed = False
        for i, spec in enumerate(plan.filters):
            col_key = spec.column.lower()
            canonical = global_aliases.get(col_key)
            if canonical and canonical != spec.column:
                result.record(
                    RepairAction(
                        repair_type="J_filter_column_repair",
                        description=f"Resolved filter column alias: {spec.column} → {canonical}",
                        field_path=f"filters[{i}].column",
                        original_value=spec.column,
                        repaired_value=canonical,
                    )
                )
                new_filters[i] = spec.model_copy(update={"column": canonical})
                changed = True

        if changed:
            return plan.model_copy(update={"filters": new_filters})
        return plan

    def _normalize_syntax(self, plan: QueryPlan, result: RepairResult) -> QueryPlan:
        mutations: dict[str, Any] = {}

        new_select: list[str] = []
        for i, col in enumerate(plan.select_columns):
            if _is_expression(col):
                new_select.append(col)
                continue
            bare, tbl = _split_qualifier(col)
            if tbl is not None:
                result.record(
                    RepairAction(
                        repair_type="syntax_normalize",
                        description="Stripped qualifier from select column",
                        field_path=f"select_columns[{i}]",
                        original_value=col,
                        repaired_value=bare,
                    )
                )
            new_select.append(bare)
        if new_select != list(plan.select_columns):
            mutations["select_columns"] = new_select

        new_group: list[str] = []
        for i, col in enumerate(plan.group_by):
            if _is_expression(col):
                new_group.append(col)
                continue
            bare, tbl = _split_qualifier(col)
            if tbl is not None:
                result.record(
                    RepairAction(
                        repair_type="syntax_normalize",
                        description="Stripped qualifier from group_by",
                        field_path=f"group_by[{i}]",
                        original_value=col,
                        repaired_value=bare,
                    )
                )
            new_group.append(bare)
        if new_group != list(plan.group_by):
            mutations["group_by"] = new_group

        new_order: list[OrderSpec] = []
        order_changed = False
        for i, spec in enumerate(plan.order_by):
            if _is_expression(spec.column):
                new_order.append(spec)
                continue
            bare, tbl = _split_qualifier(spec.column)
            if tbl is not None:
                upd = spec.model_copy(update={"column": bare, "table": spec.table or tbl})
                new_order.append(upd)
                order_changed = True
                result.record(
                    RepairAction(
                        repair_type="syntax_normalize",
                        description="Stripped qualifier from order_by",
                        field_path=f"order_by[{i}]",
                        original_value=spec.column,
                        repaired_value=bare,
                    )
                )
            else:
                new_order.append(spec)
        if order_changed:
            mutations["order_by"] = new_order

        new_filters: list[FilterSpec] = []
        filters_changed = False
        for i, spec in enumerate(plan.filters):
            col = spec.column
            op = spec.op
            val = spec.value
            tbl_update: str | None = spec.table

            if not _is_expression(col):
                bare, tbl = _split_qualifier(col)
                if tbl is not None:
                    col = bare
                    tbl_update = spec.table or tbl
                    filters_changed = True
                    result.record(
                        RepairAction(
                            repair_type="syntax_normalize",
                            description="Stripped qualifier from filter column",
                            field_path=f"filters[{i}].column",
                            original_value=spec.column,
                            repaired_value=col,
                        )
                    )

            if val is None and op == FilterOp.EQ:
                op = FilterOp.IS_NULL
            elif val is None and op == FilterOp.NEQ:
                op = FilterOp.IS_NOT_NULL

            if isinstance(val, list):
                val2 = [_normalize_date_value(x) for x in val]
            else:
                val2 = _normalize_date_value(val)

            if op == FilterOp.LIKE and isinstance(val2, str):
                vv = val2.strip()
                if vv and "%" not in vv and "_" not in vv:
                    val2 = f"%{vv}%"

            if col != spec.column or op != spec.op or val2 != spec.value or tbl_update != spec.table:
                filters_changed = True
                result.record(
                    RepairAction(
                        repair_type="syntax_normalize",
                        description="Canonicalized filter operator/value",
                        field_path=f"filters[{i}]",
                        original_value={"column": spec.column, "op": spec.op.value, "value": spec.value, "table": spec.table},
                        repaired_value={"column": col, "op": op.value, "value": val2, "table": tbl_update},
                    )
                )
                new_filters.append(spec.model_copy(update={"column": col, "op": op, "value": val2, "table": tbl_update}))
            else:
                new_filters.append(spec)

        if filters_changed:
            mutations["filters"] = new_filters

        new_aggs: list[AggregationSpec] = []
        agg_changed = False
        for i, spec in enumerate(plan.aggregations):
            if _is_expression(spec.column):
                new_aggs.append(spec)
                continue
            bare, tbl = _split_qualifier(spec.column)
            if tbl is not None:
                upd = spec.model_copy(update={"column": bare, "table": spec.table or tbl})
                new_aggs.append(upd)
                agg_changed = True
                result.record(
                    RepairAction(
                        repair_type="syntax_normalize",
                        description="Stripped qualifier from aggregation column",
                        field_path=f"aggregations[{i}]",
                        original_value=spec.column,
                        repaired_value=bare,
                    )
                )
            else:
                new_aggs.append(spec)
        if agg_changed:
            mutations["aggregations"] = new_aggs

        if mutations:
            return plan.model_copy(update=mutations)
        return plan

    def _enforce_semantics(self, plan: QueryPlan, result: RepairResult) -> QueryPlan:
        registry = _load_registry()
        entity = _resolve_entity(plan, registry)
        if entity is None:
            return plan

        updates: dict[str, Any] = {}
        updates["root_entity"] = entity.entity_id

        entity_tables = _entity_table_set(entity)
        if plan.table is None:
            updates["table"] = entity.root_table
            result.record(
                RepairAction(
                    repair_type="semantic_enforce",
                    description="Injected root table from semantic registry",
                    field_path="table",
                    original_value=None,
                    repaired_value=entity.root_table,
                )
            )
        elif plan.table.upper() in entity_tables and plan.table.upper() != entity.root_table.upper():
            updates["table"] = entity.root_table
            result.record(
                RepairAction(
                    repair_type="semantic_enforce",
                    description="Anchored entity child table to semantic root table",
                    field_path="table",
                    original_value=plan.table,
                    repaired_value=entity.root_table,
                )
            )

        semantic_intent = _resolve_semantic_intent(plan, entity, registry)
        if semantic_intent:
            updates["semantic_intent"] = semantic_intent

            join_path_id = registry.intent_join_paths.get(semantic_intent)
            path = entity.get_join_path(join_path_id) if join_path_id else None
            if join_path_id and path is not None:
                joins = _joins_from_path(path)
                if plan.join_path_id != join_path_id or list(plan.joins) != joins:
                    updates["join_path_id"] = join_path_id
                    updates["joins"] = joins
                    updates["table"] = entity.root_table
                    result.record(
                        RepairAction(
                            repair_type="semantic_enforce",
                            description="Applied canonical join path from semantic registry",
                            field_path="joins",
                            original_value=[(j.left_table, j.right_table) for j in plan.joins],
                            repaired_value=[(j.left_table, j.right_table) for j in joins],
                        )
                    )
                    # Pass I: separately flag when joins were entirely absent before repair
                    if not list(plan.joins):
                        result.record(
                            RepairAction(
                                repair_type="I_missing_join_path_fix",
                                description="Injected missing join path required by semantic intent",
                                field_path="joins",
                                original_value=[],
                                repaired_value=[(j.left_table, j.right_table) for j in joins],
                            )
                        )

            defaults = entity.intent_defaults.get(semantic_intent)
            if defaults is not None:
                if defaults.group_by and list(plan.group_by) != list(defaults.group_by):
                    updates["group_by"] = list(defaults.group_by)
                    result.record(
                        RepairAction(
                            repair_type="semantic_enforce",
                            description="Applied registry group_by defaults",
                            field_path="group_by",
                            original_value=list(plan.group_by),
                            repaired_value=list(defaults.group_by),
                        )
                    )

                if defaults.aggregations:
                    aggs = [
                        AggregationSpec(
                            function=AggregateFn(a.function),
                            column=a.column,
                            table=a.table,
                            alias=a.alias,
                        )
                        for a in defaults.aggregations
                    ]
                    if list(plan.aggregations) != aggs:
                        updates["aggregations"] = aggs
                        result.record(
                            RepairAction(
                                repair_type="semantic_enforce",
                                description="Applied registry aggregation defaults",
                                field_path="aggregations",
                                original_value=[f"{a.function.value}({a.column})" for a in plan.aggregations],
                                repaired_value=[f"{a.function.value}({a.column})" for a in aggs],
                            )
                        )
                    if plan.select_columns:
                        updates["select_columns"] = []

                if defaults.filters:
                    filters = [
                        FilterSpec(column=f.column, table=f.table, op=FilterOp(f.op), value=f.value)
                        for f in defaults.filters
                    ]
                    if list(plan.filters) != filters:
                        updates["filters"] = filters
                        result.record(
                            RepairAction(
                                repair_type="semantic_enforce",
                                description="Applied registry filter defaults",
                                field_path="filters",
                                original_value=[{"column": f.column, "op": f.op.value, "value": f.value} for f in plan.filters],
                                repaired_value=[{"column": f.column, "op": f.op.value, "value": f.value} for f in filters],
                            )
                        )

                if defaults.computed_measures:
                    cms = [
                        ComputedMeasureSpec(
                            name=cm.name,
                            expression_ref=cm.expression_ref,
                            alias=cm.alias,
                            table=cm.table,
                        )
                        for cm in defaults.computed_measures
                    ]
                    if list(plan.computed_measures) != cms:
                        updates["computed_measures"] = cms

                owner = _defaults_table_owner(defaults)
                if owner:
                    f_changed = False
                    new_filters = []
                    for f in updates.get("filters", plan.filters):
                        t = owner.get(f.column.upper())
                        if f.table is None and t:
                            f_changed = True
                            new_filters.append(f.model_copy(update={"table": t}))
                        else:
                            new_filters.append(f)
                    if f_changed:
                        updates["filters"] = new_filters

                    a_changed = False
                    new_aggs = []
                    for a in updates.get("aggregations", plan.aggregations):
                        t = owner.get(a.column.upper())
                        if a.table is None and t:
                            a_changed = True
                            new_aggs.append(a.model_copy(update={"table": t}))
                        else:
                            new_aggs.append(a)
                    if a_changed:
                        updates["aggregations"] = new_aggs

            # Pass H: anchor plan.table to the intent's sole aggregation target table
            # when the intent has NO join path (direct single-table aggregation) and
            # all aggregations consistently reference one non-root child table.
            current_table = updates.get("table", plan.table)
            defaults_h = entity.intent_defaults.get(semantic_intent) if semantic_intent else None
            if defaults_h and defaults_h.aggregations and current_table and not join_path_id:
                agg_tables = {a.table for a in defaults_h.aggregations if a.table}
                if len(agg_tables) == 1:
                    agg_anchor = next(iter(agg_tables))
                    if (
                        agg_anchor.upper() != entity.root_table.upper()
                        and current_table.upper() == entity.root_table.upper()
                    ):
                        updates["table"] = agg_anchor
                        result.record(
                            RepairAction(
                                repair_type="H_wrong_root_table_fix",
                                description=(
                                    f"Fixed root table for intent '{semantic_intent}': "
                                    f"{current_table} → {agg_anchor}"
                                ),
                                field_path="table",
                                original_value=current_table,
                                repaired_value=agg_anchor,
                            )
                        )

        if updates:
            return plan.model_copy(update=updates)
        return plan

    def _apply_clarification_policy(self, plan: QueryPlan, result: RepairResult) -> QueryPlan:
        if not plan.needs_clarification:
            return plan

        registry = _load_registry()
        entity = _resolve_entity(plan, registry)
        if entity is None:
            return plan

        semantic_intent = _resolve_semantic_intent(plan, entity, registry)
        if semantic_intent is None:
            return plan

        defaults = entity.intent_defaults.get(semantic_intent)
        if defaults is None or not defaults.stable:
            return plan

        result.record(
            RepairAction(
                repair_type="clarification_policy",
                description="Suppressed clarification using stable registry intent",
                field_path="needs_clarification",
                original_value=True,
                repaired_value=False,
            )
        )
        updates: dict[str, Any] = {
            "needs_clarification": False,
            "clarification_message": None,
            "semantic_intent": semantic_intent,
            "root_entity": entity.entity_id,
        }
        if plan.table is None:
            updates["table"] = entity.root_table
        return plan.model_copy(update=updates)
