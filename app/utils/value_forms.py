"""Multi-representation value forms for column-aware matching.

Generates comparison forms from a string value.  These forms are used for
matching ONLY — canonical DB values are always preserved for execution/output.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.utils.turkish import casefold_tr

# Separators that may vary between DB representations (hyphen, underscore, etc.)
_SEPARATOR_RE = re.compile(r"[-_/\\.,;:]+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ValueForms:
    """Multiple normalized representations of a single value for matching."""

    raw: str
    casefolded: str  # Turkish-aware casefold (preserves diacritics)
    ascii_folded: str  # casefold + diacritic strip + ı→i
    compact: str  # ascii_folded with separators collapsed to space, stripped
    tokens: tuple[str, ...]  # sorted unique tokens from compact form

    def as_trace_dict(self) -> dict[str, object]:
        """Serialize for trace/observability output."""
        return {
            "raw": self.raw,
            "casefolded": self.casefolded,
            "ascii_folded": self.ascii_folded,
            "compact": self.compact,
            "tokens": list(self.tokens),
        }


def _ascii_fold(text: str) -> str:
    """Turkish-casefold, strip diacritics, collapse ı→i."""
    folded = casefold_tr(text)
    decomposed = unicodedata.normalize("NFKD", folded)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.replace("ı", "i")


def _compact(text: str) -> str:
    """Replace separators with space, collapse whitespace, strip."""
    result = _SEPARATOR_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", result).strip()


def build_value_forms(text: str) -> ValueForms:
    """Build all comparison forms for a value string."""
    raw = text.strip()
    casefolded = casefold_tr(raw)
    ascii_folded = _ascii_fold(raw)
    compact = _compact(ascii_folded)
    tokens = tuple(sorted(set(compact.split()))) if compact else ()
    return ValueForms(
        raw=raw,
        casefolded=casefolded,
        ascii_folded=ascii_folded,
        compact=compact,
        tokens=tokens,
    )
