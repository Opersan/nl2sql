"""OpenAI-compatible LLM provider.

Communicates with any server exposing the ``/v1/chat/completions``
endpoint (vLLM, llama.cpp, OpenAI, etc.).

Sprint 3 notes
==============
* Structured output uses JSON mode + ``model_validate_json``.
* No streaming – each call blocks until the full response arrives.
* Error handling propagates as ``PlannerError`` / ``NarratorError``
  via the calling service layer.
* **vLLM integration** – set ``openai_base_url`` to the vLLM server
  address (e.g. ``http://gpu-host:8000/v1``) and ``openai_model`` to
  the served model name.  No code changes needed.
* **Structured output enforcement** – when the backend supports
  ``guided_json`` (vLLM 0.4+), pass the Pydantic schema as
  ``response_format.schema`` for guaranteed valid JSON.  Fallback is
  JSON mode + post-validation.

Sprint 4 – Pre-parse normalization
===================================
When *response_model* is ``QueryPlan``, the raw JSON is first fed through
``normalize_raw_plan`` to fix LLM-produced enum variants (e.g.
``GREATER_THAN_OR_EQUAL`` → ``>=``) before Pydantic validation.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.providers.llm.base import LLMProvider

T = TypeVar("T", bound=BaseModel)

logger = get_logger(__name__)

_PLAN_KEYS = {
    "intent",
    "table",
    "candidate_tables",
    "joins",
    "select_columns",
    "filters",
    "aggregations",
    "group_by",
    "order_by",
    "limit",
    "needs_clarification",
    "clarification_message",
}
_CLARIFICATION_KEYS = {
    "clarification_message",
    "needs_clarification",
    "clarification",
    "question_to_user",
    "follow_up_question",
}


def _extract_user_question(prompt: str) -> str:
    match = re.search(r"Kullanıcı sorusu:\s*(.+)$", prompt, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    return ""


def _iter_json_candidates(text: str) -> Iterable[dict[str, Any]]:
    # Fast path: full payload is JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            yield data
    except (json.JSONDecodeError, ValueError):
        pass

    # Code fences: ```json ... ``` or generic ``` ... ```
    for fence in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL):
        block = fence.group(1).strip()
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                yield data
        except (json.JSONDecodeError, ValueError):
            continue

    # Strip explicit reasoning blocks, then scan balanced JSON objects.
    scrubbed = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(scrubbed):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(scrubbed[i:])
            if isinstance(obj, dict):
                yield obj
        except (json.JSONDecodeError, ValueError):
            continue


def _unique_json_candidates(text: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for candidate in _iter_json_candidates(text):
        try:
            fingerprint = json.dumps(candidate, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            fingerprint = repr(candidate)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(candidate)
    return unique


def _looks_like_plan_dict(obj: dict[str, Any]) -> bool:
    return bool(_PLAN_KEYS.intersection(obj.keys()))


def _extract_clarification_message(content: str, payload: Any | None = None) -> str | None:
    def _walk(node: Any) -> str | None:
        if isinstance(node, dict):
            for key in (
                "clarification_message",
                "clarification",
                "follow_up_question",
                "question_to_user",
                "message",
            ):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in node.values():
                found = _walk(value)
                if found:
                    return found
        if isinstance(node, list):
            for item in node:
                found = _walk(item)
                if found:
                    return found
        return None

    found = _walk(payload)
    if found:
        return found
    if payload is not None:
        return None

    text = content.strip()
    if not text:
        return None
    if any(token in text.lower() for token in (
        "netleştir",
        "netlestir",
        "detaylandır",
        "detaylandir",
        "clarification",
        "hangi ",
        "hangi",
        "lütfen",
        "lutfen",
    )):
        return text[:400]
    return None


def _coerce_plan_candidate(
    payload: dict[str, Any],
    *,
    prompt: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Coerce a plan-like payload into a QueryPlan-compatible dict when safe."""
    candidate = dict(payload)
    salvage_applied = False

    if not _looks_like_plan_dict(candidate) and not _CLARIFICATION_KEYS.intersection(candidate.keys()):
        return None, False

    user_question = _extract_user_question(prompt)
    clarification_message = _extract_clarification_message("", candidate)

    if "intent" not in candidate or not str(candidate.get("intent") or "").strip():
        if candidate.get("needs_clarification") is True or clarification_message:
            candidate["intent"] = "clarification_required"
        elif user_question:
            candidate["intent"] = user_question
        else:
            return None, False
        salvage_applied = True

    if clarification_message and "clarification_message" not in candidate:
        candidate["clarification_message"] = clarification_message
        salvage_applied = True

    if candidate.get("clarification_message") and candidate.get("needs_clarification") is not False:
        if candidate.get("needs_clarification") is not True:
            candidate["needs_clarification"] = True
            salvage_applied = True

    if candidate.get("needs_clarification") is True:
        if not candidate.get("clarification_message"):
            candidate["clarification_message"] = "Soruyu biraz daha detaylandırabilir misiniz?"
            salvage_applied = True
        for key in ("select_columns", "filters", "aggregations", "group_by", "order_by", "joins"):
            if candidate.get(key) is None:
                candidate[key] = []
                salvage_applied = True

    return candidate, salvage_applied


