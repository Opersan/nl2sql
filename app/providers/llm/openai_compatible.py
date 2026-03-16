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

import json
import re
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from app.core.logging import get_logger
from app.providers.llm.base import LLMProvider

T = TypeVar("T", bound=BaseModel)

logger = get_logger(__name__)


def _safe_extract_plan_dict(content: str) -> dict[str, Any] | None:
    """Extract a QueryPlan-shaped dict from raw LLM output.

    Handles these real-world failure modes:
    * Reasoning wrappers: ``{"reasoning": "...", "plan": {"intent": ...}}``
    * Thinking prefix before JSON: ``<think>...</think>{"intent": ...}``
    * Empty or error objects: ``{}``, ``{"error": "..."}``, ``{"status": "ok"}``
    * JSON embedded inside markdown code fences.

    Returns ``None`` when no QueryPlan-shaped object can be found.
    """
    # 1. Direct parse — fast path for well-formed output
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            if "intent" in data:
                return data
            # Nested: {"plan": {"intent": ...}, "reasoning": ...}
            for v in data.values():
                if isinstance(v, dict) and "intent" in v:
                    return v
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Extract JSON objects from mixed content (think tags, markdown fences, etc.)
    # Try all ``{...}`` blocks from largest to smallest.
    for match in re.finditer(r'\{(?:[^{}]|\{[^{}]*\})*\}', content, re.DOTALL):
        try:
            candidate = json.loads(match.group())
            if isinstance(candidate, dict) and "intent" in candidate:
                return candidate
        except (json.JSONDecodeError, ValueError):
            continue

    return None


def _make_clarification_plan() -> Any:
    """Return a minimal QueryPlan with needs_clarification=True.

    Used as a safe fallback when the LLM response cannot be parsed.
    Imported lazily to avoid circular imports.
    """
    from app.domain.query_plan import QueryPlan

    return QueryPlan(
        intent="Yanit yorumlanamadi",
        needs_clarification=True,
        clarification_message="Lutfen sorunuzu daha acik bir sekilde belirtin.",
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

        # Pre-parse normalization for QueryPlan
        from app.domain.query_plan import QueryPlan

        if response_model is QueryPlan:
            from app.services.plan_normalizer import (
                NormalizationStats,
                normalize_raw_plan,
            )

            # --- Firewall: extract QueryPlan-shaped dict from any LLM output ---
            extracted = _safe_extract_plan_dict(content)
            if extracted is None:
                logger.warning(
                    "LLM returned non-QueryPlan JSON (no 'intent' key found). "
                    "Preview: %r. Falling back to clarification plan.",
                    content[:300],
                )
                return _make_clarification_plan()  # type: ignore[return-value]

            stats = NormalizationStats()
            normalised = normalize_raw_plan(extracted, stats=stats)
            if stats.total_normalizations > 0:
                logger.info(
                    "Pre-parse normalization applied %d fixes to LLM output.",
                    stats.total_normalizations,
                )
            try:
                return response_model.model_validate(normalised)  # type: ignore[return-value]
            except Exception as validate_exc:
                logger.warning(
                    "QueryPlan model_validate failed after normalization: %s. "
                    "Preview: %r. Falling back to clarification plan.",
                    validate_exc,
                    content[:300],
                )
                return _make_clarification_plan()  # type: ignore[return-value]

        return response_model.model_validate_json(content)

    async def generate_text(self, prompt: str) -> str:
        return await self._chat_completion(prompt)

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
