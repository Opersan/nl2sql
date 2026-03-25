"""Metadata-driven semantic normalization for planner outputs."""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

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
from app.semantic.repository import build_runtime_semantic_registry, load_semantic_repository
from app.utils.turkish import casefold_tr

logger = get_logger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data" / "semantic_registry.json"
_SEMANTIC_DIR = Path(__file__).resolve().parents[2] / "data" / "semantic"
_TOKEN_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _norm(text: str) -> str:
    folded = casefold_tr(text or "")
    return (
        folded.replace("ı", "i")
        .replace("ş", "s")
        .replace("ç", "c")
        .replace("ğ", "g")
        .replace("ö", "o")
        .replace("ü", "u")
    )

@lru_cache(maxsize=1)
def _load_registry() -> SemanticRegistry:
    """Load and cache the planner registry projection from the canonical repository."""
    repository = load_semantic_repository(
        semantic_dir=_SEMANTIC_DIR,
        legacy_registry_path=_REGISTRY_PATH,
    )
    return build_runtime_semantic_registry(repository)


def _match_entity(
    user_message: str,
    plan: QueryPlan,
    registry: SemanticRegistry,
) -> BusinessEntitySemantic | None:
    """Return the strongest registry entity match by keywords, then table membership."""
    folded = _norm(" ".join(part for part in [user_message, plan.intent] if part))
    referenced_columns = {
        _norm(column)
        for column in [
            *plan.select_columns,
            *plan.group_by,
            *(flt.column for flt in plan.filters),
            *(agg.column for agg in plan.aggregations),
            *(order.column for order in plan.order_by),
        ]
        if column
    }
    best_match: BusinessEntitySemantic | None = None
    best_score: tuple[int, int, int, int] = (0, 0, 0, 0)

    for entity in registry.entities:
        entity_tables = {entity.root_table, *entity.child_tables}
        keyword_hits = sum(1 for keyword in entity.keywords if keyword and keyword in folded)
        entity_columns = {
            _norm(column)
            for column in [
                *entity.dimensions.values(),
                *entity.measures.values(),
                *entity.status_semantics.values(),
                *entity.time_semantics.values(),
            ]
            if column
        }

        column_hits = 0
        if referenced_columns and entity_columns:
            column_hits = len(referenced_columns & entity_columns)

        table_strength = 0
        if plan.table == entity.root_table:
            table_strength = 3
        elif entity.root_table in plan.all_tables:
            table_strength = 2
        elif any(table in entity_tables for table in plan.all_tables):
            table_strength = 1

        score = (1 if keyword_hits > 0 else 0, keyword_hits, column_hits, table_strength)
        if score > best_score:
            best_match = entity
            best_score = score

    return best_match


def _entity_tables(entity: BusinessEntitySemantic) -> set[str]:
    return {entity.root_table.upper(), *(table.upper() for table in entity.child_tables)}


def _tokenize(text: str) -> set[str]:
    folded = _norm(text or "")
    cleaned = _TOKEN_RE.sub(" ", folded)
    return {token for token in cleaned.split() if len(token) >= 2}


def _semantic_columns(entity: BusinessEntitySemantic) -> set[str]:
    return {
        _norm(column)
        for column in [
            *entity.dimensions.values(),
            *entity.measures.values(),
            *entity.status_semantics.values(),
            *entity.time_semantics.values(),
        ]
        if column
    }


def _context_tables(context: Any) -> list[Any]:
    return list(getattr(context, "tables", []) or [])


def _context_get_table(context: Any, table_name: str) -> Any | None:
    getter = getattr(context, "get_table", None)
    if callable(getter):
        return getter(table_name)
    return None


def _score_filter_ownership(
    plan: QueryPlan,
    entity: BusinessEntitySemantic,
    context: Any,
) -> tuple[int, int]:
    owned = 0
    conflicts = 0
    entity_tables = _entity_tables(entity)

    for flt in plan.filters:
        if flt.table:
            if flt.table.upper() in entity_tables:
                owned += 1
            else:
                conflicts += 1
            continue

        owner_candidates = []
        for table_name in entity_tables:
            table = _context_get_table(context, table_name)
            if table is not None and table.has_column(flt.column):
                owner_candidates.append(table_name)
        if owner_candidates:
            owned += 1
        else:
            conflicts += 1

    return owned, conflicts