def _classify_validation_error(exc: ValidationError) -> str:
    seen_missing = False
    seen_type = False
    for error in exc.errors():
        err_type = str(error.get("type", ""))
        if err_type == "missing":
            seen_missing = True
        elif err_type.endswith("_type") or err_type in {"list_type", "model_type", "string_type", "bool_type", "int_type"}:
            seen_type = True
    if seen_missing:
        return "missing_required_field"
    if seen_type:
        return "invalid_field_type"
    return "malformed_json"


def _safe_extract_plan_dict(content: str) -> dict[str, Any] | None:
    """Extract a QueryPlan-shaped dict from raw LLM output.

    Handles these real-world failure modes:
    * Reasoning wrappers: ``{"reasoning": "...", "plan": {"intent": ...}}``
    * Thinking prefix before JSON: ``<think>...</think>{"intent": ...}``
    * Empty or error objects: ``{}``, ``{"error": "..."}``, ``{"status": "ok"}``
    * JSON embedded inside markdown code fences.

    Returns ``None`` when no QueryPlan-shaped object can be found.
    """
    def _find_plan_dict(obj: Any) -> dict[str, Any] | None:
        if isinstance(obj, dict):
            if isinstance(obj.get("intent"), str) and obj.get("intent", "").strip():
                return obj
            if _looks_like_plan_dict(obj):
                return obj
            for value in obj.values():
                found = _find_plan_dict(value)
                if found is not None:
                    return found
        if isinstance(obj, list):
            for item in obj:
                found = _find_plan_dict(item)
                if found is not None:
                    return found
        return None

    for candidate in _iter_json_candidates(content):
        found = _find_plan_dict(candidate)
        if found is not None:
            return found

    return None


def _make_clarification_plan() -> Any:
    """Return a minimal QueryPlan with needs_clarification=True.

    Used as a safe fallback when the LLM response cannot be parsed.
    Imported lazily to avoid circular imports.
    """
    from app.domain.query_plan import QueryPlan

    return QueryPlan(
        intent="clarification_required",
        needs_clarification=True,
        clarification_message="Soruyu biraz daha detaylandırabilir misiniz?",
    )


def _make_clarification_plan_with_message(message: str | None) -> Any:
    from app.domain.query_plan import QueryPlan

    return QueryPlan(
        intent="clarification_required",
        needs_clarification=True,
        clarification_message=message or "Soruyu biraz daha detaylandırabilir misiniz?",
    )


