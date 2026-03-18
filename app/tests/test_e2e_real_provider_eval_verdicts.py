from __future__ import annotations

from scripts.e2e_real_provider_eval import (
    EvalQuestion,
    EvalResult,
    LLMRetryStats,
    _build_single_output_markdown,
    _build_business_quality_safety,
    _classify_root_cause_category,
    _compute_diff_flags,
    _determine_root_cause_stage,
    _determine_fail_stages,
    _immutable_snapshot,
    _make_stage_statuses,
    _make_summary,
    _render_short_verdict_index,
    compute_trace_summary,
)
from scripts.e2e_real_provider_eval import (
    _classify_technical_pipeline_status,
    _classify_user_visible_status,
    _classify_planner_output_usable,
    _classify_semantic_rescue,
    _classify_sql_shape_change,
)


def _base_result() -> EvalResult:
    return EvalResult(
        id="q1",
        domain="EMP",
        category="LISTING",
        question="Aktif calisanlari listele",
        expected_table="XXBT_PDKS_PER_DETAILS_V",
        expected_intent_type="list",
        status="success",
        raw_status="success",
    )


def test_narrator_leak_classification_success_business_but_quality_fail() -> None:
    result = _base_result()
    result.final_narrator_presentation_leak = True
    result.final_narrator_chain_of_thought_leak = True
    trace = {
        "llm_raw_output": {"parse_error": None},
        "repair": {"repair_applied": False},
        "semantic_normalization": {"diff": {"changed_fields": []}},
        "validation": {"ok": True, "errors": []},
        "compile": {"ok": True, "error": None},
        "execute": {"status": "success", "error_message": None},
        "narration": {
            "raw_response": "Thinking Process:\n1. analyze",
            "final_response": "Thinking Process:\n1. analyze",
            "final_response_policy_violations": ["chain_of_thought_leak", "presentation_leak"],
            "sql_leak": False,
            "presentation_leak": True,
            "chain_of_thought_leak": True,
            "oracle_error_leak": False,
            "available": True,
        },
    }

    stage_statuses = _make_stage_statuses(result, trace)
    business_status, quality_status, safety_status = _build_business_quality_safety(result, stage_statuses)
    root_cause_category, _ = _classify_root_cause_category(result, trace)
    first_fail, final_fail = _determine_fail_stages(result, stage_statuses, root_cause_category)

    assert business_status == "success"
    assert quality_status == "fail"
    assert safety_status == "fail"
    assert first_fail == "narration"
    assert final_fail == "narration"
    assert root_cause_category == "narrator_leak"


def test_stage_status_validation_failure() -> None:
    result = _base_result()
    result.raw_status = "validation_error"
    trace = {
        "llm_raw_output": {"parse_error": None},
        "repair": {"repair_applied": True},
        "semantic_normalization": {"diff": {"changed_fields": ["semantic_intent"]}},
        "validation": {"ok": False, "errors": [{"code": "invalid_column"}]},
        "compile": {"ok": True},
        "execute": {"status": None},
        "narration": {
            "raw_response": "x",
            "final_response": "x",
            "final_response_policy_violations": [],
            "sql_leak": False,
            "presentation_leak": False,
            "chain_of_thought_leak": False,
            "oracle_error_leak": False,
            "available": True,
        },
    }

    statuses = _make_stage_statuses(result, trace)
    assert statuses["planner"]["ok"] is True
    assert statuses["validation"]["ok"] is False
    assert statuses["compile"]["stage_outcome"] == "skipped"
    assert statuses["execute"]["stage_outcome"] == "skipped"
    assert statuses["narration"]["ok"] is True


def test_short_verdict_index_rendering() -> None:
    traces = [
        {
            "final_judgment": {
                "business_status": "success",
                "quality_status": "fail",
                "first_failing_stage": "narration",
                "root_cause_category": "narrator_leak",
            }
        },
        {
            "final_judgment": {
                "business_status": "validation_error",
                "quality_status": "fail",
                "first_failing_stage": "validation",
                "root_cause_category": "validation_failure",
            }
        },
    ]

    lines = _render_short_verdict_index(traces)
    assert lines[0] == "- Q01 | success | quality_fail | narration | narrator_leak | unknown | unknown"
    assert lines[1] == "- Q02 | validation_error | quality_fail | validation | validation_failure | unknown | unknown"


