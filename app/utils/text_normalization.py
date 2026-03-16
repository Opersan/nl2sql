"""Text normalisation utilities for identifiers and aliases."""

from __future__ import annotations

import re

from app.utils.turkish import casefold_tr

# Pre-compiled patterns --------------------------------------------------

_MULTI_WS = re.compile(r"\s+")
_NON_IDENTIFIER = re.compile(r"[^a-zA-Z0-9_çğıöşüÇĞİÖŞÜ]")


def normalize_whitespace(text: str) -> str:
    """Collapse consecutive whitespace into a single space and strip."""
    return _MULTI_WS.sub(" ", text).strip()


def normalize_user_input(text: str) -> str:
    """Strip and collapse whitespace in raw user input."""
    return normalize_whitespace(text)


def safe_identifier(text: str) -> str:
    """Turn arbitrary text into a safe SQL-style identifier.

    * Strips leading/trailing whitespace.
    * Replaces non-identifier characters with underscores.
    * Collapses consecutive underscores.
    * Lower-cases (Turkish-aware).
    """
    cleaned = normalize_whitespace(text)
    cleaned = _NON_IDENTIFIER.sub("_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return casefold_tr(cleaned)


def alias_match(candidate: str, target: str) -> bool:
    """Return True when *candidate* and *target* are considered the same alias.

    Comparison is whitespace-normalised and Turkish case-folded.
    """
    return casefold_tr(normalize_whitespace(candidate)) == casefold_tr(
        normalize_whitespace(target)
    )


def contains_any(text: str, keywords: list[str]) -> bool:
    """Check whether *text* contains **any** of *keywords* (Turkish case-insensitive)."""
    folded = casefold_tr(text)
    return any(casefold_tr(kw) in folded for kw in keywords)


def truncate(text: str, max_length: int = 200) -> str:
    """Truncate *text* to *max_length* characters with an ellipsis."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"
