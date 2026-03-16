"""Real-provider NL2SQL reliability evaluation runner.

Purpose
=======
Evaluate the existing deterministic pipeline under:
- real LLM provider (openai_compatible / vLLM)
- real Oracle execution (or optional mock fallback)

For each question:
user_question -> retrieval -> planner -> semantic_normalization -> validation
-> sql_compiler -> oracle_executor -> narrator

The script does not modify architecture. It only measures behavior and reports.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Make project root importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


VALID_OUTCOMES = {
    "success",
    "empty_result",
    "clarification",
    "validation_error",
    "compile_error",
    "execution_error",
    "wrong_plan",
}


@dataclass
class EvalQuestion:
    id: str
    domain: str
    category: str
    text: str
    expected_table: str | None
    expected_intent_type: str
    wrong_plan_risk: str
    notes: str


@dataclass
class EvalResult:
    id: str
    domain: str
    category: str
    question: str
    expected_table: str | None
    expected_intent_type: str

    semantic_intent: str | None = None
    predicted_tables: list[str] = field(default_factory=list)
    join_path: list[str] = field(default_factory=list)
    compiled_sql: str | None = None
    execution_status: str | None = None
    row_count: int | None = None
    latency_ms: int = 0
    narrator_response: str | None = None
    raw_narrator_response: str | None = None  # pre-strip, for audit

    status: str = "execution_error"
    raw_status: str = "execution_error"
    error_detail: str | None = None
    execution_error_subtype: str | None = None  # oracle_syntax_error / invalid_date_value / etc.
    structured_parse_error: bool = False  # True when LLM returned non-QueryPlan JSON

    # Wrong-plan analysis fields
    wrong_plan: bool = False
    wrong_plan_reasons: list[str] = field(default_factory=list)

    # Clarification quality fields
    clarification_class: str | None = None

    # Narrator leak classification
    narrator_sql_leak: bool = False       # SELECT...FROM pattern in raw response
    narrator_presentation_leak: bool = False  # reasoning/thinking header in raw response


@dataclass
class EvalSummary:
    total_questions: int
    counts: dict[str, int]
    success_rate: float
    clarification_rate: float
    wrong_plan_rate: float
    validation_error_rate: float
    compile_error_rate: float
    execution_error_rate: float
    avg_latency_ms: float
    p95_latency_ms: float
    timeout_count: int
    row_count_distribution: dict[str, int]
    heavy_join_queries: list[dict[str, Any]]
    top_slowest_queries: list[dict[str, Any]]
    clarification_breakdown: dict[str, int]
    safety_checks: dict[str, Any]
    manual_review_list_size: int
    readiness_decision: str
    execution_error_subtypes: dict[str, int]  # oracle_syntax_error / invalid_date_value / ...
    structured_parse_errors: int              # LLM returned non-QueryPlan JSON
    top_failure_buckets: list[dict[str, Any]] # top-20 failure patterns


def _load_dataset(path: Path) -> list[EvalQuestion]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[EvalQuestion] = []
    for row in data:
        out.append(
            EvalQuestion(
                id=row["id"],
                domain=row["domain"],
                category=row["category"],
                text=row["text"],
                expected_table=row.get("expected_table"),
                expected_intent_type=row.get("expected_intent_type", "list"),
                wrong_plan_risk=row.get("wrong_plan_risk", "medium"),
                notes=row.get("notes", ""),
            )
        )
    return out


def _plan_tables(plan: Any) -> list[str]:
    tables: list[str] = []
    if hasattr(plan, "table") and plan.table:
        tables.append(str(plan.table))
    if hasattr(plan, "tables") and plan.tables:
        tables.extend(str(t) for t in plan.tables)
    # Keep order but dedupe
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tables:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


def _join_path(plan: Any) -> list[str]:
    joins = getattr(plan, "joins", None) or []
    return [str(j) for j in joins]


def _expected_filter_columns(question_text: str) -> set[str]:
    q = question_text.lower()
    expected: set[str] = set()
    if "son 30" in q or "30 gunde" in q:
        expected.add("creation_date_or_ise_giris_tarihi")
    if "son 6 ay" in q:
        expected.add("ise_giris_tarihi")
    if "2024" in q or "2025" in q or "2023" in q:
        expected.add("ise_giris_tarihi_or_cikis_tarihi")
    if "onay" in q or "acik" in q or "kapali" in q:
        expected.add("authorization_status")
    if "departman" in q or "birim" in q:
        expected.add("birim_adi")
    if "istanbul" in q or "lokasyon" in q:
        expected.add("location_adi")
    return expected


def _evaluate_wrong_plan(item: EvalQuestion, result: EvalResult, plan: Any) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    # Only evaluate wrong-plan for non-clarification pipeline statuses
    if result.raw_status in {"clarification", "validation_error", "compile_error", "execution_error"}:
        return False, reasons

    # 1) Wrong table
    if item.expected_table:
        predicted_upper = {t.upper() for t in result.predicted_tables}
        if item.expected_table.upper() not in predicted_upper:
            reasons.append("wrong_table")

    # 2) Wrong join (only strict for JOIN category)
    joins = getattr(plan, "joins", None) or []
    if item.category == "JOIN" and len(joins) == 0:
        reasons.append("wrong_join")

    # 3) Wrong aggregation
    aggs = getattr(plan, "aggregations", None) or []
    if item.expected_intent_type == "aggregation" and len(aggs) == 0:
        reasons.append("wrong_aggregation")

    # 4) Wrong filter column (heuristic)
    expected_filter_hints = _expected_filter_columns(item.text)
    if expected_filter_hints:
        plan_filter_cols = {
            str(getattr(f, "column", "")).lower()
            for f in (getattr(plan, "filters", None) or [])
        }
        # Heuristic soft checks
        if "authorization_status" in expected_filter_hints and "authorization_status" not in plan_filter_cols:
            reasons.append("wrong_filter_column")
        if "birim_adi" in expected_filter_hints and "birim_adi" not in plan_filter_cols and item.expected_intent_type != "aggregation":
            reasons.append("wrong_filter_column")
        if "location_adi" in expected_filter_hints and "location_adi" not in plan_filter_cols and item.expected_intent_type != "aggregation":
            reasons.append("wrong_filter_column")
        if "ise_giris_tarihi" in expected_filter_hints and "ise_giris_tarihi" not in plan_filter_cols and item.domain == "EMP":
            reasons.append("wrong_filter_column")

    # 5) Semantically incorrect result (heuristic): expected clarification but got confident SQL
    if item.expected_intent_type.startswith("clarification") and result.raw_status in {"success", "empty_result"}:
        reasons.append("semantically_incorrect_result")

    return (len(reasons) > 0), sorted(set(reasons))


def _classify_clarification(item: EvalQuestion, result: EvalResult, plan: Any) -> str | None:
    if result.raw_status != "clarification":
        return None

    q = item.text.lower()
    if item.category in {"AMBIGUOUS", "CROSS_DOMAIN"}:
        return "genuine_ambiguity"

    # Recoverable ambiguity: enough signal exists but planner still clarifies.
    if any(tok in q for tok in ["son 30", "2024", "onay", "departman", "tedarik", "calisan"]):
        return "recoverable_ambiguity"

    # Metadata gap / schema linking hints
    if any(tok in q for tok in ["tedarikci adi", "performans", "maas", "terfi", "teslim tarihi"]):
        return "metadata_gap"

    joins = getattr(plan, "joins", None) or []
    if item.category == "JOIN" and len(joins) == 0:
        return "schema_linking_failure"

    return "recoverable_ambiguity"


def _categorize_row_count(n: int | None) -> str:
    if n is None:
        return "unknown"
    if n == 0:
        return "0"
    if 1 <= n <= 10:
        return "1_10"
    if 11 <= n <= 100:
        return "11_100"
    return "100_plus"


def _is_heavy_join(res: EvalResult) -> bool:
    sql = (res.compiled_sql or "").upper()
    join_count = sql.count(" JOIN ")
    if join_count >= 2:
        return True
    return (
        "PO_DISTRIBUTIONS_ALL" in sql
        or "MTL_SYSTEM_ITEMS_B" in sql
    )


# Compiled once for performance
_SQL_LEAK_PAT = re.compile(r'\bSELECT\b.{1,800}\bFROM\b', re.IGNORECASE | re.DOTALL)
_REASONING_LEAK_PAT = re.compile(
    r'^(?:#+\s*)?(thinking|reasoning|analysis|thought process|'
    r'd\u00fc\u015f\u00fcnce|analiz|muhakeme|i\u00e7 muhakeme)',
    re.IGNORECASE | re.MULTILINE,
)
_EXEC_ERR_CLASS_PAT = re.compile(r'\[([a-z_]+)\]')


def _classify_narrator_leaks(text: str | None) -> tuple[bool, bool]:
    """Return (sql_leak, presentation_leak) for a narrator response."""
    if not text:
        return False, False
    sql_leak = bool(_SQL_LEAK_PAT.search(text))
    presentation_leak = bool(_REASONING_LEAK_PAT.search(text))
    return sql_leak, presentation_leak


def _extract_exec_error_subtype(detail: str | None) -> str | None:
    """Extract the bracketed error class written by OracleExecutor, e.g. '[oracle_syntax_error]'."""
    if not detail:
        return None
    m = _EXEC_ERR_CLASS_PAT.search(detail)
    if m:
        return m.group(1)
    if "timeout" in detail.lower():
        return "timeout_error"
    return "unknown_execution_error"


def _safety_audit(results: list[EvalResult], oracle_timeout: int) -> dict[str, Any]:
    sqls = [r.compiled_sql or "" for r in results if r.compiled_sql]

    select_only_ok = all((s.lstrip().upper().startswith("SELECT") or s.lstrip().upper().startswith("WITH")) for s in sqls)
    multi_statement_block_ok = all(";" not in s.strip().rstrip(";") for s in sqls)

    bind_param_ok_count = sum(1 for s in sqls if (":p" in s.lower() or " rownum <= " in s.lower()))
    bind_param_usage_ok = bind_param_ok_count >= int(0.80 * len(sqls)) if sqls else True

    row_limit_enforced_count = sum(1 for s in sqls if "ROWNUM <=" in s.upper())
    row_limit_enforced_ok = row_limit_enforced_count == len(sqls) if sqls else True

    timeout_enforced = oracle_timeout > 0

    # Use pre-computed per-result leak flags for accurate counts
    sql_leak_count = sum(1 for r in results if r.narrator_sql_leak)
    presentation_leak_count = sum(1 for r in results if r.narrator_presentation_leak)
    # Fallback: if fields not yet populated (e.g. older result objects) use pattern match
    if sql_leak_count == 0 and presentation_leak_count == 0:
        for r in results:
            sl, pl = _classify_narrator_leaks(r.narrator_response)
            if sl:
                sql_leak_count += 1
            if pl:
                presentation_leak_count += 1

    restricted_fields_exposure_count = sum(1 for s in sqls if "DOGUM_TARIHI" in s.upper())

    return {
        "sqlguard_select_only": select_only_ok,
        "multi_statement_block": multi_statement_block_ok,
        "bind_param_usage": bind_param_usage_ok,
        "bind_param_usage_count": bind_param_ok_count,
        "row_limit_enforced": row_limit_enforced_ok,
        "row_limit_enforced_count": row_limit_enforced_count,
        "timeout_enforced": timeout_enforced,
        "sql_leak_count": sql_leak_count,
        "presentation_leak_count": presentation_leak_count,
        "restricted_fields_exposure_count": restricted_fields_exposure_count,
    }


async def _run_one(chat: Any, item: EvalQuestion, session_prefix: str) -> EvalResult:
    t0 = time.perf_counter()
    result = EvalResult(
        id=item.id,
        domain=item.domain,
        category=item.category,
        question=item.text,
        expected_table=item.expected_table,
        expected_intent_type=item.expected_intent_type,
    )
    session_id = f"{session_prefix}_{item.id}"

    try:
        chat_result = await chat.handle_message(session_id, item.text)
    except Exception as exc:
        result.raw_status = "execution_error"
        result.status = "execution_error"
        result.error_detail = str(exc)
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        return result

    result.latency_ms = int((time.perf_counter() - t0) * 1000)

    result.semantic_intent = chat_result.plan.intent if chat_result.plan else None
    if chat_result.plan:
        result.predicted_tables = _plan_tables(chat_result.plan)
        result.join_path = _join_path(chat_result.plan)
    result.compiled_sql = chat_result.sql
    result.narrator_response = chat_result.answer

    # --- Narrator leak classification (on final, post-strip response) ---
    result.narrator_sql_leak, result.narrator_presentation_leak = _classify_narrator_leaks(
        chat_result.answer
    )

    # --- Detect structured parse fallback (firewall produced clarification for bad JSON) ---
    if (
        chat_result.plan is not None
        and getattr(chat_result.plan, "needs_clarification", False)
        and getattr(chat_result.plan, "intent", "") == "Yanit yorumlanamadi"
    ):
        result.structured_parse_error = True

    # Normalize status
    status = chat_result.status
    if status == "success":
        if chat_result.rows_preview is not None and len(chat_result.rows_preview) == 0:
            result.raw_status = "empty_result"
            result.row_count = 0
        else:
            result.raw_status = "success"
            result.row_count = len(chat_result.rows_preview or [])
    elif status == "clarification":
        result.raw_status = "clarification"
    elif status == "validation_error":
        result.raw_status = "validation_error"
        result.error_detail = chat_result.error_message
    elif status == "execution_error":
        err = (chat_result.error_message or "").lower()
        if "compilation" in err or "compile" in err:
            result.raw_status = "compile_error"
        else:
            result.raw_status = "execution_error"
            result.execution_error_subtype = _extract_exec_error_subtype(chat_result.error_message)
        result.error_detail = chat_result.error_message
    else:
        result.raw_status = "execution_error"
        result.error_detail = f"Unknown status: {status}"

    result.execution_status = result.raw_status

    # Wrong-plan analysis on non-ambiguous questions (as requested)
    non_ambiguous = item.category not in {"AMBIGUOUS", "CROSS_DOMAIN"}
    if chat_result.plan and non_ambiguous:
        wp, reasons = _evaluate_wrong_plan(item, result, chat_result.plan)
        result.wrong_plan = wp
        result.wrong_plan_reasons = reasons

    # Clarification quality classes
    if chat_result.plan:
        result.clarification_class = _classify_clarification(item, result, chat_result.plan)

    # Promote outcome to wrong_plan class where relevant
    if result.wrong_plan:
        result.status = "wrong_plan"
    else:
        result.status = result.raw_status

    if result.status not in VALID_OUTCOMES:
        result.status = "execution_error"

    return result


def _make_summary(results: list[EvalResult], oracle_timeout: int) -> EvalSummary:
    total = len(results)
    counts = Counter(r.status for r in results)

    def rate(n: int) -> float:
        return (n / total) if total else 0.0

    latencies = [r.latency_ms for r in results]
    avg_latency = float(statistics.mean(latencies)) if latencies else 0.0
    if latencies:
        sorted_lat = sorted(latencies)
        idx = max(0, min(len(sorted_lat) - 1, math.ceil(0.95 * len(sorted_lat)) - 1))
        p95 = float(sorted_lat[idx])
    else:
        p95 = 0.0

    timeout_count = sum(
        1
        for r in results
        if (r.status == "execution_error" and (r.error_detail or "").lower().find("timeout") >= 0)
    )

    row_count_dist = Counter(_categorize_row_count(r.row_count) for r in results)

    heavy = [
        {
            "id": r.id,
            "question": r.question,
            "latency_ms": r.latency_ms,
            "status": r.status,
            "tables": r.predicted_tables,
        }
        for r in results
        if _is_heavy_join(r)
    ]

    slowest = sorted(
        [
            {
                "id": r.id,
                "question": r.question,
                "latency_ms": r.latency_ms,
                "status": r.status,
                "tables": r.predicted_tables,
            }
            for r in results
        ],
        key=lambda x: x["latency_ms"],
        reverse=True,
    )[:10]

    clarif_breakdown = Counter(
        r.clarification_class for r in results if r.clarification_class is not None
    )

    safety = _safety_audit(results, oracle_timeout)

    non_ambiguous = [r for r in results if r.category not in {"AMBIGUOUS", "CROSS_DOMAIN"}]
    wrong_plan_non_ambiguous = sum(1 for r in non_ambiguous if r.wrong_plan)
    wrong_plan_rate = (wrong_plan_non_ambiguous / len(non_ambiguous)) if non_ambiguous else 0.0

    # readiness decision
    if (
        rate(counts.get("success", 0) + counts.get("empty_result", 0)) >= 0.80
        and wrong_plan_rate <= 0.10
        and counts.get("execution_error", 0) == 0
        and safety.get("sqlguard_select_only", False)
        and safety.get("multi_statement_block", False)
    ):
        decision = "pilot_ready"
    elif (
        rate(counts.get("success", 0) + counts.get("empty_result", 0)) >= 0.65
        and wrong_plan_rate <= 0.20
    ):
        decision = "pilot_ready_with_guards"
    else:
        decision = "not_ready"

    manual_review_size = sum(1 for r in results if r.wrong_plan or r.status in {"validation_error", "compile_error", "execution_error"})

    # --- execution_error subtypes breakdown ---
    exec_subtypes: Counter[str] = Counter()
    for r in results:
        if r.raw_status == "execution_error" and r.execution_error_subtype:
            exec_subtypes[r.execution_error_subtype] += 1

    # --- structured parse error count ---
    structured_parse_count = sum(1 for r in results if r.structured_parse_error)

    # --- top-20 failure buckets: (status, error_subtype_or_reason) ---
    failure_labels: list[str] = []
    for r in results:
        if r.status in {"success", "empty_result"}:
            continue
        if r.status == "execution_error" and r.execution_error_subtype:
            failure_labels.append(f"execution_error/{r.execution_error_subtype}")
        elif r.status == "validation_error" and r.error_detail:
            # Extract first validation error code from detail
            first_code = (r.error_detail or "").split(";")[0][:60]
            failure_labels.append(f"validation_error/{first_code}")
        elif r.wrong_plan and r.wrong_plan_reasons:
            for reason in r.wrong_plan_reasons:
                failure_labels.append(f"wrong_plan/{reason}")
        elif r.structured_parse_error:
            failure_labels.append("structured_parse_error")
        else:
            failure_labels.append(r.status)
    top_buckets = [
        {"bucket": label, "count": cnt}
        for label, cnt in Counter(failure_labels).most_common(20)
    ]

    return EvalSummary(
        total_questions=total,
        counts=dict(counts),
        success_rate=rate(counts.get("success", 0) + counts.get("empty_result", 0)),
        clarification_rate=rate(counts.get("clarification", 0)),
        wrong_plan_rate=wrong_plan_rate,
        validation_error_rate=rate(counts.get("validation_error", 0)),
        compile_error_rate=rate(counts.get("compile_error", 0)),
        execution_error_rate=rate(counts.get("execution_error", 0)),
        avg_latency_ms=avg_latency,
        p95_latency_ms=p95,
        timeout_count=timeout_count,
        row_count_distribution=dict(row_count_dist),
        heavy_join_queries=heavy,
        top_slowest_queries=slowest,
        clarification_breakdown=dict(clarif_breakdown),
        safety_checks=safety,
        manual_review_list_size=manual_review_size,
        readiness_decision=decision,
        execution_error_subtypes=dict(exec_subtypes),
        structured_parse_errors=structured_parse_count,
        top_failure_buckets=top_buckets,
    )


def _format_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _build_report_markdown(
    dataset: list[EvalQuestion],
    results: list[EvalResult],
    summary: EvalSummary,
) -> str:
    c = summary.counts
    po_n = sum(1 for x in dataset if x.domain == "PO")
    emp_n = sum(1 for x in dataset if x.domain == "EMP")
    cross_n = len(dataset) - po_n - emp_n

    # Required final metric table
    metric_table = "\n".join(
        [
            "| metric | value |",
            "|---|---:|",
            f"| success_rate | {_format_pct(summary.success_rate)} |",
            f"| clarification_rate | {_format_pct(summary.clarification_rate)} |",
            f"| wrong_plan_rate | {_format_pct(summary.wrong_plan_rate)} |",
            f"| validation_error_rate | {_format_pct(summary.validation_error_rate)} |",
            f"| compile_error_rate | {_format_pct(summary.compile_error_rate)} |",
            f"| execution_error_rate | {_format_pct(summary.execution_error_rate)} |",
            f"| avg_latency | {summary.avg_latency_ms:.1f} ms |",
            f"| p95_latency | {summary.p95_latency_ms:.1f} ms |",
        ]
    )

    return "\n".join(
        [
            "A. Eval dataset ozeti",
            f"- Toplam soru: {len(dataset)}",
            f"- PO: {po_n}",
            f"- HR/EMP: {emp_n}",
            f"- Cross/Ambiguous/Invalid: {cross_n}",
            "",
            "B. Pipeline sonuclari",
            f"- success: {c.get('success', 0)}",
            f"- empty_result: {c.get('empty_result', 0)}",
            f"- clarification: {c.get('clarification', 0)}",
            f"- validation_error: {c.get('validation_error', 0)}",
            f"- compile_error: {c.get('compile_error', 0)}",
            f"- execution_error: {c.get('execution_error', 0)}",
            f"- wrong_plan: {c.get('wrong_plan', 0)}",
            "",
            "C. Wrong-plan analizi",
            f"- wrong_plan_rate (ambiguous olmayan sorular): {_format_pct(summary.wrong_plan_rate)}",
            f"- Manual review listesi boyutu: {summary.manual_review_list_size}",
            "",
            "D. Oracle runtime davranisi",
            f"- avg_latency: {summary.avg_latency_ms:.1f} ms",
            f"- p95_latency: {summary.p95_latency_ms:.1f} ms",
            f"- timeout_count: {summary.timeout_count}",
            f"- row_count_distribution: {summary.row_count_distribution}",
            f"- heavy_join_queries: {len(summary.heavy_join_queries)}",
            f"- top_slowest_queries: {len(summary.top_slowest_queries)} kayit",
            "",
            "E. Clarification analizi",
            f"- genuine_ambiguity: {summary.clarification_breakdown.get('genuine_ambiguity', 0)}",
            f"- recoverable_ambiguity: {summary.clarification_breakdown.get('recoverable_ambiguity', 0)}",
            f"- metadata_gap: {summary.clarification_breakdown.get('metadata_gap', 0)}",
            f"- schema_linking_failure: {summary.clarification_breakdown.get('schema_linking_failure', 0)}",
            "",
            "F. Guvenlik dogrulamasi",
            f"- SQLGuard SELECT-only: {summary.safety_checks.get('sqlguard_select_only')}",
            f"- multi-statement block: {summary.safety_checks.get('multi_statement_block')}",
            f"- bind param usage: {summary.safety_checks.get('bind_param_usage')}",
            f"- row limit enforced: {summary.safety_checks.get('row_limit_enforced')}",
            f"- timeout enforced: {summary.safety_checks.get('timeout_enforced')}",
            f"- SQL leak count: {summary.safety_checks.get('sql_leak_count')}",
            f"- restricted fields exposure count: {summary.safety_checks.get('restricted_fields_exposure_count')}",
            "",
            "G. Execution error alt tipleri",
        ]
        + [
            f"- {k}: {v}"
            for k, v in sorted(summary.execution_error_subtypes.items(), key=lambda x: -x[1])
        ]
        + [
            f"- structured_parse_errors: {summary.structured_parse_errors}",
            "",
            "H. Narrator leak analizi",
            f"- sql_leak_count: {summary.safety_checks.get('sql_leak_count', 0)}",
            f"- presentation_leak_count: {summary.safety_checks.get('presentation_leak_count', 0)}",
            "",
            "I. Top-20 failure buckets",
        ]
        + [
            f"- [{b['count']:3d}] {b['bucket']}"
            for b in summary.top_failure_buckets
        ]
        + [
            "",
            "J. Production readiness karari",
            f"- karar: {summary.readiness_decision}",
            "",
            "K. Sonuc metrikleri",
            metric_table,
        ]
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Real provider + Oracle NL2SQL evaluation")
    parser.add_argument("--dataset", default="data/eval_dataset_100.json", help="Dataset JSON path")
    parser.add_argument("--report-json", default="data/real_provider_eval_report.json", help="Output JSON report")
    parser.add_argument("--report-md", default="data/real_provider_eval_summary.md", help="Output summary markdown")
    parser.add_argument("--manual-review-json", default="data/real_provider_manual_review.json", help="Output manual review list")
    parser.add_argument("--no-oracle", action="store_true", help="Use mock executor instead of Oracle (for dry-run)")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise SystemExit(f"Dataset file not found: {dataset_path}")

    dataset = _load_dataset(dataset_path)
    if len(dataset) < 100:
        raise SystemExit(f"Dataset must be at least 100 questions, got {len(dataset)}")

    # Reuse existing wiring (no architecture change)
    from app.core.config import settings
    from scripts.e2e_llm_flow import _build_orchestrator

    chat, oracle_exec = await _build_orchestrator(use_oracle=not args.no_oracle)

    results: list[EvalResult] = []
    session_prefix = f"real_eval_{int(time.time())}"

    for idx, item in enumerate(dataset, start=1):
        print(f"[{idx:03d}/{len(dataset)}] {item.id} ...", flush=True)
        r = await _run_one(chat, item, session_prefix)
        results.append(r)

    if oracle_exec is not None:
        await oracle_exec.close()

    summary = _make_summary(results, oracle_timeout=settings.oracle_timeout)

    # Manual review list for wrong-plan detection and hard errors
    manual_review = [
        {
            "id": r.id,
            "question": r.question,
            "category": r.category,
            "expected_table": r.expected_table,
            "expected_intent_type": r.expected_intent_type,
            "status": r.status,
            "raw_status": r.raw_status,
            # Failure analysis
            "wrong_plan": r.wrong_plan,
            "wrong_plan_reasons": r.wrong_plan_reasons,
            "structured_parse_error": r.structured_parse_error,
            "execution_error_subtype": r.execution_error_subtype,
            "error_detail": r.error_detail,
            # Plan details
            "semantic_intent": r.semantic_intent,
            "predicted_tables": r.predicted_tables,
            "join_path": r.join_path,
            "compiled_sql": r.compiled_sql,
            # Narrator audit
            "narrator_response": r.narrator_response,
            "narrator_sql_leak": r.narrator_sql_leak,
            "narrator_presentation_leak": r.narrator_presentation_leak,
            # Clarification
            "clarification_class": r.clarification_class,
        }
        for r in results
        if r.wrong_plan or r.status in {"validation_error", "compile_error", "execution_error"}
    ]

    report_payload = {
        "dataset_path": str(dataset_path),
        "dataset_size": len(dataset),
        "summary": asdict(summary),
        "results": [asdict(r) for r in results],
    }

    report_json_path = Path(args.report_json)
    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    manual_json_path = Path(args.manual_review_json)
    manual_json_path.parent.mkdir(parents=True, exist_ok=True)
    manual_json_path.write_text(json.dumps(manual_review, ensure_ascii=False, indent=2), encoding="utf-8")

    report_md_path = Path(args.report_md)
    report_md_path.parent.mkdir(parents=True, exist_ok=True)
    report_md_path.write_text(_build_report_markdown(dataset, results, summary), encoding="utf-8")

    print("\nEvaluation complete.")
    print(f"JSON report: {report_json_path}")
    print(f"Summary MD : {report_md_path}")
    print(f"Manual review list: {manual_json_path}")


if __name__ == "__main__":
    asyncio.run(main())
