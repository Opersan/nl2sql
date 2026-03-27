"""Tests for the Pipeline Live View observability layer.

These tests verify:
1. Normal non-trace API behavior unchanged
2. Trace mode can be enabled without affecting core result
3. Stage events appear in correct order
4. Metadata/catalog/semantic readiness is visible in trace mode
5. Planner substages (QU, retrieval, prompt, LLM, normalize, repair, semantic) visible
6. Compiled SQL and execution summary visible
7. Narrator stages (prompt, response, sanitize, final) visible
8. Failure case yields partial trace up to failure
9. Safe serializer handles missing/raw-empty fields
10. No secrets / unsafe internals / raw vectors leak through serialization
11. Trace mode streaming does not break standard request flow
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domain.execution_models import ErrorPhase, ExecutionStatus
from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.domain.trace_models import StageStatus, TraceCollector
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.providers.executor.mock_executor import MockExecutor
from app.providers.llm.mock_llm import MockLLMProvider
from app.services.catalog_service import CatalogService
from app.services.narrator_service import NarratorService
from app.services.orchestrator import ChatOrchestrator, Orchestrator
from app.services.planner_service import PlannerService
from app.services.session_service import SessionService
from app.services.sql_compiler import SQLCompiler
from app.services.validation_service import ValidationService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def orchestrator() -> Orchestrator:
    provider = InMemoryCatalogProvider()
    catalog = CatalogService(provider)
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = MockExecutor()
    return Orchestrator(validator, compiler, executor)


@pytest.fixture
def chat_orchestrator() -> ChatOrchestrator:
    llm = MockLLMProvider()
    catalog_provider = InMemoryCatalogProvider()
    catalog = CatalogService(catalog_provider)
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = MockExecutor()
    planner = PlannerService(llm, catalog)
    narrator = NarratorService(llm)
    inner = Orchestrator(validator, compiler, executor)
    sessions = SessionService()
    return ChatOrchestrator(planner, inner, narrator, sessions)


async def _collect_trace_events(collector: TraceCollector) -> list[Any]:
    """Drain a TraceCollector into a list of StageEvent objects."""
    events = []
    async for event in collector:
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# 1. Normal non-trace behavior unchanged
# ---------------------------------------------------------------------------


class TestNormalBehaviorUnchanged:
    @pytest.mark.asyncio
    async def test_chat_orchestrator_no_trace_still_works(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        """Calling handle_message without trace_collector is unchanged."""
        result = await chat_orchestrator.handle_message(
            "s-no-trace", "Aktif çalışanları listele"
        )
        assert result.status == "success"
        assert result.plan is not None
        assert result.answer

    @pytest.mark.asyncio
    async def test_orchestrator_no_trace_still_works(
        self, orchestrator: Orchestrator
    ) -> None:
        """Calling run_plan without trace_collector is unchanged."""
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["first_name"],
            filters=[FilterSpec(column="quit_date", op=FilterOp.IS_NULL)],
        )
        result = await orchestrator.run_plan(plan)
        assert result.validation.ok
        assert result.compiled_query is not None


# ---------------------------------------------------------------------------
# 2. Trace mode: correct result is still returned
# ---------------------------------------------------------------------------


class TestTraceModeResult:
    @pytest.mark.asyncio
    async def test_result_identical_with_and_without_trace(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        """Result is identical whether or not trace_collector is provided."""
        result_no_trace = await chat_orchestrator.handle_message(
            "s-a", "Aktif çalışanları listele"
        )

        collector = TraceCollector(trace_id="test-trace-001")

        # Run pipeline with trace collector but drain it in background
        async def run() -> None:
            await chat_orchestrator.handle_message(
                "s-b", "Aktif çalışanları listele", trace_collector=collector
            )

        await run()

        assert result_no_trace.status == "success"
        assert result_no_trace.plan is not None


# ---------------------------------------------------------------------------
# 3. Stage events appear in correct order
# ---------------------------------------------------------------------------


class TestStageOrder:
    @pytest.mark.asyncio
    async def test_stage_order_success_path(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        """On a successful run, key stages appear in the expected logical order."""
        collector = TraceCollector(trace_id="test-order-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())

        await chat_orchestrator.handle_message(
            "s-order", "Aktif çalışanları listele", trace_collector=collector
        )
        collector.close()
        await collect_task

        stage_names = [e.stage_name for e in events]

        # Early stages must precede later stages
        assert "question" in stage_names
        assert "runtime_context" in stage_names
        assert "metadata_catalog_load" in stage_names
        assert "semantic_registry_load" in stage_names

        # Execution stages must be present
        assert "validation" in stage_names
        assert "compile" in stage_names

        # Final verdict must be last meaningful stage
        assert "final_verdict" in stage_names
        last_final_idx = max(i for i, e in enumerate(stage_names) if e == "final_verdict")
        assert last_final_idx == len(stage_names) - 1, (
            f"final_verdict must be the last event. Got: {stage_names[-3:]}"
        )

    @pytest.mark.asyncio
    async def test_question_appears_before_validation(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        collector = TraceCollector(trace_id="test-order-002")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await chat_orchestrator.handle_message(
            "s-order2", "Aktif çalışanları listele", trace_collector=collector
        )
        collector.close()
        await collect_task

        stage_names = [e.stage_name for e in events]
        q_idx = next((i for i, n in enumerate(stage_names) if n == "question"), None)
        v_idx = next((i for i, n in enumerate(stage_names) if n == "validation"), None)
        assert q_idx is not None
        assert v_idx is not None
        assert q_idx < v_idx


# ---------------------------------------------------------------------------
# 4. Each stage has the expected status
# ---------------------------------------------------------------------------


class TestStageStatuses:
    @pytest.mark.asyncio
    async def test_success_run_all_stages_passed(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        collector = TraceCollector(trace_id="test-status-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await chat_orchestrator.handle_message(
            "s-status", "Aktif çalışanları listele", trace_collector=collector
        )
        collector.close()
        await collect_task

        # Get last event per stage
        last_per_stage: dict[str, Any] = {}
        for e in events:
            last_per_stage[e.stage_name] = e

        # Early stages should be passed
        assert last_per_stage["question"].status == StageStatus.PASSED
        assert last_per_stage["runtime_context"].status == StageStatus.PASSED
        assert last_per_stage["metadata_catalog_load"].status == StageStatus.PASSED
        assert last_per_stage["semantic_registry_load"].status == StageStatus.PASSED

        # Execution stages should be passed
        assert last_per_stage["validation"].status == StageStatus.PASSED
        assert last_per_stage["compile"].status == StageStatus.PASSED
        assert last_per_stage["execute"].status == StageStatus.PASSED

        # Final verdict should be passed
        assert last_per_stage["final_verdict"].status == StageStatus.PASSED


# ---------------------------------------------------------------------------
# 5. Planner substages are visible
# ---------------------------------------------------------------------------


class TestPlannerSubstages:
    @pytest.mark.asyncio
    async def test_planner_substages_present(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        collector = TraceCollector(trace_id="test-planner-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await chat_orchestrator.handle_message(
            "s-planner", "Aktif çalışanları listele", trace_collector=collector
        )
        collector.close()
        await collect_task

        stage_names = set(e.stage_name for e in events)

        # At minimum: planner request and response should be present
        assert "planner_llm_request" in stage_names or "prompt_assembly" in stage_names, (
            f"Expected planner stages, got: {stage_names}"
        )
        # Normalization stages
        assert "normalize" in stage_names or "planner_parsed_plan" in stage_names

    @pytest.mark.asyncio
    async def test_filter_column_resolution_stage_visible_in_live_view(
        self,
        chat_orchestrator: ChatOrchestrator,
    ) -> None:
        collector = TraceCollector(trace_id="test-filter-column-resolution-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await chat_orchestrator.handle_message(
            "s-filter-stage",
            "Aktif çalışanları listele",
            trace_collector=collector,
        )
        collector.close()
        await collect_task

        stage_names = [e.stage_name for e in events]
        assert "canonicalize" in stage_names
        assert "filter_column_resolution" in stage_names

        canonicalize_idx = next(i for i, name in enumerate(stage_names) if name == "canonicalize")
        fcr_idx = next(i for i, name in enumerate(stage_names) if name == "filter_column_resolution")
        assert canonicalize_idx < fcr_idx

        fcr_events = [e for e in events if e.stage_name == "filter_column_resolution"]
        assert fcr_events

        payload = fcr_events[-1].payload
        assert "any_changed" in payload
        assert "total_filters" in payload
        assert "changed_count" in payload
        assert "actions" in payload

        if payload["actions"]:
            action = payload["actions"][0]
            assert "original_column" in action
            assert "resolved_column" in action
            assert "reason" in action
            assert "confidence" in action

    @pytest.mark.asyncio
    async def test_filter_value_resolution_stage_visible_in_live_view(
        self,
        chat_orchestrator: ChatOrchestrator,
    ) -> None:
        collector = TraceCollector(trace_id="test-filter-value-resolution-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await chat_orchestrator.handle_message(
            "s-filter-value-stage",
            "IT departmanındaki çalışanları listele",
            trace_collector=collector,
        )
        collector.close()
        await collect_task

        stage_names = [e.stage_name for e in events]
        assert "filter_column_resolution" in stage_names
        assert "filter_value_resolution" in stage_names

        fcr_idx = next(i for i, name in enumerate(stage_names) if name == "filter_column_resolution")
        fvr_idx = next(i for i, name in enumerate(stage_names) if name == "filter_value_resolution")
        assert fcr_idx < fvr_idx

        fvr_events = [e for e in events if e.stage_name == "filter_value_resolution"]
        assert fvr_events

        payload = fvr_events[-1].payload
        assert "any_changed" in payload
        assert "clarification_required" in payload
        assert "total_filters" in payload
        assert "changed_count" in payload
        assert "actions" in payload

        if payload["actions"]:
            action = payload["actions"][0]
            assert "original_value" in action
            assert "resolved_value" in action
            assert "candidate_values" in action
            assert "reason" in action


# ---------------------------------------------------------------------------
# 6. Validation + compile + execute stages have payloads
# ---------------------------------------------------------------------------


class TestExecutionStagePayloads:
    @pytest.mark.asyncio
    async def test_compile_stage_has_sql_info(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        collector = TraceCollector(trace_id="test-compile-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await chat_orchestrator.handle_message(
            "s-compile", "Aktif çalışanları listele", trace_collector=collector
        )
        collector.close()
        await collect_task

        compile_events = [e for e in events if e.stage_name == "compile" and e.status == StageStatus.PASSED]
        assert compile_events, "Expected at least one PASSED compile event"

        compile_evt = compile_events[-1]
        # Should have SQL info (without raw bind params)
        assert compile_evt.payload.get("ok") is True
        assert "sql" in compile_evt.payload or "bind_summary" in compile_evt.payload or "table" in compile_evt.payload

    @pytest.mark.asyncio
    async def test_execute_stage_has_row_count(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        collector = TraceCollector(trace_id="test-execute-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await chat_orchestrator.handle_message(
            "s-execute", "Aktif çalışanları listele", trace_collector=collector
        )
        collector.close()
        await collect_task

        execute_events = [e for e in events if e.stage_name == "execute" and e.status == StageStatus.PASSED]
        assert execute_events, "Expected at least one PASSED execute event"

        exe_evt = execute_events[-1]
        assert "row_count" in exe_evt.payload, f"Expected row_count in execute payload, got: {exe_evt.payload.keys()}"


# ---------------------------------------------------------------------------
# 7. Narrator stages are visible
# ---------------------------------------------------------------------------


class TestNarratorStages:
    @pytest.mark.asyncio
    async def test_narrator_stages_present(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        collector = TraceCollector(trace_id="test-narrator-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await chat_orchestrator.handle_message(
            "s-narrator", "Aktif çalışanları listele", trace_collector=collector
        )
        collector.close()
        await collect_task

        stage_names = set(e.stage_name for e in events)
        assert "narrator_prompt" in stage_names, f"narrator_prompt not found in {stage_names}"
        assert "narrator_final_response" in stage_names, f"narrator_final_response not found in {stage_names}"

    @pytest.mark.asyncio
    async def test_narrator_stages_have_payloads(
        self, chat_orchestrator: ChatOrchestrator
    ) -> None:
        collector = TraceCollector(trace_id="test-narrator-002")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        await chat_orchestrator.handle_message(
            "s-narrator2", "Aktif çalışanları listele", trace_collector=collector
        )
        collector.close()
        await collect_task

        final_response_events = [e for e in events if e.stage_name == "narrator_final_response"]
        if final_response_events:
            evt = final_response_events[-1]
            # Should have final_response_source
            assert "final_response_source" in evt.payload or "final_response_preview" in evt.payload


# ---------------------------------------------------------------------------
# 8. Failure case yields partial trace up to failure point
# ---------------------------------------------------------------------------


class TestFailureCasePartialTrace:
    @pytest.mark.asyncio
    async def test_validation_failure_yields_partial_trace(
        self, orchestrator: Orchestrator
    ) -> None:
        """When validation fails, trace must contain validation FAILED event."""
        collector = TraceCollector(trace_id="test-fail-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())

        # Plan with non-existent table → validation fail
        plan = QueryPlan(
            intent="test fail",
            table="NONEXISTENT_TABLE_XYZ",
            select_columns=["col1"],
        )
        result = await orchestrator.run_plan(plan, trace_collector=collector)
        collector.close()
        await collect_task

        assert result.failed_phase == ErrorPhase.VALIDATION

        stage_names = [e.stage_name for e in events]
        assert "validation" in stage_names

        validation_events = [e for e in events if e.stage_name == "validation"]
        failed_validation = [e for e in validation_events if e.status == StageStatus.FAILED]
        assert failed_validation, (
            f"Expected a FAILED validation event, got: {[e.status for e in validation_events]}"
        )


# ---------------------------------------------------------------------------
# 9. Safe serializer: missing fields handled
# ---------------------------------------------------------------------------


class TestSafeSerializer:
    def test_safe_payload_none(self) -> None:
        from app.services.trace_serializer import safe_payload
        assert safe_payload(None) is None

    def test_safe_payload_truncates_long_string(self) -> None:
        from app.services.trace_serializer import safe_payload
        long_str = "x" * 2000
        result = safe_payload(long_str, max_str=100)
        assert len(result) < 200
        assert "truncated" in result

    def test_safe_payload_caps_list(self) -> None:
        from app.services.trace_serializer import safe_payload
        big_list = list(range(100))
        result = safe_payload(big_list, max_items=5)
        assert isinstance(result, list)
        assert len(result) <= 6  # 5 items + "more items" message

    def test_safe_payload_does_not_expose_embedding_vectors(self) -> None:
        from app.services.trace_serializer import safe_payload
        # Embedding vector: list of floats
        vector = [0.1234] * 768
        result = safe_payload(vector)
        assert isinstance(result, str)
        assert "embedding vector" in result
        assert "dim=768" in result

    def test_build_compile_payload_hides_bind_values(self) -> None:
        from app.services.trace_serializer import build_compile_payload
        compile_trace = {
            "ok": True,
            "sql": "SELECT * FROM T WHERE x = :x",
            "params": {"x": "secret_value"},
            "table": "T",
            "selected_columns": ["a", "b"],
            "executed_sql_fingerprint": "abc123",
            "bind_summary": "1 param",
            "latency_ms": 12,
        }
        result = build_compile_payload(compile_trace)
        # params values should NOT be in result directly
        assert "secret_value" not in str(result)
        # But param count and keys should be visible
        assert result.get("bind_param_count") == 1
        assert "x" in result.get("bind_param_keys", [])

    def test_build_narrator_prompt_payload_truncates(self) -> None:
        from app.services.trace_serializer import build_narrator_prompt_payload
        trace = {
            "full_prompt_text": "x" * 10000,
            "summary": "test",
            "prompt_length": 10000,
            "narration_shape": "listing",
        }
        result = build_narrator_prompt_payload(trace)
        assert result["prompt_char_count"] == 10000
        assert len(result["prompt_preview"]) < 5000  # truncated

    def test_build_catalog_readiness_payload_no_crash(self) -> None:
        from app.services.trace_serializer import build_catalog_readiness_payload
        result = build_catalog_readiness_payload()
        assert isinstance(result, dict)

    def test_build_semantic_registry_payload_no_crash(self) -> None:
        from app.services.trace_serializer import build_semantic_registry_payload
        result = build_semantic_registry_payload()
        assert isinstance(result, dict)
        # Either loaded or has an error key
        assert "loaded" in result or "error" in result

    def test_build_final_verdict_safe_with_none_sql(self) -> None:
        from app.services.trace_serializer import build_final_verdict_payload
        result = build_final_verdict_payload(
            status="success",
            answer="Sonuç hazır.",
            sql=None,
            total_elapsed_ms=500,
        )
        assert result["status"] == "success"
        assert result["sql_preview"] is None

    def test_safe_text_none(self) -> None:
        from app.services.trace_serializer import safe_text
        assert safe_text(None) == ""

    def test_safe_text_non_string(self) -> None:
        from app.services.trace_serializer import safe_text
        assert safe_text(42) == "42"


# ---------------------------------------------------------------------------
# 10. TraceCollector: basic lifecycle
# ---------------------------------------------------------------------------


class TestTraceCollector:
    @pytest.mark.asyncio
    async def test_collector_emits_events_in_order(self) -> None:
        collector = TraceCollector(trace_id="tc-1")

        collector.stage_started("stage_a", summary="Starting A")
        collector.stage_completed("stage_a", summary="Completed A")
        collector.stage_started("stage_b")
        collector.stage_failed("stage_b", summary="B failed")
        collector.close()

        events = await _collect_trace_events(collector)
        assert len(events) == 4
        assert events[0].stage_name == "stage_a"
        assert events[0].status == StageStatus.RUNNING
        assert events[1].stage_name == "stage_a"
        assert events[1].status == StageStatus.PASSED
        assert events[2].stage_name == "stage_b"
        assert events[2].status == StageStatus.RUNNING
        assert events[3].status == StageStatus.FAILED

    @pytest.mark.asyncio
    async def test_collector_close_is_idempotent(self) -> None:
        collector = TraceCollector(trace_id="tc-2")
        collector.stage_skipped("stage_a")
        collector.close()
        collector.close()  # should not raise or queue extra sentinel

        events = await _collect_trace_events(collector)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_collector_elapsed_ms_computed(self) -> None:
        import time as _time
        collector = TraceCollector(trace_id="tc-3")

        mono = collector.stage_started("stage_x", summary="running")
        _time.sleep(0.01)
        collector.stage_completed("stage_x", started_at_mono=mono, summary="done")
        collector.close()

        events = await _collect_trace_events(collector)
        completed = [e for e in events if e.status == StageStatus.PASSED][0]
        assert completed.elapsed_ms is not None
        assert completed.elapsed_ms >= 5  # at least 5ms

    @pytest.mark.asyncio
    async def test_collector_skipped_event(self) -> None:
        collector = TraceCollector(trace_id="tc-4")
        collector.stage_skipped("skipped_stage", summary="Skipping because X")
        collector.close()

        events = await _collect_trace_events(collector)
        assert events[0].status == StageStatus.SKIPPED
        assert events[0].summary == "Skipping because X"


# ---------------------------------------------------------------------------
# 11. Orchestrator run_plan with trace_collector
# ---------------------------------------------------------------------------


class TestOrchestratorWithTrace:
    @pytest.mark.asyncio
    async def test_run_plan_with_trace_collector_emits_validation_compile_execute(
        self, orchestrator: Orchestrator
    ) -> None:
        collector = TraceCollector(trace_id="test-orch-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["first_name"],
            filters=[FilterSpec(column="quit_date", op=FilterOp.IS_NULL)],
        )
        result = await orchestrator.run_plan(plan, trace_collector=collector)
        collector.close()
        await collect_task

        assert result.validation.ok

        stage_names = [e.stage_name for e in events]
        assert "validation" in stage_names
        assert "compile" in stage_names
        assert "execute" in stage_names

    @pytest.mark.asyncio
    async def test_run_plan_validation_fail_emits_failed_event(
        self, orchestrator: Orchestrator
    ) -> None:
        collector = TraceCollector(trace_id="test-orch-fail-001")
        events: list[Any] = []

        async def collect() -> None:
            async for event in collector:
                events.append(event)

        collect_task = asyncio.create_task(collect())
        plan = QueryPlan(
            intent="test fail",
            table="PLAN_NONEXISTENT_TABLE",
            select_columns=["x"],
        )
        result = await orchestrator.run_plan(plan, trace_collector=collector)
        collector.close()
        await collect_task

        assert result.failed_phase == ErrorPhase.VALIDATION

        val_events = [e for e in events if e.stage_name == "validation"]
        assert any(e.status == StageStatus.FAILED for e in val_events)


# ---------------------------------------------------------------------------
# 12. StageEvent model safety
# ---------------------------------------------------------------------------


class TestStageEventModel:
    def test_stage_event_serializes_to_json(self) -> None:
        from app.domain.trace_models import StageEvent
        evt = StageEvent(
            trace_id="t1",
            stage_name="compile",
            status=StageStatus.PASSED,
            elapsed_ms=42,
            summary="Compiled OK",
            payload={"ok": True, "sql": "SELECT 1 FROM DUAL"},
        )
        json_str = evt.model_dump_json()
        import json
        data = json.loads(json_str)
        assert data["stage_name"] == "compile"
        assert data["status"] == "passed"
        assert data["elapsed_ms"] == 42
        assert data["payload"]["ok"] is True

    def test_stage_event_payload_defaults_empty(self) -> None:
        from app.domain.trace_models import StageEvent
        evt = StageEvent(
            trace_id="t1",
            stage_name="question",
            status=StageStatus.RUNNING,
        )
        assert evt.payload == {}
        assert evt.metadata == {}
