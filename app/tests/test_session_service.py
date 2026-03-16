"""Tests for SessionService."""

from __future__ import annotations

import pytest

from app.domain.query_plan import QueryPlan
from app.services.session_service import SessionService


@pytest.fixture
def sessions() -> SessionService:
    return SessionService(max_history=5)


class TestCreateGet:
    def test_get_or_create_new(self, sessions: SessionService) -> None:
        """First call should create a new session."""
        session = sessions.get_or_create("s1")

        assert session.session_id == "s1"
        assert session.messages == []
        assert session.last_plan is None

    def test_get_or_create_existing(self, sessions: SessionService) -> None:
        """Second call should return the same session."""
        s1 = sessions.get_or_create("s1")
        s2 = sessions.get_or_create("s1")

        assert s1 is s2

    def test_has_session(self, sessions: SessionService) -> None:
        sessions.get_or_create("exists")

        assert sessions.has_session("exists") is True
        assert sessions.has_session("missing") is False


class TestUpdate:
    def test_append_user_message(self, sessions: SessionService) -> None:
        sessions.append_user_message("s1", "Merhaba")
        session = sessions.get_or_create("s1")

        assert len(session.messages) == 1
        assert session.messages[0].role == "user"
        assert session.messages[0].content == "Merhaba"

    def test_append_assistant_message(self, sessions: SessionService) -> None:
        sessions.append_assistant_message("s1", "Nasıl yardımcı olabilirim?")
        session = sessions.get_or_create("s1")

        assert len(session.messages) == 1
        assert session.messages[0].role == "assistant"

    def test_conversation_order(self, sessions: SessionService) -> None:
        """Messages should appear in chronological order."""
        sessions.append_user_message("s1", "Q1")
        sessions.append_assistant_message("s1", "A1")
        sessions.append_user_message("s1", "Q2")
        session = sessions.get_or_create("s1")

        assert [m.role for m in session.messages] == ["user", "assistant", "user"]

    def test_history_trimmed(self, sessions: SessionService) -> None:
        """History beyond max_history should be trimmed."""
        for i in range(10):
            sessions.append_user_message("s1", f"msg-{i}")

        session = sessions.get_or_create("s1")
        assert len(session.messages) == 5
        # Most recent messages kept
        assert session.messages[-1].content == "msg-9"

    def test_updated_at_changes(self, sessions: SessionService) -> None:
        s1 = sessions.get_or_create("s1")
        original = s1.updated_at
        sessions.append_user_message("s1", "test")
        assert s1.updated_at >= original


class TestLastPlan:
    def test_set_and_get_last_plan(self, sessions: SessionService) -> None:
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
        )
        sessions.set_last_plan("s1", plan)

        session = sessions.get_or_create("s1")
        assert session.last_plan is not None
        assert session.last_plan.intent == "test"

    def test_overwrite_last_plan(self, sessions: SessionService) -> None:
        plan1 = QueryPlan(intent="first", table="XXBT_PDKS_PER_DETAILS_V", select_columns=["reg_no"])
        plan2 = QueryPlan(intent="second", table="XXBT_PDKS_PER_DETAILS_V", select_columns=["first_name"])

        sessions.set_last_plan("s1", plan1)
        sessions.set_last_plan("s1", plan2)

        session = sessions.get_or_create("s1")
        assert session.last_plan is not None
        assert session.last_plan.intent == "second"


class TestClear:
    def test_clear_session(self, sessions: SessionService) -> None:
        sessions.append_user_message("s1", "test")
        sessions.clear_session("s1")

        assert sessions.has_session("s1") is False

    def test_clear_nonexistent_is_safe(self, sessions: SessionService) -> None:
        """Clearing a session that doesn't exist should not raise."""
        sessions.clear_session("nonexistent")  # no error


# ---------------------------------------------------------------------------
# Pending clarification
# ---------------------------------------------------------------------------


class TestPendingClarification:
    def test_no_pending_initially(self, sessions: SessionService) -> None:
        """Non-existent session reports no pending clarification."""
        assert sessions.is_pending_clarification("s1") is False

    def test_pending_after_clarification_plan(self, sessions: SessionService) -> None:
        plan = QueryPlan(
            intent="Belirsiz",
            table="XXBT_PDKS_PER_DETAILS_V",
            needs_clarification=True,
            clarification_message="Hangi bilgi?",
        )
        sessions.set_last_plan("s1", plan)

        assert sessions.is_pending_clarification("s1") is True

    def test_not_pending_after_normal_plan(self, sessions: SessionService) -> None:
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
        )
        sessions.set_last_plan("s1", plan)

        assert sessions.is_pending_clarification("s1") is False

    def test_pending_resets_on_new_plan(self, sessions: SessionService) -> None:
        """Clarification pending should reset when a new non-clarification plan arrives."""
        clarification_plan = QueryPlan(
            intent="Belirsiz",
            table="XXBT_PDKS_PER_DETAILS_V",
            needs_clarification=True,
            clarification_message="Hangi bilgi?",
        )
        sessions.set_last_plan("s1", clarification_plan)
        assert sessions.is_pending_clarification("s1") is True

        normal_plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V", select_columns=["reg_no"],
        )
        sessions.set_last_plan("s1", normal_plan)
        assert sessions.is_pending_clarification("s1") is False


# ---------------------------------------------------------------------------
# get_last_plan convenience
# ---------------------------------------------------------------------------


class TestGetLastPlan:
    def test_no_plan_returns_none(self, sessions: SessionService) -> None:
        assert sessions.get_last_plan("s1") is None

    def test_returns_set_plan(self, sessions: SessionService) -> None:
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V", select_columns=["reg_no"],
        )
        sessions.set_last_plan("s1", plan)

        result = sessions.get_last_plan("s1")
        assert result is not None
        assert result.intent == "test"

    def test_returns_none_after_clear(self, sessions: SessionService) -> None:
        plan = QueryPlan(
            intent="test", table="XXBT_PDKS_PER_DETAILS_V", select_columns=["reg_no"],
        )
        sessions.set_last_plan("s1", plan)
        sessions.clear_session("s1")

        assert sessions.get_last_plan("s1") is None
