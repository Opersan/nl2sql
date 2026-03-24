"""Semantic resolution and canonicalization stage."""

from __future__ import annotations

from collections.abc import Callable
import inspect
from typing import Any

from app.core.logging import get_logger
from app.domain.catalog_models import CatalogSnapshot
from app.domain.query_plan import QueryPlan
from app.services.plan_normalizer import NormalizationStats, canonicalize_columns
from app.services.planning_models import SemanticResolutionResult
from app.services.semantic_planning import apply_semantic_normalization

logger = get_logger(__name__)


class SemanticResolutionService:
    """Apply semantic normalization and column canonicalization."""

    def __init__(
        self,
        semantic_normalizer: Callable[[QueryPlan, str, CatalogSnapshot], QueryPlan] = apply_semantic_normalization,
    ) -> None:
        self._semantic_normalizer = semantic_normalizer

    def resolve(
        self,
        plan: QueryPlan,
        user_message: str,
        context: CatalogSnapshot,
        *,
        query_understanding: Any | None = None,
        retrieval_diagnostics: Any | None = None,
        schema_docs: list[Any] | None = None,
        examples: list[Any] | None = None,
    ) -> SemanticResolutionResult:
        kwargs = {
            "query_understanding": query_understanding,
            "retrieval_diagnostics": retrieval_diagnostics,
            "schema_docs": schema_docs,
            "examples": examples,
        }
        signature = inspect.signature(self._semantic_normalizer)
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_kwargs:
            semantic_plan = self._semantic_normalizer(plan, user_message, context, **kwargs)
        else:
            supported_kwargs = {
                name: value
                for name, value in kwargs.items()
                if name in signature.parameters and value is not None
            }
            semantic_plan = self._semantic_normalizer(plan, user_message, context, **supported_kwargs)
        canonicalized_plan, stats = self.canonicalize(semantic_plan, context)
        diagnostics = getattr(semantic_plan, "_semantic_diagnostics", None)
        if diagnostics is None:
            diagnostics = getattr(self._semantic_normalizer, "last_diagnostics", {})
        return SemanticResolutionResult(
            semantic_plan=semantic_plan,
            canonicalized_plan=canonicalized_plan,
            canonicalization_stats=stats,
            diagnostics=dict(diagnostics or {}),
        )

    def canonicalize(
        self,
        plan: QueryPlan,
        context: CatalogSnapshot,
    ) -> tuple[QueryPlan, NormalizationStats]:
        if plan.needs_clarification or not plan.table:
            return plan, NormalizationStats()

        table_meta = context.get_table(plan.table)
        if table_meta is None:
            return plan, NormalizationStats()

        table_meta_map: dict[str, Any] = {}
        for table_name in plan.all_tables:
            table = context.get_table(table_name)
            if table is not None:
                table_meta_map[table.name.upper()] = table

        stats = NormalizationStats()
        result = canonicalize_columns(
            plan,
            table_meta,
            stats=stats,
            table_meta_map=table_meta_map,
        )

        if stats.column_canonicalized > 0:
            logger.info(
                "Canonicalized %d column(s) for table %s.",
                stats.column_canonicalized,
                plan.table,
            )

        return result, stats