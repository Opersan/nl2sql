"""Chat endpoints – ``/chat`` and ``/v1/chat/completions``."""

from __future__ import annotations

import hashlib
import time
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    OAIChatChoice,
    OAIChatMessage,
    OAIChatRequest,
    OAIChatResponse,
)
from app.services.orchestrator import ChatOrchestrator

router = APIRouter(tags=["chat"])


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


def _get_orchestrator(request: Request) -> ChatOrchestrator:
    """Extract the ``ChatOrchestrator`` from application state.

    Using a small dependency helper keeps route handlers decoupled from
    ``request.app.state`` and makes testing / overriding easier.
    """
    return request.app.state.chat_orchestrator  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Simple chat endpoint
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    orchestrator: ChatOrchestrator = Depends(_get_orchestrator),
) -> ChatResponse:
    """Accept a natural-language message and return a structured answer."""
    result = await orchestrator.handle_message(body.session_id, body.message)
    return ChatResponse.from_chat_result(result)


# ---------------------------------------------------------------------------
# Clarification reply endpoint
# ---------------------------------------------------------------------------


class ClarificationReplyRequest(BaseModel):
    """POST /chat/clarify request body.

    Carries the user's selection from a pending clarification interaction.
    ``clarification_id`` binds the reply to the specific pending state;
    ``message`` carries the raw user text (numeric, label, or delegation
    phrase) so the orchestrator can route it through ``interpret_reply``.
    """

    session_id: str = Field(..., min_length=1)
    clarification_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


@router.post("/chat/clarify", response_model=ChatResponse)
async def chat_clarify(
    body: ClarificationReplyRequest,
    orchestrator: ChatOrchestrator = Depends(_get_orchestrator),
) -> ChatResponse:
    """Submit a clarification reply for a pending filter-value ambiguity.

    The frontend should call this endpoint when the user clicks a choice
    button or types a clarification answer.  The ``clarification_id``
    acts as an idempotency key – the orchestrator validates it against the
    session's pending clarification state before resuming the pipeline.

    This endpoint is additive and does not change ``/chat`` behavior.
    """
    result = await orchestrator.handle_message(body.session_id, body.message)
    return ChatResponse.from_chat_result(result)


# ---------------------------------------------------------------------------
# OpenAI-compatible (non-streaming, explicitly stateless)
# ---------------------------------------------------------------------------


def _deterministic_session_id(messages: list[OAIChatMessage]) -> str:
    """Derive a deterministic session key from the message sequence.

    The same request content always maps to the same session, which makes
    the endpoint reproducible during testing while still being effectively
    **stateless** — each unique conversation produces its own session.
    """
    digest = hashlib.sha256(
        "|".join(f"{m.role}:{m.content}" for m in messages).encode()
    ).hexdigest()[:12]
    return f"oai-{digest}"


@router.post("/v1/chat/completions", response_model=OAIChatResponse)
async def openai_chat_completions(
    body: OAIChatRequest,
    orchestrator: ChatOrchestrator = Depends(_get_orchestrator),
) -> OAIChatResponse:
    """Minimal OpenAI-compatible chat completions endpoint (stateless).

    Extracts the last user message, runs the NL2SQL pipeline, and wraps
    the result in an OpenAI-style response envelope.  Each unique message
    sequence produces a deterministic session key so that identical
    requests are reproducible, but no cross-request session continuity is
    maintained.
    """
    # Extract the last user message
    user_msg = ""
    for msg in reversed(body.messages):
        if msg.role == "user":
            user_msg = msg.content
            break

    if not user_msg:
        return OAIChatResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=body.model,
            choices=[
                OAIChatChoice(
                    message=OAIChatMessage(
                        role="assistant",
                        content="Lütfen bir soru sorun.",
                    ),
                ),
            ],
        )

    session_id = _deterministic_session_id(body.messages)
    result = await orchestrator.handle_message(session_id, user_msg)

    return OAIChatResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=body.model,
        choices=[
            OAIChatChoice(
                message=OAIChatMessage(
                    role="assistant",
                    content=result.answer,
                ),
            ),
        ],
    )
