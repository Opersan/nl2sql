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
import random
import re
import statistics
import sys
import time
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


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
    narrator_sql_leak: bool = False
    narrator_presentation_leak: bool = False
    raw_narrator_sql_leak: bool = False
    raw_narrator_presentation_leak: bool = False
    raw_narrator_chain_of_thought_leak: bool = False
    raw_narrator_prompt_echo_leak: bool = False
    raw_narrator_policy_echo_leak: bool = False
    raw_narrator_oracle_error_leak: bool = False
    final_narrator_sql_leak: bool = False
    final_narrator_presentation_leak: bool = False
    final_narrator_chain_of_thought_leak: bool = False
    final_narrator_prompt_echo_leak: bool = False
    final_narrator_policy_echo_leak: bool = False
    final_narrator_oracle_error_leak: bool = False
    repair_applied: bool = False
    repair_actions: list[str] = field(default_factory=list)
    repair_fields_count: int = 0
    root_cause_layer: str | None = None
    root_cause_stage: str = "none"
    primary_failure_reason: str | None = None
    secondary_failure_reason: str | None = None
    business_status: str = "execution_error"
    quality_status: str = "fail"
    safety_status: str = "pass"
    business_failure_stage: str = "none"
    quality_failure_stage: str = "none"
    safety_failure_stage: str = "none"
    first_failing_stage: str = "none"
    final_failing_stage: str = "none"
    root_cause_category: str = "unknown"
    root_cause_detail: str = ""
    planner_ok: bool = True
    repair_ok: bool = True
    semantic_ok: bool = True
    validation_ok: bool = True
    compile_ok: bool = True
    execute_ok: bool = True
    narration_ok: bool = True
    stage_statuses: dict[str, Any] = field(default_factory=dict)
    trace_flags: dict[str, bool] = field(default_factory=dict)
    trace_id: str = ""
    planner_question: str | None = None
    execute_question: str | None = None
    narrator_question: str | None = None
    stage_alignment_ok: bool = True
    alignment_errors: list[str] = field(default_factory=list)
    narration_context_mismatch: bool = False
    narration_context_mismatch_fields: list[str] = field(default_factory=list)
    final_response_source: str = "fallback"
    sanitizer_effective: bool = False
    final_response_mapping_error: bool = False
    sql_shape_comparable: bool = False
    raw_leak_but_final_clean: bool = False
    question_trace: dict[str, Any] | None = None
    queue_wait_ms: int = 0
    processing_ms: int = 0


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
    repair_applied_total: int
    repaired_fields_total: int
    questions_with_repair_rate: float
    repair_action_counts: dict[str, int]
    wrong_plan_bucket_counts: dict[str, int]
    execution_error_bucket_counts: dict[str, int]
    repaired_wrong_plan_count: int
    repair_prevented_clarification_count: int
    repair_prevented_validation_error_count: int
    repair_prevented_execution_error_count: int
    top_semantic_intents_by_failure: list[dict[str, Any]]
    top_root_entities_by_failure: list[dict[str, Any]]
    concurrency: int
    max_retries: int
    total_wall_time_s: float
    avg_question_latency_s: float
    p50_question_latency_s: float
    p95_question_latency_s: float
    llm_retry_count: int
    llm_retry_success_count: int
    business_success_rate: float
    quality_pass_rate: float
    safety_pass_rate: float
    first_fail_stage_counts: dict[str, int]
    root_cause_category_counts: dict[str, int]
    narrator_leak_rate: float
    presentation_leak_rate: float
    sql_leak_rate: float
    final_narrator_leak_rate: float
    final_presentation_leak_rate: float
    final_sql_leak_rate: float
    final_oracle_error_leak_rate: float
    raw_narrator_leak_rate: float
    raw_presentation_leak_rate: float
    raw_sql_leak_rate: float
    raw_oracle_error_leak_rate: float
    planner_parse_fail_rate: float
    repair_apply_rate: float
    semantic_override_rate: float
    sql_shape_changed_rate: float
    trace_alignment_error_count: int
    narration_context_mismatch_count: int
    sanitizer_effective_rate: float
    final_response_mapping_error_count: int
    sanitizer_saved_response_count: int
    raw_leak_but_final_clean_count: int


@dataclass
class LLMRetryStats:
    retry_count: int = 0
    retry_success_count: int = 0


@dataclass
class BenchmarkResult:
    concurrency: int
    total_wall_time_s: float
    success: int
    clarification: int
    validation_error: int
    compile_error: int
    execution_error: int
    wrong_plan: int


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


_TRACE_PLAN_FIELDS = (
    "table",
    "joins",
    "select_columns",
    "filters",
    "aggregations",
    "group_by",
    "order_by",
    "semantic_intent",
    "root_entity",
    "join_path_id",
    "needs_clarification",
    "clarification_message",
)


def _normalize_trace_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize_trace_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_trace_value(v) for v in value]
    return value