def test_summary_includes_new_verdict_metrics() -> None:
    ok = _base_result()
    ok.business_status = "success"
    ok.quality_status = "pass"
    ok.safety_status = "pass"
    ok.first_failing_stage = "none"
    ok.root_cause_category = "unknown"
    ok.trace_flags = {"changed_sql_shape": False}
    ok.user_visible_status = "pass"
    ok.technical_pipeline_status = "pass"
    ok.semantic_rescue_applied = False
    ok.semantic_rescue_was_executable = None
    ok.sql_shape_change_stage = "none"
    ok.sql_shape_change_reason = "no_change"
    ok.false_success_risk = False
    ok.success_blocked_by_filter_loss = False
    ok.clarification_reason_code = "none"
    ok.confidence_band = "high"
    ok.pre_execution_risk_flags = []
    ok.execution_guard_reason = None
    ok.user_visible_quality = "pass"
    ok.model_behavior_quality = "pass"
    ok.question_trace = {
        "narration": {
            "narration_genericness_flag": False,
            "narrator_used_fallback_template": False,
            "sanitizer_reason_code": "no_sanitization_needed",
        }
    }

    leak = _base_result()
    leak.id = "q2"
    leak.status = "success"
    leak.raw_status = "success"
    leak.business_status = "success"
    leak.quality_status = "fail"
    leak.safety_status = "fail"
    leak.first_failing_stage = "narration"
    leak.root_cause_category = "narrator_leak"
    leak.final_narrator_presentation_leak = True
    leak.narrator_presentation_leak = True
    leak.trace_flags = {"changed_sql_shape": True}
    leak.user_visible_status = "pass_with_sanitization"
    leak.technical_pipeline_status = "fail_guarded"
    leak.semantic_rescue_applied = True
    leak.semantic_rescue_was_executable = True
    leak.sql_shape_change_stage = "semantic"
    leak.sql_shape_change_reason = "semantic_filter_override"
    leak.false_success_risk = True
    leak.success_blocked_by_filter_loss = True
    leak.clarification_reason_code = "filter_intent_missing"
    leak.confidence_band = "low"
    leak.pre_execution_risk_flags = ["oracle_date_type_error"]
    leak.execution_guard_reason = "precheck_date_literal_invalid"
    leak.user_visible_quality = "pass_with_sanitization"
    leak.model_behavior_quality = "degraded"
    leak.question_trace = {
        "narration": {
            "narration_genericness_flag": True,
            "narrator_used_fallback_template": True,
            "sanitizer_reason_code": "policy_leak_removed",
        }
    }

    summary = _make_summary(
        [ok, leak],
        oracle_timeout=30,
        concurrency=2,
        max_retries=1,
        total_wall_time_s=1.0,
        llm_retry_stats=LLMRetryStats(),
    )

    assert summary.business_success_rate == 1.0
    assert summary.quality_pass_rate == 0.5
    assert summary.safety_pass_rate == 0.5
    assert summary.first_fail_stage_counts["narration"] == 1
    assert summary.root_cause_category_counts["narrator_leak"] == 1
    assert summary.sql_shape_changed_rate == 0.5
    assert summary.narration_genericness_rate == 0.5
    assert summary.fallback_template_usage_rate == 0.5
    assert summary.pass_without_sanitization_rate == 0.5
    assert summary.false_success_risk_rate == 0.5
    assert summary.success_blocked_by_filter_loss_count == 1
    assert summary.success_blocked_by_filter_loss_rate == 0.5
    assert summary.semantic_rescue_executable_rate == 0.5
    assert summary.user_visible_quality_distribution["pass"] == 1
    assert summary.user_visible_quality_distribution["pass_with_sanitization"] == 1
    assert summary.model_behavior_quality_distribution["pass"] == 1
    assert summary.model_behavior_quality_distribution["degraded"] == 1
    assert summary.sanitizer_reason_code_distribution["no_sanitization_needed"] == 1
    assert summary.sanitizer_reason_code_distribution["policy_leak_removed"] == 1
    assert summary.clarification_reason_code_distribution["filter_intent_missing"] == 1
    assert summary.confidence_band_distribution["high"] == 1
    assert summary.confidence_band_distribution["low"] == 1
    assert summary.pre_execution_risk_flag_distribution["oracle_date_type_error"] == 1
    assert summary.execution_guard_reason_distribution["precheck_date_literal_invalid"] == 1
    assert summary.sql_shape_change_stage_distribution["semantic"] == 1
    assert summary.sql_shape_change_reason_distribution["semantic_filter_override"] == 1
    assert summary.user_visible_status_distribution["pass"] == 1
    assert summary.user_visible_status_distribution["pass_with_sanitization"] == 1
    assert summary.technical_pipeline_status_distribution["pass"] == 1
    assert summary.technical_pipeline_status_distribution["fail_guarded"] == 1


