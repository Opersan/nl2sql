"""Planner service -- natural language -> QueryPlan via LLM.

The planner **never** generates SQL.  It delegates to the configured
``LLMProvider`` to produce a structured ``QueryPlan`` that the
validation -> compilation -> execution pipeline can process.

Post-plan normalization
=======================
After receiving the raw plan from the LLM, the planner applies a
lightweight normalization pass:

* **Limit clamping** -- ensures ``plan.limit <= settings.max_row_limit``.
* **Clarification cleanup** -- strips query artifacts (select_columns,
  filters, aggregations, group_by, order_by) from plans that request
  clarification so downstream stages never receive a half-baked plan.

Hybrid retrieval (Sprint 3)
===========================
When a ``DocumentRetrievalService`` is injected, the planner enriches
the LLM prompt with two additional context layers:

1. **Schema documents** -- prose descriptions of tables, columns,
   relationships, or glossary terms that help the LLM understand the
   data model.
2. **Few-shot examples** -- past NL->SQL pairs that guide the LLM toward
   the expected output format and SQL style.

Pipeline extension points (Sprint 3+):

    classify_intent -> retrieve_schema -> retrieve_examples -> produce_plan

Restricted-field strategy
=========================
The planner prompt strongly discourages restricted-column usage via the
restricted marker in the catalog context.  However, the planner does **not**
block restricted fields -- that responsibility belongs exclusively to
``ValidationService``.  This separation keeps the planner focused on
intent translation while validation enforces access control.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
import re
from typing import Any

from app.core.config import settings
from app.core.exceptions import PlannerError
from app.core.logging import get_logger
from app.domain.catalog_models import CatalogSnapshot
from app.domain.query_plan import QueryPlan
from app.providers.llm.base import LLMProvider
from app.services.catalog_service import CatalogService
from app.services.clarification_decision_service import ClarificationDecisionService
from app.services.document_retrieval_service import DocumentRetrievalService
from app.services.plan_generation_service import PlanGenerationService
from app.services.plan_normalization_service import PlanNormalizationService
from app.services.plan_repair_service import PlanRepairService
from app.services.planning_context_service import PlanningContextAssemblyService
from app.services.planning_models import PlanPostProcessResult, PlanningContext, RequestContext
from app.services.prompt_assembly_service import PromptAssemblyService
from app.services.query_plan_repair import RepairResult
from app.services.query_understanding import QueryUnderstanding
from app.services.query_understanding_service import QueryUnderstandingService
from app.services.semantic_planning import _load_registry, apply_semantic_normalization
from app.services.semantic_resolution_service import SemanticResolutionService
from app.services.plan_normalizer import NormalizationStats

logger = get_logger(__name__)

@lru_cache(maxsize=16)
def _compile_sensitive_intent_re(patterns: tuple[str, ...]) -> re.Pattern[str] | None:
    normalized = [p.strip() for p in patterns if p and p.strip()]
    if not normalized:
        return None

    escaped = [re.escape(p).replace(r"\ ", r"\s*") for p in normalized]
    return re.compile("|".join(escaped), re.IGNORECASE)


class PlannerService:
    """Convert a user's natural-language message into a ``QueryPlan``."""

    def __init__(
        self,
        llm: LLMProvider,
        catalog: CatalogService,
        *,
        doc_retrieval: DocumentRetrievalService | None = None,
    ) -> None:
        self._llm = llm
        self._catalog = catalog
        self._doc_retrieval = doc_retrieval
        self._last_canonicalization_stats: NormalizationStats | None = None
        self._last_repair_result: RepairResult | None = None
        self._last_trace: dict[str, Any] | None = None
        self._last_trace_by_task: dict[int, dict[str, Any] | None] = {}
        self._query_understanding_service = QueryUnderstandingService()
        self._context_assembly_service = PlanningContextAssemblyService(catalog, llm)
        self._prompt_assembly_service = PromptAssemblyService(doc_retrieval)
        self._plan_generation_service = PlanGenerationService(llm)
        self._plan_normalization_service = PlanNormalizationService()
        self._plan_repair_service = PlanRepairService()
        self._semantic_resolution_service = SemanticResolutionService(
            semantic_normalizer=lambda plan, user_message, context: apply_semantic_normalization(
                plan,
                user_message,
                context,
            )
        )
        self._clarification_decision_service = ClarificationDecisionService()

    def _set_last_trace(self, trace: dict[str, Any] | None) -> None:
        self._last_trace = trace
        task = asyncio.current_task()
        if task is not None:
            self._last_trace_by_task[id(task)] = trace
            if len(self._last_trace_by_task) > 2048:
                self._last_trace_by_task.clear()

    @property
    def last_canonicalization_stats(self) -> NormalizationStats | None:
        """Return the canonicalization stats from the most recent ``plan()`` call.

        Useful for evaluation tooling to count how many columns were
        canonicalized.  Returns ``None`` before the first call.
        """
        return self._last_canonicalization_stats

    @property
    def last_repair_result(self) -> RepairResult | None:
        """Return the repair audit from the most recent ``plan()`` call.

        Useful for evaluation tooling to track how many plans were repaired
        and which repair types were applied.  Returns ``None`` before the
        first call.
        """
        return self._last_repair_result

    @property
    def last_trace(self) -> dict[str, Any] | None:
        """Return planner debug metadata from the most recent ``plan()`` call."""
        task = asyncio.current_task()
        if task is not None and id(task) in self._last_trace_by_task:
            return self._last_trace_by_task[id(task)]
        return self._last_trace

    async def plan(self, user_message: str) -> QueryPlan:
        """Produce a ``QueryPlan`` from *user_message*.

        Steps
        -----
        0. Minimal sensitive-intent guard.
        1. Query understanding pre-pass (deterministic, no LLM).
        2. Fetch catalog context (entity-aware schema retrieval).
        3. Column pruning — ask LLM which columns matter.
        4. Optionally fetch document / example context (hybrid layer).
        5. Build the planner prompt.
        6. Call the LLM for a structured ``QueryPlan``.
        7. Post-plan normalization / safety checks.
        8. Structural repair (qualified-column strip, anchor redirect).
        9. Semantic canonicalization (entity root, join paths).
        10. Canonicalize column names against table metadata.

        Raises ``PlannerError`` when the LLM fails or returns unparseable
        output.
        """
        self._set_last_trace({
            "user_message": user_message,
            "policy_guard": {"triggered": False, "reason": None},
            "query_understanding": None,
            "retrieval": None,
            "prompt": None,
            "llm": {
                "raw_response_text": None,
                "parse_error": None,
                "parse_error_taxonomy": None,
                "salvage_applied": False,
            },
            "parsed_plan": None,
            "normalize": None,
            "repair": None,
            "semantic": None,
            "canonicalize": None,
            "final_plan": None,
        })
        # Keep a local reference so concurrent tasks cannot overwrite it.
        trace = self.last_trace
        assert trace is not None

        # 0. Minimal sensitive-intent guard (no planner guesswork)
        if self._is_sensitive_or_invalid_request(user_message):
            logger.info("Planner sensitive/invalid guard triggered.")
            plan = QueryPlan(
                intent="clarification_required",
                table=None,
                needs_clarification=True,
                clarification_message="Bu talep kapsamında güvenlik veya gizlilik riski var. Lütfen iş amaçlı ve yetkili bir sorgu belirtin.",
            )
            trace["policy_guard"] = {
                "triggered": True,
                "reason": "sensitive_or_invalid_request",
            }
            # Always set intent_guard so downstream eval can read clarification diagnostics
            trace["intent_guard"] = {
                "requested_filter_signals": [],
                "planner_filter_coverage": {},
                "final_filter_coverage": {},
                "false_success_risk": False,
                "success_blocked_by_filter_loss": False,
                "clarification_reason_code": "policy_guard_triggered",
                "clarification_missing_dimensions": [],
                "clarification_was_avoidable": False,
                "plan_confidence": "rule_low",
                "semantic_confidence": "rule_low",
                "confidence_band": "low",
                "plan_confidence_band": "low",
            }
            trace["final_plan"] = self._snapshot_plan(plan)
            return plan

        # 1. Query understanding pre-pass (deterministic, no LLM)
        qu = self._query_understanding_service.analyze(user_message)
        request_context = RequestContext(
            user_message=user_message,
            normalized_user_message=qu.normalized_question,
        )
        trace["query_understanding"] = qu.as_trace_dict()
        logger.info(
            "Query understanding: modules=%s, entities=%s, confidence=%s",
            qu.inferred_modules, qu.detected_entities, qu.entity_confidence,
        )

        # 2. Structured schema context
        planning_context = await self._context_assembly_service.assemble(
            request_context,
            qu,
        )

        # 4. Document / example context (hybrid layer)
        prompt_result = await self._prompt_assembly_service.assemble(
            user_message,
            planning_context,
            query_understanding=qu,
            query_understanding_summary=qu.as_trace_dict(),
        )
        prompt = prompt_result.prompt
        planning_context = prompt_result.context
        prompt_trace = prompt_result.trace
        trace["retrieval"] = {
            "schema_tables": [table.name for table in planning_context.retrieved_snapshot.tables],
            **planning_context.retrieval_diagnostics.as_trace_dict(),
            "schema_docs": prompt_trace.get("schema_docs", []),
            "examples": prompt_trace.get("examples", []),
        }
        trace["prompt"] = {
            key: value
            for key, value in prompt_trace.items()
            if key not in {"schema_docs", "examples", "retrieval_assessment"}
        }
        trace["prompt"]["full_prompt_text"] = prompt

        try:
            generation_result = await self._plan_generation_service.generate(prompt)
        except Exception as exc:
            trace["llm"] = {
                "raw_response_text": getattr(self._llm, "last_structured_response_text", None),
                "parse_error": str(exc),
                "parse_error_taxonomy": getattr(self._llm, "last_structured_parse_taxonomy", None),
                "salvage_applied": bool(
                    getattr(self._llm, "last_structured_salvage_applied", False)
                ),
            }
            logger.error("Planner LLM call failed: %s", exc)
            raise PlannerError(
                f"Plan olusturulamadi: {exc}",
                detail=str(exc),
            ) from exc

        query_plan = generation_result.plan
        trace["llm"] = {
            "raw_response_text": generation_result.raw_response_text,
            "parse_error": generation_result.parse_error,
            "parse_error_taxonomy": generation_result.parse_error_taxonomy,
            "salvage_applied": generation_result.salvage_applied,
        }
        trace["parsed_plan"] = self._snapshot_plan(query_plan)
        planner_plan_snapshot = query_plan

        # Post-plan normalization (limit clamping, clarification cleanup)
        post_process = self._post_process_plan(
            planner_plan_snapshot,
            user_message,
            planning_context,
        )
        query_plan = post_process.final_plan
        trace["normalize"] = {
            "before": self._snapshot_plan(post_process.original_plan),
            "after": self._snapshot_plan(post_process.normalization.plan),
            "limit_clamped": post_process.normalization.limit_clamped,
            "clarification_cleanup_applied": post_process.normalization.clarification_cleanup_applied,
        }

        # Structural repair (qualified-column strip, anchor redirect, etc.)
        repair_result = post_process.repair.repair_result
        self._last_repair_result = repair_result
        trace["repair"] = {
            "before": self._snapshot_plan(post_process.normalization.plan),
            "after": self._snapshot_plan(post_process.repair.plan),
            "repair_applied": repair_result.repair_applied,
            "repair_actions": [
                {
                    "repair_type": action.repair_type,
                    "description": action.description,
                    "field_path": action.field_path,
                    "original_value": action.original_value,
                    "repaired_value": action.repaired_value,
                }
                for action in repair_result.actions
            ],
        }

        # Semantic canonicalization (entity root + canonical join paths)
        semantic_result = post_process.semantic_resolution
        self._last_canonicalization_stats = semantic_result.canonicalization_stats
        trace["semantic"] = {
            "before": self._snapshot_plan(post_process.repair.plan),
            "after": self._snapshot_plan(semantic_result.semantic_plan),
            "semantic_intent": semantic_result.semantic_plan.semantic_intent,
            "root_entity": semantic_result.semantic_plan.root_entity,
            "join_path_id": semantic_result.semantic_plan.join_path_id,
            "diagnostics": dict(semantic_result.diagnostics),
        }

        # Column canonicalization (alias -> canonical name)
        canonicalize_before = semantic_result.semantic_plan
        trace["canonicalize"] = {
            "before": self._snapshot_plan(canonicalize_before),
            "after": self._snapshot_plan(semantic_result.canonicalized_plan),
            "stats": self._last_canonicalization_stats.as_dict() if self._last_canonicalization_stats else {},
        }

        # Deterministic filter-intent preservation guard (false-success prevention)
        trace["intent_guard"] = post_process.clarification.trace

        trace["final_plan"] = self._snapshot_plan(query_plan)

        logger.info(
            "Plan produced -- intent=%s, table=%s, clarification=%s",
            query_plan.intent,
            query_plan.table,
            query_plan.needs_clarification,
        )
        return query_plan

    # ------------------------------------------------------------------
    # Prompt building (hybrid when document retrieval is available)
    # ------------------------------------------------------------------

    async def _build_prompt(
        self,
        user_message: str,
        context: CatalogSnapshot,
    ) -> str:
        qu = self._query_understanding_service.analyze(user_message)
        request_context = RequestContext(
            user_message=user_message,
            normalized_user_message=qu.normalized_question,
        )
        full_snapshot = await self._catalog.get_snapshot()
        pruned_cols = await self._run_column_prune(user_message, context)
        pruned_cols = self._harden_pruned_cols(pruned_cols, context, qu)
        prompt_result = await self._prompt_assembly_service.assemble(
            user_message,
            PlanningContext(
                request=request_context,
                query_understanding=qu,
                retrieved_context=self._make_retrieved_context(
                    full_snapshot=full_snapshot,
                    retrieved_snapshot=context,
                    pruned_columns=pruned_cols,
                ),
            ),
            query_understanding=qu,
            query_understanding_summary=qu.as_trace_dict(),
        )
        return prompt_result.prompt

    async def _build_prompt_with_trace(
        self,
        user_message: str,
        full_snapshot: CatalogSnapshot,
        context: CatalogSnapshot,
        pruned_cols: dict[str, list[str]],
        *,
        query_understanding_summary: dict[str, Any] | None = None,
        qu: QueryUnderstanding | None = None,
    ) -> tuple[str, dict[str, Any]]:
        prompt_result = await self._prompt_assembly_service.assemble(
            user_message,
            PlanningContext(
                request=RequestContext(
                    user_message=user_message,
                    normalized_user_message=(qu.normalized_question if qu is not None else user_message),
                ),
                query_understanding=qu or self._query_understanding_service.analyze(user_message),
                retrieved_context=self._make_retrieved_context(
                    full_snapshot=full_snapshot,
                    retrieved_snapshot=context,
                    pruned_columns=pruned_cols,
                ),
            ),
            query_understanding=qu,
            query_understanding_summary=query_understanding_summary,
        )
        return prompt_result.prompt, prompt_result.trace

    # ------------------------------------------------------------------
    # Column pruning
    # ------------------------------------------------------------------

    async def _run_column_prune(
        self,
        user_message: str,
        context: CatalogSnapshot,
    ) -> dict[str, list[str]]:
        return await self._context_assembly_service.prune_columns(user_message, context)

    @staticmethod
    def _harden_pruned_cols(
        pruned: dict[str, list[str]],
        context: CatalogSnapshot,
        qu: QueryUnderstanding,
    ) -> dict[str, list[str]]:
        return PlanningContextAssemblyService.harden_pruned_columns(pruned, context, qu)

    @staticmethod
    def _snapshot_plan(plan: QueryPlan) -> dict[str, Any]:
        return plan.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Post-plan normalization
    # ------------------------------------------------------------------

    def _normalize_plan(self, plan: QueryPlan) -> QueryPlan:
        return self._plan_normalization_service.normalize(plan).plan

    # ------------------------------------------------------------------
    # Column canonicalization
    # ------------------------------------------------------------------

    def _canonicalize_plan(
        self,
        plan: QueryPlan,
        context: CatalogSnapshot,
    ) -> QueryPlan:
        result, stats = self._semantic_resolution_service.canonicalize(plan, context)
        self._last_canonicalization_stats = stats
        return result

    def _is_sensitive_or_invalid_request(self, user_message: str) -> bool:
        registry = _load_registry()
        patterns = tuple(registry.policy_rules.sensitive_intent_patterns)
        matcher = _compile_sensitive_intent_re(patterns)
        if matcher is None:
            logger.warning("Sensitive policy patterns unavailable; applying fail-safe clarification guard.")
            return True
        return bool(matcher.search(user_message or ""))

    def _enforce_aggregation_intent_guard(self, plan: QueryPlan, user_message: str) -> QueryPlan:
        return self._clarification_decision_service.enforce_aggregation_intent_guard(plan, user_message)

    def _make_retrieved_context(
        self,
        *,
        full_snapshot: CatalogSnapshot,
        retrieved_snapshot: CatalogSnapshot,
        pruned_columns: dict[str, list[str]],
    ):
        from app.services.planning_models import RetrievedContext, RetrievalDiagnostics

        return RetrievedContext(
            full_snapshot=full_snapshot,
            retrieved_snapshot=retrieved_snapshot,
            pruned_columns=pruned_columns,
            retrieval_diagnostics=RetrievalDiagnostics(
                schema_table_count=len(retrieved_snapshot.tables),
                relationship_count=len(retrieved_snapshot.relationships),
            ),
        )

    def _post_process_plan(
        self,
        planner_plan_snapshot: QueryPlan,
        user_message: str,
        planning_context: PlanningContext,
    ) -> PlanPostProcessResult:
        normalization = self._plan_normalization_service.normalize(planner_plan_snapshot)
        repair = self._plan_repair_service.repair(normalization.plan, user_message)
        semantic_resolution = self._semantic_resolution_service.resolve(
            repair.plan,
            user_message,
            planning_context.retrieved_snapshot,
            query_understanding=planning_context.query_understanding,
            retrieval_diagnostics=planning_context.retrieval_diagnostics,
            schema_docs=planning_context.schema_docs,
            examples=planning_context.examples,
        )
        clarification = self._clarification_decision_service.apply(
            user_message,
            planner_plan_snapshot,
            semantic_resolution.canonicalized_plan,
        )
        return PlanPostProcessResult(
            original_plan=planner_plan_snapshot,
            normalization=normalization,
            repair=repair,
            semantic_resolution=semantic_resolution,
            clarification=clarification,
        )
