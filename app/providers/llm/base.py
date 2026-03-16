"""Abstract base class for LLM providers.

Every LLM back-end (mock, OpenAI-compatible, etc.) must implement this
interface so that the planner and narrator services remain decoupled from
any specific LLM SDK or protocol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(ABC):
    """Contract for LLM back-ends used by the planner and narrator."""

    @abstractmethod
    async def generate_structured(
        self, prompt: str, response_model: type[T],
    ) -> T:
        """Generate a response and parse it into *response_model*.

        Used by the planner to obtain a structured ``QueryPlan``.
        """
        ...

    @abstractmethod
    async def generate_text(self, prompt: str) -> str:
        """Generate free-form text.

        Used by the narrator to produce Turkish-language summaries.
        """
        ...