def test_immutable_snapshot_detaches_nested_structures() -> None:
    original = {
        "prompt": {"reduction_steps": ["examples"]},
        "compile": {"params": {"dept": "IT"}},
    }

    snapshot = _immutable_snapshot(original)
    original["prompt"]["reduction_steps"].append("docs")
    original["compile"]["params"]["dept"] = "HR"

    assert snapshot["prompt"]["reduction_steps"] == ["examples"]
    assert snapshot["compile"]["params"] == {"dept": "IT"}


def test_compute_trace_summary_uses_question_level_truth() -> None:
    ok = _base_result()
    ok.business_status = "success"
    ok.quality_status = "pass"
    ok.safety_status = "pass"
    ok.first_failing_stage = "none"
    ok.root_cause_category = "unknown"
    ok.stage_alignment_ok = True
    ok.sanitizer_effective = True

    bad = _base_result()
    bad.id = "q2"
    bad.business_status = "execution_error"
    bad.quality_status = "fail"
    bad.safety_status = "fail"
    bad.first_failing_stage = "narration"
    bad.root_cause_category = "narrator_leak"
    bad.stage_alignment_ok = False
    bad.narration_context_mismatch = True
    bad.final_response_mapping_error = True
    bad.final_narrator_presentation_leak = True
    bad.final_narrator_sql_leak = True
    bad.narrator_presentation_leak = True
    bad.narrator_sql_leak = True

    trace_summary = compute_trace_summary([ok, bad])

    assert trace_summary["business_success_rate"] == 0.5
    assert trace_summary["quality_pass_rate"] == 0.5
    assert trace_summary["safety_pass_rate"] == 0.5
    assert trace_summary["trace_alignment_error_count"] == 1
    assert trace_summary["narration_context_mismatch_count"] == 1
    assert trace_summary["sanitizer_effective_rate"] == 0.5
    assert trace_summary["final_response_mapping_error_count"] == 1


def test_raw_leak_final_clean_narration_ok() -> None:
    result = _base_result()
    trace = {
        "llm_raw_output": {"parse_error": None},
        "repair": {"repair_applied": False},
        "semantic_normalization": {"diff": {"changed_fields": []}},
        "validation": {"ok": True, "errors": []},
        "compile": {"ok": True},
        "execute": {"status": "success"},
        "narration": {
            "available": True,
            "raw_response": "Thinking Process:\n1. analyze\nKullanıcı sorusu: x",
            "final_response": "Toplam 3 kayıt listelendi.",
            "final_response_policy_violations": [],
            "raw_chain_of_thought_leak": True,
            "raw_prompt_echo_leak": True,
            "raw_policy_echo_leak": False,
            "raw_sql_leak": False,
            "raw_presentation_leak": True,
            "raw_oracle_error_leak": False,
            "final_chain_of_thought_leak": False,
            "final_prompt_echo_leak": False,
            "final_policy_echo_leak": False,
            "final_sql_leak": False,
            "final_presentation_leak": False,
            "final_oracle_error_leak": False,
            "sql_leak": False,
            "presentation_leak": False,
            "chain_of_thought_leak": False,
            "prompt_echo_leak": False,
            "policy_echo_leak": False,
            "oracle_error_leak": False,
        },
    }

    statuses = _make_stage_statuses(result, trace)
    business_status, quality_status, safety_status = _build_business_quality_safety(result, statuses)

    assert statuses["narration"]["ok"] is True
    assert statuses["narration"]["stage_outcome"] == "passed"
    assert business_status == "success"
    assert quality_status == "pass"
    assert safety_status == "pass"


