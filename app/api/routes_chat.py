"""Chat endpoints for /chat, /chat/clarify and /v1/chat/completions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    OAIChatChoice,
    OAIClarificationAction,
    OAIChatMessage,
    OAIChatRequest,
    OAIChatResponse,
    OAIModelCard,
    OAIModelListResponse,
    OAINL2SQLMetadata,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.providers.llm.base import LLMProvider
from app.services.orchestrator import ChatOrchestrator

router = APIRouter(tags=["chat"])
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


def _get_orchestrator(request: Request) -> ChatOrchestrator:
    """Extract ChatOrchestrator from application state."""
    return request.app.state.chat_orchestrator  # type: ignore[no-any-return]


def _get_llm_provider(request: Request) -> LLMProvider:
    """Extract standalone LLM provider from application state."""
    return request.app.state.llm_provider  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Intent classification (LLM-based)
# ---------------------------------------------------------------------------

_INTENT_PROMPT = """\
Sen bir intent sınıflandırıcısın. Kullanıcı mesajını aşağıdaki 4 kategoriden birine sınıfla.

DATA — Veritabanından veri çekmeyi, listelemeyi, saymayı, filtrelemeyi, \
raporlamayı veya herhangi bir çalışan/sipariş/fatura/stok vb. iş verisini \
sorgulamayı gerektiren her soru. Kişi adıyla sorgulama, burç/yaş/departman \
gibi çalışan bilgisi sorguları da DATA kapsamındadır.

METADATA — Tablo yapısı, kolon adları, veri sözlüğü, şema bilgisi, \
tablo ilişkileri (FK, PK, join) hakkında teknik sorular.

CLARIFICATION — Belirsiz, eksik boyut veya filtre içeren sorular. \
Hangi birim/departman/tarih/lokasyon gibi netleştirme gerektiren durumlar.

GENERAL — Yukarıdaki 3 kategoriye girmeyen her şey: mimari, kod, strateji, \
kavram açıklama, brainstorm, genel sohbet.

Kurallar:
- Yalnızca şu 4 kelimeden birini yaz: DATA, METADATA, CLARIFICATION, GENERAL
- Başka hiçbir şey yazma. Açıklama, sebep, düşünce ekleme.
- Şüphe durumunda DATA tercih et.

