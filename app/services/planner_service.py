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

from functools import lru_cache
import re
from typing import Any

from app.core.config import settings
from app.core.exceptions import PlannerError
from app.core.logging import get_logger
from app.domain.catalog_models import CatalogSnapshot
from app.domain.query_plan import QueryPlan
from app.providers.llm.base import LLMProvider
from app.providers.llm.prompts import (
    build_hybrid_planner_prompt_debug,
    build_planner_prompt,
)
from app.services.catalog_service import CatalogService
from app.services.document_retrieval_service import DocumentRetrievalService
from app.services.plan_normalizer import (
    NormalizationStats,
    canonicalize_columns,
)
from app.services.query_plan_repair import QueryPlanRepairEngine, RepairResult
from app.services.semantic_planning import apply_semantic_normalization, _load_registry
from app.utils.turkish import casefold_tr

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
        self._repair_engine = QueryPlanRepairEngine()
        self._last_repair_result: RepairResult | None = None
        self._last_trace: dict[str, Any] | None = None

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
        return self._last_trace

    async def plan(self, user_message: str) -> QueryPlan:
        """Produce a ``QueryPlan`` from *user_message*.

        Steps
        -----
        1. Fetch catalog context (structured metadata layer).
        2. Optionally fetch document / example context (document layer).
        3. Build the planner prompt (hybrid when docs available).
        4. Call the LLM for a structured ``QueryPlan``.
        5. Apply post-plan normalization / safety checks.
        6. Apply structural repair (qualified-column strip, anchor redirect).
        7. Apply semantic canonicalization (entity root, join paths).
        8. Canonicalize column names against table metadata.

        Raises ``PlannerError`` when the LLM fails or returns unparseable
        output.
        """
        self._last_trace = {
            "user_message": user_message,
            "policy_guard": {"triggered": False, "reason": None},
            "retrieval": None,
            "prompt": None,
            "llm": {"raw_response_text": None, "parse_error": None},
            "parsed_plan": None,
            "normalize": None,
            "repair": None,
            "semantic": None,
            "canonicalize": None,
            "final_plan": None,
        }

        # 0. Minimal sensitive-intent guard (no planner guesswork)
        if self._is_sensitive_or_invalid_request(user_message):
            logger.info("Planner sensitive/invalid guard triggered.")
            plan = QueryPlan(
                intent="clarification_required",
                table=None,
                needs_clarification=True,
                clarification_message="Bu talep kapsamında güvenlik veya gizlilik riski var. Lütfen iş amaçlı ve yetkili bir sorgu belirtin.",
            )
            self._last_trace["policy_guard"] = {
                "triggered": True,
                "reason": "sensitive_or_invalid_request",
            }
            self._last_trace["final_plan"] = self._snapshot_plan(plan)
            return plan

        # 1. Structured schema context
        context = await self._catalog.get_relevant_context(user_message)

        # 2. Document / example context (hybrid layer)
        prompt, prompt_trace = await self._build_prompt_with_trace(user_message, context)
        self._last_trace["retrieval"] = {
            "schema_tables": [table.name for table in context.tables],
            "schema_table_count": len(context.tables),
            "relationship_count": len(context.relationships),
            "retrieval_assessment": prompt_trace.get("retrieval_assessment", "unknown"),
            "schema_docs": prompt_trace.get("schema_docs", []),
            "examples": prompt_trace.get("examples", []),
        }
        self._last_trace["prompt"] = {
            key: value
            for key, value in prompt_trace.items()
            if key not in {"schema_docs", "examples", "retrieval_assessment"}
        }
        self._last_trace["prompt"]["full_prompt_text"] = prompt

        try:
            query_plan = await self._llm.generate_structured(prompt, QueryPlan)
        except Exception as exc:
            self._last_trace["llm"] = {
                "raw_response_text": getattr(self._llm, "last_structured_response_text", None),
                "parse_error": str(exc),
            }
            logger.error("Planner LLM call failed: %s", exc)
            raise PlannerError(
                f"Plan olusturulamadi: {exc}",
                detail=str(exc),
            ) from exc

        self._last_trace["llm"] = {
            "raw_response_text": getattr(self._llm, "last_structured_response_text", None),
            "parse_error": getattr(self._llm, "last_structured_parse_error", None),
        }
        self._last_trace["parsed_plan"] = self._snapshot_plan(query_plan)

        # Post-plan normalization (limit clamping, clarification cleanup)
        normalize_before = query_plan
        query_plan = self._normalize_plan(query_plan)
        self._last_trace["normalize"] = {
            "before": self._snapshot_plan(normalize_before),
            "after": self._snapshot_plan(query_plan),
            "limit_clamped": normalize_before.limit != query_plan.limit,
            "clarification_cleanup_applied": (
                normalize_before.needs_clarification
                and self._snapshot_plan(normalize_before) != self._snapshot_plan(query_plan)
            ),
        }

        # Structural repair (qualified-column strip, anchor redirect, etc.)
        repair_before = query_plan
        query_plan, repair_result = self._repair_engine.repair(query_plan, user_message)
        self._last_repair_result = repair_result
        self._last_trace["repair"] = {
            "before": self._snapshot_plan(repair_before),
            "after": self._snapshot_plan(query_plan),
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
        semantic_before = query_plan
        query_plan = apply_semantic_normalization(query_plan, user_message, context)
        # Guard: if user clearly asks an aggregate question but plan has no
        # aggregations, return clarification instead of a likely wrong listing.
        query_plan = self._enforce_aggregation_intent_guard(query_plan, user_message)
        self._last_trace["semantic"] = {
            "before": self._snapshot_plan(semantic_before),
            "after": self._snapshot_plan(query_plan),
            "semantic_intent": query_plan.semantic_intent,
            "root_entity": query_plan.root_entity,
            "join_path_id": query_plan.join_path_id,
        }

        # Column canonicalization (alias -> canonical name)
        canonicalize_before = query_plan
        query_plan = self._canonicalize_plan(query_plan, context)
        self._last_trace["canonicalize"] = {
            "before": self._snapshot_plan(canonicalize_before),
            "after": self._snapshot_plan(query_plan),
            "stats": self._last_canonicalization_stats.as_dict() if self._last_canonicalization_stats else {},
        }
        self._last_trace["final_plan"] = self._snapshot_plan(query_plan)

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
        prompt, _trace = await self._build_prompt_with_trace(user_message, context)
        return prompt

    async def _build_prompt_with_trace(
        self,
        user_message: str,
        context: CatalogSnapshot,
    ) -> tuple[str, dict[str, Any]]:
        """Build the planner prompt, enriching with docs/examples when available."""
        if self._doc_retrieval is not None:
            doc_result = await self._doc_retrieval.retrieve_context(user_message)
            prompt, debug = build_hybrid_planner_prompt_debug(
                user_message,
                context,
                schema_docs=doc_result.schema_docs or None,
                examples=doc_result.examples or None,
                max_prompt_chars=settings.planner_prompt_max_chars,
            )
            retrieval_assessment = "sufficient"
            if not context.tables:
                retrieval_assessment = "insufficient"
            elif not (doc_result.schema_docs or doc_result.examples):
                retrieval_assessment = "partial"
            debug["retrieval_assessment"] = retrieval_assessment
            return prompt, debug

        prompt = build_planner_prompt(user_message, context)
        return prompt, {
            "prompt_length": len(prompt),
            "prompt_budget": settings.planner_prompt_max_chars,
            "prompt_truncated": False,
            "reduction_steps": [],
            "schema_tables_in_prompt": [table.name for table in context.tables],
            "schema_doc_count": 0,
            "example_count": 0,
            "schema_docs": [],
            "examples": [],
            "retrieval_assessment": "schema_only" if context.tables else "insufficient",
        }

    @staticmethod
    def _snapshot_plan(plan: QueryPlan) -> dict[str, Any]:
        return plan.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Post-plan normalization
    # ------------------------------------------------------------------

    def _normalize_plan(self, plan: QueryPlan) -> QueryPlan:
        """Apply lightweight safety checks to the raw LLM plan.

        * Clamp ``limit`` to ``settings.max_row_limit``.
        * Strip query artifacts from clarification plans so downstream
          stages never receive a half-finished plan.
        """
        mutations: dict[str, Any] = {}

        # 1. Clamp limit
        if plan.limit > settings.max_row_limit:
            logger.warning(
                "Plan limit %d exceeds max_row_limit %d, clamping.",
                plan.limit,
                settings.max_row_limit,
            )
            mutations["limit"] = settings.max_row_limit

        # 2. Clarification plans should not carry query artifacts
        if plan.needs_clarification:
            if plan.select_columns:
                mutations["select_columns"] = []
            if plan.filters:
                mutations["filters"] = []
            if plan.aggregations:
                mutations["aggregations"] = []
            if plan.group_by:
                mutations["group_by"] = []
            if plan.order_by:
                mutations["order_by"] = []

        if mutations:
            return plan.model_copy(update=mutations)
        return plan

    # ------------------------------------------------------------------
    # Column canonicalization
    # ------------------------------------------------------------------

    def _canonicalize_plan(
        self,
        plan: QueryPlan,
        context: CatalogSnapshot,
    ) -> QueryPlan:
        """Resolve column aliases to canonical names using table metadata.

        Uses ``TableMetadata.resolve_column_name`` to map LLM-produced
        column names (which may be aliases or case-variants) to the
        canonical column names defined in the metadata catalog.
        """
        if plan.needs_clarification or not plan.table:
            self._last_canonicalization_stats = NormalizationStats()
            return plan

        table_meta = context.get_table(plan.table)
        if table_meta is None:
            self._last_canonicalization_stats = NormalizationStats()
            return plan

        table_map: dict[str, Any] = {}
        for t in plan.all_tables:
            tm = context.get_table(t)
            if tm is not None:
                table_map[tm.name.upper()] = tm

        stats = NormalizationStats()
        result = canonicalize_columns(
            plan,
            table_meta,
            stats=stats,
            table_meta_map=table_map,
        )
        self._last_canonicalization_stats = stats

        if stats.column_canonicalized > 0:
            logger.info(
                "Canonicalized %d column(s) for table %s.",
                stats.column_canonicalized,
                plan.table,
            )

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
        """Guard against a bare listing plan when the user clearly requested an aggregate.

        Fires only when ALL conditions hold:
        - plan carries no aggregations, filters, or group_by (bare listing fallback)
        - message is not a short domain-noun query (≤2 tokens + known domain token)
        - message contains no filter or date context cues
        - message contains both broad aggregation syntax AND a specific measure keyword

        The two-level keyword check (broad + specific) prevents false positives on
        phrases like "vendor bazında PO listesi" which use grouping syntax without
        an explicit measure word.
        """
        if plan.needs_clarification or plan.aggregations or plan.filters or plan.group_by:
            return plan

        folded = casefold_tr(user_message or "")
        tokens = [t for t in re.split(r"\s+", folded.strip()) if t]

        # Single-noun domain queries ("çalışanlar", "PO") are valid listing requests.
        _DOMAIN = ("calisan", "çalışan", "personel", "siparis", "sipariş", "po")
        if len(tokens) <= 2 and any(t in folded for t in _DOMAIN):
            return plan

        # Filter and date context cues indicate a lookup query, not an aggregate.
        _FILTER_DATE = (
            "daki", "deki", "ndaki", "indeki", "olan", "içeren", "iceren",
            "açık", "acik", "aktif", "onay", "bekleyen",
            "bugün", "bugun", "dün", "dun", "tarih", "gün", "gun", "hafta", "yıl", "yil",
        )
        if any(cue in folded for cue in _FILTER_DATE):
            return plan

        # Broad aggregation syntax (required first level).
        _BROAD = (
            "kaç", "kac", "sayı", "sayi", "adedi", "toplam", "ortalama",
            "dağılım", "dagilim", "bazında", "bazinda", "count", "sum", "avg",
        )
        # Specific measure keyword (required second level — prevents "X bazında liste" false positives).
        _MEASURE = (
            "kaç", "kac", "sayısı", "sayisi", "adedi", "count",
            "toplam", "sum", "ortalama", "average", "avg", "miktar", "quantity",
        )
        if not any(c in folded for c in _BROAD) or not any(m in folded for m in _MEASURE):
            return plan

        logger.info("Aggregation-intent guard triggered; switching to clarification.")
        return plan.model_copy(
            update={
                "intent": "clarification_required",
                "needs_clarification": True,
                "clarification_message": "Bu sorgu bir toplama/hesaplama isteği gibi görünüyor. Hangi metrik ve hangi kırılımı istediğinizi netleştirir misiniz?",
                "select_columns": [],
                "filters": [],
                "aggregations": [],
                "group_by": [],
                "order_by": [],
            },
        )
