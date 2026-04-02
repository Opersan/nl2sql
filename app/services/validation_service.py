"""Validation service.

Validates a ``QueryPlan`` against catalog metadata and business rules.
Returns a structured ``ValidationResult`` – never raises for user-facing
validation failures.
"""

from __future__ import annotations

from app.core.config import settings
from app.domain.catalog_models import TableMetadata
from app.domain.execution_models import ValidationResult
from app.domain.query_plan import STAR_COLUMN, QueryPlan
from app.services.catalog_service import CatalogService
from app.services.semantic_planning import _load_registry
from app.utils.turkish import casefold_tr


def _alias_fold(text: str) -> str:
    """Fold text for ASCII-like alias keys while remaining Turkish-safe."""
    return casefold_tr(text).replace("ı", "i")


class ValidationService:
    """Deterministic plan validator."""

    def __init__(self, catalog: CatalogService) -> None:
        self._catalog = catalog

    async def validate(self, plan: QueryPlan) -> ValidationResult:
        """Run all validation checks and return a ``ValidationResult``."""
        result = ValidationResult()

        # --- Resolve the target table ------------------------------------
        table = await self._resolve_table(plan, result)

        if table is not None:
            result.resolved_table = table

            # --- Resolve JOINed tables (Sprint 5) ---
            extra_tables: dict[str, TableMetadata] = {}
            if plan.is_multi_table:
                extra_tables = await self._resolve_join_tables(plan, table, result)
                if extra_tables:
                    result.resolved_tables = extra_tables

            # Build a combined resolver for multi-table column validation
            all_tables = {table.name.upper(): table}
            all_tables.update({k.upper(): v for k, v in extra_tables.items()})

            self._check_select_columns(plan, table, result, all_tables)
            self._check_filter_columns(plan, table, result, all_tables)
            self._check_aggregate_columns(plan, table, result, all_tables)
            self._check_group_by_columns(plan, table, result, all_tables)
            self._check_order_by_columns(plan, table, result, all_tables)
            self._check_partition_by_columns(plan, table, result, all_tables)
            self._check_restricted_columns(plan, table, result, all_tables)
            self._check_aggregate_consistency(plan, table, result, all_tables)

        self._check_limit(plan, result)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_col(
        name_or_alias: str, table: TableMetadata
    ) -> str | None:
        """Resolve *name_or_alias* to its canonical column name via the table.

        Returns ``None`` when the name/alias is not known in *table*.
        """
        return table.resolve_column_name(name_or_alias)

    @staticmethod
    def _normalize_table_key(table_name: str | None) -> str:
        return (table_name or "").strip().upper()

    def _registry_alias_maps(self) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        registry = _load_registry()

        global_aliases = {
            _alias_fold(alias): canonical
            for alias, canonical in registry.column_aliases.global_aliases.items()
        }
        table_scoped = {
            self._normalize_table_key(table_name): {
                _alias_fold(alias): canonical
                for alias, canonical in aliases.items()
            }
            for table_name, aliases in registry.column_aliases.table_scoped.items()
        }
        return global_aliases, table_scoped

    @staticmethod
    def _looks_like_expression(col: str) -> bool:
        """Return True when *col* looks like a SQL expression rather than a plain column name.

        Expressions (e.g. ``TO_CHAR(date, 'YYYY-MM')``, ``NVL(field, 0)``) cannot
        be resolved against catalog metadata and should be skipped in validation.
        """
        token = col.strip()
        if '(' in token or ')' in token:
            return True
        if any(op in token for op in ('+', '-', '*', '/', "||")):
            return True
        return bool(token and ' ' in token)

    @staticmethod
    def _strip_qualifier(col: str) -> tuple[str, str | None]:
        """If *col* is ``TABLE.COLUMN``, return ``(COLUMN, TABLE)``; otherwise ``(col, None)``.

        LLMs sometimes emit qualified references (e.g. ``PO_HEADERS_ALL.ORG_ID``)
        that need the table prefix stripped before alias / column resolution.
        Expressions containing ``(`` are excluded (no stripping for function calls).
        """
        if '.' in col and '(' not in col:
            parts = col.split('.', 1)
            return parts[1].strip(), parts[0].strip().upper()
        return col, None

    def _normalize_column_identifier(self, name: str, *, table_name: str | None) -> str:
        """Normalize identifier via registry aliases with scoped-over-global precedence."""
        raw = name.strip()
        folded = _alias_fold(raw)
        global_aliases, table_scoped_aliases = self._registry_alias_maps()

        normalized_table = self._normalize_table_key(table_name)
        scoped = table_scoped_aliases.get(normalized_table, {})
        if folded in scoped:
            return scoped[folded]
        if folded in global_aliases:
            return global_aliases[folded]
        return raw

    @staticmethod
    def _resolve_col_multi(
        name_or_alias: str,
        primary_table: TableMetadata,
        all_tables: dict[str, TableMetadata],
        *,
        table_qualifier: str | None = None,
    ) -> str | None:
        """Resolve column across multiple tables.

        If *table_qualifier* is provided, resolves only against that table.
        Otherwise tries all tables, starting with the primary.
        """
        if table_qualifier:
            meta = all_tables.get(table_qualifier.upper())
            if meta:
                return meta.resolve_column_name(name_or_alias)
            return None
        # Try primary first
        result = primary_table.resolve_column_name(name_or_alias)
        if result:
            return result
        # Try other tables
        for _name, meta in all_tables.items():
            if meta is primary_table:
                continue
            result = meta.resolve_column_name(name_or_alias)
            if result:
                return result
        return None

    @staticmethod
    def _aggregate_alias_set(plan: QueryPlan) -> set[str]:
        """Return casefold'd effective aliases of all aggregate expressions."""
        return {casefold_tr(agg.effective_alias()) for agg in plan.aggregations}

    # ------------------------------------------------------------------
    # Private validation steps
    # ------------------------------------------------------------------

    async def _resolve_table(
        self, plan: QueryPlan, result: ValidationResult
    ) -> TableMetadata | None:
        """Resolve the target table from plan.table or plan.candidate_tables."""

        if plan.table:
            table = await self._catalog.resolve_table(plan.table)
            if table is None:
                result.add_error(
                    "invalid_table",
                    f"Tablo bulunamadı: '{plan.table}'.",
                    field="table",
                )
                return None
            return table

        # No explicit table – check candidates
        if not plan.candidate_tables:
            result.add_error(
                "invalid_table",
                "Hedef tablo belirtilmedi ve aday tablo listesi boş.",
                field="table",
            )
            return None

        if len(plan.candidate_tables) == 1:
            candidate = plan.candidate_tables[0]
            table = await self._catalog.resolve_table(candidate)
            if table is None:
                result.add_error(
                    "invalid_table",
                    f"Aday tablo bulunamadı: '{candidate}'.",
                    field="table",
                )
                return None
            return table

        # Multiple candidates → ambiguous
        result.add_error(
            "ambiguous_table",
            f"Birden fazla aday tablo var: {plan.candidate_tables}. Lütfen tabloyu belirtin.",
            field="candidate_tables",
        )
        return None

    async def _resolve_join_tables(
        self,
        plan: QueryPlan,
        primary_table: TableMetadata,
        result: ValidationResult,
    ) -> dict[str, TableMetadata]:
        """Resolve all tables referenced in JOIN specs."""
        extra: dict[str, TableMetadata] = {}
        for js in plan.joins:
            for tname in (js.left_table, js.right_table):
                upper = tname.upper()
                if upper == primary_table.name.upper():
                    continue
                if upper in extra:
                    continue
                meta = await self._catalog.resolve_table(tname)
                if meta is None:
                    result.add_error(
                        "invalid_table",
                        f"JOIN tablosu bulunamadı: '{tname}'.",
                        field="joins",
                    )
                else:
                    extra[upper] = meta
        return extra

    # -- Column checks ----------------------------------------------------

    def _check_select_columns(
        self, plan: QueryPlan, table: TableMetadata, result: ValidationResult,
        all_tables: dict[str, TableMetadata] | None = None,
    ) -> None:
        if all_tables is None:
            all_tables = {table.name.upper(): table}
        if not plan.select_columns and not plan.aggregations:
            result.add_error(
                "no_columns",
                "Sorgu için en az bir kolon veya aggregate belirtilmelidir.",
                field="select_columns",
            )
            return

        for col_name in plan.select_columns:
            if col_name == "*":
                result.add_error(
                    "select_star_not_allowed",
                    "SELECT * kullanılamaz. Lütfen kolon listesi belirtin.",
                    field="select_columns",
                )
            elif self._looks_like_expression(col_name):
                continue  # SQL expression — skip catalog validation
            else:
                bare, qualifier = self._strip_qualifier(col_name)
                normalized = self._normalize_column_identifier(
                    bare,
                    table_name=qualifier or table.name,
                )
                if self._resolve_col_multi(normalized, table, all_tables, table_qualifier=qualifier) is None:
                    result.add_error(
                        "invalid_column",
                        f"Kolon bulunamadı: '{col_name}' (tablo: {table.name}).",
                        field="select_columns",
                    )

    def _check_filter_columns(
        self, plan: QueryPlan, table: TableMetadata, result: ValidationResult,
        all_tables: dict[str, TableMetadata] | None = None,
    ) -> None:
        if all_tables is None:
            all_tables = {table.name.upper(): table}
        for filt in plan.filters:
            if self._looks_like_expression(filt.column):
                continue  # SQL expression column — skip validation
            bare, qualifier = self._strip_qualifier(filt.column)
            effective_table = filt.table or qualifier
            bare = self._normalize_column_identifier(
                bare,
                table_name=effective_table or table.name,
            )
            if self._resolve_col_multi(
                bare, table, all_tables, table_qualifier=effective_table,
            ) is None:
                result.add_error(
                    "invalid_column",
                    f"Filtre kolonu bulunamadı: '{filt.column}' (tablo: {table.name}).",
                    field="filters",
                )

    def _check_aggregate_columns(
        self, plan: QueryPlan, table: TableMetadata, result: ValidationResult,
        all_tables: dict[str, TableMetadata] | None = None,
    ) -> None:
        if all_tables is None:
            all_tables = {table.name.upper(): table}
        for agg in plan.aggregations:
            if agg.column == STAR_COLUMN:
                continue  # COUNT(*) doesn't reference a real column
            if self._looks_like_expression(agg.column):
                continue
            bare, qualifier = self._strip_qualifier(agg.column)
            effective_table = agg.table or qualifier
            bare = self._normalize_column_identifier(
                bare,
                table_name=effective_table or table.name,
            )
            if self._resolve_col_multi(
                bare,
                table,
                all_tables,
                table_qualifier=effective_table,
            ) is None:
                result.add_error(
                    "invalid_column",
                    f"Aggregate kolonu bulunamadı: '{agg.column}' (tablo: {table.name}).",
                    field="aggregations",
                )

    def _check_group_by_columns(
        self, plan: QueryPlan, table: TableMetadata, result: ValidationResult,
        all_tables: dict[str, TableMetadata] | None = None,
    ) -> None:
        if all_tables is None:
            all_tables = {table.name.upper(): table}
        for col_name in plan.group_by:
            if self._looks_like_expression(col_name):
                continue  # SQL expression — skip catalog validation
            bare, qualifier = self._strip_qualifier(col_name)
            bare = self._normalize_column_identifier(
                bare,
                table_name=qualifier or table.name,
            )
            if self._resolve_col_multi(bare, table, all_tables, table_qualifier=qualifier) is None:
                result.add_error(
                    "invalid_column",
                    f"GROUP BY kolonu bulunamadı: '{col_name}' (tablo: {table.name}).",
                    field="group_by",
                )

    def _check_partition_by_columns(
        self, plan: QueryPlan, table: TableMetadata, result: ValidationResult,
        all_tables: dict[str, TableMetadata] | None = None,
    ) -> None:
        if all_tables is None:
            all_tables = {table.name.upper(): table}
        for col_name in plan.partition_by:
            if self._looks_like_expression(col_name):
                continue
            bare, qualifier = self._strip_qualifier(col_name)
            bare = self._normalize_column_identifier(
                bare,
                table_name=qualifier or table.name,
            )
            if self._resolve_col_multi(bare, table, all_tables, table_qualifier=qualifier) is None:
                result.add_error(
                    "invalid_column",
                    f"PARTITION BY kolonu bulunamadı: '{col_name}' (tablo: {table.name}).",
                    field="partition_by",
                )

    def _check_order_by_columns(
        self, plan: QueryPlan, table: TableMetadata, result: ValidationResult,
        all_tables: dict[str, TableMetadata] | None = None,
    ) -> None:
        if all_tables is None:
            all_tables = {table.name.upper(): table}
        agg_aliases = self._aggregate_alias_set(plan)

        for spec in plan.order_by:
            if self._looks_like_expression(spec.column):
                continue  # SQL expression in ORDER BY — skip validation
            bare, qualifier = self._strip_qualifier(spec.column)
            effective_table = spec.table or qualifier
            bare = self._normalize_column_identifier(
                bare,
                table_name=effective_table or table.name,
            )
            # Accept both table columns and aggregate aliases for ORDER BY.
            is_table_col = self._resolve_col_multi(
                bare, table, all_tables, table_qualifier=effective_table,
            ) is not None
            is_agg_alias = casefold_tr(spec.column) in agg_aliases
            if not is_table_col and not is_agg_alias:
                result.add_error(
                    "invalid_column",
                    f"ORDER BY kolonu/alias bulunamadı: '{spec.column}' (tablo: {table.name}).",
                    field="order_by",
                )

    def _check_restricted_columns(
        self, plan: QueryPlan, table: TableMetadata, result: ValidationResult,
        all_tables: dict[str, TableMetadata] | None = None,
    ) -> None:
        if all_tables is None:
            all_tables = {table.name.upper(): table}
        # Collect restricted columns from all tables
        restricted: set[str] = set()
        for meta in all_tables.values():
            restricted.update(meta.restricted_column_names())
        if not restricted:
            return

        all_referenced: set[str] = set()

        for col_name in plan.select_columns:
            if self._looks_like_expression(col_name):
                continue
            bare, qualifier = self._strip_qualifier(col_name)
            bare = self._normalize_column_identifier(
                bare,
                table_name=qualifier or table.name,
            )
            resolved = self._resolve_col_multi(col_name, table, all_tables)
            if resolved is None:
                resolved = self._resolve_col_multi(bare, table, all_tables, table_qualifier=qualifier)
            if resolved:
                all_referenced.add(resolved)

        for filt in plan.filters:
            if self._looks_like_expression(filt.column):
                continue
            bare, qualifier = self._strip_qualifier(filt.column)
            effective_table = filt.table or qualifier
            bare = self._normalize_column_identifier(
                bare,
                table_name=effective_table or table.name,
            )
            resolved = self._resolve_col_multi(
                bare,
                table,
                all_tables,
                table_qualifier=effective_table,
            )
            if resolved:
                all_referenced.add(resolved)

        for agg in plan.aggregations:
            if agg.column == STAR_COLUMN:
                continue
            if self._looks_like_expression(agg.column):
                continue
            bare, qualifier = self._strip_qualifier(agg.column)
            effective_table = agg.table or qualifier
            bare = self._normalize_column_identifier(
                bare,
                table_name=effective_table or table.name,
            )
            resolved = self._resolve_col_multi(
                bare,
                table,
                all_tables,
                table_qualifier=effective_table,
            )
            if resolved:
                all_referenced.add(resolved)

        for col_name in plan.group_by:
            if self._looks_like_expression(col_name):
                continue
            bare, qualifier = self._strip_qualifier(col_name)
            bare = self._normalize_column_identifier(
                bare,
                table_name=qualifier or table.name,
            )
            resolved = self._resolve_col_multi(bare, table, all_tables, table_qualifier=qualifier)
            if resolved:
                all_referenced.add(resolved)

        for spec in plan.order_by:
            if self._looks_like_expression(spec.column):
                continue
            bare, qualifier = self._strip_qualifier(spec.column)
            effective_table = spec.table or qualifier
            bare = self._normalize_column_identifier(
                bare,
                table_name=effective_table or table.name,
            )
            resolved = self._resolve_col_multi(
                bare,
                table,
                all_tables,
                table_qualifier=effective_table,
            )
            if resolved:
                all_referenced.add(resolved)

        violations = all_referenced & restricted
        for col_name in sorted(violations):
            result.add_error(
                "restricted_column",
                f"Kısıtlı kolon kullanılamaz: '{col_name}'.",
                field=col_name,
            )

    # -- Aggregate / group-by consistency ---------------------------------

    def _check_aggregate_consistency(
        self, plan: QueryPlan, table: TableMetadata, result: ValidationResult,
        all_tables: dict[str, TableMetadata] | None = None,
    ) -> None:
        if all_tables is None:
            all_tables = {table.name.upper(): table}
        if not plan.aggregations:
            return

        if not plan.select_columns:
            return  # aggregate-only query, no consistency issue

        # Resolve group_by to canonical names for comparison.
        group_canonical: set[str] = set()
        for g in plan.group_by:
            if self._looks_like_expression(g):
                continue
            bare, qualifier = self._strip_qualifier(g)
            bare = self._normalize_column_identifier(
                bare,
                table_name=qualifier or table.name,
            )
            resolved = self._resolve_col_multi(bare, table, all_tables, table_qualifier=qualifier)
            if resolved:
                group_canonical.add(casefold_tr(resolved))

        # For every non-aggregate select column, its canonical name
        # must appear in the group_by canonical set.
        for col in plan.select_columns:
            if self._looks_like_expression(col):
                continue  # SQL expression in SELECT — skip consistency check
            bare, qualifier = self._strip_qualifier(col)
            bare = self._normalize_column_identifier(
                bare,
                table_name=qualifier or table.name,
            )
            resolved = self._resolve_col_multi(bare, table, all_tables, table_qualifier=qualifier)
            canonical = casefold_tr(resolved) if resolved else casefold_tr(bare)

            if canonical not in group_canonical:
                if plan.group_by:
                    result.add_error(
                        "aggregate_select_mismatch",
                        (
                            f"Aggregate sorguda '{col}' kolonu GROUP BY içinde "
                            f"yer almalıdır veya SELECT'ten çıkarılmalıdır."
                        ),
                        field="select_columns",
                    )
                else:
                    result.add_error(
                        "aggregate_select_mismatch",
                        (
                            f"Aggregate sorguda SELECT'te '{col}' kolonu var "
                            f"ancak GROUP BY belirtilmemiş."
                        ),
                        field="select_columns",
                    )

    # -- Limit ------------------------------------------------------------

    def _check_limit(self, plan: QueryPlan, result: ValidationResult) -> None:
        if plan.limit > settings.max_row_limit:
            result.add_error(
                "invalid_limit",
                f"Limit değeri üst sınırı aşıyor (max: {settings.max_row_limit}).",
                field="limit",
            )
