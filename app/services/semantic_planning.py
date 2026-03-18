"""Metadata-driven semantic normalization for planner outputs."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

from app.domain.catalog_models import CatalogSnapshot
from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    ComputedMeasureSpec,
    FilterOp,
    FilterSpec,
    JoinCondition,
    JoinSpec,
    JoinType,
    QueryPlan,
)
from app.domain.semantic_models import (
    BusinessEntitySemantic,
    CanonicalJoinPath,
    SemanticRegistry,
)
from app.core.logging import get_logger
from app.utils.turkish import casefold_tr

logger = get_logger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "semantic_registry.json"

@lru_cache(maxsize=1)
def _load_registry() -> SemanticRegistry:
    """Load and cache the semantic registry from the default JSON metadata file."""
    try:
        payload = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Semantic registry file not found: %s", _REGISTRY_PATH)
        return SemanticRegistry()
    except Exception as exc:
        logger.warning("Semantic registry load failed (%s): %s", _REGISTRY_PATH, exc)
        return SemanticRegistry()

    try:
        return SemanticRegistry.model_validate(payload)
    except Exception as exc:
        logger.warning("Semantic registry validation failed (%s): %s", _REGISTRY_PATH, exc)
        return SemanticRegistry()


def _match_entity(
    user_message: str,
    plan: QueryPlan,
    registry: SemanticRegistry,
) -> BusinessEntitySemantic | None:
    """Return the first registry entity that matches by table membership or keyword."""
    folded = casefold_tr(user_message)
    for entity in registry.entities:
        entity_tables = {entity.root_table, *entity.child_tables}
        if any(t in entity_tables for t in plan.all_tables):
            return entity
        if any(k in folded for k in entity.keywords):
            return entity
    return None


def _infer_entity_intent(user_message: str, entity: BusinessEntitySemantic) -> str:
    """Evaluate ordered intent_rules from the registry and return the first match."""
    folded = casefold_tr(user_message)
    for rule in entity.intent_rules:
        all_match = all(k in folded for k in rule.all_of) if rule.all_of else True
        any_match = any(k in folded for k in rule.any_of) if rule.any_of else True
        if all_match and any_match:
            return rule.intent
    return entity.default_intent


def _apply_intent_defaults(
    entity: BusinessEntitySemantic,
    semantic_intent: str,
    updates: dict[str, object],
    plan: QueryPlan | None = None,
) -> None:
    """Apply canonical plan overrides defined in the registry for a known intent."""
    intent_def = entity.intent_defaults.get(semantic_intent)
    if intent_def is None:
        return

    if intent_def.stable:
        updates["needs_clarification"] = False
        updates["clarification_message"] = None

    if intent_def.group_by:
        updates["group_by"] = intent_def.group_by

    if intent_def.aggregations:
        updates["aggregations"] = [
            AggregationSpec(
                function=AggregateFn(agg.function),
                column=agg.column,
                table=agg.table,
                alias=agg.alias,
            )
            for agg in intent_def.aggregations
        ]
        # Clear LLM-generated select columns. When the registry supplies
        # aggregation structure, SELECT is derived from group_by + aggs;
        # keeping LLM columns would cause aggregate_select_mismatch errors.
        updates["select_columns"] = []

    if intent_def.filters:
        updates["filters"] = [
            FilterSpec(
                column=f.column,
                table=f.table,
                op=FilterOp(f.op),
                value=f.value,
            )
            for f in intent_def.filters
        ]

    if intent_def.computed_measures:
        updates["computed_measures"] = [
            ComputedMeasureSpec(
                name=cm.name,
                expression_ref=cm.expression_ref,
                alias=cm.alias,
                table=cm.table,
            )
            for cm in intent_def.computed_measures
        ]

    # Apply default select_columns only for listing (non-aggregation) intents.
    # When aggregations are defined the SELECT shape is determined by group_by
    # + aggregations — override would break the GROUP BY contract.
    has_aggs = bool(intent_def.aggregations) or bool(updates.get("aggregations"))
    if intent_def.select_columns and not has_aggs:
        # Only fill in when the plan has nothing selected yet.
        plan_select = plan.select_columns if plan is not None else []
        current_select = updates.get("select_columns", plan_select)
        if not current_select:
            updates["select_columns"] = list(intent_def.select_columns)


def _joins_from_path(path: CanonicalJoinPath | None) -> list[JoinSpec]:
    if path is None:
        return []
    joins: list[JoinSpec] = []
    for s in path.steps:
        joins.append(
            JoinSpec(
                left_table=s.left_table,
                right_table=s.right_table,
                join_type=JoinType.INNER,
                on=[
                    JoinCondition(
                        left_table=s.left_table,
                        left_column=s.left_column,
                        right_table=s.right_table,
                        right_column=s.right_column,
                    ),
                ],
            ),
        )
    return joins


def apply_semantic_normalization(
    plan: QueryPlan,
    user_message: str,
    _context: CatalogSnapshot,
    *,
    registry: SemanticRegistry | None = None,
) -> QueryPlan:
    """Canonicalize planner output against the semantic registry.

    This does not rely on prompt hacks.  It enforces root-entity anchoring,
    canonical join-path selection, and intent-specific plan-shape overrides
    for any entity defined in the registry.

    Parameters
    ----------
    registry:
        Override the default (cached) registry.  Pass a custom
        ``SemanticRegistry`` to test or hot-swap semantic rules without
        touching the module-level LRU cache.
    """
    reg = registry if registry is not None else _load_registry()

    entity = _match_entity(user_message, plan, reg)
    if entity is None:
        return plan

    semantic_intent = _infer_entity_intent(user_message, entity)
    join_path_id = reg.intent_join_paths.get(semantic_intent)
    path = entity.get_join_path(join_path_id) if join_path_id else None

    updates: dict[str, object] = {
        "root_entity": entity.entity_id,
        "semantic_intent": semantic_intent,
    }

    # Apply registry intent defaults. For clarification plans, only apply stable
    # defaults (semantic rescue); non-stable defaults must not override an
    # intentional clarification produced by the planner.
    intent_def = entity.intent_defaults.get(semantic_intent)
    if intent_def is not None and (not plan.needs_clarification or intent_def.stable):
        _apply_intent_defaults(entity, semantic_intent, updates, plan)

    # When registry imposes aggregations, discard stale LLM select_columns:
    # SELECT shape is fully determined by group_by + aggregations.
    if updates.get("aggregations") and plan.select_columns:
        updates["select_columns"] = []
    if plan.aggregations and updates.get("select_columns"):
        updates.pop("select_columns")

    # Anchor base table to semantic root.
    if plan.table != entity.root_table:
        updates["table"] = entity.root_table

    if join_path_id:
        updates["join_path_id"] = join_path_id
        updates["joins"] = _joins_from_path(path)

    # Observability fields derived from the *final* canonical plan shape.
    final_group_by: list[str] = list(updates.get("group_by", plan.group_by))  # type: ignore[arg-type]
    final_aggs: list[AggregationSpec] = list(updates.get("aggregations", plan.aggregations))  # type: ignore[arg-type]
    if final_group_by:
        updates["dimensions"] = final_group_by
    if final_aggs:
        updates["measures"] = [a.alias or f"{a.function.value}_{a.column}" for a in final_aggs]

    return plan.model_copy(update=updates)


# ---------------------------------------------------------------------------
# Phase 5: Registry boundary validation
# ---------------------------------------------------------------------------

def validate_registry_against_catalog(
    snapshot: CatalogSnapshot,
    *,
    registry: SemanticRegistry | None = None,
) -> list[str]:
    """Validate that every table/column reference in the registry exists in *snapshot*.

    Returns a list of human-readable error strings.  An empty list means
    the registry is fully consistent with the active catalog.

    Checks performed
    ----------------
    * Every ``root_table`` and ``child_table`` referenced by a registry entity
      must resolve to a real table in the catalog snapshot.
    * Every column referenced in ``intent_defaults`` (filters, aggregations,
      group_by, select_columns, computed_measures) must exist in its
      respective table.
    """
    reg = registry if registry is not None else _load_registry()
    catalog_table_names = {t.name.upper() for t in snapshot.tables}
    errors: list[str] = []

    def _check_table(tbl: str, context: str) -> bool:
        if tbl.upper() not in catalog_table_names:
            errors.append(f"{context}: table '{tbl}' not found in catalog")
            return False
        return True

    def _check_column(tbl: str, col: str, context: str) -> None:
        if not col:
            return
        real_tbl = snapshot.get_table(tbl)
        if real_tbl is None:
            return  # table error already recorded
        if not real_tbl.has_column(col):
            errors.append(f"{context}: column '{col}' not found in table '{tbl}'")

    for entity in reg.entities:
        ctx = f"entity '{entity.entity_id}'"
        root_ok = _check_table(entity.root_table, f"{ctx}.root_table")
        for child in entity.child_tables:
            _check_table(child, f"{ctx}.child_tables")

        for intent_name, intent_def in entity.intent_defaults.items():
            ictx = f"{ctx}.intent_defaults[{intent_name!r}]"
            tbl_for_intent = entity.root_table if root_ok else None

            for f in intent_def.filters or []:
                ftbl = f.table or tbl_for_intent or ""
                if ftbl:
                    _check_column(ftbl, f.column, f"{ictx}.filters")

            for agg in intent_def.aggregations or []:
                atbl = agg.table or tbl_for_intent or ""
                if atbl and agg.column:
                    _check_column(atbl, agg.column, f"{ictx}.aggregations")

            for col in intent_def.select_columns or []:
                if tbl_for_intent:
                    _check_column(tbl_for_intent, col, f"{ictx}.select_columns")

            for cm in intent_def.computed_measures or []:
                cmtbl = cm.table or tbl_for_intent or ""
                if cmtbl:
                    _check_column(cmtbl, cm.name, f"{ictx}.computed_measures")

    if errors:
        logger.warning(
            "[registry-validation] %d issue(s) found against catalog (%d tables):\n  %s",
            len(errors),
            len(snapshot.tables),
            "\n  ".join(errors),
        )
    else:
        logger.info(
            "[registry-validation] OK — registry consistent with catalog (%d tables)",
            len(snapshot.tables),
        )

    return errors
