"""Service wrapper for the deterministic query-understanding stage."""

from __future__ import annotations

from app.services.query_understanding import QueryUnderstanding, analyze_query


class QueryUnderstandingService:
    """Run the query-understanding pre-pass."""

    def analyze(self, user_message: str) -> QueryUnderstanding:
        return analyze_query(user_message)