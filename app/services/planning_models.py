"""Shared models for the planner pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.domain.catalog_models import CatalogSnapshot
from app.domain.query_plan import QueryPlan
from app.providers.documents.models import ExampleDocument, SchemaDocument
from app.services.plan_normalizer import NormalizationStats
from app.services.query_plan_repair import RepairResult
from app.services.query_understanding import QueryUnderstanding


@dataclass(frozen=True)
class PrincipalContext:
    """Reserved identity/policy context for future pipeline stages."""

    principal_id: str | None = None
    roles: tuple[str, ...] = ()
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestContext:
    """Immutable user request payload shared across planning stages."""

    user_message: str
    normalized_user_message: str
    principal: PrincipalContext = field(default_factory=PrincipalContext)


QueryUnderstandingResult = QueryUnderstanding


@dataclass(frozen=True)
class RetrievalDiagnostics:
    """Structured retrieval metadata kept separate from business payload."""

    assessment: str = "unknown"
    schema_table_count: int = 0
    relationship_count: int = 0
    dominant_domain_match: bool | None = None
    root_table_name: str | None = None
    root_table_confidence: str | None = None
    noisy_context_count: int = 0
    dropped_candidates: list[str] = field(default_factory=list)
    kept_candidates_reason: dict[str, str] = field(default_factory=dict)

    def as_trace_dict(self) -> dict[str, Any]:
        return {
            "schema_table_count": self.schema_table_count,
            "relationship_count": self.relationship_count,
            "retrieval_assessment": self.assessment,
            "dominant_domain_match": self.dominant_domain_match,
            "root_table_name": self.root_table_name,
            "root_table_confidence": self.root_table_confidence,
            "noisy_context_count": self.noisy_context_count,
            "dropped_candidates": list(self.dropped_candidates),
            "kept_candidates_reason": dict(self.kept_candidates_reason),
        }


@dataclass(frozen=True)
class PromptDiagnostics:
    """Prompt-budget and composition diagnostics."""

    prompt_length: int
    prompt_budget: int
    prompt_truncated: bool
    reduction_steps: list[str] = field(default_factory=list)
    schema_tables_in_prompt: list[str] = field(default_factory=list)
    schema_doc_count: int = 0
    example_count: int = 0
    doc_content_chars: int = 0
    example_explanation_chars: int = 0
    # Semantic grounding
    semantic_retrieval_used: bool = False
    semantic_matches_total: int = 0
    semantic_prompt_chars: int = 0

    @classmethod
    def from_debug_payload(cls, payload: dict[str, Any]) -> "PromptDiagnostics":
        return cls(
            prompt_length=int(payload.get("prompt_length", 0)),
            prompt_budget=int(payload.get("prompt_budget", 0)),
            prompt_truncated=bool(payload.get("prompt_truncated", False)),
            reduction_steps=list(payload.get("reduction_steps", [])),
            schema_tables_in_prompt=list(payload.get("schema_tables_in_prompt", [])),
            schema_doc_count=int(payload.get("schema_doc_count", 0)),
            example_count=int(payload.get("example_count", 0)),
            doc_content_chars=int(payload.get("doc_content_chars", 0)),
            example_explanation_chars=int(payload.get("example_explanation_chars", 0)),
            semantic_retrieval_used=bool(payload.get("semantic_retrieval_used", False)),
            semantic_matches_total=int(payload.get("semantic_matches_total", 0)),
            semantic_prompt_chars=int(payload.get("semantic_prompt_chars", 0)),
        )

    def as_trace_dict(self) -> dict[str, Any]:
        return {
            "prompt_length": self.prompt_length,
            "prompt_budget": self.prompt_budget,
            "prompt_truncated": self.prompt_truncated,
            "reduction_steps": list(self.reduction_steps),
            "schema_tables_in_prompt": list(self.schema_tables_in_prompt),
            "schema_doc_count": self.schema_doc_count,
            "example_count": self.example_count,
            "doc_content_chars": self.doc_content_chars,
            "example_explanation_chars": self.example_explanation_chars,
            "semantic_retrieval_used": self.semantic_retrieval_used,
            "semantic_matches_total": self.semantic_matches_total,
            "semantic_prompt_chars": self.semantic_prompt_chars,
        }


@dataclass(frozen=True)
class RetrievedContext:
    """Structured retrieval payload passed into prompt assembly."""

    full_snapshot: CatalogSnapshot
    retrieved_snapshot: CatalogSnapshot
    pruned_columns: dict[str, list[str]]
    schema_docs: list[SchemaDocument] = field(default_factory=list)
    examples: list[ExampleDocument] = field(default_factory=list)
    retrieval_diagnostics: RetrievalDiagnostics = field(default_factory=RetrievalDiagnostics)

    def with_documents(
        self,
        *,
        schema_docs: list[SchemaDocument],
        examples: list[ExampleDocument],
        retrieval_diagnostics: RetrievalDiagnostics | None = None,
    ) -> "RetrievedContext":
        return replace(
            self,
            schema_docs=list(schema_docs),
            examples=list(examples),
            retrieval_diagnostics=retrieval_diagnostics or self.retrieval_diagnostics,
        )

    def schema_doc_refs(self) -> list[dict[str, Any]]:
        return [
            {
                "doc_id": doc.doc_id,
                "title": doc.title,
                "table_name": doc.table_name,
                "doc_type": doc.doc_type.value,
            }
            for doc in self.schema_docs
        ]

    def example_refs(self) -> list[dict[str, Any]]:
        return [
            {
                "doc_id": example.doc_id,
                "question": example.question,
                "tables": list(example.tables),
            }
            for example in self.examples
        ]


@dataclass(frozen=True)
class PlanningContext:
    """Unified planning context shared across planner stages."""

    request: RequestContext
    query_understanding: QueryUnderstandingResult
    retrieved_context: RetrievedContext
    prompt_diagnostics: PromptDiagnostics | None = None

    @property
    def principal(self) -> PrincipalContext:
        return self.request.principal

    @property
    def full_snapshot(self) -> CatalogSnapshot:
        return self.retrieved_context.full_snapshot

    @property
    def retrieved_snapshot(self) -> CatalogSnapshot:
        return self.retrieved_context.retrieved_snapshot

    @property
    def pruned_columns(self) -> dict[str, list[str]]:
        return self.retrieved_context.pruned_columns

    @property
    def schema_docs(self) -> list[SchemaDocument]:
        return self.retrieved_context.schema_docs

    @property
    def examples(self) -> list[ExampleDocument]:
        return self.retrieved_context.examples

    @property
    def retrieval_diagnostics(self) -> RetrievalDiagnostics:
        return self.retrieved_context.retrieval_diagnostics

    def with_retrieved_context(self, retrieved_context: RetrievedContext) -> "PlanningContext":
        return replace(self, retrieved_context=retrieved_context)

    def with_prompt_diagnostics(self, prompt_diagnostics: PromptDiagnostics) -> "PlanningContext":
        return replace(self, prompt_diagnostics=prompt_diagnostics)


ContextBundle = PlanningContext


@dataclass(frozen=True)
class PromptAssemblyResult:
    """Prompt text and the typed context used to build it."""

    prompt: str
    context: PlanningContext
    semantic_retrieval_trace: dict[str, Any] = field(default_factory=dict)

    @property
    def trace(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_docs": self.context.retrieved_context.schema_doc_refs(),
            "examples": self.context.retrieved_context.example_refs(),
            "retrieval_assessment": self.context.retrieval_diagnostics.assessment,
        }
        if self.context.prompt_diagnostics is not None:
            payload.update(self.context.prompt_diagnostics.as_trace_dict())
        if self.semantic_retrieval_trace:
            payload["semantic_retrieval"] = self.semantic_retrieval_trace
        return payload


@dataclass(frozen=True)
class PlanGenerationResult:
    """Structured plan generation output and raw LLM trace fields."""

    plan: QueryPlan
    raw_response_text: str | None
    parse_error: str | None
    parse_error_taxonomy: str | None = None
    salvage_applied: bool = False


@dataclass(frozen=True)
class PlanNormalizationResult:
    """Normalized plan plus structured normalization decisions."""

    plan: QueryPlan
    limit_clamped: bool
    clarification_cleanup_applied: bool


@dataclass(frozen=True)
class RepairStageResult:
    """Repair stage output and audit details."""

    plan: QueryPlan
    repair_result: RepairResult


@dataclass(frozen=True)
class SemanticResolutionResult:
    """Semantic resolution output, including canonicalization details."""

    semantic_plan: QueryPlan
    canonicalized_plan: QueryPlan
    canonicalization_stats: NormalizationStats
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClarificationDecision:
    """Typed clarification and intent-guard signals."""

    requested_filter_signals: list[str] = field(default_factory=list)
    planner_filter_coverage: dict[str, Any] = field(default_factory=dict)
    final_filter_coverage: dict[str, Any] = field(default_factory=dict)
    false_success_risk: bool = False
    success_blocked_by_filter_loss: bool = False
    clarification_reason_code: str | None = None
    clarification_missing_dimensions: list[str] = field(default_factory=list)
    clarification_was_avoidable: bool = False
    plan_confidence: str | None = None
    semantic_confidence: str | None = None
    confidence_band: str | None = None
    plan_confidence_band: str | None = None

    def as_trace_dict(self) -> dict[str, Any]:
        return {
            "requested_filter_signals": list(self.requested_filter_signals),
            "planner_filter_coverage": dict(self.planner_filter_coverage),
            "final_filter_coverage": dict(self.final_filter_coverage),
            "false_success_risk": self.false_success_risk,
            "success_blocked_by_filter_loss": self.success_blocked_by_filter_loss,
            "clarification_reason_code": self.clarification_reason_code,
            "clarification_missing_dimensions": list(self.clarification_missing_dimensions),
            "clarification_was_avoidable": self.clarification_was_avoidable,
            "plan_confidence": self.plan_confidence,
            "semantic_confidence": self.semantic_confidence,
            "confidence_band": self.confidence_band,
            "plan_confidence_band": self.plan_confidence_band,
        }


@dataclass(frozen=True)
class ClarificationDecisionResult:
    """Final clarification decision and its trace payload."""

    plan: QueryPlan
    decision: ClarificationDecision

    @property
    def trace(self) -> dict[str, Any]:
        return self.decision.as_trace_dict()


@dataclass(frozen=True)
class PlanPostProcessResult:
    """Typed view of the normalize -> repair -> semantic -> clarification chain."""

    original_plan: QueryPlan
    normalization: PlanNormalizationResult
    repair: RepairStageResult
    semantic_resolution: SemanticResolutionResult
    clarification: ClarificationDecisionResult

    @property
    def final_plan(self) -> QueryPlan:
        return self.clarification.plan