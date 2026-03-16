"""Custom exception hierarchy for the NL2SQL assistant."""

from __future__ import annotations


class NL2SQLError(Exception):
    """Base exception for the NL2SQL assistant."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class CatalogError(NL2SQLError):
    """Raised when the catalog subsystem encounters an unrecoverable error."""


class ValidationErrorInternal(NL2SQLError):
    """Raised for internal validation-service errors (not user-facing validation failures)."""


class CompilationError(NL2SQLError):
    """Raised when the SQL compiler cannot produce a valid query."""


class ExecutionError(NL2SQLError):
    """Raised when the query executor encounters an error."""


class PlannerError(NL2SQLError):
    """Raised when the LLM planner cannot produce a valid query plan."""


class NarratorError(NL2SQLError):
    """Raised when the narrator fails to generate a response."""


class MetadataLoadError(NL2SQLError):
    """Raised when metadata ingestion encounters an unreadable source."""


class DocumentLoadError(NL2SQLError):
    """Raised when document corpus loading encounters an unreadable or malformed source."""


class RetrievalError(NL2SQLError):
    """Raised when schema retrieval fails."""
