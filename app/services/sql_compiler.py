"""SQL compiler – converts a validated QueryPlan into Oracle-compatible SQL.

Design goals
============
* **Deterministic** – same plan always produces the same SQL.
* **Bind parameters** – named params ``:p1``, ``:p2``, … (no string
  interpolation of user values).
* **Oracle syntax** – legacy ``ROWNUM`` subquery wrapping for row
  limiting (compatible with Oracle 10g+).
* **No ``SELECT *``** – the plan must list explicit columns.

Multi-table support (Sprint 5)
==============================
When the plan contains ``joins``, the compiler produces JOIN clauses and
qualifies column references with table aliases.  Table aliases are the
first letter of the table name in lowercase (e.g. ``e`` for EMPLOYEE).
If there is a collision, a numeric suffix is appended.
"""

from __future__ import annotations

import re
from datetime import date
from collections.abc import Callable

from app.core.exceptions import CompilationError
from app.domain.catalog_models import TableMetadata
from app.domain.execution_models import CompiledQuery
from app.domain.query_plan import (
    STAR_COLUMN,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    JoinSpec,
    JoinType,
    OrderSpec,
    QueryPlan,
)
from app.utils.turkish import casefold_tr


# ------------------------------------------------------------------
# Filter-value placeholder constants & helpers
# ------------------------------------------------------------------

_COLUMN_REF_PREFIX = "__COLUMN_REF__"
_EXPR_PREFIX = "__EXPR__"

_RELATIVE_DATE_SQL_RE = re.compile(
    r"^(?:TRUNC\(\s*SYSDATE\s*\)|SYSDATE|CURRENT_DATE)\s*-\s*(\d+)\s*$",
    re.IGNORECASE,
)

_EXTRACT_YEAR_RE = re.compile(
    r"^EXTRACT\s*\(\s*YEAR\s+FROM\s+([A-Za-z_][A-Za-z0-9_\.]+)\s*\)$",
    re.IGNORECASE,
)

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _normalize_relative_date_sql(expr: str) -> str:
    """Normalize relative date expressions to Oracle-safe canonical form."""
    raw = expr.strip()
    m = _RELATIVE_DATE_SQL_RE.match(raw)
    if m:
        return f"TRUNC(SYSDATE)-{m.group(1)}"
    return raw


def _normalize_expression_sql(
    expr: str,
    resolve_col: Callable[[str], str],
) -> str:
    """Normalize supported SQL expressions to canonical Oracle form."""
    raw = _normalize_relative_date_sql(expr)

    # EXTRACT(YEAR FROM date_column) -> TO_CHAR(date_column,'YYYY')
    m = _EXTRACT_YEAR_RE.match(raw)
    if m:
        date_col = m.group(1)
        return f"TO_CHAR({resolve_col(date_col)},'YYYY')"

    return raw


def _render_filter_value(
    value: object,
    resolve_col: Callable[[str], str] | None = None,
) -> tuple[str | None, object]:
    """Detect and expand placeholder filter values.

    Returns ``(raw_sql, None)`` for placeholders that must be inlined as
    literal SQL, or ``(None, original_value)`` for values that should be
    emitted as bind parameters.

    Supported placeholders
    ----------------------
    ``__COLUMN_REF__<name>``
        Resolved to a column reference via *resolve_col*.  No bind param.
    ``__EXPR__<sql>``
        Inlined as raw SQL.  No bind param and no quoting.
    """
    if isinstance(value, str):
        if value.startswith(_COLUMN_REF_PREFIX):
            col_name = value[len(_COLUMN_REF_PREFIX):]
            if resolve_col is not None:
                return resolve_col(col_name), None
            return col_name, None  # fallback: emit identifier as-is
        if value.startswith(_EXPR_PREFIX):
            raw = value[len(_EXPR_PREFIX):]
            return _normalize_relative_date_sql(raw), None
    return None, value


# ------------------------------------------------------------------
# Expression registry + expansion helper
# ------------------------------------------------------------------