def test_root_cause_prefers_planner_over_narrator() -> None:
    result = _base_result()
    result.raw_status = "clarification"
    result.final_narrator_presentation_leak = True
    trace = {
        "llm_raw_output": {"parse_error": "invalid json"},
        "validation": {"ok": False, "errors": []},
        "compile": {"ok": False},
        "execute": {"status": "skipped"},
        "narration": {
            "available": True,
            "final_response": "Thinking Process: x",
            "final_response_policy_violations": ["presentation_leak"],
            "presentation_leak": True,
            "chain_of_thought_leak": True,
            "sql_leak": False,
            "oracle_error_leak": False,
        },
    }

    assert _determine_root_cause_stage(result, trace) == "planner"
    category, detail = _classify_root_cause_category(result, trace)
    assert category == "planner_output"
    assert "planner_parse_error" in detail


def test_execute_skipped_if_compile_missing() -> None:
    result = _base_result()
    result.raw_status = "compile_error"
    trace = {
        "llm_raw_output": {"parse_error": None},
        "repair": {"repair_applied": False},
        "semantic_normalization": {"diff": {"changed_fields": []}},
        "validation": {"ok": True, "errors": []},
        "compile": {"ok": False, "error": "compile failed"},
        "execute": {"status": "skipped"},
        "narration": {"available": True, "final_response": "İşlem tamamlanamadı.", "final_response_policy_violations": []},
    }

    statuses = _make_stage_statuses(result, trace)
    assert statuses["compile"]["stage_outcome"] == "failed"
    assert statuses["execute"]["stage_outcome"] == "skipped"
    assert statuses["execute"]["ok"] is False


def test_final_leak_flags_use_final_response() -> None:
    result = _base_result()
    trace = {
        "llm_raw_output": {"parse_error": None},
        "repair": {"repair_applied": False},
        "semantic_normalization": {"diff": {"changed_fields": []}},
        "validation": {"ok": True, "errors": []},
        "compile": {"ok": True},
        "execute": {"status": "success"},
        "narration": {
            "available": True,
            "raw_response": "Thinking Process:\nRule 1",
            "final_response": "Kriterlere uygun kayıt bulunamadı.",
            "final_response_policy_violations": [],
            "raw_chain_of_thought_leak": True,
            "raw_prompt_echo_leak": False,
            "raw_policy_echo_leak": True,
            "raw_sql_leak": False,
            "raw_presentation_leak": True,
            "raw_oracle_error_leak": False,
            "final_chain_of_thought_leak": False,
            "final_prompt_echo_leak": False,
            "final_policy_echo_leak": False,
            "final_sql_leak": False,
            "final_presentation_leak": False,
            "final_oracle_error_leak": False,
            "sql_leak": False,
            "presentation_leak": False,
            "chain_of_thought_leak": False,
            "prompt_echo_leak": False,
            "policy_echo_leak": False,
            "oracle_error_leak": False,
        },
    }

    statuses = _make_stage_statuses(result, trace)
    assert statuses["narration"]["ok"] is True
    assert trace["narration"]["final_presentation_leak"] is False
    assert trace["narration"]["final_chain_of_thought_leak"] is False
    assert trace["narration"]["final_policy_echo_leak"] is False


def test_summary_uses_final_leak_rates() -> None:
    result = _base_result()
    result.raw_narrator_presentation_leak = True
    result.raw_narrator_chain_of_thought_leak = True
    result.final_narrator_presentation_leak = False
    result.final_narrator_chain_of_thought_leak = False
    result.narration_ok = True

    summary = _make_summary(
        [result],
        oracle_timeout=30,
        concurrency=1,
        max_retries=0,
        total_wall_time_s=1.0,
        llm_retry_stats=LLMRetryStats(),
    )

    assert summary.narrator_leak_rate == 0.0
    assert summary.presentation_leak_rate == 0.0


