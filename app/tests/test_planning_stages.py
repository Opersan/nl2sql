from __future__ import annotations

import pytest

from app.domain.catalog_models import CatalogSnapshot, ColumnMetadata, TableMetadata
from app.domain.query_plan import QueryPlan
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.providers.llm.mock_llm import MockLLMProvider
from app.services.catalog_service import CatalogService
from app.services.clarification_decision_service import ClarificationDecisionService
from app.services.plan_generation_service import PlanGenerationResult
from app.services.plan_normalization_service import PlanNormalizationService
from app.services.planner_service import PlannerService
from app.services.planning_context_service import PlanningContextAssemblyService
from app.services.planning_models import (
    ClarificationDecision,
    ClarificationDecisionResult,
    PlanNormalizationResult,
    PlanningContext,
    PromptAssemblyResult,
    PromptDiagnostics,
    RequestContext,
    RepairStageResult,
    RetrievalDiagnostics,
    RetrievedContext,
)
from app.services.prompt_assembly_service import PromptAssemblyService
from app.services.query_plan_repair import RepairResult
from app.services.query_understanding import QueryUnderstanding


@pytest.mark.asyncio
async def test_planning_context_assembly_hardens_filter_columns() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    service = PlanningContextAssemblyService(catalog, MockLLMProvider())
    request_context = RequestContext(
        user_message="Birim IT çalışanları",
        normalized_user_message="birim it calisanlari",
    )
    query_understanding = QueryUnderstanding(
        original_question="Birim IT çalışanları",
        normalized_question="birim it calisanlari",
        extracted_filters=[{"dimension": "BIRIM_ADI", "value": "IT"}],
    )

    async def _prune_columns(_user_message: str, context: object) -> dict[str, list[str]]:
        _ = context
        return {"XXBT_PDKS_PER_DETAILS_V": ["AD"]}

    service.prune_columns = _prune_columns  # type: ignore[method-assign]

    planning_context = await service.assemble(request_context, query_understanding)

    assert planning_context.request.user_message == "Birim IT çalışanları"
    assert planning_context.query_understanding is query_understanding
    assert "XXBT_PDKS_PER_DETAILS_V" in planning_context.pruned_columns
    assert "AD" in planning_context.pruned_columns["XXBT_PDKS_PER_DETAILS_V"]
    assert "BIRIM_ADI" in planning_context.pruned_columns["XXBT_PDKS_PER_DETAILS_V"]
    assert "PERSON_ID" in planning_context.pruned_columns["XXBT_PDKS_PER_DETAILS_V"]
    assert planning_context.retrieval_diagnostics.schema_table_count == len(planning_context.retrieved_snapshot.tables)


@pytest.mark.asyncio
async def test_prompt_assembly_service_marks_schema_only_without_docs() -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    full_snapshot = await catalog.get_snapshot()
    retrieved_snapshot = await catalog.get_relevant_context("Aktif çalışanları listele")
    service = PromptAssemblyService(doc_retrieval=None)

    result = await service.assemble(
        "Aktif çalışanları listele",
        PlanningContext(
            request=RequestContext(
                user_message="Aktif çalışanları listele",
                normalized_user_message="aktif calisanlari listele",
            ),
            query_understanding=QueryUnderstanding(
                original_question="Aktif çalışanları listele",
                normalized_question="aktif calisanlari listele",
            ),
            retrieved_context=RetrievedContext(
                full_snapshot=full_snapshot,
                retrieved_snapshot=retrieved_snapshot,
                pruned_columns={},
                retrieval_diagnostics=RetrievalDiagnostics(
                    schema_table_count=len(retrieved_snapshot.tables),
                    relationship_count=len(retrieved_snapshot.relationships),
                ),
            ),
        ),
        query_understanding_summary={"requested_output_type": "list"},
    )

    assert "Kullanıcı sorusu: Aktif çalışanları listele" in result.prompt
    assert result.context.retrieval_diagnostics.assessment == "schema_only"
    assert result.context.prompt_diagnostics is not None
    assert result.trace["retrieval_assessment"] == "schema_only"


