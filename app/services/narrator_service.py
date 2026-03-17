"""Narrator service – orchestration results → Turkish narration via LLM.

The narrator **never** fabricates data.  It receives a compact summary
of the execution outcome and asks the LLM to phrase it in natural Turkish.

It also **never** exposes raw SQL, full traces or restricted-column
values.

Leak filter (Sprint 6 hardening)
================================
Even when the LLM ignores prompt constraints, ``_strip_leakage`` removes:
* Raw SQL blocks (SELECT ... FROM ...)
* Reasoning / thinking / analysis section headers and their content
* Raw Oracle error traces
"""

from __future__ import annotations

import re
from typing import Any

from app.core.exceptions import NarratorError
from app.core.logging import get_logger
from app.domain.execution_models import (
    ExecutionStatus,
    OrchestrationResult,
    ValidationResult,
)
from app.domain.query_plan import QueryPlan
from app.providers.llm.base import LLMProvider
from app.providers.llm.prompts import build_narrator_prompt

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Leak-detection patterns
# ---------------------------------------------------------------------------

# Matches the core of a SQL SELECT statement embedded anywhere in the text.
_SQL_LEAK_RE = re.compile(
    r'\bSELECT\b.{1,1000}\bFROM\b',
    re.IGNORECASE | re.DOTALL,
)

_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)

# Matches reasoning/thinking/analysis section headers (markdown headings or bare labels).
_REASONING_HEADER_RE = re.compile(
    r'^(?:\d+[\.)]\s*)?(?:#+\s*)?(thinking|reasoning|analysis|draft|final\s*check|'
    r'thought process|d\u00fc\u015f\u00fcnce|analiz|muhakeme|i\u00e7 muhakeme|plan|step\s+\d)',
    re.IGNORECASE,
)

# Oracle error line patterns that should NOT surface to the user.
_ORA_ERROR_RE = re.compile(r'ORA-\d{5}', re.IGNORECASE)

_REASONING_LINE_RE = re.compile(
    r'^(?:\d+[\.)]\s*)?(?:#+\s*)?('
    r'thinking|reasoning|analysis|draft|final\s*polish|final\s*check|'
    r'analyze(?:\s+the\s+request)?|evaluate(?:\s+the\s+result)?|'
    r'draft(?:ing)?(?:\s+the\s+response)?|refine(?:ment)?|'
    r'check\s+constraints|final\s+review|selected\s+response|alternative|'
    r'final\s+choice|final\s+plan|final\s+decision|final\s+selection|'
    r'plan|step\s+\d+|constraint\s*\d+|rule\s*\d+|kural\s*\d+|'
    r'd\u00fc\u015f\u00fcnce|analiz|muhakeme|i\u00e7\s+muhakeme)\b',
    re.IGNORECASE,
)

_PROMPT_ECHO_LINE_RE = re.compile(
    r'\b(kullan\u0131c\u0131\s+sorusu|sonu\u00e7\s+\u00f6zeti|yan\u0131t\u0131n\u0131\s+ver|'
    r'user\s+question|result\s+summary|constraints?:|rules?:)\b',
    re.IGNORECASE,
)

_POLICY_ECHO_LINE_RE = re.compile(
    r'\b(only\s+answer\s+based\s+on|do\s+not\s+show\s+oracle|do\s+not\s+write|'
    r'never\s+produce\s+sql|return\s+only\s+a\s+sentence|'
    r'yaln\u0131zca\s+verilen\s+\u00f6zete\s+g\u00f6re|asla\s+sql|'
    r'd\u00fc\u015f\u00fcnce\s+s\u00fcreci|oracle\s+hata\s+kodlar\u0131|'
    r'tek\s+k\u0131sa\s+paragraf|i\u015f\s+dilinde\s+t\u00fcrk\u00e7e)\b',
    re.IGNORECASE,
)

_NUMBERED_OUTLINE_RE = re.compile(r'^\s*\d+[\.)]\s+')

_SQL_KEYWORD_RE = re.compile(r'\b(SELECT|FROM|WHERE|JOIN|GROUP\s+BY|ORDER\s+BY|INSERT|UPDATE|DELETE)\b', re.IGNORECASE)


