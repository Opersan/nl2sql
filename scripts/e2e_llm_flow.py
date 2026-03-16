"""End-to-end LLM flow test — 30 natural user questions.

Runs every question through the full ChatOrchestrator pipeline:
    PlannerService -> Orchestrator (validate -> compile -> execute) -> NarratorService

Catalog used: combined PO (5 tables) + XXBT_PDKS_PER_DETAILS_V (1 table).

Outcome classes
---------------
success          -- execution returned rows, narrator answered
empty_result     -- execution returned 0 rows (EMPTY status)
clarification    -- planner requested clarification
validation_error -- ValidationService rejected the plan
compile_error    -- SQLCompiler raised CompilationError
execution_error  -- Oracle / executor raised ExecutionError

Usage
-----
    python scripts/e2e_llm_flow.py                        # real Oracle
    python scripts/e2e_llm_flow.py --no-oracle            # CombinedMockExecutor
    python scripts/e2e_llm_flow.py --report-json data/e2e_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Make the project root importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# 30 natural user questions
# ---------------------------------------------------------------------------

QUESTIONS: list[dict[str, str]] = [
    # -- PO domain (purchase orders) -----------------------------------------
    {"id": "q01", "domain": "PO",  "text": "Onay bekleyen satin alma siparislerini listele"},
    {"id": "q02", "domain": "PO",  "text": "Son 30 gunde olusturulan PO basliklarini goster"},
    {"id": "q03", "domain": "PO",  "text": "Toplam tutari 100.000 TL uzerinde olan siparisler hangileri?"},
    {"id": "q04", "domain": "PO",  "text": "ACME Tedarikciye ait acik siparisleri bul"},
    {"id": "q05", "domain": "PO",  "text": "Iptal edilmis siparisleri getir"},
    {"id": "q06", "domain": "PO",  "text": "Bu ay teslim edilmesi gereken siparisleri listele"},
    {"id": "q07", "domain": "PO",  "text": "PO dagitim tutarlarini kalem kalem goster"},
    {"id": "q08", "domain": "PO",  "text": "Kapali PO basliklarini goster"},
    {"id": "q09", "domain": "PO",  "text": "Birden fazla satiri olan siparisleri listele"},
    {"id": "q10", "domain": "PO",  "text": "Teslim tarihi gecmis siparisleri bul"},
    {"id": "q11", "domain": "PO",  "text": "PO_HEADERS_ALL tablosundaki kayitlari say"},
    {"id": "q12", "domain": "PO",  "text": "Bugün onaylanan siparisleri getir"},
    {"id": "q13", "domain": "PO",  "text": "Dagitim tutari sifir olan kalemleri listele"},
    {"id": "q14", "domain": "PO",  "text": "Tedarikci site kodu BESTI olan siparisleri goster"},
    {"id": "q15", "domain": "PO",  "text": "En son olusturulan 10 siparis kaydi nedir?"},
    # -- Employee domain -------------------------------------------------------
    {"id": "q16", "domain": "EMP", "text": "Aktif calisanlari listele"},
    {"id": "q17", "domain": "EMP", "text": "IT departmanindaki calisanlari goster"},
    {"id": "q18", "domain": "EMP", "text": "Maasi 50.000 TL uzerinde olan calisanlari bul"},
    {"id": "q19", "domain": "EMP", "text": "2024 yilinda ise giren calisanlar kimler?"},
    {"id": "q20", "domain": "EMP", "text": "Yonetici pozisyonundaki calisanlari listele"},
    {"id": "q21", "domain": "EMP", "text": "Istanbul'daki calisanlari say"},
    {"id": "q22", "domain": "EMP", "text": "Son 6 ayda terfi eden calisanlar"},
    {"id": "q23", "domain": "EMP", "text": "Performans notu 4 ve uzeri olan calisanlari getir"},
    {"id": "q24", "domain": "EMP", "text": "Hangi departmanda kac calisan var?"},
    {"id": "q25", "domain": "EMP", "text": "En yuksek maasli 5 calisan kimdir?"},
    # -- Edge / clarification cases --------------------------------------------
    {"id": "q26", "domain": "PO",  "text": "Siparisleri getir"},
    {"id": "q27", "domain": "PO",  "text": "Butun verileri goster"},
    {"id": "q28", "domain": "EMP", "text": "Calisanlar"},
    {"id": "q29", "domain": "PO",  "text": "Gecen yilki PO ozetini cikar"},
    {"id": "q30", "domain": "PO",
     "text": "Son 7 gunde acilan ve toplam tutari 50.000 TL'yi gecen onay bekleyen "
             "PO basliklarini tedarikci adiyla birlikte listele"},
]

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

OUTCOME_CLASSES = (
    "success",
    "empty_result",
    "clarification",
    "validation_error",
    "compile_error",
    "execution_error",
)


@dataclass
class QuestionResult:
    id: str
    domain: str
    question: str
    outcome: str = "unknown"
    intent: str | None = None
    tables: list[str] = field(default_factory=list)
    joins: list[str] = field(default_factory=list)
    compiled_sql: str | None = None
    execution_status: str | None = None
    rows_returned: int | None = None
    narrator_response: str | None = None
    error_detail: str | None = None
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# Combined catalog: PO (5 tables) + XXBT_PDKS_PER_DETAILS_V (1 table)
# ---------------------------------------------------------------------------

def _build_combined_catalog():
    """Return a CatalogSnapshot with both PO and XXBT_PDKS_PER_DETAILS_V tables."""
    from app.domain.catalog_models import (
        CatalogSnapshot,
        ColumnMetadata,
        ColumnType,
        ForeignKeyMetadata,
        RelationshipMetadata,
        TableMetadata,
    )

    def _col(name: str, dtype: ColumnType = ColumnType.NUMBER, **kw) -> ColumnMetadata:
        return ColumnMetadata(name=name, data_type=dtype, **kw)

    ph = TableMetadata(
        name="PO_HEADERS_ALL",
        description="Satinalma siparisi basliklari",
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
        description="Satinalma siparisi kalemleri",
        aliases=["po lines", "po_lines"],
        primary_key=["po_line_id"],
        foreign_keys=[ForeignKeyMetadata(
            column="po_header_id",
            referenced_table="PO_HEADERS_ALL",
            referenced_column="po_header_id",
        )],
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
        description="Sevkiyat lokasyonlari",
        aliases=["po shipments", "po_line_locations"],
        primary_key=["line_location_id"],
        foreign_keys=[ForeignKeyMetadata(
            column="po_line_id",
            referenced_table="PO_LINES_ALL",
            referenced_column="po_line_id",
        )],
        columns=[
            _col("line_location_id", nullable=False),
            _col("po_line_id"),
            _col("quantity_received"),
            _col("quantity_billed"),
        ],
    )
    pd_ = TableMetadata(
        name="PO_DISTRIBUTIONS_ALL",
        description="Dagitim satirlari",
        aliases=["po distributions"],
        primary_key=["po_distribution_id"],
        foreign_keys=[ForeignKeyMetadata(
            column="line_location_id",
            referenced_table="PO_LINE_LOCATIONS_ALL",
            referenced_column="line_location_id",
        )],
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
        RelationshipMetadata(from_table="PO_HEADERS_ALL",        from_column="po_header_id",    to_table="PO_LINES_ALL",           to_column="po_header_id"),
        RelationshipMetadata(from_table="PO_LINES_ALL",          from_column="po_line_id",       to_table="PO_LINE_LOCATIONS_ALL",  to_column="po_line_id"),
        RelationshipMetadata(from_table="PO_LINE_LOCATIONS_ALL", from_column="line_location_id", to_table="PO_DISTRIBUTIONS_ALL",   to_column="line_location_id"),
        RelationshipMetadata(from_table="PO_LINES_ALL",          from_column="item_id",          to_table="MTL_SYSTEM_ITEMS_B",     to_column="inventory_item_id"),
    ]

    emp = TableMetadata(
        name="XXBT_PDKS_PER_DETAILS_V",
        description="PDKS ile entegre calisan personel gorunumu. CIKIS_TARIHI NULL olanlar aktif.",
        aliases=["employee", "employees", "personel", "calisan"],
        primary_key=["PERSON_ID"],
        columns=[
            ColumnMetadata(name="PERSON_ID",        data_type=ColumnType.NUMBER,  nullable=False, description="Benzersiz personel kimligi"),
            ColumnMetadata(name="SICIL_NO",          data_type=ColumnType.VARCHAR, nullable=False, description="Sicil numarasi",     aliases=["sicil_no", "reg_no", "employee_no"]),
            ColumnMetadata(name="AD",                data_type=ColumnType.VARCHAR, nullable=False, description="Calisanin adi",       aliases=["ad", "first_name", "name"]),
            ColumnMetadata(name="SOYAD",             data_type=ColumnType.VARCHAR, nullable=False, description="Calisanin soyadi",    aliases=["soyad", "last_name", "surname"]),
            ColumnMetadata(name="FULL_NAME",         data_type=ColumnType.VARCHAR, nullable=True,  description="Ad soyad"),
            ColumnMetadata(name="BIRIM_ADI",         data_type=ColumnType.VARCHAR, nullable=True,  description="Birim adi",           aliases=["birim", "unit_name", "department"]),
            ColumnMetadata(name="ORGANIZATION_ADI",  data_type=ColumnType.VARCHAR, nullable=True,  description="Organizasyon adi"),
            ColumnMetadata(name="LOCATION_ADI",      data_type=ColumnType.VARCHAR, nullable=True,  description="Lokasyon adi",        aliases=["lokasyon", "location_name"]),
            ColumnMetadata(name="UNVAN",             data_type=ColumnType.VARCHAR, nullable=True,  description="Unvan",               aliases=["unvan", "job_title", "title"]),
            ColumnMetadata(name="GOREV_TANIMI",      data_type=ColumnType.VARCHAR, nullable=True,  description="Gorev tanimi"),
            ColumnMetadata(name="ISE_GIRIS_TARIHI",  data_type=ColumnType.DATE,    nullable=True,  description="Ise giris tarihi",    aliases=["hire_date", "start_date", "ise_baslama"]),
            ColumnMetadata(name="CIKIS_TARIHI",      data_type=ColumnType.DATE,    nullable=True,  description="Itten ayrilma tarihi (NULL=aktif)", aliases=["quit_date", "leave_date"]),
            ColumnMetadata(name="EMAIL",             data_type=ColumnType.VARCHAR, nullable=True,  description="Kurumsal e-posta",    aliases=["email", "e-posta"]),
            ColumnMetadata(name="DAHILI",            data_type=ColumnType.VARCHAR, nullable=True,  description="Dahili telefon",      aliases=["dahili", "extension_no"]),
            ColumnMetadata(name="BORDROLU",          data_type=ColumnType.NUMBER,  nullable=True,  description="Bordrolu bayragi",    aliases=["payroll_flag"]),
            ColumnMetadata(name="STAJYER",           data_type=ColumnType.NUMBER,  nullable=True,  description="Stajyer bayragi",     aliases=["employment_type"]),
            ColumnMetadata(name="MASRAF_MERKEZI",    data_type=ColumnType.VARCHAR, nullable=True,  description="Masraf merkezi"),
            ColumnMetadata(name="DOGUM_TARIHI",      data_type=ColumnType.DATE,    nullable=True,  restricted=True, description="Dogum tarihi (kisitli)", aliases=["birth_date"]),
        ],
    )

    return CatalogSnapshot(tables=[ph, pl, pll, pd_, mtl, emp], relationships=rels)


class _CombinedCatalogProvider:
    """In-process catalog provider: PO (5) + XXBT_PDKS_PER_DETAILS_V (1) tables."""

    def __init__(self) -> None:
        self._snapshot = _build_combined_catalog()

    async def get_snapshot(self):
        return self._snapshot

    async def get_table(self, name: str):
        return self._snapshot.get_table(name)

    async def search_tables(self, query: str):
        return self._snapshot.search_tables(query)


# ---------------------------------------------------------------------------
# Combined mock executor: PO synthetic rows + XXBT_PDKS_PER_DETAILS_V mock executor
# ---------------------------------------------------------------------------

_PO_ROWS: list[dict[str, Any]] = [
    {"po_header_id": 10001, "vendor_id": 501, "creation_date": "2026-02-10", "authorization_status": "APPROVED",   "currency_code": "TRY", "type_lookup_code": "STANDARD"},
    {"po_header_id": 10002, "vendor_id": 502, "creation_date": "2026-02-22", "authorization_status": "INCOMPLETE",  "currency_code": "USD", "type_lookup_code": "STANDARD"},
    {"po_header_id": 10003, "vendor_id": 501, "creation_date": "2026-03-01", "authorization_status": "APPROVED",   "currency_code": "TRY", "type_lookup_code": "BLANKET"},
    {"po_header_id": 10004, "vendor_id": 503, "creation_date": "2026-01-15", "authorization_status": "CLOSED",     "currency_code": "EUR", "type_lookup_code": "STANDARD"},
]
_PO_LINE_ROWS: list[dict[str, Any]] = [
    {"po_line_id": 20001, "po_header_id": 10001, "item_id": 1, "line_num": 1, "item_description": "Kirtasiye",  "quantity": 100, "unit_price":  5.0},
    {"po_line_id": 20002, "po_header_id": 10001, "item_id": 2, "line_num": 2, "item_description": "Bilgisayar", "quantity": 10,  "unit_price": 15000.0},
    {"po_line_id": 20003, "po_header_id": 10002, "item_id": 3, "line_num": 1, "item_description": "Yazici",     "quantity": 5,   "unit_price":  3200.0},
    {"po_line_id": 20004, "po_header_id": 10003, "item_id": 1, "line_num": 1, "item_description": "Kirtasiye",  "quantity": 200, "unit_price":    4.8},
]
_PO_SHIPMENT_ROWS: list[dict[str, Any]] = [
    {"line_location_id": 30001, "po_line_id": 20001, "quantity_received": 80,  "quantity_billed": 80},
    {"line_location_id": 30002, "po_line_id": 20002, "quantity_received": 8,   "quantity_billed": 8},
    {"line_location_id": 30003, "po_line_id": 20003, "quantity_received": 0,   "quantity_billed": 0},
    {"line_location_id": 30004, "po_line_id": 20004, "quantity_received": 100, "quantity_billed": 100},
]
_PO_DIST_ROWS: list[dict[str, Any]] = [
    {"po_distribution_id": 40001, "line_location_id": 30001, "quantity_ordered": 100, "code_combination_id": 9001, "unit_price":  5.0},
    {"po_distribution_id": 40002, "line_location_id": 30002, "quantity_ordered": 10,  "code_combination_id": 9002, "unit_price": 15000.0},
    {"po_distribution_id": 40003, "line_location_id": 30003, "quantity_ordered": 5,   "code_combination_id": 9001, "unit_price":  3200.0},
    {"po_distribution_id": 40004, "line_location_id": 30004, "quantity_ordered": 200, "code_combination_id": 9003, "unit_price":    4.8},
]
_MTL_ROWS: list[dict[str, Any]] = [
    {"inventory_item_id": 1, "segment1": "KRT-001", "description": "Kirtasiye"},
    {"inventory_item_id": 2, "segment1": "BLG-001", "description": "Bilgisayar"},
    {"inventory_item_id": 3, "segment1": "YZC-001", "description": "Yazici"},
]
_PO_TABLE_DATA: dict[str, list[dict[str, Any]]] = {
    "PO_HEADERS_ALL":        _PO_ROWS,
    "PO_LINES_ALL":          _PO_LINE_ROWS,
    "PO_LINE_LOCATIONS_ALL": _PO_SHIPMENT_ROWS,
    "PO_DISTRIBUTIONS_ALL":  _PO_DIST_ROWS,
    "MTL_SYSTEM_ITEMS_B":    _MTL_ROWS,
}
_PO_TABLES = frozenset(_PO_TABLE_DATA)


class _CombinedMockExecutor:
    """Routes PO tables to synthetic data; XXBT_PDKS_PER_DETAILS_V table to MockExecutor."""

    def __init__(self) -> None:
        from app.providers.executor.mock_executor import MockExecutor
        self._emp_exec = MockExecutor()

    async def execute(self, compiled_query: Any) -> Any:
        from app.domain.execution_models import ExecutionResult, ExecutionStatus

        table_upper = (compiled_query.table or "").upper()
        if table_upper in _PO_TABLES:
            rows_source = _PO_TABLE_DATA.get(table_upper, [])
            limit = (
                compiled_query.debug_plan.limit
                if compiled_query.debug_plan
                else 100
            )
            output_rows: list[dict[str, Any]] = []
            for row in rows_source[:limit]:
                projected = {
                    c: row.get(c)
                    for c in (compiled_query.selected_columns or list(row.keys()))
                }
                output_rows.append(projected)
            status = ExecutionStatus.SUCCESS if output_rows else ExecutionStatus.EMPTY
            return ExecutionResult(
                status=status,
                columns=compiled_query.selected_columns or [],
                rows=output_rows,
                row_count=len(output_rows),
            )
        return await self._emp_exec.execute(compiled_query)


# ---------------------------------------------------------------------------
# Oracle Instant Client auto-detection
# ---------------------------------------------------------------------------

def _find_64bit_oracle_client() -> str:
    import struct

    def _pe_bits(path: str) -> int:
        try:
            with open(path, "rb") as f:
                if f.read(2) != b"MZ":
                    return 0
                f.seek(0x3C)
                pe_off = struct.unpack("<I", f.read(4))[0]
                f.seek(pe_off)
                if f.read(4) != b"PE\x00\x00":
                    return 0
                machine = struct.unpack("<H", f.read(2))[0]
                return 64 if machine == 0x8664 else 32
        except Exception:
            return 0

    for d in [
        r"C:\app\furkan.kiraz\product\21c\dbhomeXE\bin",
        r"C:\instantclient_21_11",
        r"C:\instantclient_21_3",
        r"C:\oracle\instantclient",
    ]:
        if _pe_bits(os.path.join(d, "oci.dll")) == 64:
            return d
    return ""


# ---------------------------------------------------------------------------
# Wiring — manual to use combined catalog
# ---------------------------------------------------------------------------

async def _build_orchestrator(use_oracle: bool):
    from app.api.deps import build_document_retrieval, build_llm_provider
    from app.core.config import settings
    from app.providers.retrieval.in_memory_retriever import InMemoryRetriever
    from app.services.catalog_service import CatalogService
    from app.services.narrator_service import NarratorService
    from app.services.orchestrator import ChatOrchestrator, Orchestrator
    from app.services.planner_service import PlannerService
    from app.services.schema_retrieval_service import SchemaRetrievalService
    from app.services.session_service import SessionService
    from app.services.sql_compiler import SQLCompiler
    from app.services.validation_service import ValidationService

    llm = build_llm_provider()
    doc_retrieval = await build_document_retrieval()
    catalog_provider = _CombinedCatalogProvider()

    retrieval = None
    if settings.enable_metadata_retrieval:
        retriever = InMemoryRetriever(catalog_provider)
        retrieval = SchemaRetrievalService(retriever)

    catalog = CatalogService(catalog_provider, retrieval=retrieval)
    validator = ValidationService(catalog)
    compiler = SQLCompiler()

    oracle_exec = None
    if use_oracle:
        from app.providers.executor.oracle_executor import OracleExecutor
        oracle_exec = OracleExecutor()
        await oracle_exec.init_pool(thick_mode_lib_dir=_find_64bit_oracle_client() or None)
        executor = oracle_exec
    else:
        executor = _CombinedMockExecutor()

    orchestrator = Orchestrator(validator, compiler, executor)
    planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)
    narrator = NarratorService(llm)
    sessions = SessionService()
    chat = ChatOrchestrator(planner, orchestrator, narrator, sessions)
    return chat, oracle_exec


# ---------------------------------------------------------------------------
# Per-question runner
# ---------------------------------------------------------------------------

async def _run_question(
    chat: Any,
    q: dict[str, str],
    session_prefix: str,
) -> QuestionResult:
    result = QuestionResult(id=q["id"], domain=q["domain"], question=q["text"])
    session_id = f"{session_prefix}_{q['id']}"
    t0 = time.monotonic()

    try:
        chat_result = await chat.handle_message(session_id, q["text"])
    except Exception as exc:
        result.outcome = "execution_error"
        result.error_detail = str(exc)
        result.elapsed_s = time.monotonic() - t0
        return result

    result.elapsed_s = time.monotonic() - t0
    result.narrator_response = chat_result.answer

    if chat_result.plan:
        plan = chat_result.plan
        result.intent = plan.intent
        if hasattr(plan, "table") and plan.table:
            result.tables = [plan.table]
        if hasattr(plan, "tables") and plan.tables:
            result.tables = list(plan.tables)
        if hasattr(plan, "joins") and plan.joins:
            result.joins = [str(j) for j in plan.joins]

    result.compiled_sql = chat_result.sql

    status = chat_result.status
    if status == "success":
        if chat_result.rows_preview:
            result.rows_returned = len(chat_result.rows_preview)
            result.outcome = "success"
        elif chat_result.rows_preview is not None:  # empty list
            result.outcome = "empty_result"
        else:
            result.outcome = "success"
    elif status == "clarification":
        result.outcome = "clarification"
    elif status == "validation_error":
        result.outcome = "validation_error"
        result.error_detail = chat_result.error_message
    elif status == "execution_error":
        err_msg = chat_result.error_message or ""
        result.outcome = (
            "compile_error"
            if "compilation" in err_msg.lower() or "compileerror" in err_msg.lower()
            else "execution_error"
        )
        result.error_detail = err_msg
    else:
        result.outcome = str(status)

    return result


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

_ICON = {
    "success":          "[+]",
    "empty_result":     "[ ]",
    "clarification":    "[?]",
    "validation_error": "[x]",
    "compile_error":    "[x]",
    "execution_error":  "[x]",
    "unknown":          "[.]",
}


def _print_report(results: list[QuestionResult]) -> None:
    total = len(results)
    counts: dict[str, int] = {k: 0 for k in (*OUTCOME_CLASSES, "unknown")}

    print("\n" + "=" * 72)
    print("  END-TO-END LLM FLOW REPORT")
    print("=" * 72)

    for r in results:
        icon = _ICON.get(r.outcome, "[.]")
        tables_str = ", ".join(r.tables) if r.tables else "---"
        print(f"\n  {icon} {r.id} ({r.domain})  {r.elapsed_s:.1f}s")
        print(f"       Question   : {r.question}")
        print(f"       Intent     : {r.intent or '---'}")
        print(f"       Tables     : {tables_str}")
        if r.joins:
            print(f"       Joins      : {'; '.join(r.joins[:2])}")
        if r.compiled_sql:
            sql_preview = r.compiled_sql.replace("\n", " ").strip()
            if len(sql_preview) > 110:
                sql_preview = sql_preview[:107] + "..."
            print(f"       SQL        : {sql_preview}")
        print(f"       Exec.status: {r.execution_status or r.outcome}")
        if r.rows_returned is not None:
            print(f"       Rows       : {r.rows_returned}")
        if r.narrator_response:
            narr = r.narrator_response.replace("\n", " ").strip()
            if len(narr) > 130:
                narr = narr[:127] + "..."
            print(f"       Narrator   : {narr}")
        if r.error_detail:
            print(f"       Error      : {r.error_detail[:130]}")

        counts[r.outcome] = counts.get(r.outcome, 0) + 1

    success_n     = counts["success"]
    empty_n       = counts["empty_result"]
    clarif_n      = counts["clarification"]
    val_err_n     = counts["validation_error"]
    compile_err_n = counts["compile_error"]
    exec_err_n    = counts["execution_error"]

    def pct(n: int) -> str:
        return f"{n / total * 100:.0f}%" if total else "---"

    print("\n" + "=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    print(f"  A. Toplam soru sayisi    : {total}")
    print(f"  B. Success orani")
    print(f"     * success             : {success_n}  ({pct(success_n)})")
    print(f"     * empty_result        : {empty_n}  ({pct(empty_n)})")
    print(f"     * pipeline_ok toplam  : {success_n + empty_n}  ({pct(success_n + empty_n)})")
    print(f"  C. Clarification orani   : {clarif_n}  ({pct(clarif_n)})")
    print(f"  D. Hata sayilari")
    print(f"     * validation_error    : {val_err_n}")
    print(f"     * compile_error       : {compile_err_n}")
    print(f"     * execution_error     : {exec_err_n}")

    hard_failures = val_err_n + compile_err_n + exec_err_n
    pipeline_ok   = success_n + empty_n + clarif_n
    rate          = pipeline_ok / total if total else 0

    print("\n  E. Production Readiness Onerisi")
    if rate >= 0.85 and hard_failures == 0:
        verdict = "READY"
        detail  = (
            f">= 85%% pipeline basarisi ({pct(pipeline_ok)}), sifir kritik hata. "
            "Production ortamina alinabilir."
        )
    elif rate >= 0.70 and hard_failures <= 3:
        verdict = "CONDITIONAL"
        detail  = (
            f"Pipeline basari orani {pct(pipeline_ok)}, "
            f"{hard_failures} kritik hata. "
            "Asagidaki hatalar giderildiginde production'a alinabilir."
        )
    else:
        verdict = "NOT READY"
        detail  = (
            f"Pipeline basari orani {pct(pipeline_ok)}, "
            f"{hard_failures} kritik hata. "
            "Once validation/compilation/execution hatalarini giderin."
        )

    print(f"     Verdict : {verdict}")
    print(f"     Detail  : {detail}")

    if hard_failures:
        print("\n     Kritik hatalar:")
        for r in results:
            if r.outcome in ("validation_error", "compile_error", "execution_error"):
                print(f"       [{r.id}] {r.outcome}: {(r.error_detail or '')[:80]}")

    print("=" * 72 + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(use_oracle: bool, report_json: str | None) -> None:
    print("\n  Building pipeline...")
    try:
        chat, oracle_exec = await _build_orchestrator(use_oracle)
    except Exception as exc:
        print(f"  FATAL -- could not build orchestrator: {exc}")
        sys.exit(1)

    print(f"  Executor  : {'OracleExecutor (thick mode)' if use_oracle else 'CombinedMockExecutor'}")
    print(f"  Catalog   : PO (5 tables) + XXBT_PDKS_PER_DETAILS_V (1 table)")
    print(f"  Questions : {len(QUESTIONS)}")
    print("  Running pipeline for each question...\n")

    session_prefix = f"e2e_{int(time.time())}"
    results: list[QuestionResult] = []

    for q in QUESTIONS:
        sys.stdout.write(f"  {q['id']} ({q['domain']}) ... ")
        sys.stdout.flush()
        r = await _run_question(chat, q, session_prefix)
        print(f"{_ICON.get(r.outcome, '[.]')}  ({r.elapsed_s:.1f}s)")
        results.append(r)

    if oracle_exec is not None:
        await oracle_exec.close()

    _print_report(results)

    if report_json:
        out_path = Path(report_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  JSON report saved -> {out_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="End-to-end LLM flow test -- 30 questions")
    parser.add_argument(
        "--no-oracle",
        action="store_true",
        help="Use CombinedMockExecutor instead of real Oracle (default: real Oracle)",
    )
    parser.add_argument(
        "--report-json",
        metavar="PATH",
        default="data/e2e_report.json",
        help="Write JSON report to this path (default: data/e2e_report.json)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(use_oracle=not args.no_oracle, report_json=args.report_json))