def _immutable_snapshot(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_copy"):
        try:
            return value.model_copy(deep=True)
        except Exception:
            pass
    if hasattr(value, "model_dump"):
        try:
            return deepcopy(value.model_dump(mode="json"))
        except Exception:
            pass
    return deepcopy(value)


def _ensure_trace_locks(chat: Any) -> dict[str, asyncio.Lock]:
    locks = getattr(chat, "_eval_trace_locks", None)
    if locks is None:
        locks = {
            "planner": asyncio.Lock(),
            "orchestrator": asyncio.Lock(),
            "narrator": asyncio.Lock(),
        }
        setattr(chat, "_eval_trace_locks", locks)
    return locks


def _default_retrieval_trace() -> dict[str, Any]:
    return {
        "enabled": False,
        "schema_tables": [],
        "schema_docs": [],
        "examples": [],
        "sufficiency": "none",
    }


def _default_prompt_trace() -> dict[str, Any]:
    return {
        "available": False,
        "prompt_length": 0,
        "prompt_budget": 0,
        "prompt_truncated": False,
        "reduction_steps": [],
        "full_prompt_text": None,
    }


def _default_llm_trace() -> dict[str, Any]:
    return {
        "available": False,
        "raw_response_text": None,
        "parse_error": None,
        "tokens_in": None,
        "tokens_out": None,
        "stop_reason": None,
    }


def _default_validation_trace() -> dict[str, Any]:
    return {
        "available": False,
        "ok": False,
        "errors": [],
        "warnings": [],
        "stage_outcome": "skipped",
        "note": "validation skipped",
    }


def _default_compile_trace() -> dict[str, Any]:
    return {
        "available": False,
        "ok": False,
        "error": None,
        "sql": None,
        "params": {},
        "selected_columns": [],
        "selected_columns_count": 0,
        "filter_count": 0,
        "join_count": 0,
        "aggregation_count": 0,
        "group_by_count": 0,
        "bind_param_count": 0,
        "expression_count": 0,
        "compile_warning_list": [],
        "stage_outcome": "skipped",
        "note": "compile skipped",
    }


def _default_execute_trace() -> dict[str, Any]:
    return {
        "available": False,
        "status": "skipped",
        "row_count": None,
        "columns": [],
        "error": None,
        "error_message": None,
        "execution_time_ms": None,
        "latency_ms": None,
        "executor_class": None,
        "db_latency_ms": None,
        "fetch_latency_ms": None,
        "timeout_applied": None,
        "row_limit_applied": None,
        "rows_returned_before_limit": None,
        "rows_returned_after_limit": None,
        "execution_error_subtype": None,
        "ok": False,
        "stage_outcome": "skipped",
        "note": "execution skipped",
    }


def _default_narration_trace() -> dict[str, Any]:
    return {
        "available": False,
        "raw_response": None,
        "sanitized_response": None,
        "final_response": None,
        "final_response_source": "fallback",
        "sanitizer_applied": False,
        "sanitizer_effective": False,
        "sanitizer_mode": "pass_through",
        "raw_vs_final_changed": False,
        "sanitizer_actions": [],
        "narrator_policy_violation_types": [],
        "raw_response_policy_violations": [],
        "sanitized_response_policy_violations": [],
        "final_response_policy_violations": [],
        "sql_leak": False,
        "presentation_leak": False,
        "chain_of_thought_leak": False,
        "prompt_echo_leak": False,
        "policy_echo_leak": False,
        "oracle_error_leak": False,
        "raw_chain_of_thought_leak": False,
        "raw_prompt_echo_leak": False,
        "raw_policy_echo_leak": False,
        "raw_sql_leak": False,
        "raw_presentation_leak": False,
        "raw_oracle_error_leak": False,
        "final_chain_of_thought_leak": False,
        "final_prompt_echo_leak": False,
        "final_policy_echo_leak": False,
        "final_sql_leak": False,
        "final_presentation_leak": False,
        "final_oracle_error_leak": False,
        "narration_ok": False,
        "stage_outcome": "skipped",
        "note": "narration skipped",
        "source_question_for_narrator": None,
        "source_execution_status_for_narrator": None,
        "source_row_count_for_narrator": None,
        "source_columns_for_narrator": [],
        "source_summary_text_for_narrator": None,
        "narration_context_mismatch": False,
        "narration_context_mismatch_fields": [],
    }


def _derive_retrieval_sufficiency(retrieval: dict[str, Any]) -> str:
    has_schema = bool(retrieval.get("schema_tables"))
    has_docs = bool(retrieval.get("schema_docs"))
    has_examples = bool(retrieval.get("examples"))
    if has_schema and has_docs and has_examples:
        return "full"
    if has_schema and has_docs:
        return "schema_plus_docs"
    if has_schema and has_examples:
        return "schema_plus_examples"
    if has_schema:
        return "schema_only"
    if has_docs:
        return "docs_only"
    return "none"


def _build_expected_narrator_context(*, item: EvalQuestion, plan: Any, orchestration_result: Any, raw_status: str) -> dict[str, Any]:
    from app.services.narrator_service import NarratorService

    if raw_status == "clarification":
        summary_text = NarratorService._build_clarification_summary(plan)  # noqa: SLF001
        return {
            "source_question_for_narrator": item.text,
            "source_execution_status_for_narrator": "clarification",
            "source_row_count_for_narrator": None,
            "source_columns_for_narrator": [],
            "source_summary_text_for_narrator": summary_text,
            "narrator_summary_source_stage": "clarification",
        }

    if raw_status == "validation_error":
        summary_text = NarratorService._build_validation_error_summary(orchestration_result.validation)  # noqa: SLF001
        return {
            "source_question_for_narrator": item.text,
            "source_execution_status_for_narrator": "validation_error",
            "source_row_count_for_narrator": None,
            "source_columns_for_narrator": [],
            "source_summary_text_for_narrator": summary_text,
            "narrator_summary_source_stage": "validation",
        }

    if raw_status == "compile_error":
        summary_text = NarratorService._build_execution_error_summary(orchestration_result)  # noqa: SLF001
        return {
            "source_question_for_narrator": item.text,
            "source_execution_status_for_narrator": "compile_error",
            "source_row_count_for_narrator": None,
            "source_columns_for_narrator": [],
            "source_summary_text_for_narrator": summary_text,
            "narrator_summary_source_stage": "compile",
        }

    if raw_status == "execution_error":
        summary_text = NarratorService._build_execution_error_summary(orchestration_result)  # noqa: SLF001
        row_count = None
        columns = []
        if orchestration_result and orchestration_result.execution_result:
            row_count = orchestration_result.execution_result.row_count
            columns = list(orchestration_result.execution_result.columns)
        return {
            "source_question_for_narrator": item.text,
            "source_execution_status_for_narrator": "execution_error",
            "source_row_count_for_narrator": row_count,
            "source_columns_for_narrator": columns,
            "source_summary_text_for_narrator": summary_text,
            "narrator_summary_source_stage": "execute",
        }

    summary_text = NarratorService._build_success_summary(orchestration_result)  # noqa: SLF001
    row_count = None
    columns = []
    if orchestration_result and orchestration_result.execution_result:
        row_count = orchestration_result.execution_result.row_count
        columns = list(orchestration_result.execution_result.columns)
    return {
        "source_question_for_narrator": item.text,
        "source_execution_status_for_narrator": raw_status,
        "source_row_count_for_narrator": row_count,
        "source_columns_for_narrator": columns,
        "source_summary_text_for_narrator": summary_text,
        "narrator_summary_source_stage": "execute",
    }


def _detect_narration_context_mismatch(*, item: EvalQuestion, narrator_trace: dict[str, Any], expected_context: dict[str, Any], raw_status: str) -> tuple[bool, list[str]]:
    mismatches: list[str] = []
    traced_question = narrator_trace.get("user_message")
    expected_question = expected_context.get("source_question_for_narrator")
    if raw_status != "clarification" and traced_question != expected_question:
        mismatches.append("question")
    traced_summary = narrator_trace.get("summary")
    if traced_summary != expected_context.get("source_summary_text_for_narrator"):
        mismatches.append("summary")
    return bool(mismatches), mismatches


def _compute_alignment(*, planner_question: str | None, execute_question: str | None, narrator_question: str | None, raw_status: str, item_question: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if planner_question not in {None, item_question}:
        errors.append("planner_question")
    if execute_question not in {None, item_question}:
        errors.append("execute_question")
    if raw_status != "clarification" and narrator_question not in {None, item_question}:
        errors.append("narrator_question")
    return len(errors) == 0, errors


def compute_trace_summary(results: list[EvalResult]) -> dict[str, Any]:
    total = len(results)

    def rate(n: int) -> float:
        return (n / total) if total else 0.0

    return {
        "business_success_rate": rate(sum(1 for r in results if r.business_status in {"success", "empty_result"})),
        "quality_pass_rate": rate(sum(1 for r in results if r.quality_status == "pass")),
        "safety_pass_rate": rate(sum(1 for r in results if r.safety_status == "pass")),
        "narrator_leak_rate": rate(sum(1 for r in results if any([
            r.final_narrator_chain_of_thought_leak,
            r.final_narrator_prompt_echo_leak,
            r.final_narrator_policy_echo_leak,
            r.final_narrator_sql_leak,
            r.final_narrator_presentation_leak,
            r.final_narrator_oracle_error_leak,
        ]))),
        "presentation_leak_rate": rate(sum(1 for r in results if r.final_narrator_presentation_leak)),
        "sql_leak_rate": rate(sum(1 for r in results if r.final_narrator_sql_leak)),
        "final_narrator_leak_rate": rate(sum(1 for r in results if any([
            r.final_narrator_chain_of_thought_leak,
            r.final_narrator_prompt_echo_leak,
            r.final_narrator_policy_echo_leak,
            r.final_narrator_sql_leak,
            r.final_narrator_presentation_leak,
            r.final_narrator_oracle_error_leak,
        ]))),
        "final_presentation_leak_rate": rate(sum(1 for r in results if r.final_narrator_presentation_leak)),
        "final_sql_leak_rate": rate(sum(1 for r in results if r.final_narrator_sql_leak)),
        "final_oracle_error_leak_rate": rate(sum(1 for r in results if r.final_narrator_oracle_error_leak)),
        "raw_narrator_leak_rate": rate(sum(1 for r in results if any([
            r.raw_narrator_chain_of_thought_leak,
            r.raw_narrator_prompt_echo_leak,
            r.raw_narrator_policy_echo_leak,
            r.raw_narrator_sql_leak,
            r.raw_narrator_presentation_leak,
            r.raw_narrator_oracle_error_leak,
        ]))),
        "raw_presentation_leak_rate": rate(sum(1 for r in results if r.raw_narrator_presentation_leak)),
        "raw_sql_leak_rate": rate(sum(1 for r in results if r.raw_narrator_sql_leak)),
        "raw_oracle_error_leak_rate": rate(sum(1 for r in results if r.raw_narrator_oracle_error_leak)),
        "first_fail_stage_counts": dict(Counter(r.first_failing_stage for r in results)),
        "root_cause_category_counts": dict(Counter(r.root_cause_category for r in results)),
        "trace_alignment_error_count": sum(1 for r in results if not r.stage_alignment_ok),
        "narration_context_mismatch_count": sum(1 for r in results if r.narration_context_mismatch),
        "sanitizer_effective_rate": rate(sum(1 for r in results if r.sanitizer_effective)),
        "final_response_mapping_error_count": sum(1 for r in results if r.final_response_mapping_error),
        "sanitizer_saved_response_count": sum(1 for r in results if r.sanitizer_effective and r.narration_ok),
        "raw_leak_but_final_clean_count": sum(1 for r in results if r.raw_leak_but_final_clean),
    }


def _plan_trace_view(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {field: _normalize_trace_value(plan.get(field)) for field in _TRACE_PLAN_FIELDS}


def _is_empty_trace_value(value: Any) -> bool:
    return value in (None, [], {}, "")


def _plan_diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    before_view = _plan_trace_view(before) or {}
    after_view = _plan_trace_view(after) or {}
    added: dict[str, Any] = {}
    removed: dict[str, Any] = {}
    changed: dict[str, Any] = {}
    for field in _TRACE_PLAN_FIELDS:
        left = before_view.get(field)
        right = after_view.get(field)
        if left == right:
            continue
        if _is_empty_trace_value(left) and not _is_empty_trace_value(right):
            added[field] = right
        elif not _is_empty_trace_value(left) and _is_empty_trace_value(right):
            removed[field] = left
        else:
            changed[field] = {"before": left, "after": right}
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "changed_fields": list(added.keys()) + list(removed.keys()) + list(changed.keys()),
    }


def _balanced_group(item: EvalQuestion) -> str:
    if item.category == "INVALID":
        return "invalid"
    if item.category in {"AMBIGUOUS", "CROSS_DOMAIN"}:
        return "ambiguous"
    if item.domain == "PO":
        return "po"
    if item.domain == "EMP":
        return "emp"
    return "other"


def _select_dataset_batch(
    dataset: list[EvalQuestion],
    *,
    max_questions: int | None,
    batch_index: int,
) -> list[EvalQuestion]:
    if max_questions is None or max_questions <= 0 or max_questions >= len(dataset):
        return dataset

    groups: dict[str, list[EvalQuestion]] = {
        "po": [],
        "emp": [],
        "ambiguous": [],
        "invalid": [],
        "other": [],
    }
    for item in dataset:
        groups[_balanced_group(item)].append(item)

    for key in groups:
        groups[key] = sorted(groups[key], key=lambda x: x.id)

    if max_questions == 20:
        target = {"po": 8, "emp": 8, "ambiguous": 2, "invalid": 2, "other": 0}
    else:
        target = {
            "po": max(0, round(max_questions * 0.40)),
            "emp": max(0, round(max_questions * 0.40)),
            "ambiguous": max(0, round(max_questions * 0.10)),
            "invalid": max(0, round(max_questions * 0.10)),
            "other": 0,
        }

    selected: list[EvalQuestion] = []
    consumed: dict[str, int] = {key: 0 for key in groups}
    for key in ("po", "emp", "ambiguous", "invalid", "other"):
        start = max(0, batch_index - 1) * target.get(key, 0)
        end = start + target.get(key, 0)
        chunk = groups[key][start:end]
        selected.extend(chunk)
        consumed[key] = end

    if len(selected) < max_questions:
        leftovers: list[EvalQuestion] = []
        for key in ("po", "emp", "ambiguous", "invalid", "other"):
            leftovers.extend(groups[key][consumed[key]:])
        leftovers = sorted(leftovers, key=lambda x: x.id)
        needed = max_questions - len(selected)
        selected.extend(leftovers[:needed])

    return sorted(selected[:max_questions], key=lambda x: x.id)


def _snapshot_wrong_plan_reasons(item: EvalQuestion, plan_snapshot: dict[str, Any] | None) -> list[str]:
    if plan_snapshot is None or item.category in {"AMBIGUOUS", "CROSS_DOMAIN"}:
        return []

    reasons: list[str] = []
    predicted_tables: list[str] = []
    if plan_snapshot.get("table"):
        predicted_tables.append(str(plan_snapshot["table"]))
    predicted_tables.extend(
        str(join.get("right_table"))
        for join in (plan_snapshot.get("joins") or [])
        if join.get("right_table")
    )

    if item.expected_table:
        predicted_upper = {t.upper() for t in predicted_tables}
        if item.expected_table.upper() not in predicted_upper:
            reasons.append("wrong_table")

    joins = plan_snapshot.get("joins") or []
    if item.category == "JOIN" and len(joins) == 0:
        reasons.append("wrong_join")

    aggs = plan_snapshot.get("aggregations") or []
    if item.expected_intent_type == "aggregation" and len(aggs) == 0:
        reasons.append("wrong_aggregation")

    expected_filter_hints = _expected_filter_columns(item.text)
    if expected_filter_hints:
        plan_filter_cols = {
            str((flt or {}).get("column", "")).lower()
            for flt in (plan_snapshot.get("filters") or [])
        }
        if "authorization_status" in expected_filter_hints and "authorization_status" not in plan_filter_cols:
            reasons.append("wrong_filter_column")
        if "birim_adi" in expected_filter_hints and "birim_adi" not in plan_filter_cols and item.expected_intent_type != "aggregation":
            reasons.append("wrong_filter_column")
        if "location_adi" in expected_filter_hints and "location_adi" not in plan_filter_cols and item.expected_intent_type != "aggregation":
            reasons.append("wrong_filter_column")
        if "ise_giris_tarihi" in expected_filter_hints and "ise_giris_tarihi" not in plan_filter_cols and item.domain == "EMP":
            reasons.append("wrong_filter_column")

    return sorted(set(reasons))


def _attribute_root_cause(item: EvalQuestion, result: EvalResult, trace: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    if result.narrator_sql_leak or result.narrator_presentation_leak:
        secondary = None
        if result.narrator_sql_leak and result.narrator_presentation_leak:
            secondary = "sql_and_presentation_leak"
        elif result.narrator_sql_leak:
            secondary = "sql_leak"
        else:
            secondary = "presentation_leak"
        return "narrator", secondary, None

    llm = trace.get("llm_raw_output") or {}
    if llm.get("parse_error"):
        return "parse", str(llm.get("parse_error")), None

    if result.status == "validation_error":
        validation = (trace.get("validation") or {})
        errors = validation.get("errors") or []
        primary = errors[0].get("code") if errors else (result.error_detail or "validation_error")
        secondary = errors[0].get("message") if errors else None
        return "validation", primary, secondary

    if result.status == "compile_error":
        return "compiler", result.error_detail, None

    if result.status == "execution_error":
        return "executor", result.execution_error_subtype or result.error_detail, None

    if result.status == "clarification":
        return "llm_output", result.clarification_class or "clarification_required", None

    if result.status == "wrong_plan":
        stages = [
            ("llm_output", trace.get("parsed_query_plan")),
            ("normalize", (trace.get("normalize") or {}).get("after")),
            ("repair", (trace.get("repair") or {}).get("after")),
            ("semantic", (trace.get("semantic_normalization") or {}).get("after")),
            ("canonicalize", (trace.get("canonicalization") or {}).get("after")),
        ]
        previous_reasons: list[str] = []
        for layer, snapshot in stages:
            reasons = _snapshot_wrong_plan_reasons(item, snapshot)
            if reasons and not previous_reasons:
                primary = reasons[0]
                secondary = reasons[1] if len(reasons) > 1 else None
                return layer, primary, secondary
            previous_reasons = reasons
        primary = result.wrong_plan_reasons[0] if result.wrong_plan_reasons else "wrong_plan"
        secondary = result.wrong_plan_reasons[1] if len(result.wrong_plan_reasons) > 1 else None
        return "dataset_expectation", primary, secondary

    return None, None, None


def _contains_thinking_leak(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return bool(
        "<think>" in lowered
        or "</think>" in lowered
        or "thinking process" in lowered
        or "analyze the request" in lowered
        or "draft" in lowered
        or "final polish" in lowered
        or re.search(r"(^|\n)\s*\d+[\.)]\s+(analyze|evaluate|draft|refine|final)", lowered)
    )


_PROMPT_ECHO_PAT = re.compile(
    r"\b(kullanıcı\s+sorusu|kullanici\s+sorusu|sonuç\s+özeti|sonuc\s+ozeti|yanıtını\s+ver|yanitini\s+ver|"
    r"user\s+question|result\s+summary|rules?:|constraints?:|kurallar:)\b",
    re.IGNORECASE,
)
_POLICY_ECHO_PAT = re.compile(
    r"\b(asla\s+sql|do\s+not\s+write|never\s+produce\s+sql|return\s+only\s+a\s+sentence|"
    r"yalnızca\s+verilen\s+özete\s+göre|yalnizca\s+verilen\s+ozete\s+gore|"
    r"oracle\s+hata\s+kod|no\s+oracle\s+error\s+codes|düşünce\s+süreci|dusunce\s+sureci|"
    r"thinking\s+process|analyze\s+the\s+request|draft\s+the\s+response|final\s+polish)\b",
    re.IGNORECASE,
)
_PRESENTATION_LEAK_PAT = re.compile(
    r"(^|\n)\s*(?:\d+[\.)]\s+|[-*]\s+|#{1,6}\s+)"
    r"(?:analyze|evaluate|draft|refine|thinking|reasoning|policy|rule|kural|constraint)",
    re.IGNORECASE,
)
_REAL_SQL_PAT = re.compile(
    r"\bSELECT\b[\s\S]{1,1200}\bFROM\s+[A-Z_][A-Z0-9_$#.]*\b",
    re.IGNORECASE,
)
_SQL_QUERY_SHAPE_PAT = re.compile(r"\b(WHERE|JOIN|GROUP\s+BY|ORDER\s+BY|ROWNUM|FETCH)\b", re.IGNORECASE)


def _contains_real_sql_leak(text: str | None) -> bool:
    if not text:
        return False
    if _POLICY_ECHO_PAT.search(text) and not _SQL_QUERY_SHAPE_PAT.search(text):
        return False
    for match in _REAL_SQL_PAT.finditer(text):
        snippet = match.group(0)
        if _POLICY_ECHO_PAT.search(snippet) or _PROMPT_ECHO_PAT.search(snippet):
            continue
        # Ignore policy echoes like "ASLA ... SELECT/FROM ifadesi".
        lowered = snippet.lower()
        if "select/from" in lowered and ("asla" in lowered or "do not" in lowered or "never" in lowered):
            continue
        if not _SQL_QUERY_SHAPE_PAT.search(text) and ";" not in text and "\n" not in text:
            continue
        return True
    return False


def _classify_narration_policy_violations(text: str | None) -> dict[str, Any]:
    if not text:
        return {
            "violations": [],
            "chain_of_thought_leak": False,
            "prompt_echo_leak": False,
            "policy_echo_leak": False,
            "sql_leak": False,
            "presentation_leak": False,
            "oracle_error_leak": False,
        }

    chain_of_thought = _contains_thinking_leak(text)
    prompt_echo = bool(_PROMPT_ECHO_PAT.search(text))
    policy_echo = bool(_POLICY_ECHO_PAT.search(text))
    sql_leak = _contains_real_sql_leak(text)
    presentation_leak = bool(_PRESENTATION_LEAK_PAT.search(text) or chain_of_thought)
    oracle_error = bool(re.search(r"ORA-\d{5}", text, re.IGNORECASE))

    violations: list[str] = []
    if chain_of_thought:
        violations.append("chain_of_thought_leak")
    if prompt_echo:
        violations.append("prompt_echo_leak")
    if policy_echo:
        violations.append("policy_echo_leak")
    if sql_leak:
        violations.append("sql_leak")
    if presentation_leak:
        violations.append("presentation_leak")
    if oracle_error:
        violations.append("oracle_error_leak")

    return {
        "violations": violations,
        "chain_of_thought_leak": chain_of_thought,
        "prompt_echo_leak": prompt_echo,
        "policy_echo_leak": policy_echo,
        "sql_leak": sql_leak,
        "presentation_leak": presentation_leak,
        "oracle_error_leak": oracle_error,
    }


def _fallback_narration_text(*, raw_status: str, expected_context: dict[str, Any]) -> tuple[str, str]:
    summary = str(expected_context.get("source_summary_text_for_narrator") or "")
    if raw_status == "clarification":
        m = re.search(r"Mesaj:\s*(.+)$", summary)
        return (m.group(1).strip() if m else "Lütfen soruyu biraz daha netleştirin."), "fallback_clarification"
    if raw_status in {"execution_error", "compile_error", "validation_error"}:
        return "İşlem tamamlanamadı. Lütfen daha sonra tekrar deneyin.", "fallback_error"
    if raw_status == "empty_result":
        return "Kriterlere uygun kayıt bulunamadı.", "fallback_summary"
    m = re.search(r"Satır sayısı:\s*(\d+)", summary, re.IGNORECASE)
    if m:
        count = int(m.group(1))
        if count <= 0:
            return "Kriterlere uygun kayıt bulunamadı.", "fallback_summary"
        return f"Toplam {count} kayıt listelendi.", "fallback_summary"
    return "Sorgu sonucu hazırlandı.", "fallback_summary"


def _sanitize_narration_output(
    *,
    raw_response: str | None,
    answer: str | None,
    raw_status: str,
    expected_context: dict[str, Any],
) -> dict[str, Any]:
    from app.services.narrator_service import NarratorService

    raw_text = (raw_response or "").strip() or None
    raw_checks = _classify_narration_policy_violations(raw_text)

    if raw_text is None:
        fallback_text, fallback_mode = _fallback_narration_text(raw_status=raw_status, expected_context=expected_context)
        sanitized_checks = _classify_narration_policy_violations(fallback_text)
        final_checks = _classify_narration_policy_violations(fallback_text)
        return {
            "sanitized_response": fallback_text,
            "final_response": fallback_text,
            "final_response_source": "fallback",
            "sanitizer_mode": fallback_mode,
            "sanitizer_applied": True,
            "sanitizer_effective": True,
            "raw_vs_final_changed": True,
            "sanitizer_actions": [fallback_mode],
            "raw_response_policy_violations": [],
            "sanitized_response_policy_violations": sanitized_checks["violations"],
            "final_response_policy_violations": final_checks["violations"],
            "raw_checks": raw_checks,
            "sanitized_checks": sanitized_checks,
            "final_checks": final_checks,
        }

    sanitized = NarratorService._strip_leakage(raw_text)  # noqa: SLF001
    sanitized = (sanitized or "").strip()
    if sanitized == "Sorgu işlendi." and raw_checks["violations"]:
        fallback_text, fallback_mode = _fallback_narration_text(raw_status=raw_status, expected_context=expected_context)
        sanitized_checks = _classify_narration_policy_violations(fallback_text)
        final_checks = _classify_narration_policy_violations(fallback_text)
        return {
            "sanitized_response": fallback_text,
            "final_response": fallback_text,
            "final_response_source": "fallback",
            "sanitizer_mode": fallback_mode,
            "sanitizer_applied": True,
            "sanitizer_effective": raw_text != fallback_text,
            "raw_vs_final_changed": True,
            "sanitizer_actions": [fallback_mode],
            "raw_response_policy_violations": raw_checks["violations"],
            "sanitized_response_policy_violations": sanitized_checks["violations"],
            "final_response_policy_violations": final_checks["violations"],
            "raw_checks": raw_checks,
            "sanitized_checks": sanitized_checks,
            "final_checks": final_checks,
        }
    sanitized_checks = _classify_narration_policy_violations(sanitized)

    if sanitized and not sanitized_checks["violations"]:
        if raw_checks["violations"]:
            mode = "strip_reasoning"
            source = "sanitized"
        else:
            mode = "pass_through"
            source = "raw"
        final_response = sanitized if source == "sanitized" else raw_text
        final_checks = _classify_narration_policy_violations(final_response)
        return {
            "sanitized_response": sanitized,
            "final_response": final_response,
            "final_response_source": source,
            "sanitizer_mode": mode,
            "sanitizer_applied": source != "raw",
            "sanitizer_effective": raw_text != sanitized,
            "raw_vs_final_changed": raw_text != final_response,
            "sanitizer_actions": (["strip_reasoning"] if source == "sanitized" else []),
            "raw_response_policy_violations": raw_checks["violations"],
            "sanitized_response_policy_violations": sanitized_checks["violations"],
            "final_response_policy_violations": final_checks["violations"],
            "raw_checks": raw_checks,
            "sanitized_checks": sanitized_checks,
            "final_checks": final_checks,
        }

    fallback_text, fallback_mode = _fallback_narration_text(raw_status=raw_status, expected_context=expected_context)
    sanitized_checks = _classify_narration_policy_violations(fallback_text)
    final_checks = _classify_narration_policy_violations(fallback_text)
    return {
        "sanitized_response": fallback_text,
        "final_response": fallback_text,
        "final_response_source": "fallback",
        "sanitizer_mode": fallback_mode,
        "sanitizer_applied": True,
        "sanitizer_effective": raw_text != fallback_text,
        "raw_vs_final_changed": True,
        "sanitizer_actions": [fallback_mode],
        "raw_response_policy_violations": raw_checks["violations"],
        "sanitized_response_policy_violations": sanitized_checks["violations"],
        "final_response_policy_violations": final_checks["violations"],
        "raw_checks": raw_checks,
        "sanitized_checks": sanitized_checks,
        "final_checks": final_checks,
    }


def _stage_note(*, ok: bool, note: str, stage_outcome: str) -> dict[str, Any]:
    return {"ok": ok, "note": note, "stage_outcome": stage_outcome}


def _classify_root_cause_category(result: EvalResult, trace: dict[str, Any]) -> tuple[str, str]:
    llm = trace.get("llm_raw_output") or {}
    parse_error = llm.get("parse_error")
    if parse_error:
        return "planner_output", f"planner_parse_error:{parse_error}"

    if result.raw_status == "validation_error":
        validation = trace.get("validation") or {}
        errors = validation.get("errors") or []
        code = errors[0].get("code") if errors else "validation_error"
        return "validation_failure", f"validation:{code}"

    if result.raw_status == "compile_error":
        err = ((trace.get("compile") or {}).get("error") or result.error_detail or "compile_error")
        return "compile_failure", f"compile:{err}"

    if result.raw_status == "execution_error":
        subtype = result.execution_error_subtype or "execution_error"
        return "execution_failure", f"execute:{subtype}"

    final_violations = ((trace.get("narration") or {}).get("final_response_policy_violations") or [])
    if final_violations:
        if result.final_narrator_sql_leak:
            return "narrator_leak", "narrator:sql_leak"
        return "narrator_leak", "narrator:chain_of_thought_or_reasoning_leak"

    semantic_diff = (trace.get("semantic_normalization") or {}).get("diff") or {}
    semantic_changed = bool((semantic_diff.get("changed_fields") or []))
    if semantic_changed and result.status in {"wrong_plan", "clarification", "validation_error", "compile_error", "execution_error"}:
        return "semantic_override", "semantic:critical_override"

    repair = trace.get("repair") or {}
    if repair.get("repair_applied") and result.status in {"wrong_plan", "clarification", "validation_error", "compile_error", "execution_error"}:
        return "repair_mutation", "repair:critical_mutation"

    return "unknown", "unknown"


def _determine_root_cause_stage(result: EvalResult, trace: dict[str, Any]) -> str:
    llm = trace.get("llm_raw_output") or {}
    if llm.get("parse_error"):
        return "planner"
    if result.raw_status == "validation_error":
        return "validation"
    if result.raw_status == "compile_error":
        return "compile"
    if result.raw_status == "execution_error":
        return "execute"
    if not result.narration_ok:
        return "narration"
    return "none"


def _compute_diff_flags(trace: dict[str, Any], narration: dict[str, Any] | None) -> dict[str, bool]:
    semantic_keys = {"semantic_intent", "root_entity", "joins", "group_by", "aggregations", "filters"}
    sql_shape_keys = {"select_columns", "filters", "aggregations", "joins", "order_by", "limit", "table", "group_by"}

    semantic_diff = (trace.get("semantic_normalization") or {}).get("diff") or {}
    canonical_diff = (trace.get("canonicalization") or {}).get("diff") or {}
    compile_trace = trace.get("compile") or {}
    sql_shape_comparable = bool(
        compile_trace.get("stage_outcome") == "passed"
        and (compile_trace.get("sql") or compile_trace.get("compiled_sql"))
    )

    changed_fields = set(canonical_diff.get("changed_fields") or [])

    raw_response = (narration or {}).get("raw_response")
    final_response = (narration or {}).get("final_response")
    changed_user_visible_output = bool(raw_response is not None and final_response is not None and raw_response != final_response)

    return {
        "changed_semantics": bool(set(semantic_diff.get("changed_fields") or []).intersection(semantic_keys)),
        "sql_shape_comparable": sql_shape_comparable,
        "changed_sql_shape": bool(sql_shape_comparable and changed_fields.intersection(sql_shape_keys)),
        "changed_user_visible_output": changed_user_visible_output,
    }


def _make_stage_statuses(result: EvalResult, trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    llm = trace.get("llm_raw_output") or {}
    parse_error = llm.get("parse_error")

    planner_ok = not bool(parse_error)
    planner_stage = _stage_note(
        ok=planner_ok,
        note="planner output parsed" if planner_ok else f"planner parse error: {parse_error}",
        stage_outcome="passed" if planner_ok else "failed",
    )

    if planner_ok:
        repair_stage = _stage_note(ok=True, note="repair completed", stage_outcome="passed")
        semantic_stage = _stage_note(ok=True, note="semantic normalization completed", stage_outcome="passed")
    else:
        repair_stage = _stage_note(ok=False, note="repair skipped due to planner failure", stage_outcome="skipped")
        semantic_stage = _stage_note(ok=False, note="semantic normalization skipped due to planner failure", stage_outcome="skipped")

    validation_trace = trace.get("validation") or {}
    if not planner_ok or result.raw_status == "clarification":
        validation_stage = _stage_note(ok=False, note="validation skipped", stage_outcome="skipped")
    elif result.raw_status == "validation_error" or validation_trace.get("ok") is False:
        validation_stage = _stage_note(ok=False, note="validation failed", stage_outcome="failed")
    else:
        validation_stage = _stage_note(ok=True, note="validation passed", stage_outcome="passed")

    compile_trace = trace.get("compile") or {}
    if validation_stage["stage_outcome"] == "skipped" or validation_stage["stage_outcome"] == "failed":
        compile_stage = _stage_note(ok=False, note="compile skipped", stage_outcome="skipped")
    elif result.raw_status == "compile_error" or compile_trace.get("ok") is False:
        compile_stage = _stage_note(ok=False, note="compile failed", stage_outcome="failed")
    else:
        compile_stage = _stage_note(ok=True, note="compile passed", stage_outcome="passed")

    execute_trace = trace.get("execute") or {}
    if compile_stage["stage_outcome"] != "passed":
        execute_stage = _stage_note(ok=False, note="execution skipped", stage_outcome="skipped")
    elif result.raw_status == "execution_error":
        execute_stage = _stage_note(ok=False, note="execution failed", stage_outcome="failed")
    elif execute_trace.get("status") in {"success", "empty"}:
        execute_stage = _stage_note(ok=True, note="execution passed", stage_outcome="passed")
    else:
        execute_stage = _stage_note(ok=False, note="execution skipped", stage_outcome="skipped")

    narration = trace.get("narration") or {}
    narration_ok = bool(
        narration.get("final_response")
        and not narration.get("final_response_policy_violations")
        and not result.final_response_mapping_error
    )
    if narration.get("available"):
        narration_stage = _stage_note(
            ok=narration_ok,
            note="narration safe" if narration_ok else "final narration output violated safety or mapping rules",
            stage_outcome="passed" if narration_ok else "failed",
        )
    else:
        narration_stage = _stage_note(ok=False, note="narration skipped", stage_outcome="skipped")

    return {
        "planner": planner_stage,
        "repair": repair_stage,
        "semantic": semantic_stage,
        "validation": validation_stage,
        "compile": compile_stage,
        "execute": execute_stage,
        "narration": narration_stage,
        "_flags": {
            "repair_applied": bool((trace.get("repair") or {}).get("repair_applied")),
            "semantic_changed": bool(((trace.get("semantic_normalization") or {}).get("diff") or {}).get("changed_fields")),
            "narration_sql_leak": bool(narration.get("final_sql_leak", False)),
            "narration_presentation_leak": bool(narration.get("final_presentation_leak", False)),
            "narration_cot_leak": bool(narration.get("final_chain_of_thought_leak", False)),
        },
    }


def _determine_fail_stages(result: EvalResult, stage_statuses: dict[str, dict[str, Any]], root_cause_category: str) -> tuple[str, str]:
    order = ["planner", "repair", "semantic", "validation", "compile", "execute", "narration"]
    failed = [stage for stage in order if (stage_statuses.get(stage) or {}).get("stage_outcome") == "failed"]

    if not failed:
        return "none", "none"

    uniq: list[str] = []
    for s in failed:
        if s not in uniq:
            uniq.append(s)
    return uniq[0], uniq[-1]


def _build_business_quality_safety(result: EvalResult, stage_statuses: dict[str, dict[str, Any]]) -> tuple[str, str, str]:
    business_status = result.raw_status if result.raw_status in {
        "success",
        "clarification",
        "validation_error",
        "compile_error",
        "execution_error",
        "empty_result",
    } else "execution_error"

    safety_fail = (stage_statuses.get("narration") or {}).get("stage_outcome") == "failed"
    quality_fail = (
        result.wrong_plan
        or (stage_statuses.get("planner") or {}).get("stage_outcome") == "failed"
        or (stage_statuses.get("validation") or {}).get("stage_outcome") == "failed"
        or (stage_statuses.get("compile") or {}).get("stage_outcome") == "failed"
        or (stage_statuses.get("execute") or {}).get("stage_outcome") == "failed"
        or safety_fail
    )

    return business_status, ("fail" if quality_fail else "pass"), ("fail" if safety_fail else "pass")


def _build_llm_call_summary(
    *,
    stage: str,
    request_prompt: str | None,
    response_raw: str | None,
    response_final: str | None = None,
    parse_error: str | None = None,
    error: str | None = None,
    model: str | None = None,
    latency_ms: int | None = None,
    leak_detected: bool = False,
    clarification_detected: bool = False,
    response_policy_ok: bool | None = None,
) -> dict[str, Any]:
    response_parse_ok = bool(response_raw) and not bool(parse_error or error)
    policy_ok = response_policy_ok if response_policy_ok is not None else True
    response_shape_ok = bool(response_parse_ok and policy_ok)
    return {
        "stage": stage,
        "model": model,
        "latency_ms": latency_ms,
        "tokens_in": None,
        "tokens_out": None,
        "stop_reason": None,
        "parse_error": parse_error or error,
        "response_parse_ok": response_parse_ok,
        "response_policy_ok": policy_ok,
        "response_shape_ok": response_shape_ok,
        "leak_detected": leak_detected,
        "clarification_detected": clarification_detected,
        "request_prompt": request_prompt,
        "response_raw": response_raw,
        "response_final": response_final,
    }


def _render_short_verdict_index(traces: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for idx, trace in enumerate(traces, 1):
        final = trace.get("final_judgment") or {}
        quality_label = "quality_pass" if final.get("quality_status") == "pass" else "quality_fail"
        rows.append(
            f"- Q{idx:02d} | {final.get('business_status')} | {quality_label} | "
            f"{final.get('first_failing_stage')} | {final.get('root_cause_category')}"
        )
    return rows


def _render_plan_compact(plan: dict[str, Any] | None) -> str:
    if not plan:
        return "{}"
    parts: list[str] = []
    table = plan.get("table")
    if table:
        parts.append(f"table={table}")
    if plan.get("select_columns"):
        parts.append(f"select={plan['select_columns']}")
    if plan.get("filters"):
        parts.append(f"filters={plan['filters']}")
    if plan.get("aggregations"):
        parts.append(f"aggs={plan['aggregations']}")
    if plan.get("group_by"):
        parts.append(f"group_by={plan['group_by']}")
    if plan.get("semantic_intent"):
        parts.append(f"semantic_intent={plan['semantic_intent']}")
    if plan.get("join_path_id"):
        parts.append(f"join_path_id={plan['join_path_id']}")
    if plan.get("needs_clarification"):
        parts.append(f"clarification={plan.get('clarification_message')}")
    return "; ".join(parts) if parts else "{}"


def _build_trace_markdown(traces: list[dict[str, Any]]) -> str:
    lines: list[str] = ["# Question Trace Report", ""]
    for trace in traces:
        question = trace.get("input") or {}
        lines.extend(
            [
                f"## {question.get('question_id')} - {question.get('question')}",
                f"- domain/category: {question.get('domain')}/{question.get('category')}",
                f"- expected_table: {question.get('expected_table')}",
                f"- expected_intent_type: {question.get('expected_intent_type')}",
                f"- final_status: {trace.get('final_judgment', {}).get('status')}",
                f"- root_cause_stage: {trace.get('final_judgment', {}).get('root_cause_stage')}",
                f"- root_cause_category: {trace.get('final_judgment', {}).get('root_cause_category')}",
                f"- primary_failure_reason: {trace.get('final_judgment', {}).get('primary_failure_reason')}",
                "",
                "### Retrieval",
                f"- schema_tables: {(trace.get('retrieval') or {}).get('schema_tables')}",
                f"- schema_docs: {[doc.get('doc_id') for doc in ((trace.get('retrieval') or {}).get('schema_docs') or [])]}",
                f"- examples: {[ex.get('doc_id') for ex in ((trace.get('retrieval') or {}).get('examples') or [])]}",
                f"- sufficiency: {(trace.get('retrieval') or {}).get('retrieval_assessment')}",
                "",
                "### Prompt",
                f"- prompt_length: {(trace.get('prompt') or {}).get('prompt_length')}",
                f"- prompt_budget: {(trace.get('prompt') or {}).get('prompt_budget')}",
                f"- prompt_truncated: {(trace.get('prompt') or {}).get('prompt_truncated')}",
                f"- reduction_steps: {(trace.get('prompt') or {}).get('reduction_steps')}",
                "",
                "### LLM",
                f"- parse_error: {(trace.get('llm_raw_output') or {}).get('parse_error')}",
                f"- parsed_plan: {_render_plan_compact(trace.get('parsed_query_plan'))}",
                "",
                "### Normalize",
                f"- diff: {(trace.get('normalize') or {}).get('diff')}",
                "",
                "### Repair",
                f"- actions: {[a.get('repair_type') for a in ((trace.get('repair') or {}).get('repair_actions') or [])]}",
                f"- diff: {(trace.get('repair') or {}).get('diff')}",
                "",
                "### Semantic",
                f"- semantic_intent: {(trace.get('semantic_normalization') or {}).get('semantic_intent')}",
                f"- root_entity: {(trace.get('semantic_normalization') or {}).get('root_entity')}",
                f"- join_path_id: {(trace.get('semantic_normalization') or {}).get('join_path_id')}",
                f"- diff: {(trace.get('semantic_normalization') or {}).get('diff')}",
                "",
                "### Validation",
                f"- ok: {(trace.get('validation') or {}).get('ok')}",
                f"- errors: {(trace.get('validation') or {}).get('errors')}",
                "",
                "### Compile",
                f"- error: {(trace.get('compile') or {}).get('error')}",
            ]
        )
        sql = (trace.get("compile") or {}).get("sql")
        if sql:
            lines.extend(["```sql", sql, "```"])
        lines.extend(
            [
                "### Execute",
                f"- status: {(trace.get('execute') or {}).get('status')}",
                f"- row_count: {(trace.get('execute') or {}).get('row_count')}",
                f"- latency_ms: {(trace.get('execute') or {}).get('execution_time_ms') or (trace.get('execute') or {}).get('latency_ms')}",
                f"- error: {(trace.get('execute') or {}).get('error_message')}",
                "",
                "### Narration",
                f"- raw_response: {(trace.get('narration') or {}).get('raw_response')}",
                f"- sanitized_response: {(trace.get('narration') or {}).get('sanitized_response')}",
                f"- final_response: {(trace.get('narration') or {}).get('final_response')}",
                f"- raw_response_policy_violations: {(trace.get('narration') or {}).get('raw_response_policy_violations')}",
                f"- sanitized_response_policy_violations: {(trace.get('narration') or {}).get('sanitized_response_policy_violations')}",
                f"- final_response_policy_violations: {(trace.get('narration') or {}).get('final_response_policy_violations')}",
                f"- raw_chain_of_thought_leak: {(trace.get('narration') or {}).get('raw_chain_of_thought_leak')}",
                f"- raw_prompt_echo_leak: {(trace.get('narration') or {}).get('raw_prompt_echo_leak')}",
                f"- raw_policy_echo_leak: {(trace.get('narration') or {}).get('raw_policy_echo_leak')}",
                f"- raw_sql_leak: {(trace.get('narration') or {}).get('raw_sql_leak')}",
                f"- raw_presentation_leak: {(trace.get('narration') or {}).get('raw_presentation_leak')}",
                f"- raw_oracle_error_leak: {(trace.get('narration') or {}).get('raw_oracle_error_leak')}",
                f"- final_chain_of_thought_leak: {(trace.get('narration') or {}).get('final_chain_of_thought_leak')}",
                f"- final_prompt_echo_leak: {(trace.get('narration') or {}).get('final_prompt_echo_leak')}",
                f"- final_policy_echo_leak: {(trace.get('narration') or {}).get('final_policy_echo_leak')}",
                f"- final_sql_leak: {(trace.get('narration') or {}).get('final_sql_leak')}",
                f"- final_presentation_leak: {(trace.get('narration') or {}).get('final_presentation_leak')}",
                f"- final_oracle_error_leak: {(trace.get('narration') or {}).get('final_oracle_error_leak')}",
                f"- narration_ok: {(trace.get('narration') or {}).get('narration_ok')}",
                f"- sql_leak: {(trace.get('narration') or {}).get('sql_leak')}",
                f"- presentation_leak: {(trace.get('narration') or {}).get('presentation_leak')}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _build_single_output_markdown(
    dataset: list[EvalQuestion],
    results: list[EvalResult],
    summary: EvalSummary,
    traces: list[dict[str, Any]],
    run_info: dict[str, Any],
) -> str:
    lines: list[str] = [
        "# NL2SQL Eval Trace (Single File)",
        "",
        "## Summary",
        f"- llm_provider: {run_info.get('llm_provider')}",
        f"- executor: {run_info.get('executor')}",
        f"- oracle_enabled: {run_info.get('oracle_enabled')}",
        f"- dataset_path: {run_info.get('dataset_path')}",
        f"- run_name: {run_info.get('run_name')}",
        f"- total_questions: {len(dataset)}",
        f"- success_rate: {_format_pct(summary.success_rate)}",
        f"- business_success_rate: {_format_pct(summary.business_success_rate)}",
        f"- quality_pass_rate: {_format_pct(summary.quality_pass_rate)}",
        f"- safety_pass_rate: {_format_pct(summary.safety_pass_rate)}",
        f"- clarification_rate: {_format_pct(summary.clarification_rate)}",
        f"- wrong_plan_rate: {_format_pct(summary.wrong_plan_rate)}",
        f"- validation_error_rate: {_format_pct(summary.validation_error_rate)}",
        f"- compile_error_rate: {_format_pct(summary.compile_error_rate)}",
        f"- execution_error_rate: {_format_pct(summary.execution_error_rate)}",
        f"- narrator_leak_rate: {_format_pct(summary.narrator_leak_rate)}",
        f"- presentation_leak_rate: {_format_pct(summary.presentation_leak_rate)}",
        f"- sql_leak_rate: {_format_pct(summary.sql_leak_rate)}",
        f"- final_narrator_leak_rate: {_format_pct(summary.final_narrator_leak_rate)}",
        f"- final_presentation_leak_rate: {_format_pct(summary.final_presentation_leak_rate)}",
        f"- final_sql_leak_rate: {_format_pct(summary.final_sql_leak_rate)}",
        f"- final_oracle_error_leak_rate: {_format_pct(summary.final_oracle_error_leak_rate)}",
        f"- raw_narrator_leak_rate: {_format_pct(summary.raw_narrator_leak_rate)}",
        f"- raw_presentation_leak_rate: {_format_pct(summary.raw_presentation_leak_rate)}",
        f"- raw_sql_leak_rate: {_format_pct(summary.raw_sql_leak_rate)}",
        f"- raw_oracle_error_leak_rate: {_format_pct(summary.raw_oracle_error_leak_rate)}",
        f"- planner_parse_fail_rate: {_format_pct(summary.planner_parse_fail_rate)}",
        f"- repair_apply_rate: {_format_pct(summary.repair_apply_rate)}",
        f"- semantic_override_rate: {_format_pct(summary.semantic_override_rate)}",
        f"- sql_shape_changed_rate: {_format_pct(summary.sql_shape_changed_rate)}",
        f"- trace_alignment_error_count: {summary.trace_alignment_error_count}",
        f"- narration_context_mismatch_count: {summary.narration_context_mismatch_count}",
        f"- sanitizer_effective_rate: {_format_pct(summary.sanitizer_effective_rate)}",
        f"- final_response_mapping_error_count: {summary.final_response_mapping_error_count}",
        f"- sanitizer_saved_response_count: {summary.sanitizer_saved_response_count}",
        f"- raw_leak_but_final_clean_count: {summary.raw_leak_but_final_clean_count}",
        f"- avg_latency_ms: {summary.avg_latency_ms:.1f}",
        f"- p95_latency_ms: {summary.p95_latency_ms:.1f}",
        "",
        "## Status Counts",
    ]
    for status, count in sorted(summary.counts.items(), key=lambda x: x[0]):
        lines.append(f"- {status}: {count}")

    lines.extend(["", "## First Fail Stage Counts"])
    for stage, count in sorted(summary.first_fail_stage_counts.items(), key=lambda x: x[0]):
        lines.append(f"- {stage}: {count}")

    lines.extend(["", "## Root Cause Category Counts"])
    for cat, count in sorted(summary.root_cause_category_counts.items(), key=lambda x: x[0]):
        lines.append(f"- {cat}: {count}")

    lines.extend(["", "## Short Verdict Index"])
    lines.extend(_render_short_verdict_index(traces))

    lines.extend(["", "## Question Traces", ""])

    for idx, trace in enumerate(traces, 1):
        question = trace.get("input") or {}
        final = trace.get("final_judgment") or {}
        lines.extend(
            [
                "\n" + "=" * 90,
                f"QUESTION {idx:02d} | {question.get('question_id')} | {question.get('domain')}/{question.get('category')}",
                "=" * 90,
                f"Question: {question.get('question')}",
                f"Expected: table={question.get('expected_table')} intent_type={question.get('expected_intent_type')}",
                "Final:",
                f"business={final.get('business_status')}",
                f"quality={final.get('quality_status')}",
                f"safety={final.get('safety_status')}",
                f"raw_status={final.get('raw_status')}",
                f"root_cause_stage={final.get('root_cause_stage')}",
                f"root_cause_category={final.get('root_cause_category')}",
                f"Failure: primary={final.get('primary_failure_reason')} secondary={final.get('secondary_failure_reason')}",
                f"Trace: trace_id={trace.get('trace_id')} stage_alignment_ok={trace.get('stage_alignment_ok')} narration_context_mismatch={(trace.get('narration') or {}).get('narration_context_mismatch')}",
                "",
                "### Verdict Card",
                f"- trace_id: {final.get('trace_id')}",
                f"- business_status: {final.get('business_status')}",
                f"- quality_status: {final.get('quality_status')}",
                f"- safety_status: {final.get('safety_status')}",
                f"- root_cause_stage: {final.get('root_cause_stage')}",
                f"- first_failing_stage: {final.get('first_failing_stage')}",
                f"- final_failing_stage: {final.get('final_failing_stage')}",
                f"- root_cause_category: {final.get('root_cause_category')}",
                f"- root_cause_detail: {final.get('root_cause_detail')}",
                f"- business_failure_stage: {final.get('business_failure_stage')}",
                f"- quality_failure_stage: {final.get('quality_failure_stage')}",
                f"- safety_failure_stage: {final.get('safety_failure_stage')}",
                f"- planner_ok: {final.get('planner_ok')}",
                f"- repair_ok: {final.get('repair_ok')}",
                f"- semantic_ok: {final.get('semantic_ok')}",
                f"- validation_ok: {final.get('validation_ok')}",
                f"- compile_ok: {final.get('compile_ok')}",
                f"- execute_ok: {final.get('execute_ok')}",
                f"- narration_ok: {final.get('narration_ok')}",
                f"- stage_alignment_ok: {final.get('stage_alignment_ok')}",
                f"- alignment_errors: {final.get('alignment_errors')}",
                f"- narration_context_mismatch: {final.get('narration_context_mismatch')}",
                f"- narration_context_mismatch_fields: {final.get('narration_context_mismatch_fields')}",
                f"- final_response_source: {final.get('final_response_source')}",
                f"- sanitizer_effective: {final.get('sanitizer_effective')}",
                f"- narrator_summary_source_stage: {final.get('narrator_summary_source_stage')}",
                f"- narrator_final_source_stage: {final.get('narrator_final_source_stage')}",
                "",
                "### Retrieval",
                f"- schema_tables: {(trace.get('retrieval') or {}).get('schema_tables')}",
                f"- schema_docs: {[doc.get('doc_id') for doc in ((trace.get('retrieval') or {}).get('schema_docs') or [])]}",
                f"- examples: {[ex.get('doc_id') for ex in ((trace.get('retrieval') or {}).get('examples') or [])]}",
                f"- sufficiency: {(trace.get('retrieval') or {}).get('retrieval_assessment')}",
                "",
                "### Prompt",
                f"- prompt_length: {(trace.get('prompt') or {}).get('prompt_length')}",
                f"- prompt_budget: {(trace.get('prompt') or {}).get('prompt_budget')}",
                f"- prompt_truncated: {(trace.get('prompt') or {}).get('prompt_truncated')}",
                f"- reduction_steps: {(trace.get('prompt') or {}).get('reduction_steps')}",
                "",
            ]
        )

        llm_calls = trace.get("llm_calls") or []
        if llm_calls:
            lines.append("### LLM Calls (Full Request/Response)")
            for call in llm_calls:
                lines.extend(
                    [
                        f"- stage: {call.get('stage')}",
                        f"- model: {call.get('model')}",
                        f"- latency_ms: {call.get('latency_ms')}",
                        f"- tokens_in: {call.get('tokens_in')}",
                        f"- tokens_out: {call.get('tokens_out')}",
                        f"- stop_reason: {call.get('stop_reason')}",
                        f"- parse_error: {call.get('parse_error') or call.get('error')}",
                        f"- response_parse_ok: {call.get('response_parse_ok')}",
                        f"- response_policy_ok: {call.get('response_policy_ok')}",
                        f"- response_shape_ok: {call.get('response_shape_ok')}",
                        f"- leak_detected: {call.get('leak_detected')}",
                        f"- clarification_detected: {call.get('clarification_detected')}",
                        "- request_prompt:",
                        "```text",
                        str(call.get("request_prompt") or ""),
                        "```",
                        "- response_raw:",
                        "```text",
                        str(call.get("response_raw") or ""),
                        "```",
                    ]
                )
                if call.get("response_final") is not None:
                    lines.extend(
                        [
                            "- response_final:",
                            "```text",
                            str(call.get("response_final") or ""),
                            "```",
                        ]
                    )
        else:
            lines.append("### LLM Calls (Full Request/Response)")
            lines.append("- none")

        lines.extend(
            [
                "",
                "### Stage Diffs",
                f"- normalize.diff: {(trace.get('normalize') or {}).get('diff')}",
                f"- repair.diff: {(trace.get('repair') or {}).get('diff')}",
                f"- semantic.diff: {(trace.get('semantic_normalization') or {}).get('diff')}",
                f"- canonicalize.diff: {(trace.get('canonicalization') or {}).get('diff')}",
                f"- changed_semantics: {(trace.get('stage_diff_flags') or {}).get('changed_semantics')}",
                f"- sql_shape_comparable: {(trace.get('stage_diff_flags') or {}).get('sql_shape_comparable')}",
                f"- changed_sql_shape: {(trace.get('stage_diff_flags') or {}).get('changed_sql_shape')}",
                f"- changed_user_visible_output: {(trace.get('stage_diff_flags') or {}).get('changed_user_visible_output')}",
                "",
                "### Stage Status",
                f"- planner.status: {(trace.get('stage_status') or {}).get('planner')}",
                f"- repair.status: {(trace.get('stage_status') or {}).get('repair')}",
                f"- semantic.status: {(trace.get('stage_status') or {}).get('semantic')}",
                f"- validation.status: {(trace.get('stage_status') or {}).get('validation')}",
                f"- compile.status: {(trace.get('stage_status') or {}).get('compile')}",
                f"- execute.status: {(trace.get('stage_status') or {}).get('execute')}",
                f"- narration.status: {(trace.get('stage_status') or {}).get('narration')}",
                f"- planner_question: {trace.get('planner_question')}",
                f"- execute_question: {trace.get('execute_question')}",
                f"- narrator_question: {trace.get('narrator_question')}",
                "",
                "### Validation",
                f"- ok: {(trace.get('validation') or {}).get('ok')}",
                f"- errors: {(trace.get('validation') or {}).get('errors')}",
                "",
                "### Compile",
                f"- error: {(trace.get('compile') or {}).get('error')}",
                f"- selected_columns_count: {(trace.get('compile') or {}).get('selected_columns_count')}",
                f"- filter_count: {(trace.get('compile') or {}).get('filter_count')}",
                f"- join_count: {(trace.get('compile') or {}).get('join_count')}",
                f"- aggregation_count: {(trace.get('compile') or {}).get('aggregation_count')}",
                f"- group_by_count: {(trace.get('compile') or {}).get('group_by_count')}",
                f"- bind_param_count: {(trace.get('compile') or {}).get('bind_param_count')}",
                f"- expression_count: {(trace.get('compile') or {}).get('expression_count')}",
                f"- compile_warning_list: {(trace.get('compile') or {}).get('compile_warning_list')}",
                f"- compile_input_plan_snapshot: {(trace.get('compile') or {}).get('compile_input_plan_snapshot')}",
                f"- compile_input_diff_from_planner_raw: {(trace.get('compile') or {}).get('compile_input_diff_from_planner_raw')}",
                f"- compile_input_diff_from_semantic: {(trace.get('compile') or {}).get('compile_input_diff_from_semantic')}",
                f"- compiled_sql_source_plan_stage: {(trace.get('compile') or {}).get('compiled_sql_source_plan_stage')}",
            ]
        )
        sql = (trace.get("compile") or {}).get("sql")
        if sql:
            lines.extend(["```sql", sql, "```"])
        lines.extend(
            [
                "### Execute",
                f"- status: {(trace.get('execute') or {}).get('status')}",
                f"- row_count: {(trace.get('execute') or {}).get('row_count')}",
                f"- latency_ms: {(trace.get('execute') or {}).get('execution_time_ms') or (trace.get('execute') or {}).get('latency_ms')}",
                f"- executor_class: {(trace.get('execute') or {}).get('executor_class')}",
                f"- db_latency_ms: {(trace.get('execute') or {}).get('db_latency_ms')}",
                f"- fetch_latency_ms: {(trace.get('execute') or {}).get('fetch_latency_ms')}",
                f"- timeout_applied: {(trace.get('execute') or {}).get('timeout_applied')}",
                f"- row_limit_applied: {(trace.get('execute') or {}).get('row_limit_applied')}",
                f"- rows_returned_before_limit: {(trace.get('execute') or {}).get('rows_returned_before_limit')}",
                f"- rows_returned_after_limit: {(trace.get('execute') or {}).get('rows_returned_after_limit')}",
                f"- error: {(trace.get('execute') or {}).get('error') or (trace.get('execute') or {}).get('error_message')}",
                f"- execution_error_subtype: {(trace.get('execute') or {}).get('execution_error_subtype')}",
                "",
                "### Narration",
                f"- raw_response: {(trace.get('narration') or {}).get('raw_response')}",
                f"- sanitized_response: {(trace.get('narration') or {}).get('sanitized_response')}",
                f"- final_response: {(trace.get('narration') or {}).get('final_response')}",
                f"- final_response_source: {(trace.get('narration') or {}).get('final_response_source')}",
                f"- raw_vs_final_changed: {(trace.get('narration') or {}).get('raw_vs_final_changed')}",
                f"- sanitizer_applied: {(trace.get('narration') or {}).get('sanitizer_applied')}",
                f"- sanitizer_effective: {(trace.get('narration') or {}).get('sanitizer_effective')}",
                f"- sanitizer_mode: {(trace.get('narration') or {}).get('sanitizer_mode')}",
                f"- sanitizer_actions: {(trace.get('narration') or {}).get('sanitizer_actions')}",
                f"- narrator_policy_violation_types: {(trace.get('narration') or {}).get('narrator_policy_violation_types')}",
                f"- raw_response_policy_violations: {(trace.get('narration') or {}).get('raw_response_policy_violations')}",
                f"- sanitized_response_policy_violations: {(trace.get('narration') or {}).get('sanitized_response_policy_violations')}",
                f"- final_response_policy_violations: {(trace.get('narration') or {}).get('final_response_policy_violations')}",
                f"- sql_leak: {(trace.get('narration') or {}).get('sql_leak')}",
                f"- presentation_leak: {(trace.get('narration') or {}).get('presentation_leak')}",
                f"- chain_of_thought_leak: {(trace.get('narration') or {}).get('chain_of_thought_leak')}",
                f"- prompt_echo_leak: {(trace.get('narration') or {}).get('prompt_echo_leak')}",
                f"- policy_echo_leak: {(trace.get('narration') or {}).get('policy_echo_leak')}",
                f"- oracle_error_leak: {(trace.get('narration') or {}).get('oracle_error_leak')}",
                f"- raw_chain_of_thought_leak: {(trace.get('narration') or {}).get('raw_chain_of_thought_leak')}",
                f"- raw_prompt_echo_leak: {(trace.get('narration') or {}).get('raw_prompt_echo_leak')}",
                f"- raw_policy_echo_leak: {(trace.get('narration') or {}).get('raw_policy_echo_leak')}",
                f"- raw_sql_leak: {(trace.get('narration') or {}).get('raw_sql_leak')}",
                f"- raw_presentation_leak: {(trace.get('narration') or {}).get('raw_presentation_leak')}",
                f"- raw_oracle_error_leak: {(trace.get('narration') or {}).get('raw_oracle_error_leak')}",
                f"- final_chain_of_thought_leak: {(trace.get('narration') or {}).get('final_chain_of_thought_leak')}",
                f"- final_prompt_echo_leak: {(trace.get('narration') or {}).get('final_prompt_echo_leak')}",
                f"- final_policy_echo_leak: {(trace.get('narration') or {}).get('final_policy_echo_leak')}",
                f"- final_sql_leak: {(trace.get('narration') or {}).get('final_sql_leak')}",
                f"- final_presentation_leak: {(trace.get('narration') or {}).get('final_presentation_leak')}",
                f"- final_oracle_error_leak: {(trace.get('narration') or {}).get('final_oracle_error_leak')}",
                f"- narration_ok: {(trace.get('narration') or {}).get('narration_ok')}",
                f"- source_question_for_narrator: {(trace.get('narration') or {}).get('source_question_for_narrator')}",
                f"- source_execution_status_for_narrator: {(trace.get('narration') or {}).get('source_execution_status_for_narrator')}",
                f"- source_row_count_for_narrator: {(trace.get('narration') or {}).get('source_row_count_for_narrator')}",
                f"- source_columns_for_narrator: {(trace.get('narration') or {}).get('source_columns_for_narrator')}",
                f"- source_summary_text_for_narrator: {(trace.get('narration') or {}).get('source_summary_text_for_narrator')}",
                f"- narration_context_mismatch: {(trace.get('narration') or {}).get('narration_context_mismatch')}",
                f"- narration_context_mismatch_fields: {(trace.get('narration') or {}).get('narration_context_mismatch_fields')}",
            ]
        )

    return "\n".join(lines).strip() + "\n"


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


_EXEC_ERR_CLASS_PAT = re.compile(r'\[([a-z_]+)\]')


def _classify_narrator_leaks(text: str | None) -> tuple[bool, bool]:
    """Return (sql_leak, presentation_leak) for a narrator response."""
    checks = _classify_narration_policy_violations(text)
    return bool(checks["sql_leak"]), bool(checks["presentation_leak"])


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


def _is_retryable_llm_exception(exc: Exception) -> bool:
    """Return True when *exc* is safe to retry for LLM HTTP calls."""
    try:
        import httpx as _httpx
    except ImportError:
        return False
    if isinstance(exc, (_httpx.ConnectError, _httpx.ReadTimeout, _httpx.ConnectTimeout, _httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, _httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    return False


async def _call_with_retry(
    call: Any,
    *,
    max_retries: int,
    retry_stats: LLMRetryStats,
) -> Any:
    """Execute async *call* with bounded exponential backoff retries."""
    had_retry = False
    attempt = 0
    while True:
        try:
            out = await call()
            if had_retry:
                retry_stats.retry_success_count += 1
            return out
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable_llm_exception(exc):
                raise
            had_retry = True
            attempt += 1
            retry_stats.retry_count += 1
            sleep_s = min(3.0, (0.35 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.25))
            await asyncio.sleep(sleep_s)


def _patch_llm_with_retry(chat: Any, max_retries: int, retry_stats: LLMRetryStats) -> None:
    """Patch LLM provider methods on planner/narrator with retry wrappers.

    Retries are applied only to LLM network calls; Oracle and deterministic
    pipeline stages are untouched.
    """
    if max_retries <= 0:
        return

    providers: list[Any] = []
    planner = getattr(chat, "_planner", None)
    narrator = getattr(chat, "_narrator", None)
    if planner is not None:
        p = getattr(planner, "_llm", None)
        if p is not None:
            providers.append(p)
    if narrator is not None:
        n = getattr(narrator, "_llm", None)
        if n is not None:
            providers.append(n)

    seen: set[int] = set()
    for provider in providers:
        pid = id(provider)
        if pid in seen:
            continue
        seen.add(pid)

        if getattr(provider, "_eval_retry_wrapped", False):
            continue

        orig_generate_structured = provider.generate_structured
        orig_generate_text = provider.generate_text

        async def _wrapped_generate_structured(prompt: str, response_model: Any, _orig=orig_generate_structured) -> Any:
            return await _call_with_retry(
                lambda: _orig(prompt, response_model),
                max_retries=max_retries,
                retry_stats=retry_stats,
            )

        async def _wrapped_generate_text(prompt: str, _orig=orig_generate_text) -> str:
            return await _call_with_retry(
                lambda: _orig(prompt),
                max_retries=max_retries,
                retry_stats=retry_stats,
            )

        provider.generate_structured = _wrapped_generate_structured  # type: ignore[assignment]
        provider.generate_text = _wrapped_generate_text  # type: ignore[assignment]
        provider._eval_retry_wrapped = True


async def _run_dataset_concurrent(
    chat: Any,
    dataset: list[EvalQuestion],
    *,
    session_prefix: str,
    concurrency: int,
    question_timeout_s: float,
) -> list[EvalResult]:
    """Run dataset with bounded concurrency and stable output ordering."""
    total = len(dataset)
    sem = asyncio.Semaphore(max(1, concurrency))
    results: list[EvalResult | None] = [None] * total
    done = 0
    lock = asyncio.Lock()

    async def _one(index: int, item: EvalQuestion) -> None:
        nonlocal done
        queued_at = time.perf_counter()
        async with sem:
            started_at = time.perf_counter()
            queue_wait_ms = int((started_at - queued_at) * 1000)

            try:
                if question_timeout_s > 0:
                    r = await asyncio.wait_for(
                        _run_one(chat, item, session_prefix),
                        timeout=question_timeout_s,
                    )
                else:
                    r = await _run_one(chat, item, session_prefix)
            except asyncio.TimeoutError:
                r = EvalResult(
                    id=item.id,
                    domain=item.domain,
                    category=item.category,
                    question=item.text,
                    expected_table=item.expected_table,
                    expected_intent_type=item.expected_intent_type,
                    status="execution_error",
                    raw_status="execution_error",
                    error_detail=f"question_timeout>{question_timeout_s}s",
                    execution_error_subtype="timeout_error",
                    execution_status="execution_error",
                    root_cause_layer="executor",
                    primary_failure_reason=f"question_timeout>{question_timeout_s}s",
                    question_trace={
                        "input": {
                            "question_id": item.id,
                            "question": item.text,
                            "domain": item.domain,
                            "category": item.category,
                            "expected_table": item.expected_table,
                            "expected_intent_type": item.expected_intent_type,
                        },
                        "final_judgment": {
                            "status": "execution_error",
                            "raw_status": "execution_error",
                            "root_cause_layer": "executor",
                            "root_cause_stage": "execute",
                            "primary_failure_reason": f"question_timeout>{question_timeout_s}s",
                            "secondary_failure_reason": None,
                            "business_status": "execution_error",
                            "quality_status": "fail",
                            "safety_status": "pass",
                            "business_failure_stage": "execute",
                            "quality_failure_stage": "execute",
                            "safety_failure_stage": "none",
                            "first_failing_stage": "execute",
                            "final_failing_stage": "execute",
                            "root_cause_category": "execution_failure",
                            "root_cause_detail": f"execute:question_timeout>{question_timeout_s}s",
                            "planner_ok": True,
                            "repair_ok": True,
                            "semantic_ok": True,
                            "validation_ok": True,
                            "compile_ok": True,
                            "execute_ok": False,
                            "narration_ok": True,
                        },
                    },
                )
            except Exception as exc:
                r = EvalResult(
                    id=item.id,
                    domain=item.domain,
                    category=item.category,
                    question=item.text,
                    expected_table=item.expected_table,
                    expected_intent_type=item.expected_intent_type,
                    status="execution_error",
                    raw_status="execution_error",
                    error_detail=str(exc),
                    execution_error_subtype="unknown_execution_error",
                    execution_status="execution_error",
                    root_cause_layer="executor",
                    primary_failure_reason=str(exc),
                    question_trace={
                        "input": {
                            "question_id": item.id,
                            "question": item.text,
                            "domain": item.domain,
                            "category": item.category,
                            "expected_table": item.expected_table,
                            "expected_intent_type": item.expected_intent_type,
                        },
                        "final_judgment": {
                            "status": "execution_error",
                            "raw_status": "execution_error",
                            "root_cause_layer": "executor",
                            "root_cause_stage": "execute",
                            "primary_failure_reason": str(exc),
                            "secondary_failure_reason": None,
                            "business_status": "execution_error",
                            "quality_status": "fail",
                            "safety_status": "pass",
                            "business_failure_stage": "execute",
                            "quality_failure_stage": "execute",
                            "safety_failure_stage": "none",
                            "first_failing_stage": "execute",
                            "final_failing_stage": "execute",
                            "root_cause_category": "execution_failure",
                            "root_cause_detail": "execute:unknown_execution_error",
                            "planner_ok": True,
                            "repair_ok": True,
                            "semantic_ok": True,
                            "validation_ok": True,
                            "compile_ok": True,
                            "execute_ok": False,
                            "narration_ok": True,
                        },
                    },
                )

            finished_at = time.perf_counter()
            r.queue_wait_ms = queue_wait_ms
            r.processing_ms = int((finished_at - started_at) * 1000)
            if r.latency_ms <= 0:
                r.latency_ms = int((finished_at - queued_at) * 1000)

            results[index] = r

            async with lock:
                done += 1
                print(
                    f"[{done:03d}/{total}] done {item.id} status={r.status} latency={r.latency_ms / 1000.0:.1f}s",
                    flush=True,
                )

    tasks = [asyncio.create_task(_one(i, q)) for i, q in enumerate(dataset)]
    await asyncio.gather(*tasks)

    # Output order is guaranteed to match input order by index assignment.
    return [r for r in results if r is not None]


def _bucket_wrong_plan(result: EvalResult) -> list[str]:
    """Classify a wrong_plan result into normalized root-cause buckets."""
    if not result.wrong_plan:
        return []

    out: list[str] = []
    reasons = set(result.wrong_plan_reasons or [])

    if "wrong_table" in reasons:
        exp = (result.expected_table or "").upper()
        pred = (result.predicted_tables[0].upper() if result.predicted_tables else "")
        if exp.startswith("PO_") and pred.startswith("XXBT_"):
            out.append("wrong_domain_entity")
        elif exp.startswith("XXBT_") and pred.startswith("PO_"):
            out.append("wrong_domain_entity")
        else:
            out.append("wrong_root_table")

    if "wrong_join" in reasons:
        if result.join_path:
            out.append("wrong_join_path")
        else:
            out.append("missing_join")

    if "wrong_aggregation" in reasons:
        out.append("missing_aggregation")

    if "wrong_filter_column" in reasons:
        out.append("wrong_filter_column")

    if "semantically_incorrect_result" in reasons:
        out.append("unnecessary_clarification_disguised_as_success")

    return sorted(set(out))


def _bucket_execution_error(result: EvalResult) -> str | None:
    """Map execution_error to stable diagnostic buckets."""
    if result.raw_status != "execution_error":
        return None
    subtype = result.execution_error_subtype or "unknown_execution_error"
    detail = (result.error_detail or "").upper()

    if subtype == "invalid_date_value" or "ORA-018" in detail:
        return "oracle_date_type_error"
    if subtype == "invalid_identifier" or "ORA-00904" in detail:
        return "invalid_identifier"
    if subtype == "ambiguous_column" or "ORA-00918" in detail:
        return "ambiguous_column"
    if "JOIN" in detail and "ORA" in detail:
        return "missing_join_side_effect"
    if subtype == "expression_rendering_issue" or "ORA-00979" in detail or "ORA-00937" in detail:
        return "expression_rendering_issue"
    if subtype == "mis_shaped_params" or "ORA-01008" in detail:
        return "runtime_mis_shaped_params"
    if subtype == "timeout_error" or "TIMEOUT" in detail:
        return "timeout_heavy_join"
    return "data_specific_edge_case_or_unknown"


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
    from app.core.config import settings
    from app.domain.execution_models import ErrorPhase

    t0 = time.perf_counter()
    result = EvalResult(
        id=item.id,
        domain=item.domain,
        category=item.category,
        question=item.text,
        expected_table=item.expected_table,
        expected_intent_type=item.expected_intent_type,
    )
    trace_id = f"{session_prefix}:{item.id}:{uuid4().hex[:12]}"
    result.trace_id = trace_id
    session_id = f"{session_prefix}_{item.id}"
    planner = getattr(chat, "_planner", None)
    orchestrator = getattr(chat, "_orchestrator", None)
    narrator = getattr(chat, "_narrator", None)
    trace_locks = _ensure_trace_locks(chat)

    trace: dict[str, Any] = {
        "trace_id": trace_id,
        "input": {
            "trace_id": trace_id,
            "question_id": item.id,
            "question": item.text,
            "domain": item.domain,
            "category": item.category,
            "expected_table": item.expected_table,
            "expected_intent_type": item.expected_intent_type,
            "wrong_plan_risk": item.wrong_plan_risk,
            "notes": item.notes,
            "session_id": session_id,
        },
        "retrieval": _default_retrieval_trace(),
        "prompt": _default_prompt_trace(),
        "llm_raw_output": _default_llm_trace(),
        "llm_calls": [],
        "parsed_query_plan": None,
        "normalize": None,
        "repair": None,
        "semantic_normalization": None,
        "canonicalization": None,
        "validation": _default_validation_trace(),
        "compile": _default_compile_trace(),
        "execute": _default_execute_trace(),
        "narration": _default_narration_trace(),
        "final_judgment": None,
    }

    plan = None
    orchestration_result = None
    answer = None

    try:
        if planner is None or orchestrator is None or narrator is None:
            if hasattr(chat, "handle_message"):
                legacy_out = await chat.handle_message(session_id, item.text)
                result.raw_status = getattr(legacy_out, "status", "execution_error")
                result.status = result.raw_status if result.raw_status in VALID_OUTCOMES else "execution_error"
                result.business_status = result.raw_status if result.raw_status in {
                    "success",
                    "clarification",
                    "validation_error",
                    "compile_error",
                    "execution_error",
                    "empty_result",
                } else "execution_error"
                result.quality_status = "pass" if result.status in {"success", "empty_result", "clarification"} else "fail"
                result.safety_status = "pass"
                result.first_failing_stage = "none" if result.status in {"success", "empty_result", "clarification"} else "execute"
                result.final_failing_stage = result.first_failing_stage
                result.root_cause_category = "unknown" if result.status in {"success", "empty_result", "clarification"} else "execution_failure"
                result.root_cause_detail = result.root_cause_category
                result.execute_ok = result.status in {"success", "empty_result", "clarification"}
                result.execution_status = result.raw_status
                result.compiled_sql = getattr(legacy_out, "sql", None)
                result.narrator_response = getattr(legacy_out, "answer", None)
                result.error_detail = getattr(legacy_out, "error_message", None)
                if result.status == "execution_error":
                    result.execution_error_subtype = _extract_exec_error_subtype(result.error_detail)
                trace["final_judgment"] = {
                    "status": result.status,
                    "raw_status": result.raw_status,
                    "business_status": result.business_status,
                    "quality_status": result.quality_status,
                    "safety_status": result.safety_status,
                    "root_cause_stage": "execute" if result.status == "execution_error" else "none",
                    "first_failing_stage": result.first_failing_stage,
                    "final_failing_stage": result.final_failing_stage,
                    "root_cause_category": result.root_cause_category,
                    "root_cause_detail": result.root_cause_detail,
                    "planner_ok": True,
                    "repair_ok": True,
                    "semantic_ok": True,
                    "validation_ok": True,
                    "compile_ok": True,
                    "execute_ok": result.execute_ok,
                    "narration_ok": True,
                }
                result.question_trace = trace
                result.latency_ms = int((time.perf_counter() - t0) * 1000)
                return result
            raise RuntimeError("Chat orchestrator missing planner/orchestrator/narrator components")

        async with trace_locks["planner"]:
            planner_started = time.perf_counter()
            plan = await planner.plan(item.text)
            planner_latency_ms = int((time.perf_counter() - planner_started) * 1000)
            planner_trace = _immutable_snapshot(getattr(planner, "last_trace", None) or {})

        planner_question = planner_trace.get("user_message") or item.text
        result.planner_question = planner_question

        retrieval_state = _default_retrieval_trace()
        retrieval_state.update(_immutable_snapshot(planner_trace.get("retrieval") or {}))
        retrieval_state["enabled"] = True
        retrieval_state["schema_tables"] = list(retrieval_state.get("schema_tables") or [])
        retrieval_state["schema_docs"] = list(retrieval_state.get("schema_docs") or [])
        retrieval_state["examples"] = list(retrieval_state.get("examples") or [])
        retrieval_state["sufficiency"] = _derive_retrieval_sufficiency(retrieval_state)
        trace["retrieval"] = retrieval_state

        prompt_state = _default_prompt_trace()
        prompt_state.update(_immutable_snapshot(planner_trace.get("prompt") or {}))
        prompt_state["available"] = True
        prompt_state["prompt_length"] = int(prompt_state.get("prompt_length") or 0)
        prompt_state["prompt_budget"] = int(prompt_state.get("prompt_budget") or 0)
        prompt_state["prompt_truncated"] = bool(prompt_state.get("prompt_truncated"))
        prompt_state["reduction_steps"] = list(prompt_state.get("reduction_steps") or [])
        trace["prompt"] = prompt_state

        llm_state = _default_llm_trace()
        llm_state.update(_immutable_snapshot(planner_trace.get("llm") or {}))
        llm_state["available"] = True
        trace["llm_raw_output"] = llm_state

        llm_model = (
            getattr(getattr(planner, "_llm", None), "_model", None)
            or getattr(getattr(planner, "_llm", None), "model", None)
            or getattr(getattr(planner, "_llm", None), "model_name", None)
        )
        trace["llm_calls"] = [
            _build_llm_call_summary(
                stage="planner",
                request_prompt=(trace.get("prompt") or {}).get("full_prompt_text"),
                response_raw=(trace.get("llm_raw_output") or {}).get("raw_response_text"),
                parse_error=(trace.get("llm_raw_output") or {}).get("parse_error"),
                model=str(llm_model) if llm_model is not None else None,
                latency_ms=planner_latency_ms,
                leak_detected=False,
                clarification_detected=bool(getattr(plan, "needs_clarification", False)),
            )
        ]
        trace["llm_calls"][0]["trace_id"] = trace_id
        trace["parsed_query_plan"] = _immutable_snapshot(planner_trace.get("parsed_plan"))

        normalize_stage = _immutable_snapshot(planner_trace.get("normalize") or {})
        trace["normalize"] = {
            **normalize_stage,
            "diff": _plan_diff(normalize_stage.get("before"), normalize_stage.get("after")),
            "trace_id": trace_id,
        }
        repair_stage = _immutable_snapshot(planner_trace.get("repair") or {})
        trace["repair"] = {
            **repair_stage,
            "diff": _plan_diff(repair_stage.get("before"), repair_stage.get("after")),
            "trace_id": trace_id,
        }
        semantic_stage = _immutable_snapshot(planner_trace.get("semantic") or {})
        trace["semantic_normalization"] = {
            **semantic_stage,
            "diff": _plan_diff(semantic_stage.get("before"), semantic_stage.get("after")),
            "trace_id": trace_id,
        }
        canonical_stage = _immutable_snapshot(planner_trace.get("canonicalize") or {})
        trace["canonicalization"] = {
            **canonical_stage,
            "diff": _plan_diff(canonical_stage.get("before"), canonical_stage.get("after")),
            "trace_id": trace_id,
        }

        result.semantic_intent = plan.semantic_intent or plan.intent
        result.predicted_tables = _plan_tables(plan)
        result.join_path = _join_path(plan)
        result.repair_applied = bool((trace.get("repair") or {}).get("repair_applied"))
        result.repair_actions = [
            str(action.get("repair_type", "unknown"))
            for action in ((trace.get("repair") or {}).get("repair_actions") or [])
        ]
        result.repair_fields_count = len((trace.get("repair") or {}).get("repair_actions") or [])
        result.structured_parse_error = bool((trace.get("llm_raw_output") or {}).get("parse_error"))

        if plan.needs_clarification:
            async with trace_locks["narrator"]:
                narration_started = time.perf_counter()
                answer = await narrator.narrate_clarification(plan)
                narration_latency_ms = int((time.perf_counter() - narration_started) * 1000)
                narrator_trace = _immutable_snapshot(getattr(narrator, "last_trace", None) or {})
            result.raw_status = "clarification"
        else:
            async with trace_locks["orchestrator"]:
                orchestration_result = await orchestrator.run_plan(plan)
                orchestrator_trace = _immutable_snapshot(getattr(orchestrator, "last_trace", None) or {})
            validation_state = _default_validation_trace()
            validation_state.update(_immutable_snapshot(orchestrator_trace.get("validation") or {}))
            validation_state["available"] = True
            trace["validation"] = validation_state
            compile_state = _default_compile_trace()
            compile_state.update(_immutable_snapshot(orchestrator_trace.get("compile") or {}))
            compile_state["available"] = True
            trace["compile"] = compile_state
            execute_state = _default_execute_trace()
            execute_state.update(_immutable_snapshot(orchestrator_trace.get("execute") or {}))
            execute_state["available"] = True
            trace["execute"] = execute_state
            result.execute_question = item.text

            if orchestration_result.failed_phase == ErrorPhase.VALIDATION:
                async with trace_locks["narrator"]:
                    narration_started = time.perf_counter()
                    answer = await narrator.narrate_validation_error(item.text, orchestration_result.validation)
                    narration_latency_ms = int((time.perf_counter() - narration_started) * 1000)
                    narrator_trace = _immutable_snapshot(getattr(narrator, "last_trace", None) or {})
                result.raw_status = "validation_error"
                result.error_detail = "; ".join(err.message for err in orchestration_result.validation.errors)
            elif orchestration_result.failed_phase == ErrorPhase.COMPILATION:
                async with trace_locks["narrator"]:
                    narration_started = time.perf_counter()
                    answer = await narrator.narrate_execution_error(item.text, orchestration_result)
                    narration_latency_ms = int((time.perf_counter() - narration_started) * 1000)
                    narrator_trace = _immutable_snapshot(getattr(narrator, "last_trace", None) or {})
                result.raw_status = "compile_error"
                result.error_detail = orchestration_result.compilation_error
            elif orchestration_result.failed_phase == ErrorPhase.EXECUTION:
                async with trace_locks["narrator"]:
                    narration_started = time.perf_counter()
                    answer = await narrator.narrate_execution_error(item.text, orchestration_result)
                    narration_latency_ms = int((time.perf_counter() - narration_started) * 1000)
                    narrator_trace = _immutable_snapshot(getattr(narrator, "last_trace", None) or {})
                result.raw_status = "execution_error"
                result.error_detail = (
                    orchestration_result.execution_result.error_message
                    if orchestration_result.execution_result
                    else None
                )
                result.execution_error_subtype = _extract_exec_error_subtype(result.error_detail)
            else:
                async with trace_locks["narrator"]:
                    narration_started = time.perf_counter()
                    answer = await narrator.narrate_success(item.text, orchestration_result)
                    narration_latency_ms = int((time.perf_counter() - narration_started) * 1000)
                    narrator_trace = _immutable_snapshot(getattr(narrator, "last_trace", None) or {})
                if orchestration_result.execution_result and orchestration_result.execution_result.row_count == 0:
                    result.raw_status = "empty_result"
                    result.row_count = 0
                else:
                    result.raw_status = "success"
                    result.row_count = (
                        orchestration_result.execution_result.row_count
                        if orchestration_result.execution_result
                        else None
                    )

        narrator_trace = _immutable_snapshot(locals().get("narrator_trace") or {})
        result.narrator_question = item.text if result.raw_status == "clarification" else (narrator_trace.get("user_message") or item.text)
        result.narrator_response = answer
        result.raw_narrator_response = narrator_trace.get("raw_response")
        expected_narrator_context = _build_expected_narrator_context(
            item=item,
            plan=plan,
            orchestration_result=orchestration_result,
            raw_status=result.raw_status,
        )
        narration_integrity = _sanitize_narration_output(
            raw_response=result.raw_narrator_response,
            answer=answer,
            raw_status=result.raw_status,
            expected_context=expected_narrator_context,
        )
        sanitized_response = narration_integrity["sanitized_response"]
        final_response = narration_integrity["final_response"]
        final_response_source = narration_integrity["final_response_source"]
        sanitizer_mode = narration_integrity["sanitizer_mode"]
        sanitizer_applied = bool(narration_integrity["sanitizer_applied"])
        sanitizer_effective = bool(narration_integrity["sanitizer_effective"])
        raw_vs_final_changed = bool(narration_integrity["raw_vs_final_changed"])
        sanitizer_actions = list(narration_integrity["sanitizer_actions"])
        raw_response_policy_violations = list(narration_integrity["raw_response_policy_violations"])
        sanitized_response_policy_violations = list(narration_integrity["sanitized_response_policy_violations"])
        final_response_policy_violations = list(narration_integrity["final_response_policy_violations"])
        raw_checks = narration_integrity["raw_checks"]
        sanitized_checks = narration_integrity["sanitized_checks"]
        final_checks = narration_integrity["final_checks"]

        result.final_response_source = final_response_source
        result.sanitizer_effective = sanitizer_effective
        result.narrator_response = final_response
        result.final_response_mapping_error = bool(
            (final_response_source == "sanitized" and final_response != sanitized_response)
            or (final_response_source == "raw" and final_response != (result.raw_narrator_response or ""))
        )

        result.raw_narrator_chain_of_thought_leak = bool(raw_checks["chain_of_thought_leak"])
        result.raw_narrator_prompt_echo_leak = bool(raw_checks["prompt_echo_leak"])
        result.raw_narrator_policy_echo_leak = bool(raw_checks["policy_echo_leak"])
        result.raw_narrator_sql_leak = bool(raw_checks["sql_leak"])
        result.raw_narrator_presentation_leak = bool(raw_checks["presentation_leak"])
        result.raw_narrator_oracle_error_leak = bool(raw_checks["oracle_error_leak"])
        result.final_narrator_chain_of_thought_leak = bool(final_checks["chain_of_thought_leak"])
        result.final_narrator_prompt_echo_leak = bool(final_checks["prompt_echo_leak"])
        result.final_narrator_policy_echo_leak = bool(final_checks["policy_echo_leak"])
        result.final_narrator_sql_leak = bool(final_checks["sql_leak"])
        result.final_narrator_presentation_leak = bool(final_checks["presentation_leak"])
        result.final_narrator_oracle_error_leak = bool(final_checks["oracle_error_leak"])
        result.narrator_sql_leak = result.final_narrator_sql_leak
        result.narrator_presentation_leak = result.final_narrator_presentation_leak
        narration_ok = bool(final_response and not final_response_policy_violations and not result.final_response_mapping_error)
        result.raw_leak_but_final_clean = bool(raw_response_policy_violations and not final_response_policy_violations)
        violation_types = list(raw_response_policy_violations)

        narration_context_mismatch, narration_context_mismatch_fields = _detect_narration_context_mismatch(
            item=item,
            narrator_trace=narrator_trace,
            expected_context=expected_narrator_context,
            raw_status=result.raw_status,
        )
        result.narration_context_mismatch = narration_context_mismatch
        result.narration_context_mismatch_fields = narration_context_mismatch_fields

        trace["narration"] = {
            **_default_narration_trace(),
            **narrator_trace,
            **expected_narrator_context,
            "available": True,
            "raw_response": result.raw_narrator_response,
            "sanitized_response": sanitized_response,
            "final_response": final_response,
            "final_response_source": final_response_source,
            "raw_vs_final_changed": raw_vs_final_changed,
            "sanitizer_applied": sanitizer_applied,
            "sanitizer_effective": sanitizer_effective,
            "sanitizer_mode": sanitizer_mode,
            "sanitizer_actions": sanitizer_actions,
            "narrator_policy_violation_types": violation_types,
            "raw_response_policy_violations": raw_response_policy_violations,
            "sanitized_response_policy_violations": sanitized_response_policy_violations,
            "final_response_policy_violations": final_response_policy_violations,
            "sql_leak": result.final_narrator_sql_leak,
            "presentation_leak": result.final_narrator_presentation_leak,
            "chain_of_thought_leak": result.final_narrator_chain_of_thought_leak,
            "prompt_echo_leak": result.final_narrator_prompt_echo_leak,
            "policy_echo_leak": result.final_narrator_policy_echo_leak,
            "oracle_error_leak": result.final_narrator_oracle_error_leak,
            "raw_chain_of_thought_leak": result.raw_narrator_chain_of_thought_leak,
            "raw_prompt_echo_leak": result.raw_narrator_prompt_echo_leak,
            "raw_policy_echo_leak": result.raw_narrator_policy_echo_leak,
            "raw_sql_leak": result.raw_narrator_sql_leak,
            "raw_presentation_leak": result.raw_narrator_presentation_leak,
            "raw_oracle_error_leak": result.raw_narrator_oracle_error_leak,
            "final_chain_of_thought_leak": result.final_narrator_chain_of_thought_leak,
            "final_prompt_echo_leak": result.final_narrator_prompt_echo_leak,
            "final_policy_echo_leak": result.final_narrator_policy_echo_leak,
            "final_sql_leak": result.final_narrator_sql_leak,
            "final_presentation_leak": result.final_narrator_presentation_leak,
            "final_oracle_error_leak": result.final_narrator_oracle_error_leak,
            "narration_ok": narration_ok,
            "note": "narration safe" if narration_ok else "final narration output violated safety or mapping rules",
            "stage_outcome": "passed" if narration_ok else "failed",
            "narration_context_mismatch": narration_context_mismatch,
            "narration_context_mismatch_fields": narration_context_mismatch_fields,
            "trace_id": trace_id,
        }
        narrator_model = (
            getattr(getattr(narrator, "_llm", None), "_model", None)
            or getattr(getattr(narrator, "_llm", None), "model", None)
            or getattr(getattr(narrator, "_llm", None), "model_name", None)
        )
        trace["llm_calls"].append(
            _build_llm_call_summary(
                stage="narrator",
                request_prompt=narrator_trace.get("full_prompt_text"),
                response_raw=result.raw_narrator_response,
                response_final=final_response,
                error=narrator_trace.get("error"),
                model=str(narrator_model) if narrator_model is not None else None,
                latency_ms=narration_latency_ms,
                leak_detected=bool(raw_response_policy_violations),
                clarification_detected=False,
                response_policy_ok=not bool(final_response_policy_violations),
            )
        )
        trace["llm_calls"][-1]["trace_id"] = trace_id

        if orchestration_result is not None:
            compiled = orchestration_result.compiled_query
            if compiled is not None:
                result.compiled_sql = compiled.sql if settings.enable_sql_in_api_response else compiled.sql
                trace["compile"].update(
                    {
                        "available": True,
                        "ok": True,
                        "stage_outcome": "passed",
                        "note": "compile passed",
                        "sql": compiled.sql,
                        "params": _immutable_snapshot(compiled.params),
                        "table": compiled.table,
                        "selected_columns": list(compiled.selected_columns),
                    }
                )
                plan_for_counts = ((trace.get("canonicalization") or {}).get("after") or {})
                compile_input_plan_snapshot = _immutable_snapshot(plan_for_counts)
                trace["compile"].update(
                    {
                        "selected_columns_count": len((trace.get("compile") or {}).get("selected_columns") or []),
                        "filter_count": len(plan_for_counts.get("filters") or []),
                        "join_count": len(plan_for_counts.get("joins") or []),
                        "aggregation_count": len(plan_for_counts.get("aggregations") or []),
                        "group_by_count": len(plan_for_counts.get("group_by") or []),
                        "bind_param_count": len((trace.get("compile") or {}).get("params") or {}),
                        "expression_count": sum(
                            1
                            for agg in (plan_for_counts.get("aggregations") or [])
                            if (agg or {}).get("expression_ref")
                        ),
                        "compile_warning_list": [],
                        "compile_input_plan_snapshot": compile_input_plan_snapshot,
                        "compile_input_diff_from_planner_raw": _plan_diff(trace.get("parsed_query_plan"), compile_input_plan_snapshot),
                        "compile_input_diff_from_semantic": _plan_diff((trace.get("semantic_normalization") or {}).get("after"), compile_input_plan_snapshot),
                        "compile_metrics": {
                            "selected_columns_count": len((trace.get("compile") or {}).get("selected_columns") or []),
                            "filter_count": len(plan_for_counts.get("filters") or []),
                            "join_count": len(plan_for_counts.get("joins") or []),
                            "aggregation_count": len(plan_for_counts.get("aggregations") or []),
                            "group_by_count": len(plan_for_counts.get("group_by") or []),
                            "bind_param_count": len((trace.get("compile") or {}).get("params") or {}),
                            "expression_count": sum(
                                1
                                for agg in (plan_for_counts.get("aggregations") or [])
                                if (agg or {}).get("expression_ref")
                            ),
                        },
                        "compiled_sql": compiled.sql,
                        "compiled_sql_source_plan_stage": "canonicalize",
                        "trace_id": trace_id,
                    }
                )
            execution = orchestration_result.execution_result
            if execution is not None:
                result.execution_status = execution.status.value
                if result.row_count is None:
                    result.row_count = execution.row_count
                timeout_applied = settings.oracle_timeout > 0
                row_limit_applied = "ROWNUM <=" in (result.compiled_sql or "").upper()
                trace["execute"].update(
                    {
                        "available": True,
                        "executor_class": chat._orchestrator._executor.__class__.__name__,
                        "status": execution.status.value,
                        "ok": result.raw_status != "execution_error",
                        "stage_outcome": "failed" if result.raw_status == "execution_error" else "passed",
                        "note": "execution failed" if result.raw_status == "execution_error" else "execution passed",
                        "row_count": execution.row_count,
                        "columns": list(execution.columns),
                        "error_message": execution.error_message,
                        "execution_time_ms": execution.execution_time_ms,
                        "db_latency_ms": None,
                        "fetch_latency_ms": None,
                        "timeout_applied": timeout_applied,
                        "row_limit_applied": row_limit_applied,
                        "rows_returned_before_limit": None,
                        "rows_returned_after_limit": execution.row_count,
                        "error": execution.error_message,
                        "execution_error_subtype": result.execution_error_subtype,
                        "trace_id": trace_id,
                    }
                )
        else:
            result.execution_status = result.raw_status

    except Exception as exc:
        result.raw_status = "execution_error"
        result.status = "execution_error"
        result.error_detail = str(exc)
        result.execution_error_subtype = _extract_exec_error_subtype(str(exc)) or "unknown_execution_error"
        result.execution_status = "execution_error"
        trace["final_judgment"] = {
            "status": result.status,
            "raw_status": result.raw_status,
            "root_cause_layer": "executor",
            "root_cause_stage": "execute",
            "primary_failure_reason": str(exc),
            "secondary_failure_reason": None,
            "business_status": "execution_error",
            "quality_status": "fail",
            "safety_status": "pass",
            "business_failure_stage": "execute",
            "quality_failure_stage": "execute",
            "safety_failure_stage": "none",
            "first_failing_stage": "execute",
            "final_failing_stage": "execute",
            "root_cause_category": "execution_failure",
            "root_cause_detail": "execute:unknown_execution_error",
            "planner_ok": True,
            "repair_ok": True,
            "semantic_ok": True,
            "validation_ok": True,
            "compile_ok": True,
            "execute_ok": False,
            "narration_ok": True,
        }
        result.question_trace = trace
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        return result

    result.latency_ms = int((time.perf_counter() - t0) * 1000)
    if result.execution_status is None:
        result.execution_status = result.raw_status

    non_ambiguous = item.category not in {"AMBIGUOUS", "CROSS_DOMAIN"}
    if plan is not None and non_ambiguous:
        wp, reasons = _evaluate_wrong_plan(item, result, plan)
        result.wrong_plan = wp
        result.wrong_plan_reasons = reasons

    if plan is not None:
        result.clarification_class = _classify_clarification(item, result, plan)

    if result.wrong_plan:
        result.status = "wrong_plan"
    else:
        result.status = result.raw_status

    if result.status not in VALID_OUTCOMES:
        result.status = "execution_error"

    diff_flags = _compute_diff_flags(trace, trace.get("narration") or {})
    trace["stage_diff_flags"] = diff_flags
    result.sql_shape_comparable = bool(diff_flags.get("sql_shape_comparable"))

    stage_alignment_ok, alignment_errors = _compute_alignment(
        planner_question=result.planner_question,
        execute_question=result.execute_question,
        narrator_question=result.narrator_question,
        raw_status=result.raw_status,
        item_question=item.text,
    )
    result.stage_alignment_ok = stage_alignment_ok
    result.alignment_errors = alignment_errors
    trace["planner_question"] = result.planner_question
    trace["execute_question"] = result.execute_question
    trace["narrator_question"] = result.narrator_question
    trace["stage_alignment_ok"] = stage_alignment_ok
    trace["alignment_errors"] = list(alignment_errors)

    stage_statuses = _make_stage_statuses(result, trace)
    trace["stage_status"] = {k: v for k, v in stage_statuses.items() if not k.startswith("_")}

    business_status, quality_status, safety_status = _build_business_quality_safety(result, stage_statuses)
    root_cause_category, root_cause_detail = _classify_root_cause_category(result, trace)
    first_failing_stage, final_failing_stage = _determine_fail_stages(result, stage_statuses, root_cause_category)

    result.business_status = business_status
    result.quality_status = quality_status
    result.safety_status = safety_status
    result.first_failing_stage = first_failing_stage
    result.final_failing_stage = final_failing_stage
    result.root_cause_stage = _determine_root_cause_stage(result, trace)
    result.root_cause_category = root_cause_category
    result.root_cause_detail = root_cause_detail
    result.planner_ok = bool((stage_statuses.get("planner") or {}).get("ok", True))
    result.repair_ok = bool((stage_statuses.get("repair") or {}).get("ok", True))
    result.semantic_ok = bool((stage_statuses.get("semantic") or {}).get("ok", True))
    result.validation_ok = bool((stage_statuses.get("validation") or {}).get("ok", True))
    result.compile_ok = bool((stage_statuses.get("compile") or {}).get("ok", True))
    result.execute_ok = bool((stage_statuses.get("execute") or {}).get("ok", True))
    result.narration_ok = bool((stage_statuses.get("narration") or {}).get("ok", True))
    result.business_failure_stage = result.root_cause_stage if result.root_cause_stage in {"planner", "validation", "compile", "execute"} else "none"
    result.quality_failure_stage = result.root_cause_stage if result.root_cause_stage != "none" else "none"
    result.safety_failure_stage = "narration" if (stage_statuses.get("narration") or {}).get("stage_outcome") == "failed" else "none"
    result.stage_statuses = {k: v for k, v in stage_statuses.items() if not k.startswith("_")}
    result.trace_flags = {
        "changed_semantics": diff_flags["changed_semantics"],
        "changed_sql_shape": diff_flags["changed_sql_shape"],
        "sql_shape_comparable": diff_flags["sql_shape_comparable"],
        "changed_user_visible_output": diff_flags["changed_user_visible_output"],
    }

    result.root_cause_layer = result.root_cause_stage
    if result.root_cause_stage == "planner":
        result.primary_failure_reason = (trace.get("llm_raw_output") or {}).get("parse_error")
        result.secondary_failure_reason = None
    elif result.root_cause_stage == "validation":
        result.primary_failure_reason = result.error_detail or root_cause_detail
        result.secondary_failure_reason = None
    elif result.root_cause_stage == "compile":
        result.primary_failure_reason = result.error_detail or root_cause_detail
        result.secondary_failure_reason = None
    elif result.root_cause_stage == "execute":
        result.primary_failure_reason = result.execution_error_subtype or result.error_detail or root_cause_detail
        result.secondary_failure_reason = None
    elif result.root_cause_stage == "narration":
        result.primary_failure_reason = ", ".join((trace.get("narration") or {}).get("final_response_policy_violations") or []) or "final_narration_violation"
        result.secondary_failure_reason = None
    else:
        result.primary_failure_reason = None
        result.secondary_failure_reason = None

    trace["final_judgment"] = {
        "status": result.status,
        "raw_status": result.raw_status,
        "wrong_plan": result.wrong_plan,
        "wrong_plan_reasons": list(result.wrong_plan_reasons),
        "root_cause_layer": result.root_cause_layer,
        "root_cause_stage": result.root_cause_stage,
        "primary_failure_reason": result.primary_failure_reason,
        "secondary_failure_reason": result.secondary_failure_reason,
        "business_status": result.business_status,
        "quality_status": result.quality_status,
        "safety_status": result.safety_status,
        "business_failure_stage": result.business_failure_stage,
        "quality_failure_stage": result.quality_failure_stage,
        "safety_failure_stage": result.safety_failure_stage,
        "first_failing_stage": result.first_failing_stage,
        "final_failing_stage": result.final_failing_stage,
        "root_cause_category": result.root_cause_category,
        "root_cause_detail": result.root_cause_detail,
        "planner_ok": result.planner_ok,
        "repair_ok": result.repair_ok,
        "semantic_ok": result.semantic_ok,
        "validation_ok": result.validation_ok,
        "compile_ok": result.compile_ok,
        "execute_ok": result.execute_ok,
        "narration_ok": result.narration_ok,
        "trace_id": trace_id,
        "stage_alignment_ok": result.stage_alignment_ok,
        "alignment_errors": list(result.alignment_errors),
        "narration_context_mismatch": result.narration_context_mismatch,
        "narration_context_mismatch_fields": list(result.narration_context_mismatch_fields),
        "final_response_source": result.final_response_source,
        "sanitizer_effective": result.sanitizer_effective,
        "sanitizer_saved_response": result.sanitizer_effective and result.narration_ok,
        "raw_leak_but_final_clean": result.raw_leak_but_final_clean,
        "sql_shape_comparable": result.sql_shape_comparable,
        "final_response_policy_violations": (trace.get("narration") or {}).get("final_response_policy_violations"),
        "sanitized_response_policy_violations": (trace.get("narration") or {}).get("sanitized_response_policy_violations"),
        "raw_response_policy_violations": (trace.get("narration") or {}).get("raw_response_policy_violations"),
        "compile_input_plan_snapshot": (trace.get("compile") or {}).get("compile_input_plan_snapshot"),
        "compile_input_diff_from_planner_raw": (trace.get("compile") or {}).get("compile_input_diff_from_planner_raw"),
        "compile_input_diff_from_semantic": (trace.get("compile") or {}).get("compile_input_diff_from_semantic"),
        "final_plan_source_chain": ["planner_raw", "normalize", "repair", "semantic", "canonicalize"],
        "compiled_sql_source_plan_stage": "canonicalize",
        "narrator_summary_source_stage": (trace.get("narration") or {}).get("narrator_summary_source_stage"),
        "narrator_final_source_stage": "sanitize" if result.final_response_source == "sanitized" else result.final_response_source,
    }
    result.question_trace = trace
    return result


def _make_summary(
    results: list[EvalResult],
    oracle_timeout: int,
    *,
    concurrency: int,
    max_retries: int,
    total_wall_time_s: float,
    llm_retry_stats: LLMRetryStats,
) -> EvalSummary:
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
        p50 = float(statistics.median(sorted_lat))
    else:
        p95 = 0.0
        p50 = 0.0

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

    # --- repair metrics ---
    repair_applied_total = sum(1 for r in results if r.repair_applied)
    repaired_fields_total = sum(int(r.repair_fields_count) for r in results)
    repair_action_counts = Counter(
        action for r in results for action in (r.repair_actions or [])
    )

    # --- wrong-plan and execution-error bucket counts ---
    wrong_plan_bucket_counts = Counter(
        bucket for r in results for bucket in _bucket_wrong_plan(r)
    )
    execution_error_bucket_counts = Counter(
        b for b in (_bucket_execution_error(r) for r in results) if b is not None
    )

    # --- repair effectiveness proxies (deterministic, conservative) ---
    repaired_wrong_plan_count = sum(
        1 for r in results if r.repair_applied and r.status == "wrong_plan"
    )
    repair_prevented_clarification_count = sum(
        1
        for r in results
        if "F_clarification_rescue" in (r.repair_actions or []) and r.raw_status != "clarification"
    )
    repair_prevented_validation_error_count = sum(
        1
        for r in results
        if r.repair_applied
        and any(a in {"A_domain_entity_reroute", "E_anchor_table", "F_column_ownership", "F_qualified_column"} for a in (r.repair_actions or []))
        and r.raw_status != "validation_error"
    )
    repair_prevented_execution_error_count = sum(
        1
        for r in results
        if r.repair_applied
        and any(a in {"B_join_skeleton", "C_aggregation_skeleton", "D_group_by_fill", "E_filter_repair"} for a in (r.repair_actions or []))
        and r.raw_status != "execution_error"
    )

    # --- top semantic intents/root entities by failure ---
    failed = [r for r in results if r.status in {"wrong_plan", "validation_error", "compile_error", "execution_error", "clarification"}]
    intent_fail = Counter((r.semantic_intent or "unknown") for r in failed)
    root_fail = Counter(((r.predicted_tables[0] if r.predicted_tables else "None")) for r in failed)
    top_semantic_intents_by_failure = [
        {"semantic_intent": k, "count": v}
        for k, v in intent_fail.most_common(10)
    ]
    top_root_entities_by_failure = [
        {"root_entity": k, "count": v}
        for k, v in root_fail.most_common(10)
    ]

    trace_summary = compute_trace_summary(results)

    business_success = sum(1 for r in results if r.business_status in {"success", "empty_result"})
    quality_pass = sum(1 for r in results if r.quality_status == "pass")
    safety_pass = sum(1 for r in results if r.safety_status == "pass")

    planner_parse_fail_count = sum(1 for r in results if r.structured_parse_error)
    repair_apply_count = sum(1 for r in results if r.repair_applied)
    semantic_override_count = sum(1 for r in results if r.root_cause_category == "semantic_override")
    sql_shape_changed_count = sum(1 for r in results if (r.trace_flags or {}).get("changed_sql_shape"))

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
        repair_applied_total=repair_applied_total,
        repaired_fields_total=repaired_fields_total,
        questions_with_repair_rate=rate(repair_applied_total),
        repair_action_counts=dict(repair_action_counts),
        wrong_plan_bucket_counts=dict(wrong_plan_bucket_counts),
        execution_error_bucket_counts=dict(execution_error_bucket_counts),
        repaired_wrong_plan_count=repaired_wrong_plan_count,
        repair_prevented_clarification_count=repair_prevented_clarification_count,
        repair_prevented_validation_error_count=repair_prevented_validation_error_count,
        repair_prevented_execution_error_count=repair_prevented_execution_error_count,
        top_semantic_intents_by_failure=top_semantic_intents_by_failure,
        top_root_entities_by_failure=top_root_entities_by_failure,
        concurrency=concurrency,
        max_retries=max_retries,
        total_wall_time_s=total_wall_time_s,
        avg_question_latency_s=avg_latency / 1000.0,
        p50_question_latency_s=p50 / 1000.0,
        p95_question_latency_s=p95 / 1000.0,
        llm_retry_count=llm_retry_stats.retry_count,
        llm_retry_success_count=llm_retry_stats.retry_success_count,
        business_success_rate=rate(business_success),
        quality_pass_rate=rate(quality_pass),
        safety_pass_rate=rate(safety_pass),
        first_fail_stage_counts=trace_summary["first_fail_stage_counts"],
        root_cause_category_counts=trace_summary["root_cause_category_counts"],
        narrator_leak_rate=trace_summary["narrator_leak_rate"],
        presentation_leak_rate=trace_summary["presentation_leak_rate"],
        sql_leak_rate=trace_summary["sql_leak_rate"],
        final_narrator_leak_rate=trace_summary["final_narrator_leak_rate"],
        final_presentation_leak_rate=trace_summary["final_presentation_leak_rate"],
        final_sql_leak_rate=trace_summary["final_sql_leak_rate"],
        final_oracle_error_leak_rate=trace_summary["final_oracle_error_leak_rate"],
        raw_narrator_leak_rate=trace_summary["raw_narrator_leak_rate"],
        raw_presentation_leak_rate=trace_summary["raw_presentation_leak_rate"],
        raw_sql_leak_rate=trace_summary["raw_sql_leak_rate"],
        raw_oracle_error_leak_rate=trace_summary["raw_oracle_error_leak_rate"],
        planner_parse_fail_rate=rate(planner_parse_fail_count),
        repair_apply_rate=rate(repair_apply_count),
        semantic_override_rate=rate(semantic_override_count),
        sql_shape_changed_rate=rate(sql_shape_changed_count),
        trace_alignment_error_count=trace_summary["trace_alignment_error_count"],
        narration_context_mismatch_count=trace_summary["narration_context_mismatch_count"],
        sanitizer_effective_rate=trace_summary["sanitizer_effective_rate"],
        final_response_mapping_error_count=trace_summary["final_response_mapping_error_count"],
        sanitizer_saved_response_count=trace_summary["sanitizer_saved_response_count"],
        raw_leak_but_final_clean_count=trace_summary["raw_leak_but_final_clean_count"],
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
            f"- concurrency: {summary.concurrency}",
            f"- max_retries: {summary.max_retries}",
            f"- total_wall_time: {summary.total_wall_time_s:.1f} s",
            f"- avg_question_latency: {summary.avg_question_latency_s:.2f} s",
            f"- p50_question_latency: {summary.p50_question_latency_s:.2f} s",
            f"- p95_question_latency: {summary.p95_question_latency_s:.2f} s",
            f"- llm_retry_count: {summary.llm_retry_count}",
            f"- llm_retry_success_count: {summary.llm_retry_success_count}",
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
            f"- narrator_leak_rate(final): {_format_pct(summary.final_narrator_leak_rate)}",
            f"- presentation_leak_rate(final): {_format_pct(summary.final_presentation_leak_rate)}",
            f"- sql_leak_rate(final): {_format_pct(summary.final_sql_leak_rate)}",
            f"- oracle_error_leak_rate(final): {_format_pct(summary.final_oracle_error_leak_rate)}",
            f"- narrator_leak_rate(raw): {_format_pct(summary.raw_narrator_leak_rate)}",
            f"- presentation_leak_rate(raw): {_format_pct(summary.raw_presentation_leak_rate)}",
            f"- sql_leak_rate(raw): {_format_pct(summary.raw_sql_leak_rate)}",
            f"- oracle_error_leak_rate(raw): {_format_pct(summary.raw_oracle_error_leak_rate)}",
            f"- sanitizer_saved_response_count: {summary.sanitizer_saved_response_count}",
            f"- raw_leak_but_final_clean_count: {summary.raw_leak_but_final_clean_count}",
            "",
            "I. Wrong-plan root-cause bucketlari",
        ]
        + [
            f"- {k}: {v}"
            for k, v in sorted(summary.wrong_plan_bucket_counts.items(), key=lambda x: -x[1])
        ]
        + [
            "",
            "J. Execution-error root-cause bucketlari",
        ]
        + [
            f"- {k}: {v}"
            for k, v in sorted(summary.execution_error_bucket_counts.items(), key=lambda x: -x[1])
        ]
        + [
            "",
            "K. Top-20 failure buckets",
        ]
        + [
            f"- [{b['count']:3d}] {b['bucket']}"
            for b in summary.top_failure_buckets
        ]
        + [
            "",
            "L. Repair engine metrikleri",
            f"- questions_with_repair: {summary.repair_applied_total}/{summary.total_questions}",
            f"- questions_with_repair_rate: {_format_pct(summary.questions_with_repair_rate)}",
            f"- repaired_fields_total: {summary.repaired_fields_total}",
            f"- repaired_wrong_plan_count: {summary.repaired_wrong_plan_count}",
            f"- repair_prevented_clarification_count: {summary.repair_prevented_clarification_count}",
            f"- repair_prevented_validation_error_count: {summary.repair_prevented_validation_error_count}",
            f"- repair_prevented_execution_error_count: {summary.repair_prevented_execution_error_count}",
        ]
        + [
            f"- repair_action/{k}: {v}"
            for k, v in sorted(summary.repair_action_counts.items(), key=lambda x: -x[1])
        ]
        + [
            "",
            "M. Failure concentration",
        ]
        + [
            f"- intent/{x['semantic_intent']}: {x['count']}"
            for x in summary.top_semantic_intents_by_failure
        ]
        + [
            f"- root/{x['root_entity']}: {x['count']}"
            for x in summary.top_root_entities_by_failure
        ]
        + [
            "",
            "N. Production readiness karari",
            f"- karar: {summary.readiness_decision}",
            "",
            "O. Sonuc metrikleri",
            metric_table,
        ]
    )


async def _run_benchmark_modes(
    *,
    dataset: list[EvalQuestion],
    use_oracle: bool,
    max_retries: int,
    question_timeout_s: float,
    benchmark_concurrency: list[int],
) -> list[BenchmarkResult]:
    """Run quick benchmark across multiple concurrency levels."""
    from scripts.e2e_llm_flow import _build_orchestrator

    out: list[BenchmarkResult] = []
    for conc in benchmark_concurrency:
        chat, oracle_exec = await _build_orchestrator(use_oracle=use_oracle)
        retry_stats = LLMRetryStats()
        _patch_llm_with_retry(chat, max_retries=max_retries, retry_stats=retry_stats)

        t0 = time.perf_counter()
        results = await _run_dataset_concurrent(
            chat,
            dataset,
            session_prefix=f"bench_c{conc}_{int(time.time())}",
            concurrency=conc,
            question_timeout_s=question_timeout_s,
        )
        wall = time.perf_counter() - t0

        if oracle_exec is not None:
            await oracle_exec.close()

        counts = Counter(r.status for r in results)
        out.append(
            BenchmarkResult(
                concurrency=conc,
                total_wall_time_s=wall,
                success=counts.get("success", 0),
                clarification=counts.get("clarification", 0),
                validation_error=counts.get("validation_error", 0),
                compile_error=counts.get("compile_error", 0),
                execution_error=counts.get("execution_error", 0),
                wrong_plan=counts.get("wrong_plan", 0),
            )
        )

    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description="Real provider + Oracle NL2SQL evaluation")
    parser.add_argument("--dataset", default="data/eval_dataset_100.json", help="Dataset JSON path")
    parser.add_argument("--run-name", default="", help="Logical run name used for trace/summary file naming")
    parser.add_argument("--max-questions", type=int, default=0, help="Deterministically evaluate only the first balanced batch of N questions")
    parser.add_argument("--batch-index", type=int, default=1, help="1-based balanced batch index when --max-questions is used")
    parser.add_argument("--report-json", default="", help="Optional legacy detailed JSON report path")
    parser.add_argument("--report-md", default="", help="Optional legacy summary markdown path")
    parser.add_argument("--summary-json", default="", help="Output aggregate summary JSON path")
    parser.add_argument("--trace-jsonl", default="", help="Output per-question trace JSONL path")
    parser.add_argument("--trace-md", default="", help="Output per-question trace markdown path")
    parser.add_argument("--single-output-md", default="", help="Single markdown output path containing summary + full question traces")
    parser.add_argument("--emit-extra-files", action="store_true", help="Also emit JSON/JSONL/manual-review/legacy files in addition to single markdown")
    parser.add_argument("--allow-mock-llm", action="store_true", help="Allow running with MockLLMProvider (default: fail fast to avoid false real-provider runs)")
    parser.add_argument("--manual-review-json", default="", help="Output manual review list path")
    parser.add_argument("--no-oracle", action="store_true", help="Use mock executor instead of Oracle (for dry-run)")
    parser.add_argument("--concurrency", type=int, default=1, help="Bounded concurrency for question execution (default: 1, keeps legacy behavior)")
    parser.add_argument("--max-retries", type=int, default=2, help="Max retries for retryable LLM HTTP failures (429/5xx)")
    parser.add_argument("--question-timeout", type=float, default=120.0, help="Per-question timeout in seconds")
    parser.add_argument("--benchmark-concurrency", default="", help="Optional benchmark mode, e.g. '1,2,4,8'")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise SystemExit(f"Dataset file not found: {dataset_path}")

    dataset = _load_dataset(dataset_path)
    max_questions = max(0, int(args.max_questions or 0))
    if max_questions:
        dataset = _select_dataset_batch(
            dataset,
            max_questions=max_questions,
            batch_index=max(1, int(args.batch_index)),
        )
    if not dataset:
        raise SystemExit("Selected dataset is empty.")

    run_name = args.run_name.strip() or f"{dataset_path.stem}_b{max(1, int(args.batch_index))}_{len(dataset)}q"
    output_dir = dataset_path.parent

    summary_json_path = Path(args.summary_json) if args.summary_json else output_dir / f"eval_summary_{run_name}.json"
    trace_jsonl_path = Path(args.trace_jsonl) if args.trace_jsonl else output_dir / f"question_trace_{run_name}.jsonl"
    trace_md_path = Path(args.trace_md) if args.trace_md else output_dir / f"question_trace_{run_name}.md"
    single_output_md_path = Path(args.single_output_md) if args.single_output_md else output_dir / f"eval_trace_{run_name}.md"
    manual_json_path = Path(args.manual_review_json) if args.manual_review_json else output_dir / f"manual_review_{run_name}.json"
    report_json_path = Path(args.report_json) if args.report_json else None
    report_md_path = Path(args.report_md) if args.report_md else None

    # Reuse existing wiring (no architecture change)
    from app.core.config import settings
    from scripts.e2e_llm_flow import _build_orchestrator

    bench_results: list[BenchmarkResult] = []
    if args.benchmark_concurrency:
        conc_values: list[int] = []
        for raw in args.benchmark_concurrency.split(","):
            raw = raw.strip()
            if raw:
                conc_values.append(max(1, int(raw)))
        if conc_values:
            print(f"Running benchmark modes: {conc_values}", flush=True)
            bench_results = await _run_benchmark_modes(
                dataset=dataset,
                use_oracle=not args.no_oracle,
                max_retries=max(0, args.max_retries),
                question_timeout_s=float(args.question_timeout),
                benchmark_concurrency=conc_values,
            )
            print("Benchmark results:", flush=True)
            for b in bench_results:
                print(
                    f"  c={b.concurrency} wall={b.total_wall_time_s:.1f}s "
                    f"success={b.success} wrong_plan={b.wrong_plan} "
                    f"exec_err={b.execution_error}",
                    flush=True,
                )

    chat, oracle_exec = await _build_orchestrator(use_oracle=not args.no_oracle)
    planner = getattr(chat, "_planner", None)
    llm_obj = getattr(planner, "_llm", None) if planner is not None else None
    llm_class = llm_obj.__class__.__name__ if llm_obj is not None else "unknown"
    executor_name = "OracleExecutor" if oracle_exec is not None else "CombinedMockExecutor"

    if llm_class == "MockLLMProvider" and not args.allow_mock_llm:
        raise SystemExit(
            "MockLLMProvider aktif. Bu komut gerçek LLM çağrısı yapmaz. "
            "Gerçek sağlayıcı için LLM_PROVIDER=openai_compatible (ve OPENAI_BASE_URL/OPENAI_MODEL) ayarlayın; "
            "mock ile devam etmek için --allow-mock-llm ekleyin."
        )

    print(f"LLM provider: {llm_class}", flush=True)
    print(f"Executor    : {executor_name}", flush=True)

    retry_stats = LLMRetryStats()
    _patch_llm_with_retry(chat, max_retries=max(0, args.max_retries), retry_stats=retry_stats)

    session_prefix = f"real_eval_{int(time.time())}"
    run_started = time.perf_counter()
    results = await _run_dataset_concurrent(
        chat,
        dataset,
        session_prefix=session_prefix,
        concurrency=max(1, args.concurrency),
        question_timeout_s=float(args.question_timeout),
    )
    total_wall_time_s = time.perf_counter() - run_started

    if oracle_exec is not None:
        await oracle_exec.close()

    summary = _make_summary(
        results,
        oracle_timeout=settings.oracle_timeout,
        concurrency=max(1, args.concurrency),
        max_retries=max(0, args.max_retries),
        total_wall_time_s=total_wall_time_s,
        llm_retry_stats=retry_stats,
    )

    traces = [r.question_trace for r in results if r.question_trace is not None]

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
            "repair_applied": r.repair_applied,
            "repair_actions": r.repair_actions,
            "repair_fields_count": r.repair_fields_count,
            "root_cause_layer": r.root_cause_layer,
            "primary_failure_reason": r.primary_failure_reason,
            "secondary_failure_reason": r.secondary_failure_reason,
            "business_status": r.business_status,
            "quality_status": r.quality_status,
            "safety_status": r.safety_status,
            "first_failing_stage": r.first_failing_stage,
            "final_failing_stage": r.final_failing_stage,
            "root_cause_category": r.root_cause_category,
            "root_cause_detail": r.root_cause_detail,
            "planner_ok": r.planner_ok,
            "repair_ok": r.repair_ok,
            "semantic_ok": r.semantic_ok,
            "validation_ok": r.validation_ok,
            "compile_ok": r.compile_ok,
            "execute_ok": r.execute_ok,
            "narration_ok": r.narration_ok,
            "stage_statuses": r.stage_statuses,
            "trace_flags": r.trace_flags,
            # Clarification
            "clarification_class": r.clarification_class,
            "trace": r.question_trace,
        }
        for r in results
        if r.wrong_plan
        or r.status in {"validation_error", "compile_error", "execution_error", "clarification"}
        or r.narrator_sql_leak
        or r.narrator_presentation_leak
    ]

    summary_payload = {
        "run_name": run_name,
        "dataset_path": str(dataset_path),
        "dataset_size": len(dataset),
        "benchmark": [asdict(b) for b in bench_results],
        "summary": asdict(summary),
    }

    single_output_md_path.parent.mkdir(parents=True, exist_ok=True)
    single_output_md_path.write_text(
        _build_single_output_markdown(
            dataset,
            results,
            summary,
            traces,
            {
                "llm_provider": llm_class,
                "executor": executor_name,
                "oracle_enabled": not args.no_oracle,
                "dataset_path": str(dataset_path),
                "run_name": run_name,
            },
        ),
        encoding="utf-8",
    )

    if args.emit_extra_files:
        summary_json_path.parent.mkdir(parents=True, exist_ok=True)
        summary_json_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        manual_json_path.parent.mkdir(parents=True, exist_ok=True)
        manual_json_path.write_text(json.dumps(manual_review, ensure_ascii=False, indent=2), encoding="utf-8")

        trace_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_jsonl_path.open("w", encoding="utf-8") as handle:
            for trace in traces:
                handle.write(json.dumps(trace, ensure_ascii=False) + "\n")

        trace_md_path.parent.mkdir(parents=True, exist_ok=True)
        trace_md_path.write_text(_build_trace_markdown(traces), encoding="utf-8")

        if report_json_path is not None:
            report_json_path.parent.mkdir(parents=True, exist_ok=True)
            report_json_path.write_text(
                json.dumps(
                    {
                        "run_name": run_name,
                        "dataset_path": str(dataset_path),
                        "dataset_size": len(dataset),
                        "benchmark": [asdict(b) for b in bench_results],
                        "summary": asdict(summary),
                        "results": [asdict(r) for r in results],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        if report_md_path is not None:
            report_md_path.parent.mkdir(parents=True, exist_ok=True)
            report_md_path.write_text(_build_report_markdown(dataset, results, summary), encoding="utf-8")

    print("\nEvaluation complete.")
    print(f"Single output MD: {single_output_md_path}")
    if args.emit_extra_files:
        print(f"Summary JSON: {summary_json_path}")
        print(f"Trace JSONL : {trace_jsonl_path}")
        print(f"Trace MD    : {trace_md_path}")
        print(f"Manual review list: {manual_json_path}")
        if report_json_path is not None:
            print(f"Legacy JSON report: {report_json_path}")
        if report_md_path is not None:
            print(f"Legacy summary MD : {report_md_path}")


if __name__ == "__main__":
    asyncio.run(main())
