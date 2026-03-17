from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app.domain.execution_models import CompiledQuery, ExecutionResult, ExecutionStatus, OrchestrationResult, ValidationResult
from app.domain.query_plan import QueryPlan
from app.services.narrator_service import NarratorService

from scripts.e2e_real_provider_eval import EvalQuestion, EvalResult, LLMRetryStats, _call_with_retry, _is_retryable_llm_exception, _make_summary, _run_dataset_concurrent, _run_one


@pytest.mark.asyncio
async def test_concurrency_order_stability() -> None:
    class _Chat:
        async def handle_message(self, session_id: str, message: str):
            await asyncio.sleep(0.02 if message.endswith("1") else 0.001)
            class _Plan:
                intent = "x"
                joins = []
                table = "PO_HEADERS_ALL"
            class _Out:
                status = "success"
                rows_preview = [{"a": 1}]
                plan = _Plan()
                sql = "SELECT 1 FROM DUAL"
                answer = "ok"
                error_message = None
            return _Out()

    ds = [
        EvalQuestion("q1", "PO", "LISTING", "m1", "PO_HEADERS_ALL", "list", "low", ""),
        EvalQuestion("q2", "PO", "LISTING", "m2", "PO_HEADERS_ALL", "list", "low", ""),
        EvalQuestion("q3", "PO", "LISTING", "m3", "PO_HEADERS_ALL", "list", "low", ""),
    ]
    res = await _run_dataset_concurrent(_Chat(), ds, session_prefix="t", concurrency=3, question_timeout_s=5.0)
    assert [r.id for r in res] == ["q1", "q2", "q3"]


@pytest.mark.asyncio
async def test_partial_failure_isolation() -> None:
    class _Chat:
        async def handle_message(self, session_id: str, message: str):
            if message == "boom":
                raise RuntimeError("fail one")
            class _Plan:
                intent = "x"
                joins = []
                table = "PO_HEADERS_ALL"
            class _Out:
                status = "success"
                rows_preview = [{"a": 1}]
                plan = _Plan()
                sql = "SELECT 1 FROM DUAL"
                answer = "ok"
                error_message = None
            return _Out()

    ds = [
        EvalQuestion("q1", "PO", "LISTING", "ok", "PO_HEADERS_ALL", "list", "low", ""),
        EvalQuestion("q2", "PO", "LISTING", "boom", "PO_HEADERS_ALL", "list", "low", ""),
        EvalQuestion("q3", "PO", "LISTING", "ok2", "PO_HEADERS_ALL", "list", "low", ""),
    ]
    res = await _run_dataset_concurrent(_Chat(), ds, session_prefix="t", concurrency=3, question_timeout_s=5.0)
    assert len(res) == 3
    assert any(r.status == "execution_error" for r in res)
    assert sum(1 for r in res if r.status == "success") == 2


@pytest.mark.asyncio
async def test_retry_behavior() -> None:
    stats = LLMRetryStats()
    attempts = {"n": 0}

    async def _fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            req = httpx.Request("POST", "http://x")
            resp = httpx.Response(503, request=req)
            raise httpx.HTTPStatusError("retry", request=req, response=resp)
        return "ok"

    with patch("scripts.e2e_real_provider_eval.random.uniform", return_value=0.0):
        out = await _call_with_retry(_fn, max_retries=3, retry_stats=stats)
    assert out == "ok"
    assert stats.retry_count == 2
    assert stats.retry_success_count == 1


def test_retryable_llm_exception_classifier() -> None:
    req = httpx.Request("POST", "http://x")
    e429 = httpx.HTTPStatusError("x", request=req, response=httpx.Response(429, request=req))
    e404 = httpx.HTTPStatusError("x", request=req, response=httpx.Response(404, request=req))
    assert _is_retryable_llm_exception(e429) is True
    assert _is_retryable_llm_exception(e404) is False


