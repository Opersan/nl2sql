"""Structured QueryPlan generation stage."""

from __future__ import annotations

from app.domain.query_plan import QueryPlan
from app.providers.llm.base import LLMProvider
from app.services.planning_models import PlanGenerationResult


class PlanGenerationService:
    """Generate a structured QueryPlan from the assembled prompt."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def generate(self, prompt: str) -> PlanGenerationResult:
        plan = await self._llm.generate_structured(prompt, QueryPlan)
        return PlanGenerationResult(
            plan=plan,
            raw_response_text=getattr(self._llm, "last_structured_response_text", None),
            parse_error=getattr(self._llm, "last_structured_parse_error", None),
            parse_error_taxonomy=getattr(self._llm, "last_structured_parse_taxonomy", None),
            salvage_applied=bool(
                getattr(self._llm, "last_structured_salvage_applied", False)
            ),
        )