def _score_query_understanding_filters(
    entity: BusinessEntitySemantic,
    query_understanding: Any | None,
    context: Any,
) -> int:
    if query_understanding is None:
        return 0

    entity_tables = _entity_tables(entity)
    semantic_columns = _semantic_columns(entity)
    score = 0
    for extracted_filter in getattr(query_understanding, "extracted_filters", []):
        dim = casefold_tr(str(extracted_filter.get("dimension", "")))
        column_hint = _norm(str(extracted_filter.get("column_hint", "")))
        value = _norm(str(extracted_filter.get("value", "")))
        dim = _norm(dim)

        if column_hint and column_hint in semantic_columns:
            score += 3
            continue
        if dim and any(dim in key for key in semantic_columns):
            score += 2
            continue
        if column_hint:
            for table_name in entity_tables:
                table = _context_get_table(context, table_name)
                if table is not None and table.has_column(column_hint):
                    score += 2
                    break
        elif value and value in " ".join(entity.keywords).lower():
            score += 1
    return score


def _score_document_agreement(
    entity: BusinessEntitySemantic,
    schema_docs: list[Any] | None,
    examples: list[Any] | None,
) -> int:
    entity_tables = _entity_tables(entity)
    score = 0
    for doc in schema_docs or []:
        table_name = str(getattr(doc, "table_name", "") or "").upper()
        if table_name == entity.root_table.upper():
            score += 3
        elif table_name and table_name in entity_tables:
            score += 1
    for example in examples or []:
        tables = {str(table).upper() for table in getattr(example, "tables", [])}
        if entity.root_table.upper() in tables:
            score += 2
        elif tables & entity_tables:
            score += 1
    return score


def _score_entity_candidates(
    plan: QueryPlan,
    user_message: str,
    context: CatalogSnapshot,
    registry: SemanticRegistry,
    *,
    query_understanding: Any | None = None,
    retrieval_diagnostics: Any | None = None,
    schema_docs: list[Any] | None = None,
    examples: list[Any] | None = None,
) -> list[dict[str, Any]]:
    folded = _norm(" ".join(part for part in [user_message, plan.intent] if part))
    tokens = _tokenize(folded)
    referenced_columns = {
        _norm(column)
        for column in [
            *plan.select_columns,
            *plan.group_by,
            *(flt.column for flt in plan.filters),
            *(agg.column for agg in plan.aggregations),
            *(order.column for order in plan.order_by),
        ]
        if column
    }
    retrieved_tables = {table.name.upper() for table in _context_tables(context)}
    retrieval_root = str(getattr(retrieval_diagnostics, "root_table_name", "") or "").upper()
    resolved_entities = set(getattr(query_understanding, "resolved_entities", []) or [])

    scored: list[dict[str, Any]] = []
    for entity in registry.entities:
        entity_tables = _entity_tables(entity)
        reasons: list[str] = []
        breakdown: dict[str, int] = defaultdict(int)

        keyword_hits = sum(1 for keyword in entity.keywords if keyword and _norm(keyword) in folded)
        if keyword_hits:
            breakdown["lexical"] += keyword_hits * 5
            reasons.append("entity_alias_match")

        entity_tokens = _tokenize(" ".join([entity.entity_id, entity.root_table, *entity.keywords]))
        token_hits = sum(1 for token in tokens if any(token == entity_token or token in entity_token or entity_token in token for entity_token in entity_tokens))
        if token_hits:
            breakdown["lexical"] += token_hits * 2

        semantic_columns = _semantic_columns(entity)
        column_hits = len(referenced_columns & semantic_columns)
        if column_hits:
            breakdown["columns"] += column_hits * 3

        if plan.table and plan.table.upper() == entity.root_table.upper():
            breakdown["selected_table"] += 7
        elif plan.table and plan.table.upper() in entity_tables:
            breakdown["selected_table"] += 4

        join_hits = sum(1 for table_name in plan.all_tables if table_name.upper() in entity_tables)
        if join_hits:
            breakdown["joined_tables"] += join_hits

        if entity.entity_id in resolved_entities:
            breakdown["query_understanding"] += 10
            reasons.append("query_understanding_alignment")

        query_filter_score = _score_query_understanding_filters(entity, query_understanding, context)
        if query_filter_score:
            breakdown["query_filters"] += query_filter_score

        if retrieval_root and retrieval_root == entity.root_table.upper():
            breakdown["retrieval"] += 8
            reasons.append("retrieval_domain_alignment")
        elif entity.root_table.upper() in retrieved_tables:
            breakdown["retrieval"] += 3

        doc_score = _score_document_agreement(entity, schema_docs, examples)
        if doc_score:
            breakdown["documents"] += doc_score

        owned_filters, conflicting_filters = _score_filter_ownership(plan, entity, context)
        if owned_filters:
            breakdown["filter_ownership"] += owned_filters * 4
        if conflicting_filters:
            breakdown["filter_conflict"] -= conflicting_filters * 5
            reasons.append("column_ownership_conflict")

        total = sum(breakdown.values())
        scored.append(
            {
                "entity": entity,
                "total": total,
                "reasons": reasons,
                "breakdown": dict(breakdown),
                "owned_filters": owned_filters,
                "conflicting_filters": conflicting_filters,
            }
        )

    scored.sort(
        key=lambda item: (
            int(item["total"]),
            int(item["owned_filters"]),
            -int(item["conflicting_filters"]),
        ),
        reverse=True,
    )
    return scored


