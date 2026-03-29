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

import asyncio
import re
from typing import Any

from app.core.exceptions import NarratorError
from app.core.logging import get_logger
from app.domain.execution_models import (
    ExecutionStatus,
    OrchestrationResult,
    ValidationResult,
)
from app.domain.query_plan import FilterOp, QueryPlan
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
    r'(kullan\u0131c\u0131\s+sorusu|sonu\u00e7\s+\u00f6zeti|yan\u0131t\u0131n\u0131\s+ver|'
    r'user\s+question|result\s+summary|constraints?:|rules?:|'
    r'istek<<<|veri_ozeti<<<|tek_cikti:)',
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

_PRESENTATION_META_RE = re.compile(
    r'(^|\n)\s*(?:\d+[\.)]\s+|[-*]\s+|#{1,6}\s+)',
    re.IGNORECASE,
)


class NarratorService:
    """Produce user-facing Turkish narrations for pipeline results."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        self._last_trace: dict[str, Any] | None = None
        self._last_trace_by_task: dict[int, dict[str, Any] | None] = {}

    def _set_last_trace(self, trace: dict[str, Any] | None) -> None:
        self._last_trace = trace
        task = asyncio.current_task()
        if task is not None:
            self._last_trace_by_task[id(task)] = trace
            if len(self._last_trace_by_task) > 2048:
                self._last_trace_by_task.clear()

    @property
    def last_trace(self) -> dict[str, Any] | None:
        """Return narrator debug metadata from the most recent call."""
        task = asyncio.current_task()
        if task is not None and id(task) in self._last_trace_by_task:
            return self._last_trace_by_task[id(task)]
        return self._last_trace

    # -- Public API --------------------------------------------------------

    async def narrate_success(
        self, user_message: str, result: OrchestrationResult,
    ) -> str:
        """Narrate a successful execution, appending a Markdown result table."""
        summary = self._build_success_summary(result)
        narration = await self._generate(user_message, summary)
        table = self._build_markdown_table(result)
        if table:
            return f"{narration}\n\n{table}"
        return narration

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

    async def narrate_clarification(
        self, plan: QueryPlan, user_message: str = "",
    ) -> str:
        """Narrate a clarification request."""
        summary = self._build_clarification_summary(plan)
        return await self._generate(user_message, summary)

    # -- Internal helpers ---------------------------------------------------

    async def _generate(self, user_message: str, summary: str) -> str:
        prompt = build_narrator_prompt(user_message, summary)
        self._set_last_trace({
            "user_message": user_message,
            "summary": summary,
            "prompt_length": len(prompt),
            "full_prompt_text": prompt,
            "raw_response": None,
            "sanitized_response": None,
            "final_response": None,
            "final_response_source": "fallback_template",
            "narration_shape": self._infer_shape_from_summary(summary),
            "narration_business_value_score": 0,
            "narration_genericness_flag": False,
            "raw_narration_quality": "unknown",
            "final_narration_quality": "unknown",
            "narrator_used_fallback_template": False,
            "prompt_contract_violated": False,
            "sanitizer_reason_code": None,
            "raw_response_empty": False,
            "raw_leak_reason_codes": [],
            "user_visible_quality": "fail",
            "model_behavior_quality": "fail",
            "error": None,
        })
        try:
            raw = await self._llm.generate_text(prompt, disable_thinking=True)
            raw_reasons = self._classify_raw_response_issues(raw)
            cleaned = self._strip_leakage(raw)
            contract_violation = bool(raw_reasons)
            generic = self._is_generic_low_value(cleaned)
            if generic:
                cleaned = self._fallback_template(
                    shape=str(self._last_trace.get("narration_shape") or "listing"),
                    summary=summary,
                )
            raw_vs_cleaned_changed = bool(raw and raw.strip() != cleaned.strip())
            if generic:
                source = "fallback_template"
            elif raw_vs_cleaned_changed:
                source = "sanitized"
            else:
                source = "raw"
            if not raw or not raw.strip():
                sanitizer_reason = "raw_missing"
            elif source == "sanitized":
                sanitizer_reason = "policy_leak_removed"
            elif source == "fallback_template":
                sanitizer_reason = "raw_unusable_final_safe"
            else:
                sanitizer_reason = "no_sanitization_needed"
            quality_score = self._business_value_score(cleaned, summary)
            narration_quality = "high" if quality_score >= 70 else ("medium" if quality_score >= 40 else "low")
            if not raw or not raw.strip():
                model_bq = "fail"
            elif source == "raw" and not contract_violation:
                model_bq = "pass"
            elif contract_violation or source in {"sanitized", "fallback_template"}:
                model_bq = "degraded"
            else:
                model_bq = "degraded"
            self._last_trace.update(
                {
                    "raw_response": raw,
                    "sanitized_response": cleaned if raw_vs_cleaned_changed else None,
                    "final_response": cleaned,
                    "final_response_source": source,
                    "narration_business_value_score": quality_score,
                    "narration_genericness_flag": generic,
                    "raw_narration_quality": (
                        "missing" if not raw or not raw.strip() else ("poor" if contract_violation or generic else "acceptable")
                    ),
                    "final_narration_quality": narration_quality,
                    "narrator_used_fallback_template": generic,
                    "prompt_contract_violated": contract_violation,
                    "sanitizer_reason_code": sanitizer_reason,
                    "raw_response_empty": not bool(raw and raw.strip()),
                    "raw_leak_reason_codes": raw_reasons,
                    "user_visible_quality": "pass" if source == "raw" else "pass_with_sanitization",
                    "model_behavior_quality": model_bq,
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
        skipping_prompt_block = False
        for line in cleaned.splitlines():
            stripped = line.strip()
            if not stripped:
                result_lines.append("")
                continue
            if stripped in {"ISTEK<<<", "VERI_OZETI<<<"}:
                skipping_prompt_block = True
                continue
            if skipping_prompt_block and stripped == ">>>":
                skipping_prompt_block = False
                continue
            if skipping_prompt_block:
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
            if stripped in {">>>", "<<<"}:
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

    @staticmethod
    def _classify_raw_response_issues(text: str | None) -> list[str]:
        reasons: list[str] = []
        if not text or not text.strip():
            return ["empty_raw_response"]

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        if _THINK_BLOCK_RE.search(text) or any(
            _REASONING_HEADER_RE.match(line) or _REASONING_LINE_RE.match(line)
            for line in lines
        ):
            reasons.append("chain_of_thought_like")
        if _PROMPT_ECHO_LINE_RE.search(text):
            reasons.append("prompt_echo")
        if _POLICY_ECHO_LINE_RE.search(text):
            reasons.append("policy_echo")
        if _PRESENTATION_META_RE.search(text) and (":" in text or any(_NUMBERED_OUTLINE_RE.match(line) for line in lines)):
            reasons.append("presentation_meta")

        return sorted(set(reasons))

    # -- Summary builders (no raw SQL, no restricted values) ----------------

    @staticmethod
    def _infer_shape_from_summary(summary: str) -> str:
        lowered = (summary or "").lower()
        if "açıklama gerekli" in lowered:
            return "clarification"
        if "çalıştırma hatası" in lowered or "doğrulama hatası" in lowered:
            return "error"
        if "satır_sayısı=0" in lowered or "satır sayısı: 0" in lowered:
            return "empty_result"
        if "shape=scalar_metric" in lowered:
            return "scalar_metric"
        if "shape=grouped_aggregate" in lowered:
            return "grouped_aggregate"
        return "listing"

    @staticmethod
    def _fallback_template(*, shape: str, summary: str) -> str:
        """Deterministic higher-value fallback templates per output shape."""
        if shape == "empty_result":
            # Try to mention filter criteria
            m_filter = re.search(r"uygulanan_filtreler=([^\n]+)", summary)
            filter_hint = (m_filter.group(1).strip() if m_filter and m_filter.group(1).strip() not in {"yok", ""} else None)
            if filter_hint:
                return f"Belirtilen kriterlere ({filter_hint}) uygun kayıt bulunamadı."
            return "Belirtilen kriterlere uygun kayıt bulunamadı."
        if shape == "scalar_metric":
            # Try to extract row count (which in scalar context is the metric value set)
            m_rows = re.search(r"satır sayısı:\s*(\d+)", summary, re.IGNORECASE)
            m_cols = re.search(r"seçili_alanlar=([^\n]+)", summary)
            col_hint = ""
            if m_cols:
                cols = [c.strip() for c in m_cols.group(1).split(",") if c.strip()]
                agg_cols = [c for c in cols if any(k in c.lower() for k in ("count", "sum", "avg", "miktar", "toplam", "sayi", "sayı"))]
                col_hint = agg_cols[0] if agg_cols else (cols[0] if cols else "")
            if m_rows and col_hint:
                return f"Sorgu metrikleri hesaplandı. {col_hint.upper()} değeri {m_rows.group(1)} kayıt üzerinden hesaplandı."
            if m_rows:
                return f"Sorgu tamamlandı. Toplam {m_rows.group(1)} kayıt üzerinden metrik hesaplandı."
            return "Sorgu sonucu metrik bazında üretildi. Özet değerleri kriterlerinize göre hazır."
        if shape == "grouped_aggregate":
            m_rows = re.search(r"satır sayısı:\s*(\d+)", summary, re.IGNORECASE)
            m_group = re.search(r"group_by_hint=([^\n]+)", summary)
            m_top = re.search(r"top_group_label=([^\n]+)", summary)
            row_hint = f" {m_rows.group(1)} satırda" if m_rows else ""
            group_hint = f" {m_group.group(1).strip()}" if m_group else ""
            top_hint = f" En yüksek grup: {m_top.group(1).strip()}." if m_top else ""
            return f"Sorgulama{row_hint}{group_hint} kırılımıyla tamamlandı.{top_hint}"
        if shape == "clarification":
            # Use clarification message if available, otherwise return useful prompt
            m_dims = re.search(r"missing_dimensions=([^\n]+)", summary)
            m_msg = re.search(r"Mesaj:\s*(.+)$", summary)
            if m_dims:
                dims = m_dims.group(1).strip()
                return f"Sorguyu yanıtlamak için şu bilgilere ihtiyacım var: {dims}. Lütfen netleştirin."
            if m_msg:
                return m_msg.group(1).strip()
            return "İstenen sonucu üretebilmem için tarih aralığı veya metrik boyutunu netleştirmeniz gerekiyor."
        # listing
        m_rows = re.search(r"satır sayısı:\s*(\d+)", summary, re.IGNORECASE)
        m_fields = re.search(r"iş_alanları=([^\n]+)", summary)
        m_filter = re.search(r"uygulanan_filtreler=([^\n]+)", summary)
        m_sort = re.search(r"uygulanan_sıralama=([^\n]+)", summary)
        m_clip = re.search(r"row_limit_hit=evet", summary)
        parts: list[str] = []
        if m_rows:
            n = int(m_rows.group(1))
            parts.append(f"Toplam {n} kayıt listelendi.")
        else:
            parts.append("Sorgu tamamlandı.")
        if m_fields:
            fields = [f.strip() for f in m_fields.group(1).split(",") if f.strip()][:3]
            if fields:
                parts.append(f"Gösterilen alanlar: {', '.join(fields)}.")
        if m_filter and m_filter.group(1).strip() not in {"yok", ""}:
            parts.append(f"Uygulanan filtreler: {m_filter.group(1).strip()}.")
        if m_sort and m_sort.group(1).strip() not in {"yok", ""}:
            parts.append(f"Sıralama: {m_sort.group(1).strip()}.")
        if m_clip:
            parts.append("Sonuçlar limit nedeniyle kırpıldı; daha fazla kayıt mevcut olabilir.")
        return " ".join(parts) if parts else "Uygun kayıtlar listelendi ve özetlendi."

    @staticmethod
    def _is_generic_low_value(text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return True
        if re.search(r"\b\d+\b", lowered) and "kayıt" in lowered:
            return False
        generic_patterns = (
            "sorgu işlendi",
            "işlem tamamlandı",
            "sonuç hazırlandı",
            "kayıtlar listelendi",
        )
        if any(p in lowered for p in generic_patterns):
            return True
        return len(lowered.split()) <= 4

    @staticmethod
    def _business_value_score(text: str, summary: str) -> int:
        score = 20
        lowered = (text or "").lower()
        if re.search(r"\b\d+\b", text or ""):
            score += 20
        if any(k in lowered for k in ("kayıt", "metrik", "kırılım", "filtre", "sıralama")):
            score += 20
        if "shape=" in (summary or ""):
            score += 20
        if not NarratorService._is_generic_low_value(text):
            score += 20
        return max(0, min(100, score))

    # -- Markdown table renderer (deterministic, never goes through LLM) ---

    _TABLE_MAX_ROWS = 50          # hard cap to avoid enormous messages
    _TABLE_CELL_MAX_CHARS = 80    # truncate long cell values

    @staticmethod
    def _humanize_col(col: str) -> str:
        """Turn a DB column name into a readable Turkish header.

        Examples: UNVAN → Unvan, BIRIM_ADI → Birim Adı, EMPLOYEE_NUMBER → Employee Number
        """
        _SUFFIX_MAP = {
            "ADI": "Adı", "SOYADI": "Soyadı", "ADI_SOYADI": "Adı Soyadı",
            "NO": "No", "KODU": "Kodu", "TARIHI": "Tarihi",
            "TURU": "Türü", "TANIMI": "Tanımı", "SAYISI": "Sayısı",
        }
        words = col.strip().upper().split("_")
        result_words: list[str] = []
        for w in words:
            result_words.append(_SUFFIX_MAP.get(w, w.capitalize()))
        return " ".join(result_words)

    @classmethod
    def _build_markdown_table(cls, result: OrchestrationResult) -> str | None:
        """Return a GFM Markdown table from execution rows, or None if no data."""
        er = result.execution_result
        if er is None or er.row_count == 0 or not er.rows:
            return None

        # Prefer the executor-provided column order; fall back to first row keys
        columns: list[str] = er.columns if er.columns else list(er.rows[0].keys())
        if not columns:
            return None

        # Filter out columns that are purely internal (end with _ID, ROWNUM, etc.)
        visible_cols = [
            c for c in columns
            if not re.search(r"^(ROWNUM|ROW_NUMBER|RN)$", c, re.IGNORECASE)
        ] or columns

        rows_to_show = er.rows[: cls._TABLE_MAX_ROWS]
        total = er.row_count
        shown = len(rows_to_show)

        # -- Build header row --
        headers = [cls._humanize_col(c) for c in visible_cols]
        header_line = "| " + " | ".join(headers) + " |"
        separator = "| " + " | ".join(["---"] * len(visible_cols)) + " |"

        # -- Build data rows --
        data_lines: list[str] = []
        for row in rows_to_show:
            cells: list[str] = []
            for col in visible_cols:
                # Case-insensitive lookup
                val = row.get(col) if col in row else row.get(col.lower())
                if val is None:
                    cell = "—"
                else:
                    cell = str(val)
                    # Escape pipe characters inside cells
                    cell = cell.replace("|", "\\|")
                    if len(cell) > cls._TABLE_CELL_MAX_CHARS:
                        cell = cell[: cls._TABLE_CELL_MAX_CHARS] + "…"
                cells.append(cell)
            data_lines.append("| " + " | ".join(cells) + " |")

        table_lines = [header_line, separator] + data_lines

        # -- Footer note when rows are capped --
        footer = ""
        if shown < total:
            footer = f"\n> _İlk **{shown}** kayıt gösteriliyor. Toplam **{total}** kayıt bulundu._"
        elif total > 0:
            footer = f"\n> _Toplam **{total}** kayıt._"

        return "\n".join(table_lines) + footer

    @staticmethod
    def _build_success_summary(result: OrchestrationResult) -> str:
        if not result.execution_result:
            return "status=success\nshape=listing\nsatır_sayısı=0"

        er = result.execution_result
        shape = "listing"
        if er.status == ExecutionStatus.EMPTY or er.row_count == 0:
            shape = "empty_result"
        elif result.compiled_query and result.compiled_query.debug_plan:
            plan = result.compiled_query.debug_plan
            if plan.aggregations and plan.group_by:
                shape = "grouped_aggregate"
            elif plan.aggregations:
                shape = "scalar_metric"

        selected_columns = list(result.compiled_query.selected_columns if result.compiled_query else [])
        human_fields = [
            c for c in selected_columns
            if not c.lower().endswith("id") and "_id" not in c.lower()
        ][:6]
        if not human_fields:
            human_fields = selected_columns[:4]

        plan = result.compiled_query.debug_plan if result.compiled_query else None
        filters = []
        sort = []
        row_limit_hit = False
        group_by_hint = ""
        top_group_label = ""
        if plan is not None:
            for f in plan.filters[:4]:
                if f.op in (FilterOp.IS_NULL, FilterOp.IS_NOT_NULL):
                    filters.append(f"{f.column} {f.op.value}")
                elif f.value is not None:
                    filters.append(f"{f.column} {f.op.value} '{f.value}'")
                else:
                    filters.append(f"{f.column} {f.op.value}")
            sort = [f"{o.column} {o.direction.value}" for o in plan.order_by[:3]]
            row_limit_hit = bool(er.row_count >= plan.limit)
            if plan.group_by:
                group_by_hint = ", ".join(plan.group_by[:3])
        # For grouped_aggregate with rows: derive top group from first row of results
        if shape == "grouped_aggregate" and er.rows and plan and plan.group_by:
            top_row = er.rows[0]
            group_col = plan.group_by[0]
            top_val = top_row.get(group_col.lower()) or top_row.get(group_col)
            if top_val is not None:
                top_group_label = str(top_val)[:60]

        payload = [
            "Sorgu başarılı.",
            f"Satır sayısı: {er.row_count}.",
            "status=success",
            f"shape={shape}",
            f"satır_sayısı={er.row_count}",
            f"seçili_alanlar={','.join(selected_columns[:8])}",
            f"iş_alanları={','.join(human_fields)}",
            f"uygulanan_filtreler={'; '.join(filters) if filters else 'yok'}",
            f"uygulanan_sıralama={'; '.join(sort) if sort else 'yok'}",
            f"row_limit_hit={'evet' if row_limit_hit else 'hayır'}",
        ]
        if group_by_hint:
            payload.append(f"group_by_hint={group_by_hint}")
        if top_group_label:
            payload.append(f"top_group_label={top_group_label}")
        return "\n".join(payload)

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
        parts = [f"Açıklama gerekli. Mesaj: {msg}"]
        missing = plan.clarification_missing_dimensions or []
        if missing:
            parts.append(f"missing_dimensions={', '.join(str(d) for d in missing[:4])}")
        return "\n".join(parts)
