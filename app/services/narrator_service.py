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

# Matches reasoning/thinking/analysis section headers (markdown headings or bare labels).
_REASONING_HEADER_RE = re.compile(
    r'^(?:\d+[\.)]\s*)?(?:#+\s*)?(thinking|reasoning|analysis|draft|final\s*check|'
    r'thought process|d\u00fc\u015f\u00fcnce|analiz|muhakeme|i\u00e7 muhakeme|plan|step\s+\d)',
    re.IGNORECASE,
)

# Oracle error line patterns that should NOT surface to the user.
_ORA_ERROR_RE = re.compile(r'ORA-\d{5}', re.IGNORECASE)


class NarratorService:
    """Produce user-facing Turkish narrations for pipeline results."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

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
        try:
            raw = await self._llm.generate_text(prompt)
            cleaned = self._strip_leakage(raw)
            return cleaned
        except Exception as exc:
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

        # --- Pass 1: remove fenced code blocks entirely ---
        cleaned = _CODE_BLOCK_RE.sub('', text)

        # --- Pass 2: remove inline SQL expressions ---
        cleaned = _SQL_LEAK_RE.sub('', cleaned)

        # --- Pass 3: remove Oracle error codes from user-visible text ---
        # (They may appear in execution-error summaries passed to narrator)
        cleaned = _ORA_ERROR_RE.sub('', cleaned)

        # --- Pass 4: strip reasoning / thinking sections ---
        # Walk line-by-line; drop lines that are headers for reasoning sections
        # and all subsequent lines until we hit a blank line or a new section.
        lines = cleaned.split('\n')
        result_lines: list[str] = []
        in_leaky_section = False
        for line in lines:
            stripped = line.strip()
            if _REASONING_HEADER_RE.match(stripped):
                in_leaky_section = True
                logger.debug("Narrator leak: reasoning header detected: %r", stripped[:80])
                continue
            if in_leaky_section:
                # Exit on blank line or non-indented content that starts a new paragraph
                if not stripped:
                    in_leaky_section = False  # blank line ends the section
                continue
            if re.search(r"\b(SELECT|UPDATE|DELETE|INSERT|FROM|WHERE)\b", stripped, re.IGNORECASE):
                continue
            result_lines.append(line)

        final = '\n'.join(result_lines).strip()
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