def _confidence_for_scores(scored_entities: list[dict[str, Any]]) -> str:
    if not scored_entities:
        return "low"
    best = int(scored_entities[0]["total"])
    runner_up = int(scored_entities[1]["total"]) if len(scored_entities) > 1 else 0
    margin = best - runner_up
    if best >= 18 and margin >= 5:
        return "high"
    if best >= 10 and margin >= 3:
        return "medium"
    return "low"


def _effective_signal_table(filter_spec: FilterSpec, plan: QueryPlan, entity: BusinessEntitySemantic, context: CatalogSnapshot) -> str | None:
    if filter_spec.table:
        return filter_spec.table

    if plan.table:
        plan_table = _context_get_table(context, plan.table)
        if plan_table is not None and plan_table.has_column(filter_spec.column):
            return plan.table

    for table_name in _entity_tables(entity):
        table = _context_get_table(context, table_name)
        if table is not None and table.has_column(filter_spec.column):
            return table_name
    return None


def _preserve_filter_ownership(
    plan: QueryPlan,
    *,
    original_table: str | None,
    entity: BusinessEntitySemantic,
    context: Any,
) -> tuple[list[FilterSpec], bool]:
    if not plan.filters or not original_table or original_table.upper() == entity.root_table.upper():
        return list(plan.filters), False

    preserved = False
    original_meta = _context_get_table(context, original_table)
    root_meta = _context_get_table(context, entity.root_table)
    updated_filters: list[FilterSpec] = []

    for flt in plan.filters:
        if flt.table is not None or original_meta is None or not original_meta.has_column(flt.column):
            updated_filters.append(flt)
            continue
        root_has_column = root_meta is not None and root_meta.has_column(flt.column)
        if not root_has_column and original_table.upper() in _entity_tables(entity):
            updated_filters.append(flt.model_copy(update={"table": original_table}))
            preserved = True
        else:
            updated_filters.append(flt)

    return updated_filters, preserved


def _filter_signal_key(signal: dict[str, Any]) -> str:
    dimension = _norm(str(signal.get("dimension", "") or ""))
    value = _norm(str(signal.get("value", "") or ""))
    column_hint = _norm(str(signal.get("column_hint", "") or ""))
    return "|".join([dimension, value, column_hint])