@pytest.mark.asyncio
async def test_prompt_assembly_merges_noise_diagnostics_into_context() -> None:
    class _DocRetrieval:
        last_retrieval_diagnostics = {
            "noisy_context_count": 2,
            "dropped_candidates": ["po-doc:cross_domain_doc"],
            "kept_candidates_reason": {"hr-doc": "root_table_doc"},
        }

        async def retrieve_context(self, *args: object, **kwargs: object):
            from app.providers.retrieval.base import DocumentRetrievalResult
            return DocumentRetrievalResult()

    service = PromptAssemblyService(doc_retrieval=_DocRetrieval())
    catalog = CatalogService(InMemoryCatalogProvider())
    full_snapshot = await catalog.get_snapshot()
    snapshot = CatalogSnapshot(tables=[full_snapshot.tables[0]], relationships=[])

    result = await service.assemble(
        "Aktif çalışanları listele",
        PlanningContext(
            request=RequestContext(
                user_message="Aktif çalışanları listele",
                normalized_user_message="aktif calisanlari listele",
            ),
            query_understanding=QueryUnderstanding(
                original_question="Aktif çalışanları listele",
                normalized_question="aktif calisanlari listele",
            ),
            retrieved_context=RetrievedContext(
                full_snapshot=snapshot,
                retrieved_snapshot=snapshot,
                pruned_columns={},
                retrieval_diagnostics=RetrievalDiagnostics(
                    schema_table_count=1,
                    relationship_count=0,
                    dominant_domain_match=True,
                    root_table_name="XXBT_PDKS_PER_DETAILS_V",
                ),
            ),
        ),
        query_understanding_summary={"requested_output_type": "list"},
    )

    assert result.context.retrieval_diagnostics.assessment == "noisy"
    assert result.context.retrieval_diagnostics.noisy_context_count == 2
    assert result.context.retrieval_diagnostics.dropped_candidates == ["po-doc:cross_domain_doc"]
    assert result.context.retrieval_diagnostics.kept_candidates_reason == {"hr-doc": "root_table_doc"}


def test_planning_context_bundle_preserves_compatibility_properties() -> None:
    snapshot = CatalogSnapshot(tables=[], relationships=[])
    planning_context = PlanningContext(
        request=RequestContext(
            user_message="Çalışanları getir",
            normalized_user_message="calisanlari getir",
        ),
        query_understanding=QueryUnderstanding(
            original_question="Çalışanları getir",
            normalized_question="calisanlari getir",
        ),
        retrieved_context=RetrievedContext(
            full_snapshot=snapshot,
            retrieved_snapshot=snapshot,
            pruned_columns={"T": ["C"]},
        ),
    )

    assert planning_context.full_snapshot is snapshot
    assert planning_context.retrieved_snapshot is snapshot
    assert planning_context.pruned_columns == {"T": ["C"]}


