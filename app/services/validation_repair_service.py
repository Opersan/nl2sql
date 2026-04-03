"""Conservative validation-aware repair service.

Runs only after an initial validation failure and only attempts a narrow set of
safe column-level repairs. The goal is to recover plans with obvious alias /
shape drift without inventing schema.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.domain.catalog_models import TableMetadata
from app.domain.execution_models import ValidationResult
from app.domain.query_plan import QueryPlan
from app.services.catalog_service import CatalogService
from app.services.query_plan_repair import RepairAction, RepairResult
from app.services.semantic_planning import _load_registry
from app.utils.turkish import casefold_tr


def _alias_fold(text: str) -> str:
    return casefold_tr(text).replace("ı", "i")


def _compact_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _alias_fold(text))


_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


@lru_cache(maxsize=1)
def _get_token_abbreviations() -> dict[str, str]:
    """Load token-level abbreviation expansions from the semantic registry.

    E.g. {"doc": "document", "num": "number"} so that a column called
    ``PO_DOC_NUM`` can also be matched via the variant ``podocumentnumber``.
    Falls back to an empty dict when the registry is unavailable.
    """
    try:
        registry = _load_registry()
        return dict(registry.column_aliases.token_abbreviations)
    except Exception:
        return {}


def _alias_variants(text: str) -> set[str]:
    folded = _alias_fold(text)
    variants = {folded, _compact_key(text)}
    tokens = [token for token in _TOKEN_SPLIT_RE.split(folded) if token]
    if tokens:
        variants.add("".join(tokens))
        abbreviations = _get_token_abbreviations()
        if abbreviations:
            expanded = [abbreviations.get(token, token) for token in tokens]
            variants.add("".join(expanded))
    return {variant for variant in variants if variant}


class ValidationRepairService:
    """Attempt one-shot, validation-driven QueryPlan repairs."""

    def __init__(self, catalog: CatalogService) -> None:
        self._catalog = catalog

    async def repair(
        self,
        plan: QueryPlan,
        validation: ValidationResult,
    ) -> tuple[QueryPlan, RepairResult, dict[str, Any]]:
        trace: dict[str, Any] = {
            "attempted": False,
            "repaired": False,
            "reasons": [],
            "skipped_reason_codes": [],
        }
        result = RepairResult()

        if validation.resolved_table is None:
            trace["skipped_reason_codes"].append("repair_skipped_low_confidence")
            return plan, result, trace

        allowed_error_codes = {"invalid_column", "aggregate_select_mismatch"}
        if not validation.errors or any(issue.code not in allowed_error_codes for issue in validation.errors):
            trace["skipped_reason_codes"].append("repair_skipped_low_confidence")
            return plan, result, trace

        trace["attempted"] = True
        all_tables: dict[str, TableMetadata] = {validation.resolved_table.name.upper(): validation.resolved_table}
        all_tables.update({name.upper(): table for name, table in validation.resolved_tables.items()})

        repaired = plan
        repaired = self._prune_aggregate_alias_select_columns(repaired, validation.resolved_table, all_tables, result, trace)
        repaired = self._prune_non_group_select_columns(repaired, result, trace)
        repaired = self._repair_select_columns(repaired, validation.resolved_table, all_tables, result, trace)
        repaired = self._repair_filters(repaired, validation.resolved_table, all_tables, result, trace)
        repaired = self._repair_aggregations(repaired, validation.resolved_table, all_tables, result, trace)
        repaired = self._repair_group_by(repaired, validation.resolved_table, all_tables, result, trace)
        repaired = self._repair_order_by(repaired, validation.resolved_table, all_tables, result, trace)

        if not result.repair_applied:
            trace["skipped_reason_codes"].append("repair_skipped_low_confidence")
        trace["repaired"] = result.repair_applied
        return repaired, result, trace

    def _is_real_column(self, name: str, all_tables: dict[str, TableMetadata]) -> bool:
        for meta in all_tables.values():
            if meta.resolve_column_name(name) is not None:
                return True
        return False

    def _prune_aggregate_alias_select_columns(
        self,
        plan: QueryPlan,
        primary_table: TableMetadata,
        all_tables: dict[str, TableMetadata],
        result: RepairResult,
        trace: dict[str, Any],
    ) -> QueryPlan:
        if not plan.aggregations or not plan.select_columns:
            return plan

        agg_aliases = {casefold_tr(agg.effective_alias()) for agg in plan.aggregations}
        group_by_folded = {casefold_tr(col) for col in plan.group_by}
        updated: list[str] = []
        changed = False

        for index, column in enumerate(plan.select_columns):
            folded = casefold_tr(column)
            if folded in agg_aliases and folded not in group_by_folded:
                if not self._is_real_column(column, all_tables):
                    changed = True
                    trace["reasons"].append("aggregate_alias_pruned")
                    result.record(
                        RepairAction(
                            repair_type="aggregate_alias_pruned",
                            description=f"Removed aggregate alias from select_columns: {column}",
                            field_path=f"select_columns[{index}]",
                            original_value=column,
                            repaired_value=None,
                        )
                    )
                    continue
            updated.append(column)

        return plan.model_copy(update={"select_columns": updated}) if changed else plan

    def _prune_non_group_select_columns(
        self,
        plan: QueryPlan,
        result: RepairResult,
        trace: dict[str, Any],
    ) -> QueryPlan:
        if not plan.aggregations or not plan.select_columns or not plan.group_by:
            return plan

        group_by_folded = {casefold_tr(col) for col in plan.group_by}
        updated: list[str] = []
        changed = False

        for index, column in enumerate(plan.select_columns):
            if casefold_tr(column) not in group_by_folded:
                changed = True
                trace["reasons"].append("aggregate_select_pruned")
                result.record(
                    RepairAction(
                        repair_type="aggregate_select_pruned",
                        description=f"Removed non-group select column in aggregate query: {column}",
                        field_path=f"select_columns[{index}]",
                        original_value=column,
                        repaired_value=None,
                    )
                )
                continue
            updated.append(column)

        return plan.model_copy(update={"select_columns": updated}) if changed else plan

    def _registry_alias_maps(self) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        registry = _load_registry()
        global_aliases: dict[str, str] = {}
        for alias, canonical in registry.column_aliases.global_aliases.items():
            for variant in _alias_variants(alias):
                global_aliases.setdefault(variant, canonical)
        # token_abbreviations already applied inside _alias_variants;
        # _SAFE_GLOBAL_SYNONYM_FALLBACKS removed – covered by global_aliases in registry.

        table_scoped: dict[str, dict[str, str]] = {}
        for table_name, aliases in registry.column_aliases.table_scoped.items():
            scoped: dict[str, str] = {}
            for alias, canonical in aliases.items():
                for variant in _alias_variants(alias):
                    scoped.setdefault(variant, canonical)
            table_scoped[(table_name or "").strip().upper()] = scoped
        return global_aliases, table_scoped

    def _candidate_for(
        self,
        raw_name: str,
        *,
        preferred_table: TableMetadata,
        all_tables: dict[str, TableMetadata],
        table_hint: str | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        raw = raw_name.strip()
        raw_variants = _alias_variants(raw)
        global_aliases, table_scoped_aliases = self._registry_alias_maps()

        def _maps_for(meta: TableMetadata) -> tuple[str | None, str | None]:
            scoped = table_scoped_aliases.get(meta.name.upper(), {})
            for variant in raw_variants:
                if variant in scoped:
                    return scoped[variant], "known_synonym_repaired"
                if variant in global_aliases:
                    return global_aliases[variant], "known_synonym_repaired"

            exact = meta.resolve_column_name(raw)
            if exact is not None and exact != raw:
                return exact, "alias_to_canonical"

            compact_matches: set[str] = set()
            for column in meta.columns:
                if _alias_variants(column.name).intersection(raw_variants):
                    compact_matches.add(column.name)
                for alias in column.aliases:
                    if _alias_variants(alias).intersection(raw_variants):
                        compact_matches.add(column.name)
            if len(compact_matches) == 1:
                return next(iter(compact_matches)), "alias_to_canonical"
            return None, None

        def _search_other_tables(*, excluded_tables: set[str]) -> tuple[str | None, str | None, str | None]:
            candidates: list[tuple[str, str, str]] = []
            for meta in all_tables.values():
                if meta.name.upper() in excluded_tables:
                    continue
                resolved, match_reason = _maps_for(meta)
                if resolved is not None and match_reason is not None:
                    candidates.append((meta.name, resolved, match_reason))
            if len(candidates) == 1:
                table_name, resolved, reason = candidates[0]
                return resolved, table_name, reason
            return None, None, None

        if table_hint:
            hinted = all_tables.get(table_hint.upper())
            if hinted is None:
                return None, None, None
            column, reason = _maps_for(hinted)
            if column is not None:
                return column, hinted.name, reason
            return _search_other_tables(excluded_tables={hinted.name.upper()})

        column, reason = _maps_for(preferred_table)
        if column is not None:
            return column, preferred_table.name, reason

        return _search_other_tables(excluded_tables={preferred_table.name.upper()})

    def _repair_select_columns(self, plan: QueryPlan, primary_table: TableMetadata, all_tables: dict[str, TableMetadata], result: RepairResult, trace: dict[str, Any]) -> QueryPlan:
        updated = list(plan.select_columns)
        changed = False
        for index, column in enumerate(plan.select_columns):
            resolved, _table_name, reason = self._candidate_for(column, preferred_table=primary_table, all_tables=all_tables)
            if resolved is None or resolved == column:
                continue
            updated[index] = resolved
            changed = True
            trace["reasons"].append(reason)
            result.record(RepairAction(repair_type=str(reason), description=f"Repaired select column: {column} -> {resolved}", field_path=f"select_columns[{index}]", original_value=column, repaired_value=resolved))
        return plan.model_copy(update={"select_columns": updated}) if changed else plan

    def _repair_group_by(self, plan: QueryPlan, primary_table: TableMetadata, all_tables: dict[str, TableMetadata], result: RepairResult, trace: dict[str, Any]) -> QueryPlan:
        updated = list(plan.group_by)
        changed = False
        for index, column in enumerate(plan.group_by):
            resolved, _table_name, reason = self._candidate_for(column, preferred_table=primary_table, all_tables=all_tables)
            if resolved is None or resolved == column:
                continue
            updated[index] = resolved
            changed = True
            trace["reasons"].append(reason)
            result.record(RepairAction(repair_type=str(reason), description=f"Repaired group_by column: {column} -> {resolved}", field_path=f"group_by[{index}]", original_value=column, repaired_value=resolved))
        return plan.model_copy(update={"group_by": updated}) if changed else plan

    def _repair_filters(self, plan: QueryPlan, primary_table: TableMetadata, all_tables: dict[str, TableMetadata], result: RepairResult, trace: dict[str, Any]) -> QueryPlan:
        updated = list(plan.filters)
        changed = False
        for index, spec in enumerate(plan.filters):
            resolved, resolved_table, reason = self._candidate_for(spec.column, preferred_table=primary_table, all_tables=all_tables, table_hint=spec.table)
            if resolved is None or resolved == spec.column:
                continue
            new_table = spec.table
            current_table = (spec.table or primary_table.name).upper()
            if resolved_table and resolved_table.upper() != current_table:
                new_table = resolved_table
            updated[index] = spec.model_copy(update={"column": resolved, "table": new_table})
            changed = True
            trace["reasons"].append(reason)
            result.record(RepairAction(repair_type=str(reason), description=f"Repaired filter column: {spec.column} -> {resolved}", field_path=f"filters[{index}].column", original_value=spec.column, repaired_value=resolved))
        return plan.model_copy(update={"filters": updated}) if changed else plan

    def _repair_aggregations(self, plan: QueryPlan, primary_table: TableMetadata, all_tables: dict[str, TableMetadata], result: RepairResult, trace: dict[str, Any]) -> QueryPlan:
        updated = list(plan.aggregations)
        changed = False
        for index, spec in enumerate(plan.aggregations):
            resolved, resolved_table, reason = self._candidate_for(spec.column, preferred_table=primary_table, all_tables=all_tables, table_hint=spec.table)
            if resolved is None or resolved == spec.column:
                continue
            new_table = spec.table
            current_table = (spec.table or primary_table.name).upper()
            if resolved_table and resolved_table.upper() != current_table:
                new_table = resolved_table
            updated[index] = spec.model_copy(update={"column": resolved, "table": new_table})
            changed = True
            trace["reasons"].append(reason)
            result.record(RepairAction(repair_type=str(reason), description=f"Repaired aggregation column: {spec.column} -> {resolved}", field_path=f"aggregations[{index}].column", original_value=spec.column, repaired_value=resolved))
        return plan.model_copy(update={"aggregations": updated}) if changed else plan

    def _repair_order_by(self, plan: QueryPlan, primary_table: TableMetadata, all_tables: dict[str, TableMetadata], result: RepairResult, trace: dict[str, Any]) -> QueryPlan:
        updated = []
        changed = False
        aggregate_aliases = {_compact_key(agg.effective_alias()): agg.effective_alias() for agg in plan.aggregations}
        select_aliases = {_compact_key(column): column for column in plan.select_columns}

        for index, spec in enumerate(plan.order_by):
            alias_match = aggregate_aliases.get(_compact_key(spec.column))
            if alias_match is not None:
                if alias_match != spec.column:
                    changed = True
                    trace["reasons"].append("invalid_sort_column_mapped")
                    result.record(RepairAction(repair_type="invalid_sort_column_mapped", description=f"Mapped ORDER BY to aggregate alias: {spec.column} -> {alias_match}", field_path=f"order_by[{index}].column", original_value=spec.column, repaired_value=alias_match))
                    updated.append(spec.model_copy(update={"column": alias_match}))
                else:
                    updated.append(spec)
                continue

            selected = select_aliases.get(_compact_key(spec.column))
            if selected is not None:
                mapped = selected
                if mapped != spec.column:
                    changed = True
                    trace["reasons"].append("invalid_sort_column_mapped")
                    result.record(RepairAction(repair_type="invalid_sort_column_mapped", description=f"Mapped ORDER BY to selected column: {spec.column} -> {mapped}", field_path=f"order_by[{index}]", original_value=spec.column, repaired_value=mapped))
                    updated.append(spec.model_copy(update={"column": mapped}))
                else:
                    updated.append(spec)
                continue

            resolved, resolved_table, _reason = self._candidate_for(spec.column, preferred_table=primary_table, all_tables=all_tables, table_hint=spec.table)
            if resolved is not None:
                new_table = spec.table
                current_table = (spec.table or primary_table.name).upper()
                if resolved_table and resolved_table.upper() != current_table:
                    new_table = resolved_table
                if resolved != spec.column or new_table != spec.table:
                    changed = True
                    trace["reasons"].append("invalid_sort_column_mapped")
                    result.record(RepairAction(repair_type="invalid_sort_column_mapped", description=f"Mapped ORDER BY column: {spec.column} -> {resolved}", field_path=f"order_by[{index}]", original_value=spec.column, repaired_value=resolved))
                    updated.append(spec.model_copy(update={"column": resolved, "table": new_table}))
                else:
                    updated.append(spec)
                continue

            changed = True
            trace["reasons"].append("invalid_sort_column_dropped")
            result.record(RepairAction(repair_type="invalid_sort_column_dropped", description=f"Dropped invalid ORDER BY column: {spec.column}", field_path=f"order_by[{index}]", original_value=spec.column, repaired_value=None))

        return plan.model_copy(update={"order_by": updated}) if changed else plan