def test_summary_runtime_metrics() -> None:
    results = [
        EvalResult(
            id="q1",
            domain="PO",
            category="LISTING",
            question="x",
            expected_table="PO_HEADERS_ALL",
            expected_intent_type="list",
            status="success",
            raw_status="success",
            latency_ms=1000,
        ),
        EvalResult(
            id="q2",
            domain="PO",
            category="LISTING",
            question="x",
            expected_table="PO_HEADERS_ALL",
            expected_intent_type="list",
            status="execution_error",
            raw_status="execution_error",
            execution_error_subtype="timeout_error",
            error_detail="timeout",
            latency_ms=2000,
        ),
    ]
    stats = LLMRetryStats(retry_count=3, retry_success_count=1)
    s = _make_summary(
        results,
        oracle_timeout=30,
        concurrency=4,
        max_retries=2,
        total_wall_time_s=12.5,
        llm_retry_stats=stats,
    )
    assert s.concurrency == 4
    assert s.max_retries == 2
    assert s.total_wall_time_s == 12.5
    assert s.llm_retry_count == 3
    assert s.llm_retry_success_count == 1
    assert s.p50_question_latency_s > 0
    assert s.p95_question_latency_s > 0


class _FakePlanner:
    def __init__(self, *, traced_question: str | None = None) -> None:
        self._llm = SimpleNamespace(model_name="fake-planner")
        self.last_trace: dict[str, object] = {}
        self._traced_question = traced_question

    async def plan(self, question: str) -> QueryPlan:
        plan = QueryPlan(
            intent="list employees",
            semantic_intent="list",
            table="EMP",
            select_columns=["AD_SOYAD"],
        )
        parsed_plan = {
            "table": "EMP",
            "select_columns": ["AD_SOYAD"],
            "filters": [],
            "joins": [],
            "aggregations": [],
            "group_by": [],
            "order_by": [],
            "semantic_intent": "list",
        }
        self.last_trace = {
            "user_message": self._traced_question or question,
            "retrieval": {
                "schema_tables": ["EMP"],
                "schema_docs": [{"doc_id": "schema-1"}],
                "examples": [{"doc_id": "example-1"}],
            },
            "prompt": {
                "prompt_length": 321,
                "prompt_budget": 1200,
                "prompt_truncated": False,
                "reduction_steps": ["trim_examples"],
                "full_prompt_text": "planner prompt",
            },
            "llm": {
                "raw_response_text": '{"table":"EMP"}',
                "parse_error": None,
            },
            "parsed_plan": parsed_plan,
            "normalize": {"before": parsed_plan, "after": parsed_plan},
            "repair": {"before": parsed_plan, "after": parsed_plan, "repair_applied": False, "repair_actions": []},
            "semantic": {"before": parsed_plan, "after": parsed_plan},
            "canonicalize": {"before": parsed_plan, "after": parsed_plan},
        }
        return plan


class _FakeOrchestrator:
    def __init__(self) -> None:
        self._executor = SimpleNamespace(__class__=SimpleNamespace(__name__="FakeExecutor"))
        self.last_trace: dict[str, object] = {}

    async def run_plan(self, plan: QueryPlan) -> OrchestrationResult:
        self.last_trace = {
            "validation": {"ok": True, "errors": [], "warnings": []},
            "compile": {"ok": True, "error": None, "params": {"dept": "IT"}, "selected_columns": ["AD_SOYAD"]},
            "execute": {"status": "success", "row_count": 1, "columns": ["AD_SOYAD"], "error_message": None, "execution_time_ms": 7},
        }
        return OrchestrationResult(
            validation=ValidationResult(ok=True),
            compiled_query=CompiledQuery(
                sql="SELECT AD_SOYAD FROM EMP WHERE DEPT = :dept",
                params={"dept": "IT"},
                table="EMP",
                selected_columns=["AD_SOYAD"],
            ),
            execution_result=ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                columns=["AD_SOYAD"],
                rows=[{"AD_SOYAD": "Ali"}],
                row_count=1,
                execution_time_ms=7,
            ),
            failed_phase=None,
        )


