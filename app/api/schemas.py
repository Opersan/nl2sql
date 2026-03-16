"""API request and response models (Pydantic v2)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.config import APP_VERSION
from app.core.types import ChatStatus
from app.domain.models import ChatResult


# ---------------------------------------------------------------------------
# /chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """POST /chat request body."""

    session_id: str = Field(..., min_length=1, examples=["abc-123"])
    message: str = Field(..., min_length=1, examples=["Aktif çalışanları listele"])

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, v: str) -> str:
        """Reject whitespace-only messages."""
        if not v.strip():
            msg = "Message must contain non-whitespace characters."
            raise ValueError(msg)
        return v


class ChatResponse(BaseModel):
    """POST /chat response body."""

    session_id: str
    status: ChatStatus
    answer: str
    plan: dict[str, Any] | None = None
    sql: str | None = None
    rows_preview: list[dict[str, Any]] | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def from_chat_result(cls, result: ChatResult) -> ChatResponse:
        """Convert a domain ``ChatResult`` into an API response."""
        return cls(
            session_id=result.session_id,
            status=result.status,
            answer=result.answer,
            plan=result.plan.model_dump(mode="json") if result.plan else None,
            sql=result.sql,
            rows_preview=result.rows_preview,
            error_code=result.error_code,
            error_message=result.error_message,
        )


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """GET /health response body."""

    status: str = "ok"
    version: str = APP_VERSION


# ---------------------------------------------------------------------------
# OpenAI-compatible chat completions (minimal, non-streaming)
# ---------------------------------------------------------------------------


class OAIChatMessage(BaseModel):
    role: str
    content: str


class OAIChatRequest(BaseModel):
    model: str = "nl2sql"
    messages: list[OAIChatMessage]


class OAIChatChoice(BaseModel):
    index: int = 0
    message: OAIChatMessage
    finish_reason: str = "stop"


class OAIChatUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OAIChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str = "nl2sql"
    choices: list[OAIChatChoice]
    usage: OAIChatUsage = Field(default_factory=OAIChatUsage)