@pytest.mark.asyncio
async def test_planner_service_orchestrates_stage_services_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = CatalogService(InMemoryCatalogProvider())
    planner = PlannerService(MockLLMProvider(), catalog)
    execution_order: list[str] = []

    planning_context = PlanningContext(
        request=RequestContext(
            user_message="Aktif çalışanları listele",
            normalized_user_message="aktif calisanlari listele",
        ),
        query_understanding=QueryUnderstanding(
            original_question="Aktif çalışanları listele",
            normalized_question="aktif calisanlari listele",
            detected_entities=["Employee"],
            inferred_modules=["HR"],
        ),
        retrieved_context=RetrievedContext(
            full_snapshot=await catalog.get_snapshot(),
            retrieved_snapshot=await catalog.get_relevant_context("Aktif çalışanları listele"),
            pruned_columns={"XXBT_PDKS_PER_DETAILS_V": ["AD"]},
            retrieval_diagnostics=RetrievalDiagnostics(
                assessment="schema_only",
                schema_table_count=1,
                relationship_count=0,
            ),
        ),
    )
    planner_plan = QueryPlan(intent="listing", table="XXBT_PDKS_PER_DETAILS_V", select_columns=["AD"])
    final_plan = planner_plan.model_copy(update={"select_columns": ["FULL_NAME"]})

    class _QueryUnderstandingStage:
        def analyze(self, user_message: str) -> QueryUnderstanding:
            execution_order.append("understanding")
            return QueryUnderstanding(
                original_question=user_message,
                normalized_question="aktif calisanlari listele",
                detected_entities=["Employee"],
                inferred_modules=["HR"],
                entity_confidence="high",
            )

    class _ContextStage:
        async def assemble(self, request_context: RequestContext, query_understanding: QueryUnderstanding) -> PlanningContext:
            assert request_context.user_message == "Aktif çalışanları listele"
            assert query_understanding.detected_entities == ["Employee"]
            execution_order.append("context")
            return planning_context

    class _PromptStage:
        async def assemble(self, user_message: str, stage_context: PlanningContext, **kwargs: object) -> PromptAssemblyResult:
            assert user_message == "Aktif çalışanları listele"
            assert stage_context is planning_context
            assert kwargs["query_understanding_summary"]["detected_entities"] == ["Employee"]
            execution_order.append("prompt")
            return PromptAssemblyResult(
                prompt="prompt-text",
                context=stage_context.with_prompt_diagnostics(
                    PromptDiagnostics(
                        prompt_length=11,
                        prompt_budget=24000,
                        prompt_truncated=False,
                        schema_tables_in_prompt=["XXBT_PDKS_PER_DETAILS_V"],
                    )
                ),
            )

    class _GenerationStage:
        async def generate(self, prompt: str) -> PlanGenerationResult:
            assert prompt == "prompt-text"
            execution_order.append("generation")
            return PlanGenerationResult(
                plan=planner_plan,
                raw_response_text="{...}",
                parse_error=None,
                parse_error_taxonomy=None,
                salvage_applied=False,
            )

    class _NormalizationStage:
        def normalize(self, plan: QueryPlan) -> PlanNormalizationResult:
            assert plan is planner_plan
            execution_order.append("normalize")
            return PlanNormalizationResult(
                plan=plan,
                limit_clamped=False,
                clarification_cleanup_applied=False,
            )

    class _RepairStage:
        def repair(self, plan: QueryPlan, user_message: str) -> RepairStageResult:
            assert plan is planner_plan
            assert user_message == "Aktif çalışanları listele"
            execution_order.append("repair")
            return RepairStageResult(plan=plan, repair_result=RepairResult())

    class _SemanticStage:
        def resolve(self, plan: QueryPlan, user_message: str, context: object, **kwargs: object):
            assert plan is planner_plan
            assert user_message == "Aktif çalışanları listele"
            assert context is planning_context.retrieved_snapshot
            assert kwargs["query_understanding"] is planning_context.query_understanding
            assert kwargs["retrieval_diagnostics"] is planning_context.retrieval_diagnostics
            execution_order.append("semantic")
            from app.services.plan_normalizer import NormalizationStats
            from app.services.planning_models import SemanticResolutionResult

            return SemanticResolutionResult(
                semantic_plan=plan,
                canonicalized_plan=final_plan,
                canonicalization_stats=NormalizationStats(),
                diagnostics={"decision_reasons": ["query_understanding_alignment"]},
            )

    class _ClarificationStage:
        def apply(
            self,
            user_message: str,
            planner_snapshot: QueryPlan,
            resolved_plan: QueryPlan,
            **kwargs: object,
        ) -> ClarificationDecisionResult:
            assert user_message == "Aktif çalışanları listele"
            assert planner_snapshot is planner_plan
            assert resolved_plan is final_plan
            assert kwargs["query_understanding"] is planning_context.query_understanding
            assert kwargs["retrieval_diagnostics"] is planning_context.retrieval_diagnostics
            assert kwargs["semantic_diagnostics"] == {"decision_reasons": ["query_understanding_alignment"]}
            assert kwargs["parse_error_taxonomy"] is None
            assert kwargs["salvage_applied"] is False
            assert kwargs["catalog_snapshot"] is planning_context.retrieved_snapshot
            execution_order.append("clarification")
            return ClarificationDecisionResult(
                plan=resolved_plan,
                decision=ClarificationDecision(
                    plan_confidence="rule_high",
                    semantic_confidence="rule_high",
                    confidence_band="high",
                    plan_confidence_band="high",
                ),
            )

    monkeypatch.setattr(planner, "_query_understanding_service", _QueryUnderstandingStage())
    monkeypatch.setattr(planner, "_context_assembly_service", _ContextStage())
    monkeypatch.setattr(planner, "_prompt_assembly_service", _PromptStage())
    monkeypatch.setattr(planner, "_plan_generation_service", _GenerationStage())
    monkeypatch.setattr(planner, "_plan_normalization_service", _NormalizationStage())
    monkeypatch.setattr(planner, "_plan_repair_service", _RepairStage())
    monkeypatch.setattr(planner, "_semantic_resolution_service", _SemanticStage())
    monkeypatch.setattr(planner, "_clarification_decision_service", _ClarificationStage())

    plan = await planner.plan("Aktif çalışanları listele")

    assert plan == final_plan
    assert execution_order == [
        "understanding",
        "context",
        "prompt",
        "generation",
        "normalize",
        "repair",
        "semantic",
        "clarification",
    ]
    assert planner.last_trace is not None
    assert planner.last_trace["final_plan"]["select_columns"] == ["FULL_NAME"]