class _FakeNarrator:
    def __init__(self, *, traced_question: str | None = None, summary_override: str | None = None) -> None:
        self._llm = SimpleNamespace(model_name="fake-narrator")
        self._last_trace: dict[str, object] = {}
        self._traced_question = traced_question
        self._summary_override = summary_override

    @property
    def last_trace(self) -> dict[str, object]:
        return self._last_trace

    async def narrate_success(self, user_message: str, result: OrchestrationResult) -> str:
        raw_response = "Thinking Process:\n1. plan\n\nMerhaba"
        self._last_trace = {
            "user_message": self._traced_question or user_message,
            "summary": self._summary_override or NarratorService._build_success_summary(result),  # noqa: SLF001
            "full_prompt_text": "narrator prompt",
            "raw_response": raw_response,
            "final_response": "STALE FINAL",
            "error": None,
        }
        return NarratorService._strip_leakage(raw_response)  # noqa: SLF001


class _FakeChat:
    def __init__(self, *, planner_question: str | None = None, narrator_question: str | None = None, narrator_summary: str | None = None) -> None:
        self._planner = _FakePlanner(traced_question=planner_question)
        self._orchestrator = _FakeOrchestrator()
        self._narrator = _FakeNarrator(traced_question=narrator_question, summary_override=narrator_summary)


@pytest.mark.asyncio
async def test_run_one_uses_immutable_snapshots_and_sanitized_final_response() -> None:
    chat = _FakeChat()
    item = EvalQuestion("q1", "EMP", "LISTING", "Aktif calisanlari listele", "EMP", "list", "low", "")

    result = await _run_one(chat, item, session_prefix="trace")

    assert result.status == "success"
    assert result.trace_id == result.question_trace["trace_id"]
    assert result.stage_alignment_ok is True
    assert result.narration_context_mismatch is False
    assert result.final_response_source == "sanitized"
    assert result.sanitizer_effective is True
    assert result.final_response_mapping_error is False
    assert result.raw_narrator_chain_of_thought_leak is True
    assert result.final_narrator_chain_of_thought_leak is False
    assert result.narration_ok is True
    assert result.quality_status == "pass"
    assert result.safety_status == "pass"
    assert result.root_cause_stage == "none"
    assert result.question_trace["narration"]["sanitized_response"] == "Merhaba"
    assert result.question_trace["narration"]["final_response"] == "Merhaba"
    assert result.question_trace["narration"]["raw_chain_of_thought_leak"] is True
    assert result.question_trace["narration"]["final_chain_of_thought_leak"] is False
    assert result.question_trace["narration"]["narration_ok"] is True
    assert result.question_trace["compile"]["compiled_sql_source_plan_stage"] == "canonicalize"
    assert result.question_trace["compile"]["compile_input_plan_snapshot"]["table"] == "EMP"

    chat._planner.last_trace["prompt"]["reduction_steps"].append("mutated")
    chat._orchestrator.last_trace["compile"]["params"]["dept"] = "HR"
    chat._narrator._last_trace["raw_response"] = "CHANGED"

    assert result.question_trace["prompt"]["reduction_steps"] == ["trim_examples"]
    assert result.question_trace["compile"]["params"] == {"dept": "IT"}
    assert result.question_trace["narration"]["raw_response"] == "Thinking Process:\n1. plan\n\nMerhaba"


@pytest.mark.asyncio
async def test_run_one_detects_narration_context_mismatch() -> None:
    chat = _FakeChat(narrator_question="Baska soru", narrator_summary="yanlis ozet")
    item = EvalQuestion("q1", "EMP", "LISTING", "Aktif calisanlari listele", "EMP", "list", "low", "")

    result = await _run_one(chat, item, session_prefix="trace")

    assert result.status == "success"
    assert result.stage_alignment_ok is False
    assert "narrator_question" in result.alignment_errors
    assert result.narration_context_mismatch is True
    assert set(result.narration_context_mismatch_fields) == {"question", "summary"}
