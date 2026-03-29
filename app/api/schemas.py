"""API request and response models (Pydantic v2)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.config import APP_VERSION
from app.core.types import ChatStatus
from app.domain.models import ChatResult, ClarificationPayload


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
    clarification_payload: ClarificationPayload | None = None
    """Structured clarification contract.  Non-null only when
    ``status == 'clarification'`` and a filter-value clarification is
    pending.  The frontend must render this as selectable options, not
    plain text."""

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
            clarification_payload=result.clarification_payload,
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
    metadata: "OAINL2SQLMetadata | None" = None


class OAIChatRequest(BaseModel):
    model: str = "nl2sql"
    messages: list[OAIChatMessage]
    stream: bool = False
    # Enterprise mode flag — injected by the Open WebUI filter toggle.
    enterprise_mode: bool = False
    # Additive optional fields commonly available in Open WebUI integrations.
    session_id: str | None = None
    chat_id: str | None = None
    conversation_id: str | None = None
    clarification_id: str | None = None
    clarification_reply: str | None = None
    user: str | None = None
    metadata: dict[str, Any] | None = None


class OAIClarificationAction(BaseModel):
    """UI hint for quick-reply clarification actions."""

    kind: str = "reply"
    label: str
    value: str
    clarification_id: str | None = None


class OAINL2SQLMetadata(BaseModel):
    """Additive NL2SQL metadata for Open WebUI-like clients.

    This keeps the chat response user-facing while preserving session and
    clarification linkage in a structured side channel.
    """

    session_id: str
    status: ChatStatus
    clarification_id: str | None = None
    actions: list[OAIClarificationAction] | None = None


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
    # Additive compatibility metadata (safe to ignore by strict OpenAI clients).
    session_id: str | None = None
    status: ChatStatus | None = None
    clarification_payload: ClarificationPayload | None = None


class OAIModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "nl2sql-assistant"


class OAIModelListResponse(BaseModel):
    object: str = "list"
    data: list[OAIModelCard]