class NarratorService:
    """Produce user-facing Turkish narrations for pipeline results."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        self._last_trace: dict[str, Any] | None = None

    @property
    def last_trace(self) -> dict[str, Any] | None:
        """Return narrator debug metadata from the most recent call."""
        return self._last_trace

    # -- Public API --------------------------------------------------------

    async def narrate_success(
        self, user_message: str, result: OrchestrationResult,
    ) -> str:
        """Narrate a successful execution."""
        summary = self._build_success_summary(result)
        return await self._generate(user_message, summary)

    async def narrate_validation_error(
        self, user_message: str, validation: ValidationResult,
    ) -> str:
        """Narrate a validation failure."""
        summary = self._build_validation_error_summary(validation)
        return await self._generate(user_message, summary)

    async def narrate_execution_error(
        self, user_message: str, result: OrchestrationResult,
    ) -> str:
        """Narrate an execution-phase or compilation-phase error."""
        summary = self._build_execution_error_summary(result)
        return await self._generate(user_message, summary)

    async def narrate_clarification(self, plan: QueryPlan) -> str:
        """Narrate a clarification request."""
        summary = self._build_clarification_summary(plan)
        return await self._generate("", summary)

    # -- Internal helpers ---------------------------------------------------

    async def _generate(self, user_message: str, summary: str) -> str:
        prompt = build_narrator_prompt(user_message, summary)
        self._last_trace = {
            "user_message": user_message,
            "summary": summary,
            "prompt_length": len(prompt),
            "full_prompt_text": prompt,
            "raw_response": None,
            "final_response": None,
            "error": None,
        }
        try:
            raw = await self._llm.generate_text(prompt)
            cleaned = self._strip_leakage(raw)
            self._last_trace.update(
                {
                    "raw_response": raw,
                    "final_response": cleaned,
                }
            )
            return cleaned
        except Exception as exc:
            self._last_trace.update({"error": str(exc)})
            logger.error("Narrator LLM call failed: %s", exc)
            raise NarratorError(
                f"Yanıt oluşturulamadı: {exc}", detail=str(exc),
            ) from exc

    @staticmethod
    def _strip_leakage(text: str) -> str:
        """Remove SQL blocks, reasoning sections, and Oracle error codes from narrator output.

        Called on every LLM response before it reaches the caller.  If the
        entire response is leakage the method returns a safe generic message.
        """
        if not text or not text.strip():
            return "Sorgu işlendi."

        cleaned = text
        cleaned = _THINK_BLOCK_RE.sub("", cleaned)
        cleaned = _CODE_BLOCK_RE.sub("", cleaned)
        cleaned = _ORA_ERROR_RE.sub("", cleaned)

        result_lines: list[str] = []
        for line in cleaned.splitlines():
            stripped = line.strip()
            if not stripped:
                result_lines.append("")
                continue
            lowered = stripped.lower()
            if lowered in {"<think>", "</think>", "</analysis>", "</final>", "</plan>"}:
                continue
            if _REASONING_HEADER_RE.match(stripped):
                continue
            if _REASONING_LINE_RE.match(stripped):
                continue
            if _PROMPT_ECHO_LINE_RE.search(stripped):
                continue
            if _POLICY_ECHO_LINE_RE.search(stripped):
                continue
            if _NUMBERED_OUTLINE_RE.match(stripped) and ("**" in stripped or ":" in stripped):
                continue
            if _SQL_KEYWORD_RE.search(stripped):
                continue
            if stripped.startswith(("*", "-")) and any(
                key in lowered
                for key in ["rule", "constraint", "draft", "final", "analyze", "thinking", "reasoning", "kural"]
            ):
                continue
            result_lines.append(stripped)

        compact = "\n".join(result_lines)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", compact) if p.strip()]
        final = ""
        for para in reversed(paragraphs):
            if _PROMPT_ECHO_LINE_RE.search(para) or _POLICY_ECHO_LINE_RE.search(para):
                continue
            if _REASONING_LINE_RE.match(para):
                continue
            final = para
            break

        if not final:
            logger.warning("Narrator response was entirely leakage; returning safe default.")
            return "Sorgu işlendi."
        return final

    # -- Summary builders (no raw SQL, no restricted values) ----------------

    @staticmethod
    def _build_success_summary(result: OrchestrationResult) -> str:
        parts: list[str] = ["Sorgu başarılı."]

        if result.compiled_query:
            parts.append(f"Tablo: {result.compiled_query.table}.")

        if result.execution_result:
            er = result.execution_result
            if er.status == ExecutionStatus.EMPTY or er.row_count == 0:
                parts.append("Satır sayısı: 0. Sonuç bulunamadı.")
            else:
                parts.append(f"Satır sayısı: {er.row_count}.")
            if er.columns:
                parts.append(f"Kolonlar: {', '.join(er.columns)}.")

        return " ".join(parts)

    @staticmethod
    def _build_validation_error_summary(validation: ValidationResult) -> str:
        parts: list[str] = ["Doğrulama hatası."]
        for err in validation.errors:
            code_tag = err.code
            msg = err.message
            # Flag restricted-column errors specifically so the mock narrator
            # can produce the correct deterministic response.
            if "restricted" in code_tag.lower() or "kısıtlı" in msg.lower():
                parts.append(f"Kısıtlı alan hatası: {msg}")
            else:
                parts.append(f"[{code_tag}] {msg}")
        return " ".join(parts)

    @staticmethod
    def _build_execution_error_summary(result: OrchestrationResult) -> str:
        parts: list[str] = ["Çalıştırma hatası."]
        if result.compilation_error:
            parts.append(f"Hata: {result.compilation_error}")
        elif result.execution_result and result.execution_result.error_message:
            parts.append(f"Hata: {result.execution_result.error_message}")
        return " ".join(parts)

    @staticmethod
    def _build_clarification_summary(plan: QueryPlan) -> str:
        msg = plan.clarification_message or "Daha fazla bilgi gerekiyor."
        return f"Açıklama gerekli. Mesaj: {msg}"