Mesaj: "{message}"
"""


async def _classify_intent_llm(llm: LLMProvider, text: str) -> str:
    """Classify user intent via a short LLM call."""
    prompt = _INTENT_PROMPT.format(message=text.strip()[:300])
    try:
        raw = await asyncio.wait_for(llm.generate_text(prompt, disable_thinking=True), timeout=50.0)
        token = raw.strip().upper().split()[0] if raw and raw.strip() else ""
        # Strip any markdown/punctuation
        token = re.sub(r"[^A-Z]", "", token)
        if token in ("DATA", "METADATA", "CLARIFICATION", "GENERAL"):
            return token
    except Exception:
        logger.warning("[intent] LLM intent classification failed, defaulting to DATA")
    return "DATA"


async def _call_llm_direct(
    llm: LLMProvider,
    messages: list[OAIChatMessage],
) -> str:
    """Forward messages to LLM directly (no pipeline).

    Builds a single prompt from the message history and calls
    ``llm.generate_text``.
    """
    from app.core.context_builder import ContextBuilder
    ctx_block = ContextBuilder().build().to_prompt_block()
    lines: list[str] = [ctx_block]
    for msg in messages:
        role = msg.role.upper()
        content = (msg.content or "").strip()
        if content:
            lines.append(f"[{role}]\n{content}")
    prompt = "\n\n".join(lines)
    return await llm.generate_text(prompt)


def _clarification_payload_from_pending(pending: Any, question: str):
    from app.domain.models import ClarificationOption, ClarificationPayload

    return ClarificationPayload(
        clarification_id=pending.clarification_id,
        message=question,
        options=[
            ClarificationOption(
                index=idx + 1,
                label=candidate.value,
                value=candidate.value,
                score=candidate.score,
            )
            for idx, candidate in enumerate(pending.candidates)
        ],
        target_column=pending.target_column,
        target_table=pending.target_table,
        original_filter_value=pending.original_filter_value,
    )


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
    """POST /chat/clarify request body."""

    session_id: str = Field(..., min_length=1)
    clarification_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


@router.post("/chat/clarify", response_model=ChatResponse)
async def chat_clarify(
    body: ClarificationReplyRequest,
    orchestrator: ChatOrchestrator = Depends(_get_orchestrator),
) -> ChatResponse:
    """Submit a clarification reply tied to an active clarification id."""
    clarification_manager = getattr(orchestrator, "_clarification_manager", None)
    if clarification_manager is not None:
        pending = clarification_manager.get_pending(body.session_id)
        if pending is not None and pending.clarification_id != body.clarification_id:
            question = clarification_manager.build_clarification_message(pending)
            return ChatResponse(
                session_id=body.session_id,
                status="clarification",
                answer=question,
                clarification_payload=_clarification_payload_from_pending(pending, question),
            )

    result = await orchestrator.handle_message(body.session_id, body.message)
    return ChatResponse.from_chat_result(result)


# ---------------------------------------------------------------------------
# OpenAI-compatible (non-streaming; Open WebUI-aware continuity)
# ---------------------------------------------------------------------------


_SESSION_MARKER_RE = re.compile(
    r"<!--\s*nl2sql:session=([A-Za-z0-9_.:-]{1,128})(?:;clarification=([A-Za-z0-9_.:-]{1,128}))?\s*-->",
    re.IGNORECASE,
)
_OWUI_FOLLOW_UP_RE = re.compile(
    r"^\s*###\s*Task:\s*Suggest 3-5 relevant follow-up questions or prompts",
    re.IGNORECASE | re.DOTALL,
)
_OWUI_TITLE_RE = re.compile(
    r"^\s*###\s*Task:\s*Generate a concise,\s*3-5 word title with an emoji",
    re.IGNORECASE | re.DOTALL,
)
_OWUI_TAGS_RE = re.compile(
    r"^\s*###\s*Task:\s*Generate 1-3 broad tags categorizing the main themes",
    re.IGNORECASE | re.DOTALL,
)


def _sanitize_session_component(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", candidate):
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:16]
    return f"owui-{digest}"


def _session_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not isinstance(metadata, dict):
        return None

    for key in ("session_id", "chat_id", "conversation_id"):
        value = metadata.get(key)
        if isinstance(value, str):
            candidate = _sanitize_session_component(value)
            if candidate:
                return candidate

    nested = metadata.get("metadata")
    if isinstance(nested, dict):
        return _session_from_metadata(nested)

    return None


def _session_from_headers(request: Request) -> str | None:
    header_candidates = (
        "x-session-id",
        "x-chat-id",
        "x-openwebui-session-id",
        "x-openwebui-chat-id",
    )
    for name in header_candidates:
        candidate = _sanitize_session_component(request.headers.get(name))
        if candidate:
            return candidate
    return None


def _session_from_history_markers(messages: list[OAIChatMessage]) -> str | None:
    for msg in reversed(messages):
        if msg.role != "assistant":
            continue
        found = _SESSION_MARKER_RE.search(msg.content or "")
        if found:
            candidate = _sanitize_session_component(found.group(1))
            if candidate:
                return candidate
    return None


def _conversation_fallback_session_id(body: OAIChatRequest) -> str:
    first_user = ""
    first_system = ""

    for msg in body.messages:
        if msg.role == "user" and msg.content.strip():
            first_user = msg.content.strip()
            break
    for msg in body.messages:
        if msg.role == "system" and msg.content.strip():
            first_system = msg.content.strip()
            break

    seed = (
        f"model={body.model}|user={body.user or ''}|"
        f"system={first_system}|first_user={first_user}"
    )
    digest = hashlib.sha256(seed.encode()).hexdigest()[:16]
    return f"owui-conv-{digest}"


def _resolve_oai_session_id(body: OAIChatRequest, request: Request) -> str:
    for candidate in (body.session_id, body.chat_id, body.conversation_id):
        session_id = _sanitize_session_component(candidate)
        if session_id:
            return session_id

    metadata_session = _session_from_metadata(body.metadata)
    if metadata_session:
        return metadata_session

    header_session = _session_from_headers(request)
    if header_session:
        return header_session

    marker_session = _session_from_history_markers(body.messages)
    if marker_session:
        return marker_session

    return _conversation_fallback_session_id(body)


def _extract_user_message(body: OAIChatRequest) -> str:
    if body.clarification_reply and body.clarification_reply.strip():
        return body.clarification_reply.strip()

    for msg in reversed(body.messages):
        if msg.role == "user" and msg.content.strip():
            return msg.content.strip()
    return ""


def _detect_openwebui_helper_prompt(user_msg: str) -> str | None:
    text = user_msg.strip()
    if _OWUI_FOLLOW_UP_RE.search(text):
        return "follow_up"
    if _OWUI_TITLE_RE.search(text):
        return "title"
    if _OWUI_TAGS_RE.search(text):
        return "tags"
    return None


def _extract_conversation_seed(body: OAIChatRequest) -> str:
    helper_prompt = _extract_user_message(body)
    for msg in body.messages:
        if msg.role != "user":
            continue
        content = (msg.content or "").strip()
        if not content or content == helper_prompt:
            continue
        if _detect_openwebui_helper_prompt(content):
            continue
        return content
    return ""


def _build_openwebui_helper_content(kind: str, seed_text: str) -> str:
    seed = seed_text.strip().lower()
    if "yonetici" in seed:
        if kind == "title":
            return "👤 Yonetici Unvan Sorgusu"
        if kind == "tags":
            return "hr, unvan, yonetici"
        return (
            "- Sistem Yoneticisi olan calisanlari goster\n"
            "- Proje Yoneticisi olan calisanlari goster\n"
            "- Bu calisanlarin birimlerini de goster"
        )

    if "dizayn" in seed:
        if kind == "title":
            return "🧩 Dizayn Birim Sorgusu"
        if kind == "tags":
            return "hr, birim, dizayn"
        return (
            "- Elektrik Dizayn calisanlarini goster\n"
            "- Mekanik Dizayn calisanlarini goster\n"
            "- Dizayn calisanlarinin lokasyonlarini goster"
        )

    if kind == "title":
        return "💬 NL2SQL Sohbeti"
    if kind == "tags":
        return "nl2sql, sorgu, analiz"
    return (
        "- Bu sonucun detaylarini goster\n"
        "- Ayni filtreyle farkli alanlari getir\n"
        "- Sonucu birime gore kir"
    )


def _build_clarification_actions(payload: Any) -> list[OAIClarificationAction]:
    actions = [
        OAIClarificationAction(
            label=f"{option.index}. {option.label}",
            value=str(option.index),
            clarification_id=payload.clarification_id,
        )
        for option in payload.options
    ]
    actions.append(
        OAIClarificationAction(
            label="Sen karar ver",
            value="sen karar ver",
            clarification_id=payload.clarification_id,
        )
    )
    return actions


def _render_clarification_content(answer: str, payload: Any) -> str:
    question_line = answer.strip().splitlines()[0].strip() if answer.strip() else ""
    if not question_line:
        question_line = payload.message.strip().splitlines()[0].strip()
    if not question_line:
        question_line = "Lutfen secenegi netlestirin."

    lines = [question_line, ""]
    for option in payload.options:
        lines.append(f"{option.index}. {option.label}")
    lines.append(f"{len(payload.options) + 1}. Sen karar ver")
    lines.append("")
    lines.append('Yanit olarak "1", secenek adi veya "sen karar ver" yazabilirsiniz.')
    return "\n".join(lines)


def _embed_session_marker(
    content: str,
    *,
    session_id: str,
    clarification_id: str | None = None,
) -> str:
    marker = f"<!-- nl2sql:session={session_id}"
    if clarification_id:
        marker += f";clarification={clarification_id}"
    marker += " -->"
    return f"{content.rstrip()}\n\n{marker}"


def _build_oai_response(
    *,
    body: OAIChatRequest,
    session_id: str,
    status: str,
    content: str,
    clarification_id: str | None = None,
    actions: list[OAIClarificationAction] | None = None,
    clarification_payload: Any | None = None,
) -> OAIChatResponse:
    return OAIChatResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=body.model,
        choices=[
            OAIChatChoice(
                message=OAIChatMessage(
                    role="assistant",
                    content=content.rstrip(),
                    metadata=OAINL2SQLMetadata(
                        session_id=session_id,
                        status=status,
                        clarification_id=clarification_id,
                        actions=actions,
                    ),
                ),
            ),
        ],
        session_id=session_id,
        status=status,
        clarification_payload=clarification_payload,
    )


async def _build_chat_completion_result(
    *,
    body: OAIChatRequest,
    request: Request,
    orchestrator: ChatOrchestrator,
    llm: LLMProvider,
) -> OAIChatResponse:
    session_id = _resolve_oai_session_id(body, request)
    openwebui_chat_id = request.headers.get("x-openwebui-chat-id")
    user_msg = _extract_user_message(body)
    clarification_manager = getattr(orchestrator, "_clarification_manager", None)
    helper_kind = _detect_openwebui_helper_prompt(user_msg)

    if not user_msg:
        logger.info("[openwebui] session=%s status=clarification message=<empty>", session_id)
        return _build_oai_response(
            body=body,
            session_id=session_id,
            status="clarification",
            content="Lutfen bir soru sorun.",
        )

    if helper_kind is not None:
        helper_content = _build_openwebui_helper_content(
            helper_kind,
            _extract_conversation_seed(body),
        )
        logger.info(
            "[openwebui-helper] session=%s helper=%s",
            session_id,
            helper_kind,
        )
        return _build_oai_response(
            body=body,
            session_id=session_id,
            status="success",
            content=helper_content,
        )

    # ── Pending clarification reply (must precede enterprise_mode gate) ───
    if clarification_manager is not None:
        if clarification_manager.get_pending(session_id) is not None:
            logger.info(
                "[openwebui] session=%s pending clarification detected → pipeline resume",
                session_id,
            )
            try:
                _res = await orchestrator.handle_message(
                    session_id, user_msg, openwebui_chat_id=openwebui_chat_id
                )
            except Exception:
                logger.exception("[openwebui] clarification resume pipeline failed")
                return _build_oai_response(
                    body=body, session_id=session_id, status="error",
                    content="Bir hata oluştu. Lütfen tekrar deneyin.",
                )
            _res_content = _res.answer
            _res_actions = None
            _res_clar_id = None
            if _res.status == "clarification" and _res.clarification_payload is not None:
                _res_clar_id = _res.clarification_payload.clarification_id
                _res_actions = _build_clarification_actions(_res.clarification_payload)
                _res_content = _render_clarification_content(_res.answer, _res.clarification_payload)
            return _build_oai_response(
                body=body,
                session_id=session_id,
                status=_res.status,
                content=_res_content,
                clarification_id=_res_clar_id,
                actions=_res_actions,
                clarification_payload=_res.clarification_payload,
            )

    # ── enterprise_mode=false → direct LLM (no pipeline) ────────────
    if not body.enterprise_mode:
        logger.info(
            "[openwebui] session=%s enterprise_mode=false → direct LLM, message=%r",
            session_id,
            user_msg[:160],
        )
        try:
            llm_answer = await _call_llm_direct(llm, body.messages)
        except Exception:
            logger.exception("[openwebui] direct LLM call failed")
            llm_answer = "Bir hata oluştu. Lütfen tekrar deneyin."
        return _build_oai_response(
            body=body,
            session_id=session_id,
            status="success",
            content=llm_answer,
        )

    # ── enterprise_mode=true → classify intent & route ───────────────
    intent = await _classify_intent_llm(llm, user_msg)

    # Override CLARIFICATION/GENERAL → DATA when the session has a pending
    # pipeline clarification.  The user's message is most likely an answer
    # to the previous clarification question and MUST go through the
    # pipeline so context is preserved.
    if intent in ("CLARIFICATION", "GENERAL") and orchestrator._sessions.is_pending_clarification(session_id):
        logger.info(
            "[openwebui] session=%s intent %s → DATA override (pending pipeline clarification)",
            session_id,
            intent,
        )
        intent = "DATA"

    logger.info(
        "[openwebui] session=%s enterprise_mode=true intent=%s message=%r",
        session_id,
        intent,
        user_msg[:160],
    )

    # GENERAL → direct LLM (no pipeline)
    if intent == "GENERAL":
        try:
            llm_answer = await _call_llm_direct(llm, body.messages)
        except Exception:
            logger.exception("[openwebui] direct LLM call failed (GENERAL intent)")
            llm_answer = "Bir hata oluştu. Lütfen tekrar deneyin."
        return _build_oai_response(
            body=body,
            session_id=session_id,
            status="success",
            content=llm_answer,
        )

    # CLARIFICATION → inject steering prompt, then direct LLM
    if intent == "CLARIFICATION":
        clarification_prompt = (
            "[Netleştirme Gerekli]\n"
            "Kullanıcının sorusu belirsiz — eksik boyut, filtre veya kapsam bilgisi var.\n"
            "SADECE tek bir kısa ve somut netleştirme sorusu sor.\n"
            "Veri getirme, açıklama yapma, analiz üretme.\n"
            "Örnekler:\n"
            "- 'Hangi tarih aralığı için sonuç istiyorsunuz?'\n"
            "- 'DT-Dizayn mı yoksa ELM-Dizayn mı?'\n"
            "- 'Hangi fabrika lokasyonu?'"
        )
        steered_messages = list(body.messages)
        steered_messages.insert(
            max(len(steered_messages) - 1, 0),
            OAIChatMessage(role="system", content=clarification_prompt),
        )
        try:
            llm_answer = await _call_llm_direct(llm, steered_messages)
        except Exception:
            logger.exception("[openwebui] direct LLM call failed (CLARIFICATION)")
            llm_answer = "Sorunuzu biraz daha netleştirir misiniz?"
        return _build_oai_response(
            body=body,
            session_id=session_id,
            status="success",
            content=llm_answer,
        )

    # DATA / METADATA → NL2SQL pipeline via orchestrator
    if body.clarification_id and clarification_manager is not None:
        pending = clarification_manager.get_pending(session_id)
        if pending is not None and pending.clarification_id != body.clarification_id:
            question = clarification_manager.build_clarification_message(pending)
            payload = _clarification_payload_from_pending(pending, question)
            logger.info(
                "[openwebui] session=%s status=clarification clarification_id=%s stale_reply_id=%s",
                session_id,
                payload.clarification_id,
                body.clarification_id,
            )
            return _build_oai_response(
                body=body,
                session_id=session_id,
                status="clarification",
                content=_render_clarification_content(question, payload),
                clarification_id=payload.clarification_id,
                actions=_build_clarification_actions(payload),
                clarification_payload=payload,
            )

    result = await orchestrator.handle_message(session_id, user_msg, openwebui_chat_id=openwebui_chat_id)
    content = result.answer
    actions = None
    clarification_id = None

    if result.status == "clarification" and result.clarification_payload is not None:
        clarification_id = result.clarification_payload.clarification_id
        actions = _build_clarification_actions(result.clarification_payload)
        content = _render_clarification_content(result.answer, result.clarification_payload)

    logger.info(
        "[openwebui] session=%s status=%s intent=%s clarification_id=%s",
        session_id,
        result.status,
        intent,
        clarification_id or "-",
    )

    return _build_oai_response(
        body=body,
        session_id=session_id,
        status=result.status,
        content=content,
        clarification_id=clarification_id,
        actions=actions,
        clarification_payload=result.clarification_payload,
    )


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


def _sse_chunk(
    chunk_id: str,
    model: str,
    created: int,
    *,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> str:
    """Serialize a single chat-completion-chunk to an SSE data line."""
    chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def _sse_from_token_iter(
    token_iter: AsyncIterator[str],
    *,
    chunk_id: str,
    model: str,
    created: int,
) -> AsyncIterator[str]:
    """Wrap an async token iterator in SSE envelope (role → tokens → stop → DONE)."""
    yield _sse_chunk(chunk_id, model, created, delta={"role": "assistant"})
    async for token in token_iter:
        yield _sse_chunk(chunk_id, model, created, delta={"content": token})
    yield _sse_chunk(chunk_id, model, created, delta={}, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def _call_llm_direct_stream(
    llm: LLMProvider,
    messages: list[OAIChatMessage],
) -> AsyncIterator[str]:
    """Build prompt from message history and stream tokens from the LLM."""
    from app.core.context_builder import ContextBuilder
    ctx_block = ContextBuilder().build().to_prompt_block()
    lines: list[str] = [ctx_block]
    for msg in messages:
        role = msg.role.upper()
        content = (msg.content or "").strip()
        if content:
            lines.append(f"[{role}]\n{content}")
    prompt = "\n\n".join(lines)
    async for token in llm.generate_stream(prompt):
        yield token


def _stream_chat_completion(response: OAIChatResponse) -> StreamingResponse:
    def event_lines() -> Any:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        model = response.model
        created = int(time.time())
        content = response.choices[0].message.content

        first_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"

        content_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(content_chunk, ensure_ascii=False)}\n\n"

        final_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_lines(), media_type="text/event-stream")


async def _build_streaming_chat_completion(
    *,
    body: OAIChatRequest,
    request: Request,
    orchestrator: ChatOrchestrator,
    llm: LLMProvider,
) -> StreamingResponse:
    """Return a StreamingResponse using real token streaming or fake typing effect."""
    session_id = _resolve_oai_session_id(body, request)
    openwebui_chat_id = request.headers.get("x-openwebui-chat-id")
    user_msg = _extract_user_message(body)
    clarification_manager = getattr(orchestrator, "_clarification_manager", None)
    helper_kind = _detect_openwebui_helper_prompt(user_msg)

    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    model = body.model or settings.openai_model or "nl2sql"
    created = int(time.time())

    def _fake_content_stream(content: str) -> StreamingResponse:
        """Emit pre-built content as a minimal SSE stream (no fake typing delay)."""
        return _stream_chat_completion(
            _build_oai_response(
                body=body, session_id=session_id, status="success", content=content
            )
        )

    # Empty message
    if not user_msg:
        return _fake_content_stream("Lutfen bir soru sorun.")

    # Open WebUI helper prompts (follow-up / title / tags)
    if helper_kind is not None:
        content = _build_openwebui_helper_content(helper_kind, _extract_conversation_seed(body))
        return _fake_content_stream(content)

    # ── Pending clarification reply (must precede enterprise_mode gate) ───
    if clarification_manager is not None:
        if clarification_manager.get_pending(session_id) is not None:
            logger.info(
                "[openwebui-stream] session=%s pending clarification detected → pipeline resume",
                session_id,
            )

            async def _clar_resume_stream():
                try:
                    pipeline_task = asyncio.create_task(
                        orchestrator.handle_message(
                            session_id, user_msg, openwebui_chat_id=openwebui_chat_id
                        )
                    )
                    yield _sse_chunk(chunk_id, model, created, delta={"role": "assistant"})
                    for _tok in ["⏳ Sorgu", " yeniden", " çalışıyor", "...\n",
                                 "🔍 Sonuçlar", " getiriliyor", "..."]:
                        yield _sse_chunk(chunk_id, model, created, delta={"content": _tok})
                    _result = await pipeline_task
                    _res_content = _result.answer
                    if _result.status == "clarification" and _result.clarification_payload is not None:
                        _res_content = _render_clarification_content(
                            _result.answer, _result.clarification_payload
                        )
                    yield _sse_chunk(chunk_id, model, created, delta={"content": "\n\n" + _res_content})
                    yield _sse_chunk(chunk_id, model, created, delta={}, finish_reason="stop")
                    yield "data: [DONE]\n\n"
                except Exception:
                    logger.exception("[openwebui-stream] clarification resume stream failed")
                    yield _sse_chunk(chunk_id, model, created, delta={"content": "Bir hata oluştu. Lütfen tekrar deneyin."})
                    yield _sse_chunk(chunk_id, model, created, delta={}, finish_reason="stop")
                    yield "data: [DONE]\n\n"

            return StreamingResponse(_clar_resume_stream(), media_type="text/event-stream")

    # ── enterprise_mode=false → real streaming ────────────────────────────
    if not body.enterprise_mode:
        logger.info(
            "[openwebui-stream] session=%s enterprise_mode=false → direct LLM stream",
            session_id,
        )

        async def _non_enterprise_stream():
            try:
                token_gen = _call_llm_direct_stream(llm, body.messages)
                async for sse_line in _sse_from_token_iter(
                    token_gen, chunk_id=chunk_id, model=model, created=created
                ):
                    yield sse_line
            except Exception:
                logger.exception("[openwebui-stream] direct LLM stream failed")
                yield _sse_chunk(chunk_id, model, created, delta={"content": "Bir hata oluştu. Lütfen tekrar deneyin."})
                yield _sse_chunk(chunk_id, model, created, delta={}, finish_reason="stop")
                yield "data: [DONE]\n\n"

        return StreamingResponse(_non_enterprise_stream(), media_type="text/event-stream")

    # ── enterprise_mode=true → classify intent ────────────────────────────
    intent = await _classify_intent_llm(llm, user_msg)

    # Override CLARIFICATION/GENERAL → DATA when the session has a pending
    # pipeline clarification.  The user's message is most likely an answer
    # to the previous clarification question and MUST go through the
    # pipeline so context is preserved.
    if intent in ("CLARIFICATION", "GENERAL") and orchestrator._sessions.is_pending_clarification(session_id):
        logger.info(
            "[openwebui-stream] session=%s intent %s → DATA override (pending pipeline clarification)",
            session_id,
            intent,
        )
        intent = "DATA"

    logger.info(
        "[openwebui-stream] session=%s intent=%s message=%r",
        session_id,
        intent,
        user_msg[:160],
    )

    # GENERAL → real streaming
    if intent == "GENERAL":
        async def _general_stream():
            try:
                token_gen = _call_llm_direct_stream(llm, body.messages)
                async for sse_line in _sse_from_token_iter(
                    token_gen, chunk_id=chunk_id, model=model, created=created
                ):
                    yield sse_line
            except Exception:
                logger.exception("[openwebui-stream] GENERAL stream failed")
                yield _sse_chunk(chunk_id, model, created, delta={"content": "Bir hata oluştu. Lütfen tekrar deneyin."})
                yield _sse_chunk(chunk_id, model, created, delta={}, finish_reason="stop")
                yield "data: [DONE]\n\n"

        return StreamingResponse(_general_stream(), media_type="text/event-stream")

    # CLARIFICATION → steered messages → real streaming
    if intent == "CLARIFICATION":
        clarification_prompt = (
            "[Netleştirme Gerekli]\n"
            "Kullanıcının sorusu belirsiz — eksik boyut, filtre veya kapsam bilgisi var.\n"
            "SADECE tek bir kısa ve somut netleştirme sorusu sor.\n"
            "Veri getirme, açıklama yapma, analiz üretme.\n"
            "Örnekler:\n"
            "- 'Hangi tarih aralığı için sonuç istiyorsunuz?'\n"
            "- 'DT-Dizayn mı yoksa ELM-Dizayn mı?'\n"
            "- 'Hangi fabrika lokasyonu?'"
        )
        steered_messages = list(body.messages)
        steered_messages.insert(
            max(len(steered_messages) - 1, 0),
            OAIChatMessage(role="system", content=clarification_prompt),
        )

        async def _clarification_stream():
            try:
                token_gen = _call_llm_direct_stream(llm, steered_messages)
                async for sse_line in _sse_from_token_iter(
                    token_gen, chunk_id=chunk_id, model=model, created=created
                ):
                    yield sse_line
            except Exception:
                logger.exception("[openwebui-stream] CLARIFICATION stream failed")
                yield _sse_chunk(chunk_id, model, created, delta={"content": "Sorunuzu biraz daha netleştirir misiniz?"})
                yield _sse_chunk(chunk_id, model, created, delta={}, finish_reason="stop")
                yield "data: [DONE]\n\n"

        return StreamingResponse(_clarification_stream(), media_type="text/event-stream")

    # DATA / METADATA — stale clarification check
    if body.clarification_id and clarification_manager is not None:
        pending = clarification_manager.get_pending(session_id)
        if pending is not None and pending.clarification_id != body.clarification_id:
            question = clarification_manager.build_clarification_message(pending)
            payload = _clarification_payload_from_pending(pending, question)
            return _fake_content_stream(_render_clarification_content(question, payload))

    # DATA / METADATA → pipeline with fake "thinking" animation then result
    async def _pipeline_stream():
        # Run the pipeline as a background task so we can stream thinking
        # indicators while waiting for the result.
        pipeline_task = asyncio.create_task(
            orchestrator.handle_message(
                session_id, user_msg, openwebui_chat_id=openwebui_chat_id
            )
        )

        yield _sse_chunk(chunk_id, model, created, delta={"role": "assistant"})

        thinking_tokens = [
            "⏳", " Sorgu", " analiz", " ediliyor", ".",
            ".", ".", "\n",
            "🔍", " SQL", " oluşturuluyor", ".",
            ".", ".", "\n",
            "📊", " Sonuçlar", " getiriliyor", ".",
            ".", ".",
        ]
        for token in thinking_tokens:
            if pipeline_task.done():
                break
            yield _sse_chunk(chunk_id, model, created, delta={"content": token})
            await asyncio.sleep(0.15)

        try:
            result = await pipeline_task
        except Exception:
            logger.exception("[openwebui-stream] pipeline failed")
            yield _sse_chunk(chunk_id, model, created, delta={"content": "\n\nBir hata oluştu."})
            yield _sse_chunk(chunk_id, model, created, delta={}, finish_reason="stop")
            yield "data: [DONE]\n\n"
            return

        result_content = result.answer
        if result.status == "clarification" and result.clarification_payload is not None:
            result_content = _render_clarification_content(
                result.answer, result.clarification_payload
            )

        logger.info(
            "[openwebui-stream] session=%s status=%s intent=%s",
            session_id,
            result.status,
            intent,
        )

        yield _sse_chunk(chunk_id, model, created, delta={"content": "\n\n"})
        yield _sse_chunk(chunk_id, model, created, delta={"content": result_content})
        yield _sse_chunk(chunk_id, model, created, delta={}, finish_reason="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(_pipeline_stream(), media_type="text/event-stream")


@router.get("/v1/models", response_model=OAIModelListResponse)
async def openai_models() -> OAIModelListResponse:
    """Expose a minimal OpenAI-compatible model list for Open WebUI."""
    now = int(time.time())
    return OAIModelListResponse(
        data=[OAIModelCard(id="nl2sql", created=now)]
    )


@router.post("/v1/chat/completions", response_model=OAIChatResponse)
async def openai_chat_completions(
    body: OAIChatRequest,
    request: Request,
    orchestrator: ChatOrchestrator = Depends(_get_orchestrator),
    llm: LLMProvider = Depends(_get_llm_provider),
) -> OAIChatResponse | StreamingResponse:
    """OpenAI-compatible chat endpoint with Open WebUI-friendly continuity."""
    if body.stream:
        return await _build_streaming_chat_completion(
            body=body,
            request=request,
            orchestrator=orchestrator,
            llm=llm,
        )
    return await _build_chat_completion_result(
        body=body,
        request=request,
        orchestrator=orchestrator,
        llm=llm,
    )
