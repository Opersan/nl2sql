"""Convenience re-exports for domain models.

Sprint 2 additions: SessionMessage, Session, ChatResult.
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.core.types import ChatStatus, MessageRole
from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    TableMetadata,
)
from app.domain.execution_models import (
    CompiledQuery,
    ExecutionResult,
    ExecutionStatus,
    OrchestrationResult,
    ValidationIssue,
    ValidationResult,
)
from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
    SortDirection,
)


# ---------------------------------------------------------------------------
# Session models (Sprint 2)
# ---------------------------------------------------------------------------


class SessionMessage(BaseModel):
    """A single turn in the conversation history."""

    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Session(BaseModel):
    """Lightweight in-memory session state.

    Only the last *N* messages are kept; raw SQL traces and large
    executor outputs are **not** stored.
    """

    session_id: str
    messages: list[SessionMessage] = Field(default_factory=list)
    last_plan: QueryPlan | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Clarification payload (Sprint 3 – chat UX contract)
# ---------------------------------------------------------------------------


class ClarificationOption(BaseModel):
    """A single selectable option in a clarification interaction."""

    index: int
    label: str
    value: str
    score: float | None = None


class ClarificationPayload(BaseModel):
    """Structured clarification contract emitted when the pipeline pauses for user input.

    This is the user-facing complement of the internal ``PendingClarification``.
    It carries exactly what the frontend needs to render a first-class chat
    clarification UI: the question text, selectable options, a stable ID, and
    a flag so the chat layer knows to pause and await a reply.
    """

    clarification_id: str
    """Stable identifier that must be echoed back when the user replies."""

    message: str
    """User-facing clarification question, e.g. 'Hangi birimi kastediyorsunuz?'"""

    options: list[ClarificationOption]
    """Ordered list of candidate choices to render as buttons."""

    target_column: str
    """The filter column this clarification resolves."""

    target_table: str | None = None
    original_filter_value: str = ""
    response_type: str = "clarification"
    awaiting_user_input: bool = True


# ---------------------------------------------------------------------------
# Chat result (returned by ChatOrchestrator)
# ---------------------------------------------------------------------------


class ChatResult(BaseModel):
    """Structured result of a single chat turn."""

    session_id: str
    status: ChatStatus
    answer: str
    plan: QueryPlan | None = None
    sql: str | None = None
    rows_preview: list[dict[str, Any]] | None = None
    error_code: str | None = None
    error_message: str | None = None
    clarification_payload: ClarificationPayload | None = None
    """Populated only when ``status == 'clarification'`` and a structured
    filter-value clarification is pending.  ``None`` for planner-level
    clarifications that lack candidate structure."""


__all__ = [
    "AggregateFn",
    "AggregationSpec",
    "CatalogSnapshot",
    "ClarificationOption",
    "ClarificationPayload",
    "ChatResult",
    "ColumnMetadata",
    "ColumnType",
    "CompiledQuery",
    "ExecutionResult",
    "ExecutionStatus",
    "FilterOp",
    "FilterSpec",
    "OrderSpec",
    "OrchestrationResult",
    "QueryPlan",
    "Session",
    "SessionMessage",
    "SortDirection",
    "TableMetadata",
    "ValidationIssue",
    "ValidationResult",
]
