"""Chat endpoints for /chat, /chat/clarify and /v1/chat/completions."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

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
# Intent classification (deterministic keyword-based)
# ---------------------------------------------------------------------------

_METADATA_CUES = [
    "hangi tablo", "hangi kolon", "tablo yapısı", "kolon ne", "alan ne",
    "join", "ilişki", "relationship", "field", "schema", "metadata",
    "veri sözlüğü", "data dictionary", "foreign key", "primary key",
    "tablo ilişkisi", "column", "sütun", "alan tanımı",
]
_DATA_CUES = [
    "kaç", "kaç tane", "listele", "getir", "göster", "toplam", "ortalama",
    "son 3 ay", "son 6 ay", "son 1 yıl", "trend", "rapor", "karşılaştır",
    "kıyasla", "minimum", "maksimum", "en çok", "en az", "sırala",
    "çalışan sayısı", "sipariş", "stok", "üretim", "fatura", "ciro",
    "maliyet", "performans", "kpi", "count", "total", "average",
    "report", "compare", "list", "show me", "how many", "aggregate",
    "sum", "adet", "miktar", "oran", "yüzde",
]
_GENERAL_CUES = [
    "prompt yaz", "mimari", "nasıl tasarlayalım", "debug", "review",
    "best practice", "architecture", "design", "neden", "açıkla",
    "explain", "code", "implement", "refactor", "pattern", "strateji",
    "strategy", "brainstorm", "fikir", "öneri", "konsept",
]
_CLARIFICATION_CUES = [
    "hangi birim", "hangi departman", "hangi tarih", "ne zaman",
    "hangisi", "belirtir misiniz", "netleştirir misiniz",
]


def _classify_intent(text: str) -> str:
    """Classify user intent into GENERAL / METADATA / DATA / CLARIFICATION."""
    lowered = re.sub(r"\s+", " ", text.strip()).lower()

    meta = sum(1 for c in _METADATA_CUES if c in lowered)
    data = sum(1 for c in _DATA_CUES if c in lowered)
    gen = sum(1 for c in _GENERAL_CUES if c in lowered)

    if any(kw in lowered for kw in ("pipeline", "sistem", "filtre", "filter", "mimari")):
        gen += 2

    total = meta + data + gen
    if total == 0:
        return "GENERAL"

    if meta > data and meta > gen:
        return "METADATA"

    if data > meta and data > gen:
        clr = sum(1 for c in _CLARIFICATION_CUES if c in lowered)
        if clr > 0 and data <= 2:
            return "CLARIFICATION"
        return "DATA"

    if gen > 0:
        return "GENERAL"

    if data > 0 and data == meta:
        return "CLARIFICATION"

    return "GENERAL"


async def _call_llm_direct(
    llm: LLMProvider,
    messages: list[OAIChatMessage],
) -> str:
    """Forward messages to LLM directly (no pipeline).

    Builds a single prompt from the message history and calls
    ``llm.generate_text``.
    """
    lines: list[str] = []
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
    intent = _classify_intent(user_msg)
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


@router.get("/v1/models", response_model=OAIModelListResponse)
async def openai_models() -> OAIModelListResponse:
    """Expose a minimal OpenAI-compatible model list for Open WebUI."""
    now = int(time.time())
    model_ids = [settings.openai_model, "nl2sql"]
    unique_ids = list(dict.fromkeys(mid for mid in model_ids if mid))
    return OAIModelListResponse(
        data=[OAIModelCard(id=model_id, created=now) for model_id in unique_ids]
    )


@router.post("/v1/chat/completions", response_model=OAIChatResponse)
async def openai_chat_completions(
    body: OAIChatRequest,
    request: Request,
    orchestrator: ChatOrchestrator = Depends(_get_orchestrator),
    llm: LLMProvider = Depends(_get_llm_provider),
) -> OAIChatResponse | StreamingResponse:
    """OpenAI-compatible chat endpoint with Open WebUI-friendly continuity."""
    response = await _build_chat_completion_result(
        body=body,
        request=request,
        orchestrator=orchestrator,
        llm=llm,
    )
    if body.stream:
        return _stream_chat_completion(response)
    return response
