"""
title: Enterprise Pipeline Filter
author: Best Transformer AI Team
version: 3.0
required_open_webui_version: 0.6.0
description: >
  Thin pre-LLM filter for Best Transformer / Best Trafo.
  When toggle is ON  → sets enterprise_mode=true in the request body.
  When toggle is OFF → sets enterprise_mode=false.
  Also: injects lightweight company context, sanitizes UI helper
  artifacts.  All routing / backend logic lives in the backend.
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════════════
# Open WebUI UI-artifact detection patterns
# ═══════════════════════════════════════════════════════════════════════

_OWUI_FOLLOW_UP_RE = re.compile(
    r"(?:suggest|generate)\s+\d[\d\-]*\s*(?:relevant\s+)?(?:follow[- ]?up|related)\s+"
    r"(?:questions?|prompts?)",
    re.IGNORECASE,
)
_OWUI_TITLE_RE = re.compile(
    r"(?:generate|create)\s+a?\s*(?:concise|short|brief)?\s*(?:\d[\d\-]*\s+word\s+)?title",
    re.IGNORECASE,
)
_OWUI_TAGS_RE = re.compile(
    r"(?:generate|create)\s+\d[\d\-]*\s*(?:broad\s+)?tags?\s+(?:categoriz|classif)",
    re.IGNORECASE,
)
_OWUI_TASK_PREFIX_RE = re.compile(
    r"^\s*#{1,4}\s*Task\s*:", re.IGNORECASE,
)
_SYNTHETIC_PATTERNS = [
    re.compile(r"^\s*(?:here\s+are|i\s+suggest|you\s+(?:might|may|could)\s+(?:also\s+)?(?:ask|try))", re.I),
    re.compile(r"^\s*(?:sana\s+öner|şunları\s+da\s+sorabilirsin|devam\s+etmek\s+için)", re.I),
    re.compile(r"^\s*(?:recommended|suggested)\s+(?:questions?|prompts?|follow)", re.I),
]
_BULLET_ONLY_RE = re.compile(
    r"^\s*(?:[-•*]\s+.+\n?){2,}\s*$", re.MULTILINE,
)
_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:\.{3}|…|placeholder|test|hello|hi|merhaba|selam)\s*$", re.I,
)


# ╔═══════════════════════════════════════════════════════════════════════╗
# ║  Filter                                                              ║
# ╚═══════════════════════════════════════════════════════════════════════╝


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=5,
            description="Filter chain priority (lower = runs first).",
        )
        debug_logging: bool = Field(
            default=False,
            description="Print debug diagnostics to stdout.",
        )
        inject_company_context: bool = Field(
            default=True,
            description="Inject Best Transformer company context as system message.",
        )
        sanitize_ui_messages: bool = Field(
            default=True,
            description="Detect and neutralize UI-generated helper prompts.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.toggle = True  # ON → enterprise_mode=true, OFF → false

    # ══════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_messages(body: dict) -> list[dict]:
        msgs = body.get("messages")
        return msgs if isinstance(msgs, list) else []

    @staticmethod
    def is_ui_helper_message(text: str) -> bool:
        """Detect Open WebUI auto-generated task prompts and other
        synthetic UI artifacts that should NOT trigger the enterprise
        pipeline."""
        if _OWUI_TASK_PREFIX_RE.search(text):
            return True
        if _OWUI_FOLLOW_UP_RE.search(text):
            return True
        if _OWUI_TITLE_RE.search(text):
            return True
        if _OWUI_TAGS_RE.search(text):
            return True
        for p in _SYNTHETIC_PATTERNS:
            if p.search(text):
                return True
        stripped = text.strip()
        if _BULLET_ONLY_RE.fullmatch(stripped):
            lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
            if all(ln.startswith(("-", "•", "*")) for ln in lines):
                return True
        if not stripped or len(stripped) < 2:
            return True
        if _PLACEHOLDER_RE.match(stripped):
            return True
        return False

    # ── Company context ──────────────────────────────────────────────

    _COMPANY_CTX = (
        "Sen Best Transformer (Best Trafo) şirketinin kurumsal yapay zeka asistanısın. "
        "Best Transformer bir transformatör üretim şirketidir. "
        "Kullanıcı bu şirketin bir çalışanıdır ve fabrika operasyonları, üretim, kalite, "
        "bakım, ERP sistemleri, veri analitiği ve yapay zeka dönüşümü konularında destek bekler. "
        "Cevaplarını pratik, teknik ve kurumsal tonda ver. "
        "Şirketin dahili verilerine doğrudan erişimin yoktur — canlı veriye ihtiyaç olursa "
        "Enterprise Pipeline modunun aktif olması gerektiğini belirt. "
        "Şirket hakkında iç bilgi uydurmaktan kaçın."
    )
    _CTX_MARKER = "<!-- bt-company-ctx -->"

    def _inject_company_ctx(self, body: dict) -> dict:
        if not self.valves.inject_company_context:
            return body
        messages = self._extract_messages(body)
        # Deduplicate
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                if self._CTX_MARKER in (m.get("content") or ""):
                    return body
        ctx = {
            "role": "system",
            "content": f"{self._COMPANY_CTX}\n{self._CTX_MARKER}",
        }
        # Insert after leading system messages
        insert_idx = 0
        for i, m in enumerate(messages):
            if isinstance(m, dict) and m.get("role") == "system":
                insert_idx = i + 1
            else:
                break
        messages.insert(insert_idx, ctx)
        body["messages"] = messages
        return body

    # ── Logging ──────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        if self.valves.debug_logging:
            print(f"[enterprise-filter] {msg}")

    # ══════════════════════════════════════════════════════════════════
    # inlet — runs BEFORE the request reaches the backend / LLM
    # ══════════════════════════════════════════════════════════════════

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __model__: Optional[dict] = None,
        __event_emitter__=None,
        **kwargs,
    ) -> dict:
        """Set enterprise_mode flag and inject company context.

        toggle ON  → enterprise_mode = true
        toggle OFF → enterprise_mode = false

        UI helper messages are detected and force enterprise_mode = false
        regardless of toggle state.
        """
        # 1 — Determine enterprise_mode from toggle
        enterprise_mode = bool(self.toggle)

        # 2 — Sanitization: if last user message is a UI artifact,
        #     force enterprise_mode = false
        if enterprise_mode and self.valves.sanitize_ui_messages:
            messages = self._extract_messages(body)
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = (msg.get("content") or "").strip()
                    if content and self.is_ui_helper_message(content):
                        enterprise_mode = False
                        self._log(f"UI artifact → enterprise_mode=false: {content[:60]}")
                    break

        # 3 — Set the flag on the request body
        body["enterprise_mode"] = enterprise_mode
        self._log(f"enterprise_mode={enterprise_mode}")

        # 4 — Always inject company context
        body = self._inject_company_ctx(body)

        return body

    # ══════════════════════════════════════════════════════════════════
    # outlet — runs AFTER the LLM response
    # ══════════════════════════════════════════════════════════════════

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
        **kwargs,
    ) -> dict:
        """Clean leaked artifacts from the LLM response."""
        messages = self._extract_messages(body)
        if not messages:
            return body

        last = messages[-1]
        if not isinstance(last, dict) or last.get("role") != "assistant":
            return body

        content = last.get("content") or ""
        original = content

        # Strip leaked enterprise context JSON
        content = re.sub(
            r"```json\s*\{[\s\S]*?\"enterprise_context\"[\s\S]*?\}\s*```",
            "",
            content,
        )
        # Strip pipeline markers
        content = re.sub(r"\[Enterprise Pipeline[^\]]*\]\s*", "", content)
        # Strip thinking tags
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        content = content.strip()

        if content != original:
            last["content"] = content
            self._log("outlet: cleaned artifacts")

        return body