def _filter_matches_signal(filter_spec: FilterSpec, signal: dict[str, Any]) -> bool:
    dimension = _norm(str(signal.get("dimension", "") or ""))
    value = _norm(str(signal.get("value", "") or ""))
    column_hint = _norm(str(signal.get("column_hint", "") or ""))
    column = _norm(filter_spec.column)
    filter_value = _norm(str(filter_spec.value)) if filter_spec.value is not None else ""

    if column_hint and column_hint == column:
        return True
    if value and filter_value and value == filter_value:
        return True
    if dimension and dimension in column:
        return True
    return False


def _filter_coverage_keys(plan: QueryPlan, query_understanding: Any | None) -> set[str]:
    if query_understanding is None:
        return set()
    coverage: set[str] = set()
    for signal in getattr(query_understanding, "extracted_filters", []):
        if any(_filter_matches_signal(filter_spec, signal) for filter_spec in plan.filters):
            coverage.add(_filter_signal_key(signal))
    return coverage


def _signal_owner_table(entity: BusinessEntitySemantic, context: CatalogSnapshot, column_name: str) -> str | None:
    for table_name in _entity_tables(entity):
        table = _context_get_table(context, table_name)
        if table is not None and table.has_column(column_name):
            return table.name
    return None


def _lookup_raw_values_for_column(
    registry: SemanticRegistry,
    column_name: str,
    *,
    table_name: str | None = None,
) -> set[str]:
    return {
        str(lookup.raw_value).strip().upper()
        for lookup in registry.get_lookups_for_column(column_name, table_name=table_name)
        if str(lookup.raw_value).strip()
    }


def _normalize_signal_value(raw_value: str) -> object:
    if raw_value.isdigit():
        return int(raw_value)
    return raw_value


def _expected_filters_from_query_understanding(
    entity: BusinessEntitySemantic,
    query_understanding: Any | None,
    context: CatalogSnapshot,
) -> tuple[list[FilterSpec], set[str]]:
    if query_understanding is None:
        return [], set()

    grouped_values: dict[tuple[str, str | None], list[str]] = defaultdict(list)
    filters: list[FilterSpec] = []
    replaced_columns: set[str] = set()

    for signal in getattr(query_understanding, "extracted_filters", []):
        column_hint = str(signal.get("column_hint") or "").upper()
        raw_value = str(signal.get("value") or "").strip()
        if not column_hint:
            continue

        owner_table = _signal_owner_table(entity, context, column_hint)
        if owner_table is None:
            continue

        if column_hint == "CIKIS_TARIHI" and raw_value.lower() == "active":
            filters.append(FilterSpec(column="CIKIS_TARIHI", table=owner_table, op=FilterOp.IS_NULL))
            replaced_columns.add(column_hint)
            continue

        if column_hint == "UNVAN" and raw_value:
            filters.append(
                FilterSpec(column="UNVAN", table=owner_table, op=FilterOp.LIKE, value=f"%{raw_value}%")
            )
            continue

        if raw_value:
            grouped_values[(column_hint, owner_table)].append(raw_value)

    for (column_hint, owner_table), raw_values in grouped_values.items():
        unique_values = list(dict.fromkeys(raw_values))
        if not unique_values:
            continue
        replaced_columns.add(column_hint)
        if len(unique_values) == 1:
            filters.append(
                FilterSpec(
                    column=column_hint,
                    table=owner_table,
                    op=FilterOp.EQ,
                    value=_normalize_signal_value(unique_values[0]),
                )
            )
        else:
            filters.append(
                FilterSpec(
                    column=column_hint,
                    table=owner_table,
                    op=FilterOp.IN,
                    value=[_normalize_signal_value(value) for value in unique_values],
                )
            )

    return filters, replaced_columns