def test_raw_and_final_leak_rates_separated() -> None:
    result = _base_result()
    result.raw_narrator_presentation_leak = True
    result.raw_narrator_chain_of_thought_leak = True
    result.final_narrator_presentation_leak = False
    result.final_narrator_chain_of_thought_leak = False
    result.raw_leak_but_final_clean = True

    summary = _make_summary(
        [result],
        oracle_timeout=30,
        concurrency=1,
        max_retries=0,
        total_wall_time_s=1.0,
        llm_retry_stats=LLMRetryStats(),
    )

    assert summary.raw_narrator_leak_rate == 1.0
    assert summary.final_narrator_leak_rate == 0.0
    assert summary.raw_leak_but_final_clean_count == 1


def test_sql_shape_changed_false_when_compile_skipped() -> None:
    trace = {
        "semantic_normalization": {"diff": {"changed_fields": ["semantic_intent"]}},
        "canonicalization": {"diff": {"changed_fields": ["select_columns"]}},
        "compile": {"stage_outcome": "skipped", "sql": None},
    }

    diff_flags = _compute_diff_flags(trace, None)
    assert diff_flags["sql_shape_comparable"] is False
    assert diff_flags["changed_sql_shape"] is False


def test_final_header_consistent_with_verdict_card() -> None:
    result = _base_result()
    result.status = "clarification"
    result.raw_status = "clarification"
    result.business_status = "clarification"
    result.quality_status = "fail"
    result.safety_status = "pass"
    result.root_cause_stage = "planner"
    result.root_cause_category = "planner_output"

    summary = _make_summary(
        [result],
        oracle_timeout=30,
        concurrency=1,
        max_retries=0,
        total_wall_time_s=1.0,
        llm_retry_stats=LLMRetryStats(),
    )
    trace = {
        "trace_id": "t1",
        "stage_alignment_ok": True,
        "input": {
            "question_id": "q1",
            "question": "Aktif calisanlari listele",
            "domain": "EMP",
            "category": "LISTING",
            "expected_table": "XXBT_PDKS_PER_DETAILS_V",
            "expected_intent_type": "list",
        },
        "final_judgment": {
            "status": "clarification",
            "raw_status": "clarification",
            "business_status": "clarification",
            "quality_status": "fail",
            "safety_status": "pass",
            "root_cause_stage": "planner",
            "root_cause_category": "planner_output",
            "primary_failure_reason": "parse",
            "secondary_failure_reason": None,
            "trace_id": "t1",
            "first_failing_stage": "planner",
            "final_failing_stage": "planner",
            "root_cause_detail": "planner_parse_error:parse",
            "business_failure_stage": "planner",
            "quality_failure_stage": "planner",
            "safety_failure_stage": "none",
            "planner_ok": False,
            "repair_ok": False,
            "semantic_ok": False,
            "validation_ok": False,
            "compile_ok": False,
            "execute_ok": False,
            "narration_ok": True,
            "stage_alignment_ok": True,
            "alignment_errors": [],
            "narration_context_mismatch": False,
            "narration_context_mismatch_fields": [],
            "final_response_source": "sanitized",
            "sanitizer_effective": True,
            "narrator_summary_source_stage": "clarification",
            "narrator_final_source_stage": "sanitize",
        },
        "retrieval": {},
        "prompt": {},
        "llm_calls": [],
        "normalize": {},
        "repair": {},
        "semantic_normalization": {},
        "canonicalization": {},
        "stage_diff_flags": {"changed_semantics": False, "sql_shape_comparable": False, "changed_sql_shape": False, "changed_user_visible_output": False},
        "stage_status": {},
        "validation": {},
        "compile": {},
        "execute": {},
        "narration": {"narration_context_mismatch": False},
    }
    markdown = _build_single_output_markdown(
        [EvalQuestion("q1", "EMP", "LISTING", "Aktif calisanlari listele", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "")],
        [result],
        summary,
        [trace],
        {"llm_provider": "x", "executor": "y", "oracle_enabled": False, "dataset_path": "d", "run_name": "r"},
    )

    assert "root_cause_stage=planner" in markdown
    assert "- root_cause_stage: planner" in markdown


# ---------------------------------------------------------------------------
# New regression tests for Sprint-4 trace/report integrity improvements
# ---------------------------------------------------------------------------

