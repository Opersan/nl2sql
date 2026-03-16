"""PO domain end-to-end smoke runner — q_101 through q_109.

Pipeline tested per question
-----------------------------
  Planner → semantic normalization → validation → compiler → executor → narrator

Infrastructure used
--------------------
* **LLM (planner + narrator)**: Real provider when LLM_PROVIDER=openai_compatible
  is set; falls back to MockLLMProvider otherwise.
* **Catalog**: Synthetic in-process PO catalog (all 5 tables + full columns).
  No real Oracle DB connection required.
* **Executor**: PoMockExecutor — returns synthetic PO rows; decoupled from
  EMPLOYEE-only MockExecutor.

Usage
-----
    # With real LLM:
    $env:LLM_PROVIDER="openai_compatible"
    $env:PLANNER_PROMPT_MAX_CHARS="22000"
    .\.venv\Scripts\python scripts\po_e2e_smoke.py

    # With mock LLM (offline):
    .\.venv\Scripts\python scripts\po_e2e_smoke.py

Output
------
Prints a status table + per-question detail block.
Saves full JSON to data/po_e2e_results.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
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
from app.domain.execution_models import (
    CompiledQuery,
    ExecutionResult,
    ExecutionStatus,
)
from app.domain.query_plan import QueryPlan
from app.providers.catalog.base import CatalogProvider
from app.providers.executor.base import ExecutorProvider
from app.providers.llm.base import LLMProvider
from app.services.catalog_service import CatalogService
from app.services.narrator_service import NarratorService
from app.services.orchestrator import ChatOrchestrator, Orchestrator
from app.services.planner_service import PlannerService
from app.services.session_service import SessionService
from app.services.sql_compiler import SQLCompiler
from app.services.validation_service import ValidationService


# ---------------------------------------------------------------------------
# Synthetic PO catalog
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


class PoCatalogProvider(CatalogProvider):
    """In-process PO catalog provider backed by synthetic metadata."""

    def __init__(self) -> None:
        self._snapshot = _po_catalog()

    async def get_snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    async def get_table(self, name_or_alias: str) -> TableMetadata | None:
        return self._snapshot.get_table(name_or_alias)

    async def search_tables(self, query: str) -> list[TableMetadata]:
        return self._snapshot.search_tables(query)


# ---------------------------------------------------------------------------
# Synthetic PO executor — returns fake but structurally-correct rows
# ---------------------------------------------------------------------------

_PO_ROWS: list[dict[str, Any]] = [
    {"po_header_id": 10001, "vendor_id": 501, "creation_date": "2026-02-10", "authorization_status": "APPROVED",  "currency_code": "TRY", "type_lookup_code": "STANDARD"},
    {"po_header_id": 10002, "vendor_id": 502, "creation_date": "2026-02-22", "authorization_status": "INCOMPLETE", "currency_code": "USD", "type_lookup_code": "STANDARD"},
    {"po_header_id": 10003, "vendor_id": 501, "creation_date": "2026-03-01", "authorization_status": "APPROVED",  "currency_code": "TRY", "type_lookup_code": "BLANKET"},
    {"po_header_id": 10004, "vendor_id": 503, "creation_date": "2026-01-15", "authorization_status": "CLOSED",    "currency_code": "EUR", "type_lookup_code": "STANDARD"},
]

_PO_LINE_ROWS: list[dict[str, Any]] = [
    {"po_line_id": 20001, "po_header_id": 10001, "item_id": 1, "line_num": 1, "item_description": "Kırtasiye", "quantity": 100, "unit_price": 5.0},
    {"po_line_id": 20002, "po_header_id": 10001, "item_id": 2, "line_num": 2, "item_description": "Bilgisayar", "quantity": 10,  "unit_price": 15000.0},
    {"po_line_id": 20003, "po_header_id": 10002, "item_id": 3, "line_num": 1, "item_description": "Yazıcı",    "quantity": 5,   "unit_price": 3200.0},
    {"po_line_id": 20004, "po_header_id": 10003, "item_id": 1, "line_num": 1, "item_description": "Kırtasiye", "quantity": 200, "unit_price": 4.8},
]

_PO_SHIPMENT_ROWS: list[dict[str, Any]] = [
    {"line_location_id": 30001, "po_line_id": 20001, "quantity_received": 80,  "quantity_billed": 80},
    {"line_location_id": 30002, "po_line_id": 20002, "quantity_received": 8,   "quantity_billed": 8},
    {"line_location_id": 30003, "po_line_id": 20003, "quantity_received": 0,   "quantity_billed": 0},
    {"line_location_id": 30004, "po_line_id": 20004, "quantity_received": 100, "quantity_billed": 100},
]

_PO_DIST_ROWS: list[dict[str, Any]] = [
    {"po_distribution_id": 40001, "line_location_id": 30001, "quantity_ordered": 100, "code_combination_id": 9001, "unit_price": 5.0},
    {"po_distribution_id": 40002, "line_location_id": 30002, "quantity_ordered": 10,  "code_combination_id": 9002, "unit_price": 15000.0},
    {"po_distribution_id": 40003, "line_location_id": 30003, "quantity_ordered": 5,   "code_combination_id": 9001, "unit_price": 3200.0},
    {"po_distribution_id": 40004, "line_location_id": 30004, "quantity_ordered": 200, "code_combination_id": 9003, "unit_price": 4.8},
]

_MTL_ROWS: list[dict[str, Any]] = [
    {"inventory_item_id": 1, "segment1": "KRT-001", "description": "Kırtasiye"},
    {"inventory_item_id": 2, "segment1": "BLG-001", "description": "Bilgisayar"},
    {"inventory_item_id": 3, "segment1": "YZC-001", "description": "Yazıcı"},
]


class PoMockExecutor(ExecutorProvider):
    """Returns synthetic PO rows — plan-agnostic pass-through.

    Rather than re-implementing SQL evaluation for PO columns, this executor
    returns all rows for the primary table (or a cross-join) so that the
    pipeline downstream (narrator) can generate a plausible response.

    The goal is to verify that the pipeline *doesn't crash*, not to return
    production-accurate query results.
    """

    _TABLE_DATA: dict[str, list[dict[str, Any]]] = {
        "PO_HEADERS_ALL":       _PO_ROWS,
        "PO_LINES_ALL":         _PO_LINE_ROWS,
        "PO_LINE_LOCATIONS_ALL": _PO_SHIPMENT_ROWS,
        "PO_DISTRIBUTIONS_ALL": _PO_DIST_ROWS,
        "MTL_SYSTEM_ITEMS_B":   _MTL_ROWS,
    }

    async def execute(self, compiled_query: CompiledQuery) -> ExecutionResult:
        table_upper = compiled_query.table.upper()
        rows_source = self._TABLE_DATA.get(table_upper, [])

        # Project only the selected columns (best-effort, skip unknowns)
        output_rows: list[dict[str, Any]] = []
        for row in rows_source[: (compiled_query.debug_plan.limit if compiled_query.debug_plan else 100)]:
            projected = {c: row.get(c) for c in (compiled_query.selected_columns or list(row.keys()))}
            output_rows.append(projected)

        if not output_rows:
            return ExecutionResult(
                status=ExecutionStatus.EMPTY,
                columns=compiled_query.selected_columns,
                rows=[],
                row_count=0,
            )

        return ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            columns=compiled_query.selected_columns,
            rows=output_rows,
            row_count=len(output_rows),
        )


# ---------------------------------------------------------------------------
# LLM provider selection
# ---------------------------------------------------------------------------

def _build_llm() -> LLMProvider:
    provider = os.getenv("LLM_PROVIDER", "mock")
    if provider == "openai_compatible":
        from app.providers.llm.openai_compatible import OpenAICompatibleProvider
        from app.core.config import settings
        return OpenAICompatibleProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
    from app.providers.llm.mock_llm import MockLLMProvider
    return MockLLMProvider()


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------

def _build_pipeline() -> ChatOrchestrator:
    llm = _build_llm()
    catalog_provider = PoCatalogProvider()
    catalog_service = CatalogService(catalog_provider)
    planner = PlannerService(llm, catalog_service)
    validation = ValidationService(catalog_service)
    compiler = SQLCompiler()
    executor = PoMockExecutor()
    orchestrator = Orchestrator(validation, compiler, executor)
    narrator = NarratorService(llm)
    sessions = SessionService()
    return ChatOrchestrator(planner, orchestrator, narrator, sessions)


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

QUESTIONS: list[dict] = [
    {"id": "q_101", "nl": "Açık satınalma siparişlerini listele",   "expected_intent": "po_open_orders"},
    {"id": "q_102", "nl": "Onaysız bekleyen PO'ları getir",          "expected_intent": "po_unapproved"},
    {"id": "q_103", "nl": "Kapatılmamış PO'ları göster",             "expected_intent": "po_open_orders"},
    {"id": "q_104", "nl": "Tedarikçiye göre PO sayısı",              "expected_intent": "po_vendor_count"},
    {"id": "q_105", "nl": "Kalem bazında sipariş miktarı",           "expected_intent": "po_line_quantity"},
    {"id": "q_106", "nl": "Teslim bekleyen satırları göster",        "expected_intent": "po_pending_delivery"},
    {"id": "q_107", "nl": "Dağıtım bazında tutar analizi",           "expected_intent": "po_distribution_amount"},
    {"id": "q_108", "nl": "Ürün bazında PO satırları",               "expected_intent": "po_item_line_count"},
    {"id": "q_109", "nl": "Son 30 günde açılan PO'lar",              "expected_intent": "po_last_30_days"},
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_one(
    chat: ChatOrchestrator,
    q: dict,
    session_id: str,
) -> dict:
    """Run a single question through the full pipeline and return a result dict."""
    t0 = time.monotonic()
    try:
        result = await chat.handle_message(session_id, q["nl"])
        elapsed = round((time.monotonic() - t0) * 1000)
        return {
            "id": q["id"],
            "nl": q["nl"],
            "expected_intent": q["expected_intent"],
            "status": result.status,
            "semantic_intent": result.plan.semantic_intent if result.plan else None,
            "root_table": result.plan.table if result.plan else None,
            "needs_clarification": result.plan.needs_clarification if result.plan else None,
            "select_columns": result.plan.select_columns if result.plan else None,
            "joins": [j.right_table for j in result.plan.joins] if result.plan else None,
            "filters_count": len(result.plan.filters) if result.plan else None,
            "aggregations_count": len(result.plan.aggregations) if result.plan else None,
            "sql": result.sql,
            "answer": result.answer,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "elapsed_ms": elapsed,
        }
    except Exception as exc:
        elapsed = round((time.monotonic() - t0) * 1000)
        return {
            "id": q["id"],
            "nl": q["nl"],
            "expected_intent": q["expected_intent"],
            "status": "pipeline_exception",
            "error_message": str(exc),
            "elapsed_ms": elapsed,
        }


async def main() -> None:
    llm_mode = os.getenv("LLM_PROVIDER", "mock")
    print(f"\n{'='*72}")
    print(f"  PO End-to-End Smoke  |  LLM mode: {llm_mode.upper()}")
    print(f"{'='*72}\n")

    chat = _build_pipeline()
    results: list[dict] = []

    for q in QUESTIONS:
        print(f"  Running {q['id']}: {q['nl']} …", end="", flush=True)
        rec = await run_one(chat, q, session_id=f"smoke_{q['id']}")
        results.append(rec)
        intent_ok = "✓" if rec.get("semantic_intent") == q["expected_intent"] else "✗"
        print(f"\r  {q['id']}  {rec['status']:<18}  intent {intent_ok}  {rec.get('elapsed_ms', '?')} ms")

    # Summary table
    print(f"\n{'─'*72}")
    print(f"  {'ID':<8} {'Status':<20} {'Intent':<28} {'SQL?':<5} {'Ans?':<5}")
    print(f"{'─'*72}")
    counters: dict[str, int] = {}
    for r in results:
        st = r["status"]
        counters[st] = counters.get(st, 0) + 1
        si = r.get("semantic_intent") or "—"
        sql_ok = "yes" if r.get("sql") else "—"
        ans_ok = "yes" if r.get("answer") else "—"
        marker = "✓" if st == "success" else ("⚠" if st in ("empty_result", "clarification") else "✗")
        print(f"  {r['id']:<8} {marker} {st:<18}  {si:<26}  {sql_ok:<5}  {ans_ok}")

    print(f"{'─'*72}")
    total = len(results)
    success = counters.get("success", 0)
    print(f"  SCORE: {success}/{total} success  |  {counters}")

    # Per-question detail
    print(f"\n{'='*72}")
    print("  DETAIL")
    print(f"{'='*72}")
    for r in results:
        print(f"\n  [{r['id']}] {r['nl']}")
        print(f"    semantic_intent : {r.get('semantic_intent')}")
        print(f"    root_table      : {r.get('root_table')}")
        print(f"    select_columns  : {r.get('select_columns')}")
        print(f"    joins           : {r.get('joins')}")
        print(f"    filters         : {r.get('filters_count')}")
        print(f"    aggregations    : {r.get('aggregations_count')}")
        print(f"    status          : {r['status']}")
        if r.get("error_message"):
            print(f"    error           : {r['error_message']}")
        if r.get("sql"):
            first_lines = r["sql"].split("\n")[:6]
            print(f"    sql (first 6L)  : {' | '.join(l.strip() for l in first_lines)}")
        if r.get("answer"):
            ans_preview = r["answer"][:120].replace("\n", " ")
            print(f"    narrator answer : {ans_preview}")

    # Narrator security check — scan all answers for SQL keywords
    print(f"\n{'─'*40}")
    print("  NARRATOR SECURITY SCAN")
    sql_leak_keywords = ["SELECT", "FROM", "WHERE", "JOIN", "INSERT", "UPDATE", "DELETE"]
    leaked = []
    for r in results:
        ans = r.get("answer") or ""
        found = [kw for kw in sql_leak_keywords if kw in ans.upper()]
        if found:
            leaked.append(f"{r['id']}: {found}")
    if leaked:
        print(f"  ⚠ SQL LEAKED in narrator output: {leaked}")
    else:
        print("  ✓ No SQL leakage detected in narrator answers")

    # Save JSON
    out_path = os.path.join(_ROOT, "data", "po_e2e_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  Results saved → {out_path}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
