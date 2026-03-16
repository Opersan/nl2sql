"""PO domain eval runner — q_101 through q_109.

Usage (from repo root):
    $env:LLM_PROVIDER="openai_compatible"
    $env:PLANNER_PROMPT_MAX_CHARS="22000"
    .\.venv\Scripts\python scripts\po_eval_runner.py

Outputs a table + gap-type analysis to stdout.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

# Ensure repo root is on sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# Q&A definition  (q_101 – q_109)
# ---------------------------------------------------------------------------

QUESTIONS: list[dict] = [
    {
        "id": "q_101",
        "nl": "Açık satınalma siparişlerini listele",
        "expected_root": "PO_HEADERS_ALL",
        "expected_intent": "po_open_orders",
        "expected_joins": [],
        "expected_filters": [("authorization_status", "!=")],
        "expected_aggs": [],
        "expected_group_by": [],
    },
    {
        "id": "q_102",
        "nl": "Onaysız bekleyen PO'ları getir",
        "expected_root": "PO_HEADERS_ALL",
        "expected_intent": "po_unapproved",
        "expected_joins": [],
        "expected_filters": [("authorization_status", "!=")],
        "expected_aggs": [],
        "expected_group_by": [],
    },
    {
        "id": "q_103",
        "nl": "Kapatılmamış PO'ları göster",
        "expected_root": "PO_HEADERS_ALL",
        "expected_intent": "po_open_orders",
        "expected_joins": [],
        "expected_filters": [("authorization_status", "!=")],
        "expected_aggs": [],
        "expected_group_by": [],
    },
    {
        "id": "q_104",
        "nl": "Tedarikçiye göre PO sayısı",
        "expected_root": "PO_HEADERS_ALL",
        "expected_intent": "po_vendor_count",
        "expected_joins": [],
        "expected_filters": [],
        "expected_aggs": [("COUNT", None)],
        "expected_group_by": ["vendor_id"],
    },
    {
        "id": "q_105",
        "nl": "Kalem bazında sipariş miktarı",
        "expected_root": "PO_HEADERS_ALL",
        "expected_intent": "po_line_quantity",
        "expected_joins": ["PO_LINES_ALL"],
        "expected_filters": [],
        "expected_aggs": [("SUM", "PO_LINES_ALL")],
        "expected_group_by": ["line_num"],
    },
    {
        "id": "q_106",
        "nl": "Teslim bekleyen satırları göster",
        "expected_root": "PO_HEADERS_ALL",
        "expected_intent": "po_pending_delivery",
        "expected_joins": ["PO_LINES_ALL", "PO_LINE_LOCATIONS_ALL"],
        "expected_filters": [("quantity_received", "<")],
        "expected_aggs": [],
        "expected_group_by": [],
    },
    {
        "id": "q_107",
        "nl": "Dağıtım bazında tutar analizi",
        "expected_root": "PO_HEADERS_ALL",
        "expected_intent": "po_distribution_amount",
        "expected_joins": ["PO_LINES_ALL", "PO_LINE_LOCATIONS_ALL", "PO_DISTRIBUTIONS_ALL"],
        "expected_filters": [],
        "expected_aggs": [("SUM", "PO_DISTRIBUTIONS_ALL"), ("SUM", "PO_LINES_ALL")],
        "expected_group_by": ["code_combination_id"],
    },
    {
        "id": "q_108",
        "nl": "Ürün bazında PO satırları",
        "expected_root": "PO_HEADERS_ALL",
        "expected_intent": "po_item_line_count",
        "expected_joins": ["PO_LINES_ALL", "MTL_SYSTEM_ITEMS_B"],
        "expected_filters": [],
        "expected_aggs": [("COUNT", None)],
        "expected_group_by": ["segment1"],
    },
    {
        "id": "q_109",
        "nl": "Son 30 günde açılan PO'lar",
        "expected_root": "PO_HEADERS_ALL",
        "expected_intent": "po_last_30_days",
        "expected_joins": [],
        "expected_filters": [("creation_date", ">=")],
        "expected_aggs": [],
        "expected_group_by": [],
    },
]


# ---------------------------------------------------------------------------
# Gap classifier
# ---------------------------------------------------------------------------

def _classify_gaps(plan, expected: dict) -> list[str]:
    """Return list of gap labels for *plan* vs *expected* spec."""
    gaps: list[str] = []

    if plan.needs_clarification:
        gaps.append("unnecessary_clarification")
        return gaps  # downstream fields are not reliable when clarifying

    # root table
    if plan.table and plan.table != expected["expected_root"]:
        gaps.append("wrong_root_table")

    # joins: expected right-tables that are missing
    actual_join_tables = {j.right_table for j in (plan.joins or [])}
    for expected_tbl in expected["expected_joins"]:
        if expected_tbl not in actual_join_tables:
            gaps.append("missing_join")
            break

    # filters: expected (column, op) pairs
    actual_filters = {(f.column, f.op.value) for f in (plan.filters or [])}
    for col, op in expected["expected_filters"]:
        if not any(f.column == col and f.op.value == op for f in (plan.filters or [])):
            gaps.append("wrong_filter_column")
            break

    # aggregations
    if expected["expected_aggs"]:
        actual_agg_fns = {a.function.value for a in (plan.aggregations or [])}
        for fn, _tbl in expected["expected_aggs"]:
            if fn not in actual_agg_fns:
                gaps.append("missing_aggregation")
                break

    # group_by
    if expected["expected_group_by"]:
        actual_gb = set(plan.group_by or [])
        for col in expected["expected_group_by"]:
            if col not in actual_gb:
                gaps.append("wrong_group_by")
                break

    return gaps if gaps else ["no_gap"]


# ---------------------------------------------------------------------------
# Plan summary extractor
# ---------------------------------------------------------------------------

def _plan_summary(plan) -> dict:
    return {
        "table": plan.table,
        "clarification": plan.needs_clarification,
        "clarification_msg": plan.clarification_message if plan.needs_clarification else None,
        "filters": [(f.column, f.table, f.op.value, f.value) for f in (plan.filters or [])],
        "aggregations": [(a.function.value, a.column, a.table, a.alias) for a in (plan.aggregations or [])],
        "group_by": list(plan.group_by or []),
        "joins": [(j.left_table, j.right_table) for j in (plan.joins or [])],
        "semantic_intent": getattr(plan, "semantic_intent", None),
        "join_path_id": getattr(plan, "join_path_id", None),
        "root_entity": getattr(plan, "root_entity", None),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------

async def run_eval() -> list[dict]:
    # Late import so env-overrides propagate before settings are read
    from app.core.config import settings
    from app.providers.catalog.in_memory import InMemoryCatalogProvider
    from app.providers.llm.openai_compatible import OpenAICompatibleProvider
    from app.services.catalog_service import CatalogService
    from app.services.planner_service import PlannerService

    llm = OpenAICompatibleProvider(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )
    catalog = CatalogService(InMemoryCatalogProvider())
    planner = PlannerService(llm, catalog)

    results: list[dict] = []
    for q in QUESTIONS:
        print(f"  running {q['id']} ...", end=" ", flush=True)
        summary: dict = {}
        try:
            plan = await planner.plan(q["nl"])
            summary = _plan_summary(plan)
        except Exception as exc:
            summary = {
                "table": None, "clarification": None, "clarification_msg": None,
                "filters": [], "aggregations": [], "group_by": [], "joins": [],
                "semantic_intent": None, "join_path_id": None, "root_entity": None,
                "error": str(exc),
            }
        gaps = ["parse_error"] if summary.get("error") else _classify_gaps(
            _FakePlan(summary), q
        )
        summary["gaps"] = gaps
        results.append({"id": q["id"], "nl": q["nl"], **summary})
        status = "✓" if gaps == ["no_gap"] else f"GAPS={gaps}"
        print(status)

    return results


class _FakePlan:
    """Minimal duck-type wrapper so _classify_gaps works without a real QueryPlan."""
    def __init__(self, d: dict):
        self.table = d.get("table")
        self.needs_clarification = d.get("clarification", False)
        self.joins = [type("J", (), {"right_table": rt})() for _lt, rt in d.get("joins", [])]
        self.filters = [
            type("F", (), {"column": col, "table": tbl, "op": type("O", (), {"value": op})(), "value": val})()
            for col, tbl, op, val in d.get("filters", [])
        ]
        self.aggregations = [
            type("A", (), {"function": type("Fn", (), {"value": fn})(), "column": col, "table": tbl, "alias": ali})()
            for fn, col, tbl, ali in d.get("aggregations", [])
        ]
        self.group_by = d.get("group_by", [])


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_report(results: list[dict]) -> None:
    W = 120
    print("\n" + "=" * W)
    print("PO DOMAIN EVAL REPORT — q_101 to q_109 (real provider)")
    print("=" * W)

    # Table header
    print(f"{'ID':<7} {'Root Table':<22} {'Intent':<28} {'Joins':<40} {'Gaps'}")
    print("-" * W)
    for r in results:
        joins_str = ",".join(rt for _lt, rt in r.get("joins", [])) or "—"
        intent = (r.get("semantic_intent") or "—")[:27]
        root = (r.get("root_entity") or r.get("table") or "—")[:21]
        gaps_str = ",".join(r.get("gaps", ["?"]))
        print(f"{r['id']:<7} {root:<22} {intent:<28} {joins_str:<40} {gaps_str}")

    print("-" * W)
    no_gap = sum(1 for r in results if r.get("gaps") == ["no_gap"])
    total = len(results)
    print(f"\nSCORE: {no_gap}/{total} no_gap   |   {total - no_gap} questions have issues\n")

    # Detail per question
    for r in results:
        if r.get("gaps") == ["no_gap"]:
            continue
        print(f"\n── {r['id']} — {r['nl']}")
        print(f"   table:       {r.get('table')}")
        print(f"   root_entity: {r.get('root_entity')}")
        print(f"   intent:      {r.get('semantic_intent')}")
        print(f"   join_path:   {r.get('join_path_id')}")
        print(f"   clarify:     {r.get('clarification')}  msg={r.get('clarification_msg','')!r:.60}")
        print(f"   filters:     {r.get('filters')}")
        print(f"   aggregations:{r.get('aggregations')}")
        print(f"   group_by:    {r.get('group_by')}")
        print(f"   joins:       {r.get('joins')}")
        print(f"   error:       {r.get('error')}")
        print(f"   GAPS:        {r.get('gaps')}")


if __name__ == "__main__":
    os.environ.setdefault("LLM_PROVIDER", "openai_compatible")
    os.environ.setdefault("PLANNER_PROMPT_MAX_CHARS", "22000")

    print("PO Eval — q_101..q_109 — real provider")
    print(f"  base_url: {os.environ.get('OPENAI_BASE_URL', 'http://10.50.110.11:8100/v1')}")
    print(f"  model:    {os.environ.get('OPENAI_MODEL', 'Sehyo/Qwen3.5-122B-A10B-NVFP4')}")
    print()

    results = asyncio.run(run_eval())
    _print_report(results)

    # Machine-readable dump for later analysis
    out_path = os.path.join(_ROOT, "data", "po_eval_results.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\nJSON saved → {out_path}")