def _clean_result() -> EvalResult:
    """A result that represents a fully successful trace with no failures."""
    r = _base_result()
    r.status = "success"
    r.compile_ok = True
    r.execute_ok = True
    r.narration_ok = True
    r.planner_ok = True
    r.structured_parse_error = False
    r.repair_applied = False
    r.sanitizer_effective = False
    r.final_response_mapping_error = False
    r.narration_context_mismatch = False
    r.raw_leak_but_final_clean = False
    return r


def test_full_success_has_no_failure_category() -> None:
    """A clean successful trace must produce root_cause_category='no_failure', not 'unknown'."""
    result = _clean_result()
    result.root_cause_category = "no_failure"
    result.root_cause_stage = "none"
    result.validation_ok = True
    result.compile_ok = True
    result.execute_ok = True
    result.narration_ok = True
    result.sanitizer_effective = False
    result.semantic_rescue_applied = False
    trace: dict = {
        "narration": {},
        "validation": {"ok": True},
        "compile": {"ok": True},
        "execute": {"ok": True},
        "repair": {},
        "semantic_normalization": {},
        "canonicalization": {},
        "planner": {"structured_ok": True},
    }
    category, _ = _classify_root_cause_category(result, trace)
    assert category == "no_failure", f"Expected 'no_failure', got {category!r}"

    # technical_pipeline_status must also be 'pass' for a clean trace
    stage_statuses: dict = {
        "planner": {"stage_outcome": "passed", "ok": True},
        "validation": {"stage_outcome": "passed", "ok": True},
        "compile": {"stage_outcome": "passed", "ok": True},
        "execute": {"stage_outcome": "passed", "ok": True},
    }
    tech_status = _classify_technical_pipeline_status(result, stage_statuses)
    assert tech_status == "pass", f"Expected 'pass', got {tech_status!r}"

    # user_visible_status must be 'pass' with no violations
    narration_trace: dict = {}
    uv_status = _classify_user_visible_status(result, narration_trace)
    assert uv_status == "pass", f"Expected 'pass', got {uv_status!r}"


def test_raw_leak_sanitized_user_visible_pass_with_sanitization() -> None:
    """When raw response had policy violations but sanitizer cleaned them, user_visible_status='pass_with_sanitization'."""
    result = _clean_result()
    result.raw_leak_but_final_clean = True
    result.sanitizer_effective = True

    narration_trace = {
        "raw_response_policy_violations": ["sql_exposure"],
        "final_response_policy_violations": [],
    }
    uv_status = _classify_user_visible_status(result, narration_trace)
    assert uv_status == "pass_with_sanitization", f"Expected 'pass_with_sanitization', got {uv_status!r}"

    stage_statuses = {
        "planner": {"stage_outcome": "passed", "ok": True},
        "validation": {"stage_outcome": "passed", "ok": True},
        "compile": {"stage_outcome": "passed", "ok": True},
        "execute": {"stage_outcome": "passed", "ok": True},
        "_flags": {"semantic_changed": False},
    }
    result.root_cause_category = "no_failure"
    result.root_cause_stage = "none"
    result.planner_ok = True
    result.validation_ok = True
    result.compile_ok = True
    result.execute_ok = True
    result.narration_ok = True
    tech_status = _classify_technical_pipeline_status(result, stage_statuses)
    assert tech_status == "degraded", f"Expected 'degraded', got {tech_status!r}"

    # narration_raw_unsafe_final_safe must be True (mapped from raw_leak_but_final_clean)
    assert result.raw_leak_but_final_clean is True


def test_planner_parse_fail_not_executable() -> None:
    """When planner produces a parse error and repair does not recover, planner_output_usable=False and technical_pipeline_status='fail'."""
    result = _base_result()
    result.structured_parse_error = True
    result.compile_ok = False
    result.planner_ok = False

    stage_statuses = {
        "planner": {"stage_outcome": "failed", "ok": False, "note": "parse error"},
        "compile": {"stage_outcome": "skipped", "ok": False, "note": "skipped due to upstream fail"},
    }

    usable = _classify_planner_output_usable(result, stage_statuses)
    assert usable is False, f"Expected False, got {usable!r}"

    tech_status = _classify_technical_pipeline_status(result, stage_statuses)
    assert tech_status == "fail", f"Expected 'fail', got {tech_status!r}"


