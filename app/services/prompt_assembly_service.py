"""Prompt assembly stage for planner execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.providers.llm.prompts import build_two_tier_planner_prompt_debug
from app.services.document_retrieval_service import DocumentRetrievalService
from app.services.planning_models import (
    PlanningContext,
    PromptAssemblyResult,
    PromptDiagnostics,
    RetrievedContext,
    RetrievalDiagnostics,
)

if TYPE_CHECKING:
    from app.services.query_understanding import QueryUnderstanding


class PromptAssemblyService:
    """Build planner prompts from schema context and optional doc retrieval."""

    def __init__(
        self,
        doc_retrieval: DocumentRetrievalService | None = None,
    ) -> None:
        self._doc_retrieval = doc_retrieval

    async def assemble(
        self,
        user_message: str,
        planning_context: PlanningContext,
        *,
        query_understanding: "QueryUnderstanding | None" = None,
        query_understanding_summary: dict[str, Any] | None = None,
    ) -> PromptAssemblyResult:
        schema_docs = None
        examples = None
        if self._doc_retrieval is not None:
            doc_result = await self._doc_retrieval.retrieve_context(
                user_message,
                query_understanding=query_understanding,
                retrieved_tables=[table.name for table in planning_context.retrieved_snapshot.tables],
            )
            schema_docs = doc_result.schema_docs or None
            examples = doc_result.examples or None

        retrieval_diag = planning_context.retrieval_diagnostics
        doc_diag = self._doc_retrieval.last_retrieval_diagnostics if self._doc_retrieval is not None else None

        prompt, trace = build_two_tier_planner_prompt_debug(
            user_message,
            planning_context.full_snapshot,
            planning_context.retrieved_snapshot,
            planning_context.pruned_columns,
            schema_docs=schema_docs,
            examples=examples,
            query_understanding_summary=query_understanding_summary,
            root_table_name=retrieval_diag.root_table_name,
            max_prompt_chars=settings.planner_prompt_max_chars,
        )

        kept_candidates_reason = dict(retrieval_diag.kept_candidates_reason)
        dropped_candidates = list(retrieval_diag.dropped_candidates)
        noisy_context_count = retrieval_diag.noisy_context_count
        if doc_diag is not None:
            noisy_context_count += int(doc_diag.get("noisy_context_count", 0) or 0)
            dropped_candidates.extend(str(item) for item in doc_diag.get("dropped_candidates", []))
            kept_candidates_reason.update(
                {
                    str(key): str(value)
                    for key, value in dict(doc_diag.get("kept_candidates_reason", {})).items()
                }
            )

        retrieval_assessment = "sufficient"
        if not planning_context.retrieved_snapshot.tables:
            retrieval_assessment = "insufficient"
        elif retrieval_diag.dominant_domain_match is False or noisy_context_count > 1:
            retrieval_assessment = "noisy"
        elif retrieval_diag.root_table_confidence == "low":
            retrieval_assessment = "partial"
        elif schema_docs is None and examples is None:
            retrieval_assessment = "schema_only"
        elif not (schema_docs or examples):
            retrieval_assessment = "partial"

        retrieved_context = planning_context.retrieved_context.with_documents(
            schema_docs=list(schema_docs or []),
            examples=list(examples or []),
            retrieval_diagnostics=RetrievalDiagnostics(
                assessment=retrieval_assessment,
                schema_table_count=len(planning_context.retrieved_snapshot.tables),
                relationship_count=len(planning_context.retrieved_snapshot.relationships),
                dominant_domain_match=retrieval_diag.dominant_domain_match,
                root_table_name=retrieval_diag.root_table_name,
                root_table_confidence=retrieval_diag.root_table_confidence,
                noisy_context_count=noisy_context_count,
                dropped_candidates=dropped_candidates,
                kept_candidates_reason=kept_candidates_reason,
            ),
        )
        updated_context = planning_context.with_retrieved_context(retrieved_context).with_prompt_diagnostics(
            PromptDiagnostics.from_debug_payload(trace)
        )
        return PromptAssemblyResult(prompt=prompt, context=updated_context)