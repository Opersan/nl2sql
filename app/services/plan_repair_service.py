"""Deterministic repair stage for planner output."""

from __future__ import annotations

from app.domain.query_plan import QueryPlan
from app.services.planning_models import RepairStageResult
from app.services.query_plan_repair import QueryPlanRepairEngine


class PlanRepairService:
    """Apply post-parse deterministic repairs to QueryPlans."""

    def __init__(self) -> None:
        self._engine = QueryPlanRepairEngine()

    def repair(
        self,
        plan: QueryPlan,
        user_message: str = "",
    ) -> RepairStageResult:
        repaired_plan, repair_result = self._engine.repair(plan, user_message)
        return RepairStageResult(plan=repaired_plan, repair_result=repair_result)