def _is_weak_lookup_filter(filter_spec: FilterSpec, registry: SemanticRegistry) -> bool:
    expected_values = _lookup_raw_values_for_column(
        registry,
        filter_spec.column,
        table_name=filter_spec.table,
    )
    if not expected_values:
        return False

    if filter_spec.value is None:
        return filter_spec.op not in {FilterOp.IS_NULL, FilterOp.IS_NOT_NULL}

    values = filter_spec.value if isinstance(filter_spec.value, list) else [filter_spec.value]
    normalized_values = {str(value).strip().upper() for value in values}
    if not all(normalized_values):
        return True

    return not normalized_values.issubset(expected_values)


def _replace_weak_filters(
    filters: list[FilterSpec],
    replacement_filters: list[FilterSpec],
    replacement_columns: set[str],
    registry: SemanticRegistry,
) -> tuple[list[FilterSpec], bool]:
    if not replacement_filters:
        return list(filters), False

    updated: list[FilterSpec] = []
    replaced = False
    for filter_spec in filters:
        if filter_spec.column.upper() in replacement_columns and _is_weak_lookup_filter(filter_spec, registry):
            replaced = True
            continue
        updated.append(filter_spec)

    merged = _merge_filters(updated, replacement_filters)
    return merged, replaced


def _empty_result_diagnosis_hint(
    *,
    weak_filter_mapping: bool,
    filter_ownership_conflict: bool,
    missing_filter_detected: bool,
    final_filter_coverage: set[str],
    query_understanding: Any | None,
) -> str | None:
    extracted_filters = list(getattr(query_understanding, "extracted_filters", []) or [])
    if not extracted_filters:
        return None
    if weak_filter_mapping:
        return "value_encoding_mismatch"
    if filter_ownership_conflict:
        return "wrong_filter_column"
    if missing_filter_detected:
        return "likely_semantic_mismatch"
    if final_filter_coverage:
        return "true_no_data"
    return None


