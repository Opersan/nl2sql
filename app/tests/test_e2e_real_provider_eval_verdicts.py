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
    assert lines[0] == "- Q01 | success | quality_fail | narration | narrator_leak"
    assert lines[1] == "- Q02 | validation_error | quality_fail | validation | validation_failure"


def test_summary_includes_new_verdict_metrics() -> None:
    ok = _base_result()
    ok.business_status = "success"
    ok.quality_status = "pass"
    ok.safety_status = "pass"
    ok.first_failing_stage = "none"
    ok.root_cause_category = "unknown"
    ok.trace_flags = {"changed_sql_shape": False}

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