class OpenAICompatibleProvider(LLMProvider):
    """LLM provider that speaks the OpenAI chat-completions protocol."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str = "default",
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        # Task-scoped response fields for concurrency safety.
        # Keyed by id(asyncio.current_task()); fallback stored separately.
        self._task_state: dict[int, dict[str, str | None]] = {}
        self._fallback_state: dict[str, str | None] = {
            "structured_response_text": None,
            "structured_parse_error": None,
            "structured_parse_taxonomy": None,
            "text_response_text": None,
            "structured_salvage_applied": None,
        }

    # -- Task-scoped state helpers -------------------------------------------

    def _set_field(self, key: str, value: str | None) -> None:
        """Write a response field scoped to the current asyncio task."""
        self._fallback_state[key] = value
        task = asyncio.current_task()
        if task is not None:
            tid = id(task)
            if tid not in self._task_state:
                self._task_state[tid] = dict(self._fallback_state)
            self._task_state[tid][key] = value
            # Prevent unbounded growth.
            if len(self._task_state) > 2048:
                self._task_state.clear()

    def _get_field(self, key: str) -> str | None:
        """Read a response field scoped to the current asyncio task."""
        task = asyncio.current_task()
        if task is not None:
            tid = id(task)
            if tid in self._task_state:
                return self._task_state[tid].get(key)
        return self._fallback_state.get(key)

    @property
    def last_structured_response_text(self) -> str | None:
        return self._get_field("structured_response_text")

    @last_structured_response_text.setter
    def last_structured_response_text(self, value: str | None) -> None:
        self._set_field("structured_response_text", value)

    @property
    def last_structured_parse_error(self) -> str | None:
        return self._get_field("structured_parse_error")

    @last_structured_parse_error.setter
    def last_structured_parse_error(self, value: str | None) -> None:
        self._set_field("structured_parse_error", value)

    @property
    def last_text_response_text(self) -> str | None:
        return self._get_field("text_response_text")

    @last_text_response_text.setter
    def last_text_response_text(self, value: str | None) -> None:
        self._set_field("text_response_text", value)

    @property
    def last_structured_parse_taxonomy(self) -> str | None:
        return self._get_field("structured_parse_taxonomy")

    @last_structured_parse_taxonomy.setter
    def last_structured_parse_taxonomy(self, value: str | None) -> None:
        self._set_field("structured_parse_taxonomy", value)

    @property
    def last_structured_salvage_applied(self) -> bool:
        return self._get_field("structured_salvage_applied") == "true"

    @last_structured_salvage_applied.setter
    def last_structured_salvage_applied(self, value: bool) -> None:
        self._set_field("structured_salvage_applied", "true" if value else "false")

    # -- LLMProvider interface -----------------------------------------------

    async def generate_structured(
        self, prompt: str, response_model: type[T],
    ) -> T:
        """Request JSON-mode output and parse into *response_model*.

        When *response_model* is ``QueryPlan``, a pre-parse normalization
        pass fixes common LLM enum/whitespace issues before Pydantic
        validation.
        """
        content = await self._chat_completion(
            prompt,
            system=(
                "You are a structured output generator. "
                "Respond ONLY with valid JSON matching the requested schema."
            ),
            response_format={"type": "json_object"},
        )
        self.last_structured_response_text = content
        self.last_structured_parse_error = None
        self.last_structured_parse_taxonomy = None
        self.last_structured_salvage_applied = False

        # Pre-parse normalization for QueryPlan
        from app.domain.query_plan import QueryPlan

        if response_model is QueryPlan:
            from app.services.plan_normalizer import (
                NormalizationStats,
                normalize_raw_plan,
            )

            raw_candidates = _unique_json_candidates(content)
            extracted = _safe_extract_plan_dict(content)
            coerced_candidate: dict[str, Any] | None = None
            salvage_applied = False

            if extracted is not None:
                coerced_candidate, salvage_applied = _coerce_plan_candidate(
                    extracted,
                    prompt=prompt,
                )
            if coerced_candidate is None:
                clarification_message = _extract_clarification_message(
                    content,
                    raw_candidates if raw_candidates else None,
                )
                if clarification_message is not None:
                    self.last_structured_parse_error = "no_queryplan_intent_found_in_response"
                    self.last_structured_parse_taxonomy = "free_text_clarification_only"
                    self.last_structured_salvage_applied = True
                    logger.warning(
                        "LLM returned clarification text instead of QueryPlan JSON. "
                        "Preview: %r. Converting to clarification plan.",
                        content[:300],
                    )
                    return _make_clarification_plan_with_message(clarification_message)  # type: ignore[return-value]

                if raw_candidates:
                    taxonomy = "multi_object_response" if len(raw_candidates) > 1 else "missing_required_field"
                elif "{" in content or "}" in content or "```" in content:
                    taxonomy = "malformed_json"
                else:
                    taxonomy = "no_json_found"
                self.last_structured_parse_error = "no_queryplan_intent_found_in_response"
                self.last_structured_parse_taxonomy = taxonomy
                logger.warning(
                    "LLM returned non-QueryPlan JSON. taxonomy=%s Preview: %r. Falling back to clarification plan.",
                    taxonomy,
                    content[:300],
                )
                return _make_clarification_plan()  # type: ignore[return-value]

            stats = NormalizationStats()
            normalised = normalize_raw_plan(coerced_candidate, stats=stats)
            if stats.total_normalizations > 0:
                logger.info(
                    "Pre-parse normalization applied %d fixes to LLM output.",
                    stats.total_normalizations,
                )
            if salvage_applied:
                self.last_structured_salvage_applied = True
                if len(raw_candidates) > 1:
                    self.last_structured_parse_taxonomy = "multi_object_response"
                elif extracted is not None and not isinstance(extracted.get("intent"), str):
                    self.last_structured_parse_taxonomy = "missing_required_field"
            try:
                return response_model.model_validate(normalised)  # type: ignore[return-value]
            except ValidationError as validate_exc:
                taxonomy = _classify_validation_error(validate_exc)
                self.last_structured_parse_error = str(validate_exc)
                self.last_structured_parse_taxonomy = taxonomy
                logger.warning(
                    "QueryPlan model_validate failed after normalization: %s (taxonomy=%s). "
                    "Preview: %r. Falling back to clarification plan.",
                    validate_exc,
                    taxonomy,
                    content[:300],
                )
                clarification_message = _extract_clarification_message(content, normalised)
                if clarification_message is not None:
                    self.last_structured_salvage_applied = True
                    return _make_clarification_plan_with_message(clarification_message)  # type: ignore[return-value]
                return _make_clarification_plan()  # type: ignore[return-value]
            except Exception as validate_exc:
                self.last_structured_parse_error = str(validate_exc)
                self.last_structured_parse_taxonomy = "malformed_json"
                logger.warning(
                    "QueryPlan model_validate failed after normalization: %s. "
                    "Preview: %r. Falling back to clarification plan.",
                    validate_exc,
                    content[:300],
                )
                return _make_clarification_plan()  # type: ignore[return-value]

        return response_model.model_validate_json(content)

    async def generate_text(self, prompt: str) -> str:
        content = await self._chat_completion(prompt)
        self.last_text_response_text = content
        return content

    # -- Internal ------------------------------------------------------------

    async def _chat_completion(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if response_format:
            payload["response_format"] = response_format

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            )

            # Compatibility fallback for servers exposing only /completions.
            if resp.status_code == 404:
                prompt_text = (
                    f"System: {system}\n\nUser: {prompt}" if system else prompt
                )
                completion_payload: dict[str, Any] = {
                    "model": self._model,
                    "prompt": prompt_text,
                }
                if response_format:
                    completion_payload["response_format"] = response_format

                resp = await client.post(
                    f"{self._base_url}/completions",
                    json=completion_payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["text"]

            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