def test_plan_normalization_service_returns_typed_contract() -> None:
    from app.core.config import settings

    if settings.max_row_limit >= 1000:
        pytest.skip("max_row_limit too high to exercise clamp path with QueryPlan validation")

    service = PlanNormalizationService()
    plan = QueryPlan(
        intent="Belirsiz sorgu",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["AD"],
        needs_clarification=True,
        clarification_message="Açıkla",
        limit=settings.max_row_limit + 1,
    )

    result = service.normalize(plan)

    assert result.plan.limit == settings.max_row_limit
    assert result.limit_clamped is True
    assert result.clarification_cleanup_applied is True
    assert result.plan.select_columns == []


def test_clarification_decision_service_returns_typed_decision() -> None:
    service = ClarificationDecisionService()
    planner_plan = QueryPlan(
        intent="listing",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["AD"],
        filters=[],
    )
    resolved_plan = planner_plan

    result = service.apply("Çalışanları getir", planner_plan, resolved_plan)

    assert isinstance(result.decision, ClarificationDecision)
    assert result.trace["confidence_band"] == result.decision.confidence_band


def test_clarification_decision_service_recovers_safe_listing_clarification() -> None:
    service = ClarificationDecisionService()
    planner_plan = QueryPlan(
        intent="clarification_required",
        table="XXBT_PDKS_PER_DETAILS_V",
        needs_clarification=True,
        clarification_message="Çalışan kayıtlarını biraz daha netleştirir misiniz?",
    )
    resolved_plan = planner_plan.model_copy(update={"semantic_intent": "employee_list", "root_entity": "HR_EMPLOYEES"})
    query_understanding = QueryUnderstanding(
        original_question="Çalışan kayıtları",
        normalized_question="calisan kayitlari",
        requested_output_type="list",
        entity_confidence="high",
    )
    catalog_snapshot = CatalogSnapshot(
        tables=[
            TableMetadata(
                name="XXBT_PDKS_PER_DETAILS_V",
                columns=[
                    ColumnMetadata(name="PERSON_ID", data_type="NUMBER"),
                    ColumnMetadata(name="SICIL_NO", data_type="VARCHAR"),
                    ColumnMetadata(name="AD", data_type="VARCHAR"),
                    ColumnMetadata(name="SOYAD", data_type="VARCHAR"),
                    ColumnMetadata(name="TC_NO", data_type="VARCHAR", restricted=True),
                ],
            )
        ]
    )

    result = service.apply(
        "Çalışan kayıtları",
        planner_plan,
        resolved_plan,
        query_understanding=query_understanding,
        retrieval_diagnostics=RetrievalDiagnostics(
            assessment="sufficient",
            dominant_domain_match=True,
            root_table_name="XXBT_PDKS_PER_DETAILS_V",
            root_table_confidence="high",
        ),
        semantic_diagnostics={
            "confidence": "high",
            "selected_root_table": "XXBT_PDKS_PER_DETAILS_V",
            "selected_entity_score": 9,
            "runner_up_score": 2,
        },
        catalog_snapshot=catalog_snapshot,
    )

    assert result.plan.needs_clarification is False
    assert result.plan.select_columns == ["SICIL_NO", "AD", "SOYAD"]
    assert result.decision.clarification_reason_code is None
