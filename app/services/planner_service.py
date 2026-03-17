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
from app.providers.llm.prompts import build_hybrid_planner_prompt, build_planner_prompt
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
        # 0. Minimal sensitive-intent guard (no planner guesswork)
        if self._is_sensitive_or_invalid_request(user_message):
            logger.info("Planner sensitive/invalid guard triggered.")
            return QueryPlan(
                intent="clarification_required",
                table=None,
                needs_clarification=True,
                clarification_message="Bu talep kapsamında güvenlik veya gizlilik riski var. Lütfen iş amaçlı ve yetkili bir sorgu belirtin.",
            )

        # 1. Structured schema context
        context = await self._catalog.get_relevant_context(user_message)

        # 2. Document / example context (hybrid layer)
        prompt = await self._build_prompt(user_message, context)

        try:
            query_plan = await self._llm.generate_structured(prompt, QueryPlan)
        except Exception as exc:
            logger.error("Planner LLM call failed: %s", exc)
            raise PlannerError(
                f"Plan olusturulamadi: {exc}",
                detail=str(exc),
            ) from exc

        # Post-plan normalization (limit clamping, clarification cleanup)
        query_plan = self._normalize_plan(query_plan)

        # Structural repair (qualified-column strip, anchor redirect, etc.)
        query_plan, repair_result = self._repair_engine.repair(query_plan, user_message)
        self._last_repair_result = repair_result

        # Semantic canonicalization (entity root + canonical join paths)

        query_plan = apply_semantic_normalization(query_plan, user_message, context)
        # Guard: if user clearly asks an aggregate question but plan has no
        # aggregations, return clarification instead of a likely wrong listing.
        query_plan = self._enforce_aggregation_intent_guard(query_plan, user_message)

        # Column canonicalization (alias -> canonical name)
        query_plan = self._canonicalize_plan(query_plan, context)

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
        """Build the planner prompt, enriching with docs/examples when available."""
        if self._doc_retrieval is not None:
            doc_result = await self._doc_retrieval.retrieve_context(user_message)
            return build_hybrid_planner_prompt(
                user_message,
                context,
                schema_docs=doc_result.schema_docs or None,
                examples=doc_result.examples or None,
                max_prompt_chars=settings.planner_prompt_max_chars,
            )
        return build_planner_prompt(user_message, context)

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