def _merge_filters(
    base_filters: list[FilterSpec],
    extra_filters: list[FilterSpec],
) -> list[FilterSpec]:
    merged: list[FilterSpec] = []
    seen: set[tuple[str | None, str, str, str]] = set()
    for filter_spec in [*base_filters, *extra_filters]:
        key = (
            filter_spec.table,
            filter_spec.column,
            filter_spec.op.value,
            str(filter_spec.value),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(filter_spec)
    return merged


def _safe_apply_intent_defaults(
    entity: BusinessEntitySemantic,
    semantic_intent: str,
    updates: dict[str, object],
    *,
    plan: QueryPlan,
    confidence: str,
    query_understanding: Any | None,
    diagnostics: dict[str, Any],
) -> bool:
    intent_def = entity.intent_defaults.get(semantic_intent)
    if intent_def is None:
        return False

    if confidence == "low":
        diagnostics.setdefault("decision_reasons", []).append("override_suppressed_due_to_low_confidence")
        return False

    requested_output = getattr(query_understanding, "requested_output_type", None)
    if requested_output == "aggregation" and not intent_def.aggregations and not plan.aggregations:
        diagnostics.setdefault("decision_reasons", []).append("override_suppressed_due_to_low_confidence")
        return False
    if requested_output == "list" and intent_def.aggregations and not plan.aggregations and not plan.needs_clarification:
        diagnostics.setdefault("decision_reasons", []).append("override_suppressed_due_to_low_confidence")
        return False

    before_filters = list(plan.filters)
    _apply_intent_defaults(entity, semantic_intent, updates, plan)
    if updates.get("filters"):
        updates["filters"] = _merge_filters(before_filters, list(updates["filters"]))
    return True


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
    query_understanding: Any | None = None,
    retrieval_diagnostics: Any | None = None,
    schema_docs: list[Any] | None = None,
    examples: list[Any] | None = None,
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

    scored_entities = _score_entity_candidates(
        plan,
        user_message,
        _context,
        reg,
        query_understanding=query_understanding,
        retrieval_diagnostics=retrieval_diagnostics,
        schema_docs=schema_docs,
        examples=examples,
    )
    if not scored_entities or int(scored_entities[0]["total"]) <= 0:
        return plan

    entity = scored_entities[0]["entity"]
    confidence = _confidence_for_scores(scored_entities)
    runner_up = scored_entities[1] if len(scored_entities) > 1 else None
    diagnostics: dict[str, Any] = {
        "selected_entity_id": entity.entity_id,
        "selected_root_table": entity.root_table,
        "confidence": confidence,
        "decision_reasons": list(dict.fromkeys(scored_entities[0]["reasons"])),
        "score_breakdown": {
            item["entity"].entity_id: item["breakdown"]
            for item in scored_entities[:3]
        },
        "runner_up_entity_id": runner_up["entity"].entity_id if runner_up is not None else None,
        "runner_up_score": int(runner_up["total"]) if runner_up is not None else 0,
        "selected_entity_score": int(scored_entities[0]["total"]),
        "override_applied": False,
        "stable_intent_defaults_applied": False,
        "protected_filter_preserved": False,
        "filter_ownership_conflict": bool(scored_entities[0]["conflicting_filters"]),
        "weak_filter_mapping": False,
        "missing_filter": False,
        "filter_coverage_before": sorted(_filter_coverage_keys(plan, query_understanding)),
    }
    has_external_signals = any([
        query_understanding is not None,
        retrieval_diagnostics is not None,
        bool(schema_docs),
        bool(examples),
    ])

    semantic_intent = _infer_entity_intent(user_message, entity)
    join_path_id = reg.intent_join_paths.get(semantic_intent)
    path = entity.get_join_path(join_path_id) if join_path_id else None

    updates: dict[str, object] = {
        "root_entity": entity.entity_id,
        "semantic_intent": semantic_intent,
    }

    original_table = plan.table
    current_entity_score = 0
    if plan.table:
        for item in scored_entities:
            candidate = item["entity"]
            if plan.table.upper() in _entity_tables(candidate):
                current_entity_score = int(item["total"])
                break

    should_anchor = False
    if not has_external_signals:
        should_anchor = plan.table != entity.root_table
    elif plan.table is None:
        should_anchor = confidence != "low"
    elif plan.table.upper() == entity.root_table.upper():
        should_anchor = False
    elif plan.table.upper() not in _entity_tables(entity):
        should_anchor = confidence in {"medium", "high"}
    elif (
        plan.table.upper() in _entity_tables(entity)
        and plan.table.upper() != entity.root_table.upper()
        and confidence == "high"
        and (
            entity.entity_id in set(getattr(query_understanding, "resolved_entities", []) or [])
            or str(getattr(retrieval_diagnostics, "root_table_name", "") or "").upper() == entity.root_table.upper()
            or bool(join_path_id)
        )
    ):
        should_anchor = True
    elif confidence == "high" and int(scored_entities[0]["total"]) >= current_entity_score + 5:
        should_anchor = True
    elif confidence == "medium" and retrieval_diagnostics is not None and str(getattr(retrieval_diagnostics, "root_table_name", "") or "").upper() == entity.root_table.upper():
        should_anchor = True
    else:
        diagnostics.setdefault("decision_reasons", []).append("override_suppressed_due_to_low_confidence")

    # Apply registry intent defaults. For clarification plans, only apply stable
    # defaults (semantic rescue); non-stable defaults must not override an
    # intentional clarification produced by the planner.
    intent_def = entity.intent_defaults.get(semantic_intent)
    if intent_def is not None and (not plan.needs_clarification or intent_def.stable):
        diagnostics["stable_intent_defaults_applied"] = _safe_apply_intent_defaults(
            entity,
            semantic_intent,
            updates,
            plan=plan,
            confidence=(confidence if has_external_signals else "high"),
            query_understanding=query_understanding,
            diagnostics=diagnostics,
        )

    # When registry imposes aggregations, discard stale LLM select_columns:
    # SELECT shape is fully determined by group_by + aggregations.
    if updates.get("aggregations") and plan.select_columns:
        updates["select_columns"] = []
    if plan.aggregations and updates.get("select_columns"):
        updates.pop("select_columns")

    # Anchor base table to semantic root.
    if should_anchor and plan.table != entity.root_table:
        updates["table"] = entity.root_table
        diagnostics["override_applied"] = True

    candidate_plan = plan.model_copy(update=updates)
    preserved_filters, preserved = _preserve_filter_ownership(
        candidate_plan,
        original_table=original_table,
        entity=entity,
        context=_context,
    )
    if preserved:
        updates["filters"] = preserved_filters
        diagnostics["protected_filter_preserved"] = True
        diagnostics.setdefault("decision_reasons", []).append("protected_filter_preserved")

    candidate_plan = plan.model_copy(update=updates)
    before_coverage = set(diagnostics["filter_coverage_before"])
    after_coverage = _filter_coverage_keys(candidate_plan, query_understanding)
    if before_coverage and len(after_coverage) < len(before_coverage):
        updates["filters"] = _merge_filters(list(candidate_plan.filters), list(plan.filters))
        candidate_plan = plan.model_copy(update=updates)
        after_coverage = _filter_coverage_keys(candidate_plan, query_understanding)
        if len(after_coverage) >= len(before_coverage):
            diagnostics["protected_filter_preserved"] = True
            diagnostics.setdefault("decision_reasons", []).append("protected_filter_preserved")

    inferred_filters, replacement_columns = _expected_filters_from_query_understanding(
        entity,
        query_understanding,
        _context,
    )
    if inferred_filters:
        rewritten_filters, replaced_weak_filters = _replace_weak_filters(
            list(candidate_plan.filters),
            inferred_filters,
            replacement_columns,
            reg,
        )
        if rewritten_filters != list(candidate_plan.filters):
            updates["filters"] = rewritten_filters
            candidate_plan = plan.model_copy(update=updates)
            after_coverage = _filter_coverage_keys(candidate_plan, query_understanding)
        if replaced_weak_filters:
            diagnostics["weak_filter_mapping"] = True
            diagnostics.setdefault("decision_reasons", []).append("weak_filter_mapping")

    if join_path_id:
        updates["join_path_id"] = join_path_id
        updates["joins"] = _joins_from_path(path)

    candidate_plan = plan.model_copy(update=updates)
    diagnostics["filter_coverage_after"] = sorted(_filter_coverage_keys(candidate_plan, query_understanding))
    diagnostics["filter_loss_risk"] = bool(
        diagnostics["filter_coverage_before"]
        and len(diagnostics["filter_coverage_after"]) < len(diagnostics["filter_coverage_before"])
    )
    diagnostics["missing_filter"] = bool(
        diagnostics["filter_coverage_before"]
        and len(diagnostics["filter_coverage_after"]) < len(diagnostics["filter_coverage_before"])
    )
    if diagnostics["filter_ownership_conflict"]:
        diagnostics.setdefault("decision_reasons", []).append("filter_ownership_conflict")
    if diagnostics["missing_filter"]:
        diagnostics.setdefault("decision_reasons", []).append("missing_filter")
    diagnostics["empty_result_diagnosis_hint"] = _empty_result_diagnosis_hint(
        weak_filter_mapping=bool(diagnostics["weak_filter_mapping"]),
        filter_ownership_conflict=bool(diagnostics["filter_ownership_conflict"]),
        missing_filter_detected=bool(diagnostics["missing_filter"]),
        final_filter_coverage=set(diagnostics["filter_coverage_after"]),
        query_understanding=query_understanding,
    )

    # Observability fields derived from the *final* canonical plan shape.
    final_group_by: list[str] = list(updates.get("group_by", plan.group_by))  # type: ignore[arg-type]
    final_aggs: list[AggregationSpec] = list(updates.get("aggregations", plan.aggregations))  # type: ignore[arg-type]
    if final_group_by:
        updates["dimensions"] = final_group_by
    if final_aggs:
        updates["measures"] = [a.alias or f"{a.function.value}_{a.column}" for a in final_aggs]

    final_plan = plan.model_copy(update=updates)
    object.__setattr__(final_plan, "_semantic_diagnostics", diagnostics)
    apply_semantic_normalization.last_diagnostics = diagnostics
    return final_plan


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
