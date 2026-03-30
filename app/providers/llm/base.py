"""Abstract base class for LLM providers.

Every LLM back-end (mock, OpenAI-compatible, etc.) must implement this
interface so that the planner and narrator services remain decoupled from
any specific LLM SDK or protocol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, TypeVar

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
    async def generate_text(self, prompt: str, *, disable_thinking: bool = False) -> str:
        """Generate free-form text.

        Used by the narrator to produce Turkish-language summaries.
        When *disable_thinking* is True, implementations should suppress
        chain-of-thought generation at the API level (e.g. Qwen3
        ``chat_template_kwargs``) to reduce latency.
        """
        ...

    async def generate_stream(
        self, prompt: str, *, disable_thinking: bool = False
    ) -> AsyncIterator[str]:
        """Stream tokens as they arrive.

        Default implementation falls back to ``generate_text`` and yields
        the full response as a single chunk.  Override for true streaming.
        """
        full = await self.generate_text(prompt, disable_thinking=disable_thinking)
        yield full