def test_semantic_sql_shape_change_attributed() -> None:
    """When semantic_normalization diff changes a shape field, sql_shape_change_stage='semantic' with a reason."""
    trace = {
        "normalize": {},
        "repair": {},
        "semantic_normalization": {
            "diff": {
                "changed_fields": ["filters"],
                "changed": {
                    "filters": {"before": [], "after": [{"column": "STATUS", "op": "=", "value": "A"}]}
                },
                "added": {},
                "removed": {},
            }
        },
        "canonicalization": {},
    }
    stage, reason, summary = _classify_sql_shape_change(trace)
    assert stage == "semantic", f"Expected 'semantic', got {stage!r}"
    assert reason == "semantic_filter_override", f"Expected 'semantic_filter_override', got {reason!r}"
    assert summary is not None and "filters" in summary


def test_semantic_filter_override_sets_changed_sql_shape_true() -> None:
    """Semantic filter override must set changed_sql_shape=True when SQL is executable/comparable."""
    trace = {
        "normalize": {"diff": {"changed_fields": []}},
        "repair": {"diff": {"changed_fields": []}},
        "semantic_normalization": {
            "diff": {
                "changed_fields": ["filters"],
                "changed": {
                    "filters": {
                        "before": [{"column": "authorization_status", "op": "=", "value": "APPROVAL_PENDING"}],
                        "after": [{"column": "authorization_status", "op": "!=", "value": "APPROVED"}],
                    }
                },
            }
        },
        "canonicalization": {"diff": {"changed_fields": []}},
        "compile": {"stage_outcome": "passed", "sql": "select 1 from dual"},
    }
    diff_flags = _compute_diff_flags(trace, narration=None)
    assert diff_flags["sql_shape_comparable"] is True
    assert diff_flags["changed_sql_shape"] is True


def test_no_sql_shape_change_uses_none_enum() -> None:
    """No SQL-shape diff must return controlled enum values (none/no_change)."""
    trace = {
        "normalize": {"diff": {"changed_fields": ["semantic_intent"]}},
        "repair": {"diff": {"changed_fields": []}},
        "semantic_normalization": {"diff": {"changed_fields": ["root_entity"]}},
        "canonicalization": {"diff": {"changed_fields": []}},
    }
    stage, reason, summary = _classify_sql_shape_change(trace)
    assert stage == "none", f"Expected 'none', got {stage!r}"
    assert reason == "no_change", f"Expected 'no_change', got {reason!r}"
    assert summary is None


def test_semantic_enrichment_only_is_not_rescue() -> None:
    """Semantic enrichment-only updates must not be classified as semantic rescue."""
    trace = {
        "semantic_normalization": {
            "diff": {
                "changed_fields": ["root_entity", "semantic_intent"],
                "changed": {
                    "root_entity": {"before": None, "after": "employee"},
                    "semantic_intent": {"before": "list", "after": "list"},
                },
            }
        }
    }
    stage_statuses = {
        "compile": {"stage_outcome": "passed", "ok": True},
    }
    applied, was_executable = _classify_semantic_rescue(trace, stage_statuses)
    assert applied is False
    assert was_executable is None

    trace_for_diff = {
        "normalize": {"diff": {"changed_fields": []}},
        "repair": {"diff": {"changed_fields": []}},
        "semantic_normalization": trace["semantic_normalization"],
        "canonicalization": {"diff": {"changed_fields": []}},
        "compile": {"stage_outcome": "passed", "sql": "select 1 from dual"},
    }
    diff_flags = _compute_diff_flags(trace_for_diff, narration=None)
    assert diff_flags["changed_sql_shape"] is False


def test_no_unknown_in_successful_traces() -> None:
    """root_cause_category must never be 'unknown' for traces that succeeded."""
    successful_statuses = ["success", "empty_result"]
    for status in successful_statuses:
        result = _clean_result()
        result.status = status
        result.raw_status = status
        trace: dict = {
            "narration": {},
            "validation": {"ok": True},
            "compile": {"ok": True},
            "execute": {"ok": True},
            "repair": {},
            "semantic_normalization": {},
            "canonicalization": {},
            "planner": {"structured_ok": True},
        }
        category, _ = _classify_root_cause_category(result, trace)
        assert category != "unknown", (
            f"root_cause_category='unknown' should never appear for status={status!r}, got {category!r}"
        )
