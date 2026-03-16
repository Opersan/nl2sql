"""Shared type aliases used across the project."""

from __future__ import annotations

from typing import Any, Literal

# Generic row representation coming back from an executor.
Row = dict[str, Any]

# Parameter map for bind variables (:p1, :p2, …).
ParamMap = dict[str, object]

# Chat response statuses used across the application.
ChatStatus = Literal["success", "clarification", "validation_error", "execution_error"]

# Message roles in chat sessions.
MessageRole = Literal["user", "assistant"]
