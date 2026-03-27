"""Centralized pending-clarification state manager.

Provides a structured store for filter-value clarification requests so
that when the user replies (selecting an option, naming a candidate, or
saying "sen karar ver"), the system can detect the pending clarification
and resume the pipeline from the grounding stage onward.

Thread/task-safe via per-session keying.  No external dependencies.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClarificationStatus(str, Enum):
    PENDING = "pending"
    RESOLVED_USER_SELECTED = "resolved_user_selected"
    RESOLVED_USER_DEFERRED = "resolved_user_deferred"
    RESOLVED_AUTO = "resolved_auto"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class PendingClarification:
    """Structured state for a single pending filter-value clarification."""

    clarification_id: str
    session_id: str
    original_question: str
    target_column: str
    target_table: str | None
    original_filter_value: str
    candidates: list[ClarificationCandidate]
    top_candidate: str
    top_score: float
    partial_grounded_plan_json: dict[str, Any]
    stage_paused_at: str = "filter_value_resolution"
    status: ClarificationStatus = ClarificationStatus.PENDING
    created_at: float = field(default_factory=time.time)
    resolved_value: str | None = None
    resolution_method: str | None = None

    @property
    def candidate_values(self) -> list[str]:
        return [c.value for c in self.candidates]

    @property
    def candidate_scores(self) -> list[float]:
        return [c.score for c in self.candidates]


@dataclass(frozen=True)
class ClarificationCandidate:
    """A single candidate in a clarification choice set."""

    value: str
    score: float
    reason: str


@dataclass(frozen=True)
class ClarificationReply:
    """Parsed result of a user reply to a pending clarification."""

    clarification_id: str
    chosen_value: str
    resolution_method: str   # "user_selected_option", "user_named_candidate", "user_deferred_to_system"
    original_question: str
    target_column: str
    target_table: str | None
    partial_grounded_plan_json: dict[str, Any]


# Patterns that indicate user is delegating the choice to the system
_DEFER_PATTERNS = frozenset({
    "sen karar ver",
    "sen sec",
    "sen seç",
    "karar ver",
    "sistem karar versin",
    "otomatik sec",
    "otomatik seç",
    "farketmez",
    "fark etmez",
})


class ClarificationStateManager:
    """In-memory store for pending clarification states, keyed by session."""

    def __init__(self, *, ttl_seconds: float = 600.0) -> None:
        self._store: dict[str, PendingClarification] = {}
        self._ttl = ttl_seconds

    def create_pending(
        self,
        *,
        session_id: str,
        original_question: str,
        target_column: str,
        target_table: str | None,
        original_filter_value: str,
        candidates: list[ClarificationCandidate],
        top_candidate: str,
        top_score: float,
        partial_grounded_plan_json: dict[str, Any],
    ) -> PendingClarification:
        cid = f"clar-{uuid.uuid4().hex[:12]}"
        pending = PendingClarification(
            clarification_id=cid,
            session_id=session_id,
            original_question=original_question,
            target_column=target_column,
            target_table=target_table,
            original_filter_value=original_filter_value,
            candidates=candidates,
            top_candidate=top_candidate,
            top_score=top_score,
            partial_grounded_plan_json=partial_grounded_plan_json,
        )
        self._store[session_id] = pending
        return pending

    def get_pending(self, session_id: str) -> PendingClarification | None:
        pending = self._store.get(session_id)
        if pending is None:
            return None
        if pending.status != ClarificationStatus.PENDING:
            return None
        if time.time() - pending.created_at > self._ttl:
            pending.status = ClarificationStatus.EXPIRED
            return None
        return pending

    def resolve(
        self,
        session_id: str,
        *,
        chosen_value: str,
        resolution_method: str,
    ) -> PendingClarification | None:
        pending = self._store.get(session_id)
        if pending is None or pending.status != ClarificationStatus.PENDING:
            return None
        pending.resolved_value = chosen_value
        pending.resolution_method = resolution_method
        if resolution_method == "user_deferred_to_system":
            pending.status = ClarificationStatus.RESOLVED_USER_DEFERRED
        elif resolution_method in ("user_selected_option", "user_named_candidate"):
            pending.status = ClarificationStatus.RESOLVED_USER_SELECTED
        else:
            pending.status = ClarificationStatus.RESOLVED_AUTO
        return pending

    def clear(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    def interpret_reply(
        self,
        session_id: str,
        user_reply: str,
        *,
        min_auto_resolve_score: float = 0.80,
    ) -> ClarificationReply | None:
        """Parse a user reply against the pending clarification.

        Returns ``None`` if there is no pending clarification or the reply
        cannot be interpreted.

        Supports:
        - Numeric selection ("1", "2", …)
        - Exact candidate name match
        - "sen karar ver" / delegation phrases
        - Recently resolved clarification replay (same value re-sent)
        """
        pending = self.get_pending(session_id)
        if pending is None:
            # Check for recently resolved clarification whose chosen value
            # matches the user's message.  This prevents the pipeline from
            # treating the value as a brand-new query when the user re-sends
            # the same clarification answer via /chat.
            return self._match_recently_resolved(session_id, user_reply)

        stripped = user_reply.strip()
        lowered = stripped.lower().strip()

        # 1) Check delegation phrases
        if lowered in _DEFER_PATTERNS:
            if pending.top_score >= min_auto_resolve_score:
                chosen = pending.top_candidate
                self.resolve(
                    session_id,
                    chosen_value=chosen,
                    resolution_method="user_deferred_to_system",
                )
                return ClarificationReply(
                    clarification_id=pending.clarification_id,
                    chosen_value=chosen,
                    resolution_method="user_deferred_to_system",
                    original_question=pending.original_question,
                    target_column=pending.target_column,
                    target_table=pending.target_table,
                    partial_grounded_plan_json=pending.partial_grounded_plan_json,
                )
            # Top candidate not confident enough → cannot auto-resolve
            return None

        # 2) Numeric selection ("1", "2", …)
        if stripped.isdigit():
            idx = int(stripped) - 1
            if 0 <= idx < len(pending.candidates):
                chosen = pending.candidates[idx].value
                self.resolve(
                    session_id,
                    chosen_value=chosen,
                    resolution_method="user_selected_option",
                )
                return ClarificationReply(
                    clarification_id=pending.clarification_id,
                    chosen_value=chosen,
                    resolution_method="user_selected_option",
                    original_question=pending.original_question,
                    target_column=pending.target_column,
                    target_table=pending.target_table,
                    partial_grounded_plan_json=pending.partial_grounded_plan_json,
                )

        # 3) Exact candidate name match (case-insensitive)
        for candidate in pending.candidates:
            if candidate.value.lower().strip() == lowered:
                self.resolve(
                    session_id,
                    chosen_value=candidate.value,
                    resolution_method="user_named_candidate",
                )
                return ClarificationReply(
                    clarification_id=pending.clarification_id,
                    chosen_value=candidate.value,
                    resolution_method="user_named_candidate",
                    original_question=pending.original_question,
                    target_column=pending.target_column,
                    target_table=pending.target_table,
                    partial_grounded_plan_json=pending.partial_grounded_plan_json,
                )

        # Reply not interpretable as clarification answer
        return None

    # -- Recently-resolved replay helper -----------------------------------

    _RESOLVED_REPLAY_TTL = 300.0  # seconds

    def _match_recently_resolved(
        self,
        session_id: str,
        user_reply: str,
    ) -> ClarificationReply | None:
        """Return a replay ``ClarificationReply`` if the user re-sends a
        value that matches a recently resolved clarification for this session.

        This prevents the pipeline from treating the exact same clarification
        answer as a brand-new query when it arrives via ``/chat`` instead of
        ``/chat/clarify``.
        """
        entry = self._store.get(session_id)
        if entry is None:
            return None
        # Must be resolved (not pending or expired)
        if entry.status not in (
            ClarificationStatus.RESOLVED_USER_SELECTED,
            ClarificationStatus.RESOLVED_USER_DEFERRED,
            ClarificationStatus.RESOLVED_AUTO,
        ):
            return None
        # Must be recent
        if time.time() - entry.created_at > self._RESOLVED_REPLAY_TTL:
            return None
        if entry.resolved_value is None:
            return None

        lowered = user_reply.strip().lower()
        # Match against resolved value or any candidate value
        if lowered == entry.resolved_value.lower():
            return ClarificationReply(
                clarification_id=entry.clarification_id,
                chosen_value=entry.resolved_value,
                resolution_method="replay_resolved",
                original_question=entry.original_question,
                target_column=entry.target_column,
                target_table=entry.target_table,
                partial_grounded_plan_json=entry.partial_grounded_plan_json,
            )
        for candidate in entry.candidates:
            if candidate.value.lower().strip() == lowered:
                return ClarificationReply(
                    clarification_id=entry.clarification_id,
                    chosen_value=candidate.value,
                    resolution_method="replay_resolved",
                    original_question=entry.original_question,
                    target_column=entry.target_column,
                    target_table=entry.target_table,
                    partial_grounded_plan_json=entry.partial_grounded_plan_json,
                )
        return None

    def build_clarification_message(
        self, pending: PendingClarification
    ) -> str:
        """Build a user-friendly multiple-choice clarification message."""
        label = pending.target_column or "filtre"
        lines = [
            f"'{pending.original_filter_value}' ile hangi {label} degerini kastediyorsunuz?"
        ]
        for i, candidate in enumerate(pending.candidates, start=1):
            lines.append(f"{i}. {candidate.value}")
        lines.append(f"{len(pending.candidates) + 1}. Sen karar ver")
        return "\n".join(lines)

    def as_trace_dict(self, pending: PendingClarification) -> dict[str, Any]:
        """Produce a trace-safe dict for Pipeline Live View."""
        return {
            "clarification_id": pending.clarification_id,
            "session_id": pending.session_id,
            "original_question": pending.original_question[:200],
            "target_column": pending.target_column,
            "target_table": pending.target_table,
            "original_filter_value": pending.original_filter_value,
            "candidates": [
                {"value": c.value, "score": round(c.score, 3), "reason": c.reason}
                for c in pending.candidates
            ],
            "top_candidate": pending.top_candidate,
            "top_score": round(pending.top_score, 3),
            "status": pending.status.value,
            "stage_paused_at": pending.stage_paused_at,
            "created_at": pending.created_at,
            "resolved_value": pending.resolved_value,
            "resolution_method": pending.resolution_method,
        }
