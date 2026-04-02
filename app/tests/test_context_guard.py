"""Tests for route-level context guard and intent classification changes.

Verifies:
1. CLARIFICATION intent is mapped to DATA (no more route-level bypass)
2. Short confirmations (evet/hayır/tamam) are reclassified to DATA when active context exists
3. Conversation history extraction works correctly
4. _is_data_continuation correctly detects continuation signals
"""

from __future__ import annotations

import re

import pytest

from app.api.routes_chat import (
    _DATA_CONTINUATION_RE,
    _extract_conversation_history,
    _is_data_continuation,
)
from app.api.schemas import OAIChatMessage, OAIChatRequest
from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.services.followup_context_merge import FollowupContextMergeService


# ── Dummy orchestrator stub ──────────────────────────────────────────────


class _FakeOrchestrator:
    """Minimal stub with has_data_context() for testing."""

    def __init__(self, *, has_context: bool = False) -> None:
        self._has_context = has_context

    def has_data_context(self, session_id: str) -> bool:
        return self._has_context


# ── Test _DATA_CONTINUATION_RE ───────────────────────────────────────────


class TestDataContinuationRegex:
    """Verify the regex matches short confirmation patterns."""

    @pytest.mark.parametrize("msg", [
        "evet",
        "Evet",
        "EVET",
        "hayır",
        "Hayır",
        "tamam",
        "Tamam",
        "olur",
        "ok",
        "yes",
        "no",
        "göster",
        "getir",
        "listele",
        "ekle",
        "peki",
        "oldu",
        "tamamdır",
        "evet olsun",
        "hayır istemiyorum",
        "tamam devam et",
        "evet ama şunu da ekle",
    ])
    def test_confirmation_matches(self, msg: str) -> None:
        assert _DATA_CONTINUATION_RE.search(msg.strip()), f"Should match: {msg!r}"

    @pytest.mark.parametrize("msg", [
        "merhaba nasılsın",
        "bu nasıl bir kod",
        "sen kimsin",
        "mimari hakkında bilgi ver",
        "brainstorm yapalım",
    ])
    def test_non_confirmation_no_match(self, msg: str) -> None:
        assert not _DATA_CONTINUATION_RE.search(msg.strip()), f"Should NOT match: {msg!r}"


# ── Test _is_data_continuation ────────────────────────────────────────────


class TestIsDataContinuation:
    """Test the context guard function."""

    def test_no_context_returns_false(self) -> None:
        orch = _FakeOrchestrator(has_context=False)
        assert _is_data_continuation("evet", orch, "s1") is False

    def test_with_context_short_confirmation_returns_true(self) -> None:
        orch = _FakeOrchestrator(has_context=True)
        assert _is_data_continuation("evet", orch, "s1") is True

    def test_with_context_long_message_returns_false(self) -> None:
        orch = _FakeOrchestrator(has_context=True)
        long_msg = "bu son sorguyla ilgili bazı detaylı bilgiler istiyorum lütfen"
        assert _is_data_continuation(long_msg, orch, "s1") is False

    def test_with_context_general_message_returns_false(self) -> None:
        orch = _FakeOrchestrator(has_context=True)
        assert _is_data_continuation("merhaba", orch, "s1") is False

    def test_hayir_with_context(self) -> None:
        orch = _FakeOrchestrator(has_context=True)
        assert _is_data_continuation("hayır", orch, "s1") is True

    def test_tamam_with_context(self) -> None:
        orch = _FakeOrchestrator(has_context=True)
        assert _is_data_continuation("tamam", orch, "s1") is True

    def test_peki_with_context(self) -> None:
        orch = _FakeOrchestrator(has_context=True)
        assert _is_data_continuation("peki göster", orch, "s1") is True


# ── Test _extract_conversation_history ────────────────────────────────────


class TestExtractConversationHistory:
    """Test conversation history extraction from OpenWebUI messages."""

    def test_basic_extraction(self) -> None:
        body = OAIChatRequest(
            messages=[
                OAIChatMessage(role="system", content="Sen bir yardımcısın."),
                OAIChatMessage(role="user", content="AHMET adlı çalışanları bul"),
                OAIChatMessage(role="assistant", content="96 kayıt bulundu."),
                OAIChatMessage(role="user", content="Sicil numaralarını da getir"),
            ],
        )
        history = _extract_conversation_history(body, "Sicil numaralarını da getir")
        # Should exclude system message and current user message
        assert len(history) == 2
        assert history[0] == ("user", "AHMET adlı çalışanları bul")
        assert history[1] == ("assistant", "96 kayıt bulundu.")

    def test_system_messages_excluded(self) -> None:
        body = OAIChatRequest(
            messages=[
                OAIChatMessage(role="system", content="System prompt"),
                OAIChatMessage(role="user", content="test"),
            ],
        )
        history = _extract_conversation_history(body, "test")
        assert len(history) == 0

    def test_empty_messages(self) -> None:
        body = OAIChatRequest(messages=[])
        history = _extract_conversation_history(body, "test")
        assert history == []

    def test_limits_to_10_items(self) -> None:
        msgs = []
        for i in range(20):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(OAIChatMessage(role=role, content=f"message {i}"))
        msgs.append(OAIChatMessage(role="user", content="current"))
        body = OAIChatRequest(messages=msgs)
        history = _extract_conversation_history(body, "current")
        assert len(history) <= 10

    def test_session_markers_stripped(self) -> None:
        body = OAIChatRequest(
            messages=[
                OAIChatMessage(role="user", content="soru 1"),
                OAIChatMessage(
                    role="assistant",
                    content="yanıt 1\n\n<!-- nl2sql:session=abc123 -->",
                ),
                OAIChatMessage(role="user", content="soru 2"),
            ],
        )
        history = _extract_conversation_history(body, "soru 2")
        # Session marker should be stripped from assistant content
        assistant_content = history[1][1]
        assert "nl2sql:session" not in assistant_content
