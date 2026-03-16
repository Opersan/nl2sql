"""Oracle real-DB smoke plan — q_101 through q_109.

This script does NOT connect to Oracle.
It runs the full pipeline up to (but not including) the executor,
producing compiled SQL + bind params for each question, then prints
a per-question readiness assessment.

Usage
-----
    .\.venv\Scripts\python scripts\oracle_smoke_plan.py

Output
------
* Per-question: compiled SQL, bind params, required tables/columns,
  and a READY / WARN / BLOCK verdict with rationale.
* Saves full JSON to data/oracle_smoke_plan.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    ForeignKeyMetadata,
    RelationshipMetadata,
    TableMetadata,
)
from app.domain.query_plan import QueryPlan
from app.providers.catalog.base import CatalogProvider
from app.providers.llm.mock_llm import MockLLMProvider
from app.domain.execution_models import ValidationResult
from app.services.catalog_service import CatalogService
from app.services.planner_service import PlannerService
from app.services.session_service import SessionService
from app.services.sql_compiler import SQLCompiler
from app.services.validation_service import ValidationService

# ---------------------------------------------------------------------------
# Reuse PO catalog from po_e2e_smoke (inline to avoid import-path coupling)
# ---------------------------------------------------------------------------

def _col(name: str, dtype: ColumnType = ColumnType.NUMBER, **kw) -> ColumnMetadata:
    return ColumnMetadata(name=name, data_type=dtype, **kw)


def _po_catalog() -> CatalogSnapshot:
    ph = TableMetadata(
        name="PO_HEADERS_ALL",
        description="Satınalma siparişi başlıkları",
        aliases=["po headers", "po_headers"],
        primary_key=["po_header_id"],
        columns=[
            _col("po_header_id", nullable=False),
            _col("vendor_id"),
            _col("creation_date", ColumnType.DATE),
            _col("authorization_status", ColumnType.VARCHAR),
            _col("currency_code", ColumnType.VARCHAR),
            _col("type_lookup_code", ColumnType.VARCHAR),
        ],
    )
    pl = TableMetadata(
        name="PO_LINES_ALL",
        description="Satınalma siparişi kalemleri",
        aliases=["po lines", "po_lines"],
        primary_key=["po_line_id"],
        foreign_keys=[ForeignKeyMetadata(column="po_header_id", referenced_table="PO_HEADERS_ALL", referenced_column="po_header_id")],
        columns=[
            _col("po_line_id", nullable=False),
            _col("po_header_id"),
            _col("item_id"),
            _col("line_num"),
            _col("item_description", ColumnType.VARCHAR),
            _col("quantity"),
            _col("unit_price"),
        ],
    )
    pll = TableMetadata(
        name="PO_LINE_LOCATIONS_ALL",
        description="Sevkiyat lokasyonları",
        aliases=["po shipments", "po_line_locations"],
        primary_key=["line_location_id"],
        foreign_keys=[ForeignKeyMetadata(column="po_line_id", referenced_table="PO_LINES_ALL", referenced_column="po_line_id")],
        columns=[
            _col("line_location_id", nullable=False),
            _col("po_line_id"),
            _col("quantity_received"),
            _col("quantity_billed"),
        ],
    )
    pd_ = TableMetadata(
        name="PO_DISTRIBUTIONS_ALL",
        description="Dağıtım satırları",
        aliases=["po distributions"],
        primary_key=["po_distribution_id"],
        foreign_keys=[ForeignKeyMetadata(column="line_location_id", referenced_table="PO_LINE_LOCATIONS_ALL", referenced_column="line_location_id")],
        columns=[
            _col("po_distribution_id", nullable=False),
            _col("line_location_id"),
            _col("quantity_ordered"),
            _col("code_combination_id"),
            _col("unit_price"),
        ],
    )
    mtl = TableMetadata(
        name="MTL_SYSTEM_ITEMS_B",
        description="Malzeme ana verileri",
        aliases=["items", "malzeme"],
        primary_key=["inventory_item_id"],
        columns=[
            _col("inventory_item_id", nullable=False),
            _col("segment1", ColumnType.VARCHAR),
            _col("description", ColumnType.VARCHAR),
        ],
    )
    rels = [
        RelationshipMetadata(from_table="PO_HEADERS_ALL", from_column="po_header_id", to_table="PO_LINES_ALL", to_column="po_header_id"),
        RelationshipMetadata(from_table="PO_LINES_ALL", from_column="po_line_id", to_table="PO_LINE_LOCATIONS_ALL", to_column="po_line_id"),
        RelationshipMetadata(from_table="PO_LINE_LOCATIONS_ALL", from_column="line_location_id", to_table="PO_DISTRIBUTIONS_ALL", to_column="line_location_id"),
        RelationshipMetadata(from_table="PO_LINES_ALL", from_column="item_id", to_table="MTL_SYSTEM_ITEMS_B", to_column="inventory_item_id"),
    ]
    return CatalogSnapshot(tables=[ph, pl, pll, pd_, mtl], relationships=rels)


class _PoCatalogProvider(CatalogProvider):
    def __init__(self) -> None:
        self._snapshot = _po_catalog()

    async def get_snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    async def get_table(self, name_or_alias: str) -> TableMetadata | None:
        return self._snapshot.get_table(name_or_alias)

    async def search_tables(self, query: str) -> list[TableMetadata]:
        return self._snapshot.search_tables(query)


# ---------------------------------------------------------------------------
# Risk metadata per question
# ---------------------------------------------------------------------------

# Per-question static risk assessment (independent of compiled SQL).
_RISK: dict[str, dict[str, Any]] = {
    "q_101": {
        "expected_intent": "po_open_orders",
        "required_tables": ["PO_HEADERS_ALL"],
        "required_columns": ["po_header_id", "vendor_id", "creation_date", "authorization_status"],
        "restricted_field_risk": "LOW — authorization_status is a status flag, no PII.",
        "empty_result_risk": "LOW — open orders almost always exist in EBS.",
        "runtime_risk": "LOW — single table, filter on indexed status column.",
    },
    "q_102": {
        "expected_intent": "po_unapproved",
        "required_tables": ["PO_HEADERS_ALL"],
        "required_columns": ["po_header_id", "vendor_id", "creation_date", "authorization_status"],
        "restricted_field_risk": "LOW.",
        "empty_result_risk": "MEDIUM — UAT may have all POs pre-approved.",
        "runtime_risk": "LOW — same shape as q_101.",
    },
    "q_103": {
        "expected_intent": "po_open_orders",
        "required_tables": ["PO_HEADERS_ALL"],
        "required_columns": ["po_header_id", "vendor_id", "creation_date", "authorization_status"],
        "restricted_field_risk": "LOW.",
        "empty_result_risk": "LOW.",
        "runtime_risk": "LOW.",
    },
    "q_104": {
        "expected_intent": "po_vendor_count",
        "required_tables": ["PO_HEADERS_ALL"],
        "required_columns": ["vendor_id"],
        "restricted_field_risk": "LOW — vendor_id is a surrogate key.",
        "empty_result_risk": "VERY LOW.",
        "runtime_risk": "LOW — GROUP BY on indexed vendor_id.",
    },
    "q_105": {
        "expected_intent": "po_line_quantity",
        "required_tables": ["PO_HEADERS_ALL", "PO_LINES_ALL"],
        "required_columns": ["po_header_id", "po_line_id", "line_num", "item_description", "quantity"],
        "restricted_field_risk": "LOW — item_description may contain internal product names.",
        "empty_result_risk": "VERY LOW.",
        "runtime_risk": "MEDIUM — JOIN across two large EBS tables; ROWNUM cap applied.",
    },
    "q_106": {
        "expected_intent": "po_pending_delivery",
        "required_tables": ["PO_HEADERS_ALL", "PO_LINES_ALL", "PO_LINE_LOCATIONS_ALL"],
        "required_columns": ["po_line_id", "line_num", "item_description", "quantity", "quantity_received"],
        "restricted_field_risk": "LOW.",
        "empty_result_risk": "MEDIUM — quantity_received < quantity filter may return 0 rows in fully-received UAT data.",
        "runtime_risk": "MEDIUM — three-table JOIN; __COLUMN_REF__quantity is a column-to-column comparison (no bind param used for that comparison).",
        "notes": "The __COLUMN_REF__ filter compiles to p3.quantity_received < p2.quantity — a column comparison, not a bind param. SQLGuard does not need to validate it as a value.",
    },
    "q_107": {
        "expected_intent": "po_distribution_amount",
        "required_tables": ["PO_HEADERS_ALL", "PO_LINES_ALL", "PO_LINE_LOCATIONS_ALL", "PO_DISTRIBUTIONS_ALL"],
        "required_columns": ["code_combination_id", "quantity_ordered", "unit_price", "quantity"],
        "restricted_field_risk": "LOW — code_combination_id is an accounting code, not PII.",
        "empty_result_risk": "LOW.",
        "runtime_risk": "HIGH — four-table JOIN across large EBS tables. Timeout risk if PO_DISTRIBUTIONS_ALL has millions of rows. ROWNUM=100 cap applied.",
    },
    "q_108": {
        "expected_intent": "po_item_line_count",
        "required_tables": ["PO_HEADERS_ALL", "PO_LINES_ALL", "MTL_SYSTEM_ITEMS_B"],
        "required_columns": ["item_id", "inventory_item_id", "segment1", "description"],
        "restricted_field_risk": "LOW.",
        "empty_result_risk": "LOW.",
        "runtime_risk": "MEDIUM — MTL_SYSTEM_ITEMS_B can be large; JOIN via item_id must be indexed.",
    },
    "q_109": {
        "expected_intent": "po_last_30_days",
        "required_tables": ["PO_HEADERS_ALL"],
        "required_columns": ["po_header_id", "vendor_id", "creation_date", "authorization_status", "currency_code"],
        "restricted_field_risk": "LOW.",
        "empty_result_risk": "HIGH — UAT database may not have recent data within last 30 days.",
        "runtime_risk": "LOW — single table, date filter on indexed creation_date. __EXPR__TRUNC(SYSDATE)-30 compiles to a literal Oracle expression.",
        "notes": "__EXPR__ compiles to raw SQL literal TRUNC(SYSDATE)-30. Valid Oracle syntax, no bind param needed.",
    },
}

# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

def _verdict(qid: str, status: str, sql: str | None) -> tuple[str, str]:
    """Return (READY|WARN|BLOCK, rationale)."""
    if status != "success":
        return "BLOCK", f"Pipeline status={status} — check validation or compiler errors."
    risk = _RISK.get(qid, {})
    empty_risk = risk.get("empty_result_risk", "")
    runtime_risk = risk.get("runtime_risk", "")
    if "HIGH" in runtime_risk:
        return "WARN", f"Runtime risk HIGH ({runtime_risk}). Test with ROWNUM limit first."
    if "HIGH" in empty_risk:
        return "WARN", f"Empty-result risk HIGH ({empty_risk}). UAT data may not match filter."
    return "READY", "SQL compiled and validated. Risk LOW-MEDIUM."


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

QUESTIONS = [
    {"id": "q_101", "nl": "Açık satınalma siparişlerini listele"},
    {"id": "q_102", "nl": "Onaysız bekleyen PO'ları getir"},
    {"id": "q_103", "nl": "Kapatılmamış PO'ları göster"},
    {"id": "q_104", "nl": "Tedarikçiye göre PO sayısı"},
    {"id": "q_105", "nl": "Kalem bazında sipariş miktarı"},
    {"id": "q_106", "nl": "Teslim bekleyen satırları göster"},
    {"id": "q_107", "nl": "Dağıtım bazında tutar analizi"},
    {"id": "q_108", "nl": "Ürün bazında PO satırları"},
    {"id": "q_109", "nl": "Son 30 günde açılan PO'lar"},
]


async def _run() -> None:
    catalog_provider = _PoCatalogProvider()
    catalog_service = CatalogService(catalog_provider)
    llm = MockLLMProvider()
    planner = PlannerService(llm, catalog_service)
    validation = ValidationService(catalog_service)
    compiler = SQLCompiler()
    sessions = SessionService()

    results: list[dict] = []
    print("\n  Oracle Real-DB Smoke Plan — q_101 to q_109")
    print("  " + "=" * 68)
    print(f"  {'ID':<8} {'Verdict':<8} {'Intent':<30} {'Risk'}")
    print("  " + "-" * 68)

    for q in QUESTIONS:
        session_id = f"smoke_{q['id']}"
        entry: dict[str, Any] = {"id": q["id"], "nl": q["nl"]}

        try:
            plan: QueryPlan = await planner.plan(q["nl"])

            # Validation
            vr: ValidationResult = await validation.validate(plan)
            if not vr.ok:
                entry.update({
                    "status": "validation_error",
                    "errors": [i.message for i in vr.errors],
                })
                verdict, rationale = "BLOCK", f"Validation: {[i.message for i in vr.errors]}"
            else:
                compiled = compiler.compile(
                    plan,
                    vr.resolved_table,
                    extra_tables=vr.resolved_tables or None,
                )
                entry.update({
                    "status": "success",
                    "semantic_intent": plan.semantic_intent,
                    "root_table": plan.table,
                    "required_tables": _RISK.get(q["id"], {}).get("required_tables", [plan.table]),
                    "required_columns": _RISK.get(q["id"], {}).get("required_columns", plan.select_columns),
                    "join_count": len(plan.joins),
                    "filter_count": len(plan.filters),
                    "aggregation_count": len(plan.aggregations),
                    "sql": compiled.sql,
                    "bind_params": {k: "***" for k in compiled.params},
                    "bind_param_count": len(compiled.params),
                    "restricted_field_risk": _RISK.get(q["id"], {}).get("restricted_field_risk", "UNKNOWN"),
                    "empty_result_risk": _RISK.get(q["id"], {}).get("empty_result_risk", "UNKNOWN"),
                    "runtime_risk": _RISK.get(q["id"], {}).get("runtime_risk", "UNKNOWN"),
                    "notes": _RISK.get(q["id"], {}).get("notes", ""),
                })
                verdict, rationale = _verdict(q["id"], "success", compiled.sql)

        except Exception as exc:
            entry.update({"status": "exception", "error": str(exc)})
            verdict, rationale = "BLOCK", str(exc)

        entry["verdict"] = verdict
        entry["rationale"] = rationale
        results.append(entry)

        runtime_label = _RISK.get(q["id"], {}).get("runtime_risk", "")[:4]
        print(
            f"  {q['id']:<8} {verdict:<8} "
            f"{entry.get('semantic_intent', '?'):<30} "
            f"runtime={runtime_label}"
        )

    # Summary
    verdicts = [r["verdict"] for r in results]
    print("  " + "=" * 68)
    print(
        f"  READY:{verdicts.count('READY')}  "
        f"WARN:{verdicts.count('WARN')}  "
        f"BLOCK:{verdicts.count('BLOCK')}"
    )
    print()

    # Detailed SQL output
    for r in results:
        print(f"  [{r['id']}] {r['nl']}")
        if r.get("sql"):
            for line in r["sql"].split("\n"):
                print(f"    {line}")
        if r.get("errors"):
            print(f"    ERRORS: {r['errors']}")
        if r.get("notes"):
            print(f"    NOTE: {r['notes']}")
        print(f"    Verdict: {r['verdict']} — {r['rationale']}")
        print()

    # Save
    out_path = os.path.join(_ROOT, "data", "oracle_smoke_plan.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  Results saved → {out_path}")


if __name__ == "__main__":
    asyncio.run(_run())