# Maps expression_ref -> SQL template with plain column-name operands.
# Column names are resolved to aliased references at compile time.
_EXPRESSION_REGISTRY: dict[str, str] = {
    "PO_LINE_AMOUNT": "quantity * unit_price",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _expand_expression(
    template: str,
    table_hint: str | None,
    resolve_col: Callable[[str, str | None], str],
) -> str:
    """Expand column-name identifiers in a SQL expression template.

    Each identifier-looking token is resolved via
    ``resolve_col(token, table_hint)``; operators and other non-identifier
    characters are preserved verbatim.

    Example
    -------
    ``"quantity * unit_price"`` with table_hint ``"PO_LINES_ALL"`` and a
    multi-table resolver becomes ``"p2.quantity * p2.unit_price"``.
    """
    parts = re.split(r"(\W+)", template)
    out: list[str] = []
    for part in parts:
        if part and _IDENTIFIER_RE.match(part):
            out.append(resolve_col(part, table_hint))
        else:
            out.append(part)
    return "".join(out)


def _coerce_date_bind(value: object) -> object:
    """Convert ISO date strings to python date for Oracle DATE binds."""
    if not isinstance(value, str):
        return value
    m = _ISO_DATE_RE.match(value.strip())
    if not m:
        return value
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return value


def _coerce_bind_for_column(value: object, meta: TableMetadata | None, column_name: str) -> object:
    """Coerce bind values for DATE/TIMESTAMP columns.

    Keeps non-date columns untouched.
    """
    if meta is None:
        return value
    col_meta = meta.get_column(column_name)
    if col_meta is None:
        return value
    if col_meta.data_type.value not in {"DATE", "TIMESTAMP"}:
        return value

    if isinstance(value, list):
        return [_coerce_date_bind(v) for v in value]
    return _coerce_date_bind(value)


# ------------------------------------------------------------------
# Table alias generator
# ------------------------------------------------------------------

def _generate_aliases(table_names: list[str]) -> dict[str, str]:
    """Generate short aliases for a list of table names.

    Uses the first letter of the table name (lowercased).
    On collision, appends a numeric suffix (e2, d2, etc.).
    """
    used: dict[str, int] = {}
    aliases: dict[str, str] = {}
    for name in table_names:
        base = name[0].lower() if name else "t"
        count = used.get(base, 0) + 1
        used[base] = count
        alias = base if count == 1 else f"{base}{count}"
        aliases[name.upper()] = alias
    return aliases


class SQLCompiler:
    """Compile a ``QueryPlan`` into an Oracle-compatible ``CompiledQuery``."""

    def compile(
        self,
        plan: QueryPlan,
        table: TableMetadata,
        *,
        extra_tables: dict[str, TableMetadata] | None = None,
    ) -> CompiledQuery:
        """Produce a ``CompiledQuery`` from a validated *plan*.

        Parameters
        ----------
        plan:
            The query plan (assumed already validated).
        table:
            Resolved primary table metadata.
        extra_tables:
            Optional mapping of {TABLE_NAME: TableMetadata} for JOINed
            tables.  Required when the plan has joins.
        """
        if plan.is_multi_table and extra_tables:
            return self._compile_multi(plan, table, extra_tables)
        return self._compile_single(plan, table)

    # ------------------------------------------------------------------
    # Single-table compilation (existing logic)
    # ------------------------------------------------------------------

    def _compile_single(
        self, plan: QueryPlan, table: TableMetadata,
    ) -> CompiledQuery:
        params: dict[str, object] = {}
        param_counter = _ParamCounter()

        select_clause = self._build_select(plan, table)
        from_clause = f"FROM {table.name}"
        where_clause = self._build_where(plan, table, params, param_counter)
        group_by_clause = self._build_group_by(plan, table)
        order_by_clause = self._build_order_by(plan, table)

        parts = [select_clause, from_clause]
        if where_clause:
            parts.append(where_clause)
        if group_by_clause:
            parts.append(group_by_clause)
        if order_by_clause:
            parts.append(order_by_clause)

        inner_sql = "\n".join(parts)
        sql = self._wrap_with_rownum(inner_sql, plan, params, param_counter)
        selected = self._selected_column_names(plan, table)
        column_map = self._build_column_map(plan, table)

        return CompiledQuery(
            sql=sql,
            params=params,
            table=table.name,
            selected_columns=selected,
            debug_plan=plan,
            column_map=column_map,
        )

    # ------------------------------------------------------------------
    # Multi-table compilation (Sprint 5)
    # ------------------------------------------------------------------

    def _compile_multi(
        self,
        plan: QueryPlan,
        primary_table: TableMetadata,
        extra_tables: dict[str, TableMetadata],
    ) -> CompiledQuery:
        """Compile a multi-table plan with JOINs."""
        params: dict[str, object] = {}
        param_counter = _ParamCounter()

        # Build full table map and aliases
        all_tables: dict[str, TableMetadata] = {
            primary_table.name.upper(): primary_table,
        }
        all_tables.update({k.upper(): v for k, v in extra_tables.items()})

        ordered_names = [primary_table.name.upper()]
        for js in plan.joins:
            for tname in (js.left_table.upper(), js.right_table.upper()):
                if tname not in ordered_names:
                    ordered_names.append(tname)

        aliases = _generate_aliases(ordered_names)

        # Helper to resolve a column with optional table qualifier
        def _resolve_multi(col: str, table_name: str | None = None) -> str:
            if col == STAR_COLUMN:
                return STAR_COLUMN
            if '(' in col:
                return _normalize_expression_sql(
                    col,
                    resolve_col=lambda c: _resolve_multi(c, table_name),
                )
            if table_name:
                tname = table_name.upper()
                meta = all_tables.get(tname)
                if meta:
                    canonical = meta.resolve_column_name(col)
                    if canonical:
                        alias = aliases.get(tname, tname.lower())
                        return f"{alias}.{canonical}"
            # Try all tables and detect ambiguous ownership early.
            primary_name = primary_table.name.upper()
            matches: list[tuple[str, str]] = []
            for tname, meta in all_tables.items():
                canonical = meta.resolve_column_name(col)
                if canonical:
                    matches.append((tname, canonical))

            if len(matches) == 1:
                tname, canonical = matches[0]
                alias = aliases.get(tname, tname.lower())
                return f"{alias}.{canonical}"

            if len(matches) > 1:
                primary_match = next(((t, c) for (t, c) in matches if t == primary_name), None)
                primary_prefix = primary_name.split("_")[0].lower()
                if primary_match is not None and col.lower().startswith(f"{primary_prefix}_"):
                    alias = aliases.get(primary_name, primary_name.lower())
                    return f"{alias}.{primary_match[1]}"
                owners = ", ".join(t for t, _ in matches)
                raise CompilationError(
                    f"Kolon referansi belirsiz: '{col}' birden fazla tabloda mevcut ({owners}). "
                    "Table qualifier kullanin veya repair pass table ownership atasin."
                )

            raise CompilationError(
                f"Kolon cozumlenemedi: '{col}' (multi-table plan)."
            )

        # SELECT
        expressions: list[str] = []
        if plan.aggregations and plan.group_by:
            for col in plan.group_by:
                expressions.append(_resolve_multi(col))
        for agg in plan.aggregations:
            col_ref = (
                STAR_COLUMN
                if agg.column == STAR_COLUMN
                else _resolve_multi(agg.column, agg.table)
            )
            alias_name = agg.effective_alias()
            expressions.append(f"{agg.function.value}({col_ref}) AS {alias_name}")
        if not plan.aggregations:
            if not plan.select_columns:
                raise CompilationError("SELECT kolon listesi boş ve aggregate yok.")
            for col in plan.select_columns:
                expressions.append(_resolve_multi(col))
        # Computed measures — expression_ref resolved via _EXPRESSION_REGISTRY
        for cm in plan.computed_measures:
            raw_expr = _EXPRESSION_REGISTRY.get(cm.expression_ref)
            if raw_expr is None:
                raise CompilationError(
                    f"Bilinmeyen expression_ref: '{cm.expression_ref}'. "
                    f"Desteklenen: {sorted(_EXPRESSION_REGISTRY)}"
                )
            resolved = _expand_expression(raw_expr, cm.table, _resolve_multi)
            alias_name = cm.alias or cm.name
            expressions.append(f"({resolved}) AS {alias_name}")
        if not expressions:
            raise CompilationError("SELECT ifadesi boş.")

        select_clause = f"SELECT {', '.join(expressions)}"

        # FROM + JOINs
        primary_alias = aliases.get(primary_table.name.upper(), "t")
        from_clause = f"FROM {primary_table.name} {primary_alias}"

        join_clauses: list[str] = []
        for js in plan.joins:
            join_kw = {
                JoinType.INNER: "INNER JOIN",
                JoinType.LEFT: "LEFT JOIN",
                JoinType.RIGHT: "RIGHT JOIN",
            }.get(js.join_type, "INNER JOIN")

            right_upper = js.right_table.upper()
            right_alias = aliases.get(right_upper, right_upper.lower())

            on_parts: list[str] = []
            for cond in js.on:
                la = aliases.get(cond.left_table.upper(), cond.left_table.lower())
                ra = aliases.get(cond.right_table.upper(), cond.right_table.lower())
                on_parts.append(
                    f"{la}.{cond.left_column} = {ra}.{cond.right_column}"
                )

            join_clauses.append(
                f"{join_kw} {js.right_table} {right_alias} ON {' AND '.join(on_parts)}"
            )

        # WHERE
        where_parts: list[str] = []
        for filt in plan.filters:
            col_ref = _resolve_multi(filt.column, filt.table)
            bind_filt = filt
            target_meta: TableMetadata | None = None
            if filt.table:
                target_meta = all_tables.get(filt.table.upper())
            if target_meta is None:
                matches = [m for m in all_tables.values() if m.resolve_column_name(filt.column) is not None]
                if len(matches) == 1:
                    target_meta = matches[0]
            if target_meta is not None:
                canonical = target_meta.resolve_column_name(filt.column)
                if canonical is not None:
                    coerced = _coerce_bind_for_column(filt.value, target_meta, canonical)
                    if coerced is not filt.value:
                        bind_filt = filt.model_copy(update={"value": coerced})
            clause = self._filter_clause_raw(
                col_ref, bind_filt, params, param_counter,
                resolve_col=lambda c: _resolve_multi(c),
            )
            where_parts.append(clause)
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        # GROUP BY
        group_by_clause = ""
        if plan.group_by:
            gp = [_resolve_multi(c) for c in plan.group_by]
            group_by_clause = f"GROUP BY {', '.join(gp)}"

        # ORDER BY
        order_by_clause = ""
        if plan.order_by:
            agg_alias_map: dict[str, str] = {
                casefold_tr(agg.effective_alias()): agg.effective_alias()
                for agg in plan.aggregations
            }
            ob_parts: list[str] = []
            for spec in plan.order_by:
                matched_alias = agg_alias_map.get(casefold_tr(spec.column))
                if matched_alias is not None:
                    ob_parts.append(f"{matched_alias} {spec.direction.value}")
                else:
                    col_ref = _resolve_multi(spec.column, spec.table)
                    if plan.aggregations and plan.group_by:
                        group_refs = {_resolve_multi(c) for c in plan.group_by}
                        if col_ref not in group_refs:
                            continue
                    ob_parts.append(f"{col_ref} {spec.direction.value}")
            order_by_clause = f"ORDER BY {', '.join(ob_parts)}"

        # Assemble
        parts = [select_clause, from_clause] + join_clauses
        if where_clause:
            parts.append(where_clause)
        if group_by_clause:
            parts.append(group_by_clause)
        if order_by_clause:
            parts.append(order_by_clause)

        inner_sql = "\n".join(parts)
        sql = self._wrap_with_rownum(inner_sql, plan, params, param_counter)

        # Selected column names
        selected: list[str] = []
        if plan.aggregations and plan.group_by:
            for c in plan.group_by:
                selected.append(c)
        for agg in plan.aggregations:
            selected.append(agg.effective_alias())
        if not plan.aggregations:
            for c in plan.select_columns:
                selected.append(c)
        for cm in plan.computed_measures:
            selected.append(cm.alias or cm.name)

        return CompiledQuery(
            sql=sql,
            params=params,
            table=primary_table.name,
            selected_columns=selected,
            debug_plan=plan,
            column_map={},
        )

    # ------------------------------------------------------------------
    # Raw filter clause (used by multi-table compiler)
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_clause_raw(
        col_ref: str,
        filt: FilterSpec,
        params: dict[str, object],
        counter: _ParamCounter,
        *,
        resolve_col: Callable[[str], str] | None = None,
    ) -> str:
        """Render a filter clause from a pre-resolved column reference.

        *resolve_col* is used to expand ``__COLUMN_REF__<name>`` placeholder
        values into qualified column references.  When omitted the identifier
        is emitted as-is.  ``__EXPR__<sql>`` values are always inlined as raw
        SQL regardless of *resolve_col*.
        """
        if filt.op == FilterOp.IS_NULL:
            return f"{col_ref} IS NULL"
        if filt.op == FilterOp.IS_NOT_NULL:
            return f"{col_ref} IS NOT NULL"
        if filt.op == FilterOp.BETWEEN:
            lo_name = counter.next()
            hi_name = counter.next()
            params[lo_name] = filt.value[0]
            params[hi_name] = filt.value[1]
            return f"{col_ref} BETWEEN :{lo_name} AND :{hi_name}"
        if filt.op == FilterOp.IN:
            names: list[str] = []
            for val in filt.value:
                pname = counter.next()
                params[pname] = val
                names.append(f":{pname}")
            return f"{col_ref} IN ({', '.join(names)})"
        # Standard binary operator — check for value placeholders first
        raw_sql, bind_val = _render_filter_value(filt.value, resolve_col)
        if raw_sql is not None:
            return f"{col_ref} {filt.op.value} {raw_sql}"
        pname = counter.next()
        params[pname] = bind_val
        return f"{col_ref} {filt.op.value} :{pname}"

    # ------------------------------------------------------------------
    # SELECT
    # ------------------------------------------------------------------

    def _build_select(self, plan: QueryPlan, table: TableMetadata) -> str:
        expressions: list[str] = []

        # group_by columns come first (if present alongside aggregates)
        if plan.aggregations and plan.group_by:
            for col in plan.group_by:
                canonical = self._resolve(col, table)
                expressions.append(canonical)

        # aggregate expressions
        for agg in plan.aggregations:
            expressions.append(self._agg_expr(agg, table))

        # plain select columns (only if no aggregations, or select_columns
        # is used alongside group_by – already guarded by validation).
        if not plan.aggregations:
            if not plan.select_columns:
                raise CompilationError("SELECT kolon listesi boş ve aggregate yok.")
            for col in plan.select_columns:
                canonical = self._resolve(col, table)
                expressions.append(canonical)

        if not expressions:
            raise CompilationError("SELECT ifadesi boş.")

        return f"SELECT {', '.join(expressions)}"

    # ------------------------------------------------------------------
    # WHERE
    # ------------------------------------------------------------------

    def _build_where(
        self,
        plan: QueryPlan,
        table: TableMetadata,
        params: dict[str, object],
        counter: _ParamCounter,
    ) -> str:
        if not plan.filters:
            return ""

        clauses: list[str] = []
        for filt in plan.filters:
            clauses.append(self._filter_clause(filt, table, params, counter))

        return "WHERE " + " AND ".join(clauses)

    def _filter_clause(
        self,
        filt: FilterSpec,
        table: TableMetadata,
        params: dict[str, object],
        counter: _ParamCounter,
    ) -> str:
        col = self._resolve(filt.column, table)

        if filt.op == FilterOp.IS_NULL:
            return f"{col} IS NULL"

        if filt.op == FilterOp.IS_NOT_NULL:
            return f"{col} IS NOT NULL"

        if filt.op == FilterOp.BETWEEN:
            lo_name = counter.next()
            hi_name = counter.next()
            params[lo_name] = filt.value[0]
            params[hi_name] = filt.value[1]
            return f"{col} BETWEEN :{lo_name} AND :{hi_name}"

        if filt.op == FilterOp.IN:
            names: list[str] = []
            for val in filt.value:
                pname = counter.next()
                params[pname] = val
                names.append(f":{pname}")
            return f"{col} IN ({', '.join(names)})"

        # Standard binary operator (=, !=, <, <=, >, >=, LIKE) — check placeholders
        raw_sql, bind_val = _render_filter_value(
            filt.value, resolve_col=lambda c: self._resolve(c, table),
        )
        if raw_sql is not None:
            return f"{col} {filt.op.value} {raw_sql}"
        pname = counter.next()
        params[pname] = bind_val
        return f"{col} {filt.op.value} :{pname}"

    # ------------------------------------------------------------------
    # GROUP BY
    # ------------------------------------------------------------------

    def _build_group_by(self, plan: QueryPlan, table: TableMetadata) -> str:
        if not plan.group_by:
            return ""
        cols = [self._resolve(c, table) for c in plan.group_by]
        return f"GROUP BY {', '.join(cols)}"

    # ------------------------------------------------------------------
    # ORDER BY
    # ------------------------------------------------------------------

    def _build_order_by(self, plan: QueryPlan, table: TableMetadata) -> str:
        if not plan.order_by:
            return ""

        # Aggregate aliases are valid ORDER BY targets alongside table columns.
        # Use casefold_tr for case-insensitive matching, consistent with
        # ValidationService._check_order_by_columns.
        agg_alias_map: dict[str, str] = {
            casefold_tr(agg.effective_alias()): agg.effective_alias()
            for agg in plan.aggregations
        }

        parts: list[str] = []
        group_cols: set[str] = set()
        if plan.aggregations and plan.group_by:
            group_cols = {self._resolve(c, table) for c in plan.group_by}
        for spec in plan.order_by:
            matched_alias = agg_alias_map.get(casefold_tr(spec.column))
            if matched_alias is not None:
                # Use the canonical alias form in the SQL output.
                parts.append(f"{matched_alias} {spec.direction.value}")
            else:
                col = self._resolve(spec.column, table)
                if plan.aggregations and plan.group_by and col not in group_cols:
                    continue
                parts.append(f"{col} {spec.direction.value}")
        if not parts:
            return ""
        return f"ORDER BY {', '.join(parts)}"

    # ------------------------------------------------------------------
    # LIMIT (Oracle legacy ROWNUM wrapping)
    # ------------------------------------------------------------------

    @staticmethod
    def _wrap_with_rownum(
        inner_sql: str,
        plan: QueryPlan,
        params: dict[str, object],
        counter: _ParamCounter,
    ) -> str:
        """Wrap *inner_sql* with ``SELECT * FROM (...) WHERE ROWNUM <= :pN``.

        The limit value is emitted as a bind parameter for consistency and
        safety.  ORDER BY stays inside the inner query so that ROWNUM is
        applied **after** sorting – the correct Oracle legacy pattern.
        """
        pname = counter.next()
        params[pname] = plan.limit
        return (
            f"SELECT *\nFROM (\n{inner_sql}\n)\nWHERE ROWNUM <= :{pname}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve(column_name_or_alias: str, table: TableMetadata) -> str:
        """Resolve to canonical column name; raise on miss."""
        if '(' in column_name_or_alias:
            return _normalize_expression_sql(
                column_name_or_alias,
                resolve_col=lambda c: SQLCompiler._resolve(c, table),
            )
        raw = column_name_or_alias.strip()
        if '.' in raw:
            raw = raw.split('.', 1)[1].strip()
        canonical = table.resolve_column_name(raw)
        if canonical is None:
            raise CompilationError(
                f"Kolon çözümlenemedi: '{column_name_or_alias}' (tablo: {table.name})."
            )
        return canonical

    def _agg_expr(self, agg: AggregationSpec, table: TableMetadata) -> str:
        col = STAR_COLUMN if agg.column == STAR_COLUMN else self._resolve(agg.column, table)
        alias = agg.effective_alias()
        return f"{agg.function.value}({col}) AS {alias}"

    @staticmethod
    def _build_column_map(
        plan: QueryPlan, table: TableMetadata
    ) -> dict[str, str]:
        """Map every plan-level column reference to its canonical name."""
        mapping: dict[str, str] = {}

        all_refs: list[str] = []
        all_refs.extend(plan.select_columns)
        all_refs.extend(f.column for f in plan.filters)
        all_refs.extend(
            a.column for a in plan.aggregations if a.column != STAR_COLUMN
        )
        all_refs.extend(plan.group_by)
        all_refs.extend(o.column for o in plan.order_by)

        for ref in all_refs:
            if ref not in mapping:
                canonical = table.resolve_column_name(ref)
                if canonical is not None:
                    mapping[ref] = canonical
        return mapping

    def _selected_column_names(
        self, plan: QueryPlan, table: TableMetadata
    ) -> list[str]:
        """Return the list of column names / aliases that will appear in output."""
        names: list[str] = []
        if plan.aggregations and plan.group_by:
            for c in plan.group_by:
                names.append(self._resolve(c, table))
        for agg in plan.aggregations:
            names.append(agg.effective_alias())
        if not plan.aggregations:
            for c in plan.select_columns:
                names.append(self._resolve(c, table))
        return names


# ------------------------------------------------------------------
# Internal helper
# ------------------------------------------------------------------

class _ParamCounter:
    """Sequential parameter name generator: p1, p2, p3, …"""

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> str:  # noqa: A003
        self._n += 1
        return f"p{self._n}"
