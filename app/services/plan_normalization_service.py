"""Post-generation QueryPlan normalization stage."""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.query_plan import QueryPlan
from app.services.planning_models import PlanNormalizationResult

logger = get_logger(__name__)


class PlanNormalizationService:
    """Apply lightweight safety normalization to generated QueryPlans."""

    def normalize(self, plan: QueryPlan) -> PlanNormalizationResult:
        mutations: dict[str, Any] = {}
        limit_clamped = False

        if plan.limit > settings.max_row_limit:
            logger.warning(
                "Plan limit %d exceeds max_row_limit %d, clamping.",
                plan.limit,
                settings.max_row_limit,
            )
            mutations["limit"] = settings.max_row_limit
            limit_clamped = True

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
            if plan.partition_by:
                mutations["partition_by"] = []

        normalized_plan = plan.model_copy(update=mutations) if mutations else plan
        clarification_cleanup_applied = (
            plan.needs_clarification and plan.model_dump(mode="json") != normalized_plan.model_dump(mode="json")
        )
        return PlanNormalizationResult(
            plan=normalized_plan,
            limit_clamped=limit_clamped,
            clarification_cleanup_applied=clarification_cleanup_applied,
        )