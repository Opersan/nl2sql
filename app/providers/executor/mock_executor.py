"""Mock executor – deterministic in-memory query execution for Sprint 1.

The mock executor operates on a small hard-coded dataset that mirrors the
``XXBT_PDKS_PER_DETAILS_V`` table defined in the in-memory catalog provider.
It inspects the ``CompiledQuery.debug_plan`` (the original ``QueryPlan``) to
apply basic filtering, column selection, ordering and row limiting **without**
parsing SQL.

Design notes
=============
* **Canonical columns** – the executor uses ``CompiledQuery.column_map``
  (populated by the compiler) to translate plan-level column aliases to
  canonical column names before matching against the in-memory dataset.
* **COUNT semantics** – ``COUNT(*)`` counts all rows in the group whereas
  ``COUNT(column)`` counts only rows where the column value is not NULL,
  matching standard SQL semantics.
* **STAR_COLUMN** – the ``STAR_COLUMN`` sentinel from ``query_plan.py`` is
  explicitly handled in ``_apply_aggregations``.
* **LIKE** – the mock LIKE implementation uses *case-insensitive substring
  matching* after stripping ``%`` wildcards.  This is a **simplified
  approximation** of SQL LIKE and does **not** support ``_`` (single-char
  wildcard), escape characters or anchored patterns.  See
  ``TestMockLikeSemantics`` in ``test_mock_executor.py`` for the exact
  behavioural contract.
* **Unsupported filter ops** – if a ``FilterOp`` is added to the enum but
  not yet handled by ``_match``, the executor raises ``ExecutionError``
  instead of silently passing the row through.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

from app.core.exceptions import ExecutionError
from app.domain.execution_models import (
    CompiledQuery,
    ExecutionResult,
    ExecutionStatus,
)
from app.domain.query_plan import (
    STAR_COLUMN,
    AggregateFn,
    FilterOp,
    FilterSpec,
    QueryPlan,
)
from app.providers.executor.base import ExecutorProvider


# ---------------------------------------------------------------------------
# In-memory demo dataset  (mirrors XXBT_PDKS_PER_DETAILS_V)
# ---------------------------------------------------------------------------

_EMPLOYEE_ROWS: list[dict[str, Any]] = [
    {
        "PERSON_ID": 1001,
        "SICIL_NO": "S1001",
        "AD": "Ahmet",
        "SOYAD": "Yılmaz",
        "FULL_NAME": "Ahmet Yılmaz",
        "BIRIM_ADI": "Bilgi Teknolojileri",
        "ORGANIZATION_ADI": "Genel Müdürlük",
        "LOCATION_ADI": "İstanbul",
        "UNVAN": "Yazılım Uzmanı",
        "GOREV_TANIMI": "Backend Developer",
        "ISE_GIRIS_TARIHI": date(2018, 3, 15),
        "CIKIS_TARIHI": None,
        "EMAIL": "ahmet.yilmaz@example.com",
        "BORDROLU": 1,
        "STAJYER": 0,
        "MASRAF_MERKEZI": "BT-01",
        "DOGUM_TARIHI": date(1990, 6, 10),
    },
    {
        "PERSON_ID": 1002,
        "SICIL_NO": "S1002",
        "AD": "Ayşe",
        "SOYAD": "Demir",
        "FULL_NAME": "Ayşe Demir",
        "BIRIM_ADI": "İnsan Kaynakları",
        "ORGANIZATION_ADI": "Genel Müdürlük",
        "LOCATION_ADI": "İstanbul",
        "UNVAN": "İK Uzmanı",
        "GOREV_TANIMI": "HR Specialist",
        "ISE_GIRIS_TARIHI": date(2019, 7, 1),
        "CIKIS_TARIHI": None,
        "EMAIL": "ayse.demir@example.com",
        "BORDROLU": 1,
        "STAJYER": 0,
        "MASRAF_MERKEZI": "IK-01",
        "DOGUM_TARIHI": date(1992, 11, 22),
    },
    {
        "PERSON_ID": 1003,
        "SICIL_NO": "S1003",
        "AD": "Mehmet",
        "SOYAD": "Kaya",
        "FULL_NAME": "Mehmet Kaya",
        "BIRIM_ADI": "Bilgi Teknolojileri",
        "ORGANIZATION_ADI": "Genel Müdürlük",
        "LOCATION_ADI": "Ankara",
        "UNVAN": "Proje Yöneticisi",
        "GOREV_TANIMI": "Project Manager",
        "ISE_GIRIS_TARIHI": date(2015, 1, 10),
        "CIKIS_TARIHI": None,
        "EMAIL": "mehmet.kaya@example.com",
        "BORDROLU": 1,
        "STAJYER": 0,
        "MASRAF_MERKEZI": "BT-02",
        "DOGUM_TARIHI": date(1985, 4, 5),
    },
    {
        "PERSON_ID": 1004,
        "SICIL_NO": "S1004",
        "AD": "Fatma",
        "SOYAD": "Çelik",
        "FULL_NAME": "Fatma Çelik",
        "BIRIM_ADI": "Muhasebe",
        "ORGANIZATION_ADI": "Genel Müdürlük",
        "LOCATION_ADI": "İstanbul",
        "UNVAN": "Muhasebe Uzmanı",
        "GOREV_TANIMI": "Accountant",
        "ISE_GIRIS_TARIHI": date(2020, 9, 14),
        "CIKIS_TARIHI": None,
        "EMAIL": "fatma.celik@example.com",
        "BORDROLU": 1,
        "STAJYER": 0,
        "MASRAF_MERKEZI": "MUH-01",
        "DOGUM_TARIHI": date(1995, 8, 30),
    },
    {
        "PERSON_ID": 1005,
        "SICIL_NO": "S1005",
        "AD": "Ali",
        "SOYAD": "Şahin",
        "FULL_NAME": "Ali Şahin",
        "BIRIM_ADI": "Bilgi Teknolojileri",
        "ORGANIZATION_ADI": "Genel Müdürlük",
        "LOCATION_ADI": "İzmir",
        "UNVAN": "Sistem Yöneticisi",
        "GOREV_TANIMI": "System Administrator",
        "ISE_GIRIS_TARIHI": date(2017, 5, 20),
        "CIKIS_TARIHI": date(2023, 12, 31),
        "EMAIL": "ali.sahin@example.com",
        "BORDROLU": 1,
        "STAJYER": 0,
        "MASRAF_MERKEZI": "BT-01",
        "DOGUM_TARIHI": date(1988, 2, 14),
    },
    {
        "PERSON_ID": 1006,
        "SICIL_NO": "S1006",
        "AD": "Zeynep",
        "SOYAD": "Arslan",
        "FULL_NAME": "Zeynep Arslan",
        "BIRIM_ADI": "İnsan Kaynakları",
        "ORGANIZATION_ADI": "Genel Müdürlük",
        "LOCATION_ADI": "İstanbul",
        "UNVAN": "İK Müdürü",
        "GOREV_TANIMI": "HR Manager",
        "ISE_GIRIS_TARIHI": date(2012, 3, 1),
        "CIKIS_TARIHI": None,
        "EMAIL": "zeynep.arslan@example.com",
        "BORDROLU": 1,
        "STAJYER": 0,
        "MASRAF_MERKEZI": "IK-01",
        "DOGUM_TARIHI": date(1982, 9, 18),
    },
    {
        "PERSON_ID": 1007,
        "SICIL_NO": "S1007",
        "AD": "Emre",
        "SOYAD": "Öztürk",
        "FULL_NAME": "Emre Öztürk",
        "BIRIM_ADI": "Muhasebe",
        "ORGANIZATION_ADI": "Genel Müdürlük",
        "LOCATION_ADI": "İstanbul",
        "UNVAN": "Mali İşler Müdürü",
        "GOREV_TANIMI": "Finance Manager",
        "ISE_GIRIS_TARIHI": date(2014, 11, 5),
        "CIKIS_TARIHI": None,
        "EMAIL": "emre.ozturk@example.com",
        "BORDROLU": 1,
        "STAJYER": 0,
        "MASRAF_MERKEZI": "MUH-01",
        "DOGUM_TARIHI": date(1984, 12, 1),
    },
    {
        "PERSON_ID": 1008,
        "SICIL_NO": "S1008",
        "AD": "Elif",
        "SOYAD": "Koç",
        "FULL_NAME": "Elif Koç",
        "BIRIM_ADI": "Hukuk",
        "ORGANIZATION_ADI": "Genel Müdürlük",
        "LOCATION_ADI": "Ankara",
        "UNVAN": "Avukat",
        "GOREV_TANIMI": "Legal Counsel",
        "ISE_GIRIS_TARIHI": date(2021, 6, 15),
        "CIKIS_TARIHI": date(2024, 6, 30),
        "EMAIL": "elif.koc@example.com",
        "BORDROLU": 1,
        "STAJYER": 0,
        "MASRAF_MERKEZI": "HUK-01",
        "DOGUM_TARIHI": date(1993, 3, 25),
    },
    {
        "PERSON_ID": 1009,
        "SICIL_NO": "S1009",
        "AD": "Can",
        "SOYAD": "Ak",
        "FULL_NAME": "Can Ak",
        "BIRIM_ADI": "Bilgi Teknolojileri",
        "ORGANIZATION_ADI": "Genel Müdürlük",
        "LOCATION_ADI": "İstanbul",
        "UNVAN": "Stajyer",
        "GOREV_TANIMI": "Intern Developer",
        "ISE_GIRIS_TARIHI": date(2024, 2, 1),
        "CIKIS_TARIHI": None,
        "EMAIL": "can.ak@example.com",
        "BORDROLU": 0,
        "STAJYER": 1,
        "MASRAF_MERKEZI": "BT-03",
        "DOGUM_TARIHI": date(2003, 7, 12),
    },
]


def _coerce_date(row_val: object, filt_val: object) -> object:
    """Coerce *filt_val* to the same type as *row_val* when *row_val* is a date.

    Handles ISO strings (``"2024-01-01"``) and sentinel tokens like
    ``"__RELATIVE_DATE_LAST_6_MONTHS__"`` by resolving them relative to
    ``date.today()``.  Any unrecognised token is returned unchanged.
    """
    if not isinstance(row_val, date):
        return filt_val
    if isinstance(filt_val, date):
        return filt_val
    if isinstance(filt_val, str):
        today = date.today()
        _SENTINELS: dict[str, date] = {
            "__RELATIVE_DATE_LAST_30_DAYS__": today - timedelta(days=30),
            "__RELATIVE_DATE_LAST_6_MONTHS__": today - timedelta(days=183),
            "__RELATIVE_DATE_LAST_1_YEAR__": today - timedelta(days=365),
            "__RELATIVE_DATE_1_YEAR__": today - timedelta(days=365),
            "__RELATIVE_DATE_10_YEARS_AGO__": today - timedelta(days=3650),
        }
        if filt_val in _SENTINELS:
            return _SENTINELS[filt_val]
        try:
            return date.fromisoformat(filt_val)
        except ValueError:
            pass
    return filt_val


class MockExecutor(ExecutorProvider):
    """In-memory executor for testing and Sprint 1 smoke tests.

    Uses the ``debug_plan`` attached to the ``CompiledQuery`` to apply
    basic filtering, column selection, ordering and row limiting.

    Column resolution
    -----------------
    Plan-level column references (which may be aliases like ``sicil_no``)
    are translated to canonical names (``SICIL_NO``) via the ``column_map``
    dictionary carried by ``CompiledQuery``.  This keeps the executor
    decoupled from catalog metadata while ensuring row lookups succeed.
    """

    def __init__(self, dataset: list[dict[str, Any]] | None = None) -> None:
        self._data = dataset if dataset is not None else list(_EMPLOYEE_ROWS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, compiled_query: CompiledQuery) -> ExecutionResult:
        start = time.monotonic_ns()
        try:
            plan = compiled_query.debug_plan
            col_map = compiled_query.column_map

            rows = self._filter(plan, col_map)

            # Aggregation must run BEFORE ordering/projection – aggregate
            # functions need access to original columns (e.g. reg_no for
            # COUNT) and ORDER BY may reference aggregate aliases.
            if plan and plan.aggregations:
                rows = self._apply_aggregations(rows, plan, col_map)
            else:
                rows = self._project(rows, compiled_query.selected_columns)

            rows = self._order(rows, plan, col_map)
            rows = self._limit(rows, plan)

            elapsed_ms = (time.monotonic_ns() - start) // 1_000_000

            if not rows:
                return ExecutionResult(
                    status=ExecutionStatus.EMPTY,
                    columns=compiled_query.selected_columns,
                    rows=[],
                    row_count=0,
                    execution_time_ms=elapsed_ms,
                )

            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                columns=compiled_query.selected_columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic_ns() - start) // 1_000_000
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                error_message=str(exc),
                execution_time_ms=elapsed_ms,
            )

    # ------------------------------------------------------------------
    # Column resolution helper
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve(name: str, col_map: dict[str, str]) -> str:
        """Resolve a plan-level column reference to its canonical name.

        Falls back to *name* itself when the map does not contain an entry
        (e.g. aggregate aliases which are not table columns).
        """
        return col_map.get(name, name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _filter(
        self, plan: QueryPlan | None, col_map: dict[str, str]
    ) -> list[dict[str, Any]]:
        if plan is None or not plan.filters:
            return list(self._data)

        result: list[dict[str, Any]] = []
        for row in self._data:
            if all(self._match(row, f, col_map) for f in plan.filters):
                result.append(row)
        return result

    @staticmethod
    def _match(
        row: dict[str, Any], filt: FilterSpec, col_map: dict[str, str]
    ) -> bool:
        """Evaluate a single filter against *row*.

        Uses *col_map* to translate plan-level aliases to canonical column
        names that exist in the dataset.

        Raises ``ExecutionError`` for any ``FilterOp`` not explicitly
        handled – this prevents silent data corruption from newly-added
        operations.
        """
        canonical = col_map.get(filt.column, filt.column)
        val = row.get(canonical)

        if filt.op == FilterOp.IS_NULL:
            return val is None
        if filt.op == FilterOp.IS_NOT_NULL:
            return val is not None
        if filt.op == FilterOp.EQ:
            return val == filt.value
        if filt.op == FilterOp.NEQ:
            return val != filt.value
        if filt.op == FilterOp.LT:
            return val is not None and val < _coerce_date(val, filt.value)
        if filt.op == FilterOp.LTE:
            return val is not None and val <= _coerce_date(val, filt.value)
        if filt.op == FilterOp.GT:
            return val is not None and val > _coerce_date(val, filt.value)
        if filt.op == FilterOp.GTE:
            return val is not None and val >= _coerce_date(val, filt.value)
        if filt.op == FilterOp.LIKE:
            # --- Simplified LIKE semantics (mock only) ---
            # Strips all '%' characters and performs a case-insensitive
            # substring search.  Does NOT handle '_' (single-char wildcard),
            # escape characters, or anchored patterns like 'Ali%' vs '%Ali'.
            if val is None:
                return False
            pattern = str(filt.value).replace("%", "")
            return pattern.lower() in str(val).lower()
        if filt.op == FilterOp.IN:
            return val in (filt.value or [])
        if filt.op == FilterOp.BETWEEN:
            if val is None or not filt.value or len(filt.value) != 2:
                return False
            return filt.value[0] <= val <= filt.value[1]

        # If we reach here, a new FilterOp was added but not handled.
        raise ExecutionError(
            f"Desteklenmeyen filtre operasyonu: {filt.op.value}",
            detail=f"MockExecutor._match does not handle FilterOp.{filt.op.name}.",
        )

    @staticmethod
    def _order(
        rows: list[dict[str, Any]],
        plan: QueryPlan | None,
        col_map: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Sort rows according to plan.order_by.

        For aggregate queries the order_by column may be an aggregate alias
        (e.g. ``cnt``, ``COUNT_reg_no``).  These are NOT in *col_map*
        (they are not table columns) but they ARE present as keys in the
        aggregated result dicts – so the fallback ``col_map.get(c, c)``
        correctly resolves them to themselves.
        """
        if plan is None or not plan.order_by:
            return rows
        for spec in reversed(plan.order_by):
            canonical = col_map.get(spec.column, spec.column)
            reverse = spec.direction.value == "DESC"
            rows = sorted(
                rows,
                key=lambda r, c=canonical: (r.get(c) is None, r.get(c)),
                reverse=reverse,
            )
        return rows

    @staticmethod
    def _limit(
        rows: list[dict[str, Any]], plan: QueryPlan | None
    ) -> list[dict[str, Any]]:
        if plan is None:
            return rows
        return rows[: plan.limit]

    @staticmethod
    def _project(
        rows: list[dict[str, Any]], columns: list[str]
    ) -> list[dict[str, Any]]:
        if not columns:
            return rows
        return [{c: row.get(c) for c in columns if c in row} for row in rows]

    @staticmethod
    def _apply_aggregations(
        rows: list[dict[str, Any]],
        plan: QueryPlan,
        col_map: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Apply aggregate functions.

        COUNT semantics
        ---------------
        * ``COUNT(*)`` (``STAR_COLUMN``) → counts **all rows** in the group
          (including those with NULL values in any column).
        * ``COUNT(column)`` → counts only rows where the column value is
          **not NULL**, matching standard SQL behaviour.
        """
        if not plan.aggregations:
            return rows

        # Resolve group_by aliases to canonical names.
        group_canonical = [col_map.get(c, c) for c in plan.group_by]

        # Group rows.
        if group_canonical:
            groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
            for row in rows:
                key = tuple(row.get(c) for c in group_canonical)
                groups.setdefault(key, []).append(row)
        else:
            groups = {(): rows}

        result: list[dict[str, Any]] = []
        for group_key, group_rows in groups.items():
            out: dict[str, Any] = {}

            # Fill group_by columns (use canonical name as key).
            for idx, canonical in enumerate(group_canonical):
                out[canonical] = group_key[idx]

            # Compute aggregates.
            for agg in plan.aggregations:
                alias = agg.effective_alias()

                if agg.column == STAR_COLUMN:
                    # COUNT(*) – count all rows regardless of NULLs.
                    if agg.function == AggregateFn.COUNT:
                        out[alias] = len(group_rows)
                    else:
                        # Should never happen (validated by AggregationSpec),
                        # but guard defensively.
                        raise ExecutionError(
                            f"{agg.function.value}(*) desteklenmiyor, "
                            f"sadece COUNT(*) kullanılabilir."
                        )
                    continue

                # Resolve aggregate column alias to canonical.
                agg_canonical = col_map.get(agg.column, agg.column)
                values = [
                    r[agg_canonical]
                    for r in group_rows
                    if r.get(agg_canonical) is not None
                ]

                if agg.function == AggregateFn.COUNT:
                    # COUNT(column) – count non-NULL values only.
                    out[alias] = len(values)
                elif agg.function == AggregateFn.SUM:
                    out[alias] = sum(values) if values else 0
                elif agg.function == AggregateFn.AVG:
                    out[alias] = (sum(values) / len(values)) if values else 0
                elif agg.function == AggregateFn.MIN:
                    out[alias] = min(values) if values else None
                elif agg.function == AggregateFn.MAX:
                    out[alias] = max(values) if values else None
                else:
                    raise ExecutionError(
                        f"Desteklenmeyen aggregate fonksiyonu: {agg.function.value}"
                    )

            result.append(out)
        return result
