"""Turkish-aware text helpers.

Python's built-in ``str.casefold()`` does not handle the Turkish İ/ı and I/i
distinction correctly.  These helpers provide a minimal, dependency-free
solution for case-insensitive comparison of identifiers and aliases.
"""

from __future__ import annotations

import unicodedata

# Turkish-specific case-mapping pairs that Python's casefold misses.
_TR_UPPER_TO_LOWER: dict[str, str] = {
    "İ": "i",
    "I": "ı",
}

_TR_LOWER_TO_UPPER: dict[str, str] = {
    "i": "İ",
    "ı": "I",
}


def casefold_tr(text: str) -> str:
    """Turkish-aware case-fold.

    Applies Turkish I/İ mapping *before* the standard casefold so that
    comparisons like ``casefold_tr("İSİM") == casefold_tr("isim")`` hold.
    """
    result: list[str] = []
    for ch in text:
        mapped = _TR_UPPER_TO_LOWER.get(ch)
        if mapped is not None:
            result.append(mapped)
        else:
            result.append(ch.lower())
    return "".join(result)


def upper_tr(text: str) -> str:
    """Turkish-aware upper-case."""
    result: list[str] = []
    for ch in text:
        mapped = _TR_LOWER_TO_UPPER.get(ch)
        if mapped is not None:
            result.append(mapped)
        else:
            result.append(ch.upper())
    return "".join(result)


def normalize_text(text: str) -> str:
    """Normalize whitespace and strip a string, then Turkish-casefold.

    Useful for fuzzy identifier / alias matching.
    """
    return casefold_tr(" ".join(text.split()))


def normalize_for_matching(text: str) -> str:
    """Normalize text for robust Turkish-insensitive keyword/value matching."""
    normalized = normalize_text(text)
    decomposed = unicodedata.normalize("NFKD", normalized)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    collapsed = stripped.replace("ı", "i")
    return " ".join(collapsed.split())


def eq_tr(a: str, b: str) -> bool:
    """Turkish-aware case-insensitive equality."""
    return casefold_tr(a) == casefold_tr(b)
