"""In-memory session manager.

Provides lightweight, per-session state for the chat flow.  Only the
last *max_history* messages are retained; raw SQL traces and large
executor outputs are **not** stored.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.models import Session, SessionMessage
from app.domain.query_plan import QueryPlan


class SessionService:
    """Manage in-memory chat sessions."""

    def __init__(self, *, max_history: int = 10) -> None:
        self._sessions: dict[str, Session] = {}
        self._max_history = max_history

    # -- Public API --------------------------------------------------------

    def get_or_create(self, session_id: str) -> Session:
        """Return an existing session or create a new one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id=session_id)
        return self._sessions[session_id]

    def append_user_message(self, session_id: str, content: str) -> None:
        """Append a user turn and trim history."""
        session = self.get_or_create(session_id)
        session.messages.append(
            SessionMessage(role="user", content=content),
        )
        self._trim(session)
        session.updated_at = datetime.now(timezone.utc)

    def append_assistant_message(self, session_id: str, content: str) -> None:
        """Append an assistant turn and trim history."""
        session = self.get_or_create(session_id)
        session.messages.append(
            SessionMessage(role="assistant", content=content),
        )
        self._trim(session)
        session.updated_at = datetime.now(timezone.utc)

    def set_last_plan(self, session_id: str, plan: QueryPlan) -> None:
        """Store the last successful query plan for the session."""
        session = self.get_or_create(session_id)
        session.last_plan = plan
        session.updated_at = datetime.now(timezone.utc)

    def clear_session(self, session_id: str) -> None:
        """Remove all state for *session_id*."""
        self._sessions.pop(session_id, None)

    def has_session(self, session_id: str) -> bool:
        """Check whether *session_id* exists."""
        return session_id in self._sessions

    def get_last_plan(self, session_id: str) -> QueryPlan | None:
        """Return the last plan for *session_id*, or ``None``."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return session.last_plan

    def is_pending_clarification(self, session_id: str) -> bool:
        """Whether the last plan for *session_id* requested clarification.

        Derived from ``last_plan.needs_clarification`` — no extra state
        is stored.
        """
        plan = self.get_last_plan(session_id)
        if plan is None:
            return False
        return plan.needs_clarification

    # -- Internal -----------------------------------------------------------

    def _trim(self, session: Session) -> None:
        """Keep only the last *max_history* messages."""
        if len(session.messages) > self._max_history:
            session.messages = session.messages[-self._max_history :]
