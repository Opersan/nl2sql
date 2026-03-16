"""Abstract base class for query executors."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.execution_models import CompiledQuery, ExecutionResult


class ExecutorProvider(ABC):
    """Contract for query execution back-ends."""

    @abstractmethod
    async def execute(self, compiled_query: CompiledQuery) -> ExecutionResult:
        """Execute the *compiled_query* and return a structured result."""
        ...
