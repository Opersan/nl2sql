"""Query Understanding Pre-Pass.

Deterministic, rule-based analysis of the user question *before* schema
retrieval.  Produces a ``QueryUnderstanding`` dataclass that downstream
stages (retrieval, pruning, prompt building) use to narrow context.

No LLM calls — all logic is keyword / synonym / regex based.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.utils.turkish import casefold_tr

if TYPE_CHECKING:
    from app.semantic.registry import SemanticFoundationRegistry

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class QueryUnderstanding:
    """Structured understanding of a user question."""

    original_question: str
    normalized_question: str

    # Entity / domain detection
    detected_entities: list[str] = field(default_factory=list)
    inferred_modules: list[str] = field(default_factory=list)

    # Output shape
    requested_output_type: str = "unknown"  # list | aggregation | clarification

    # Extracted signals
    extracted_filters: list[dict[str, str]] = field(default_factory=list)
    extracted_time_hints: list[str] = field(default_factory=list)
    extracted_aggregation_hints: list[str] = field(default_factory=list)
    extracted_sort_hints: list[str] = field(default_factory=list)

    # Ambiguity
    possible_ambiguities: list[str] = field(default_factory=list)
    multi_entity_flag: bool = False
    requires_cross_domain_reasoning: bool = False

    # Confidence
    entity_confidence: str = "low"  # high | medium | low

    # Registry-resolved semantic objects (Phase 3+)
    resolved_entities: list[str] = field(default_factory=list)  # entity_ids
    detected_metrics: list[str] = field(default_factory=list)   # metric_ids
    org_context: dict[str, Any] = field(default_factory=dict)
    lookup_hints: list[dict[str, str]] = field(default_factory=list)

    def primary_module(self) -> str | None:
        """Return the single primary module, or None if ambiguous."""
        if len(self.inferred_modules) == 1:
            return self.inferred_modules[0]
        return None

    def as_trace_dict(self) -> dict[str, Any]:
        return {
            "normalized_question": self.normalized_question,
            "detected_entities": self.detected_entities,
            "inferred_modules": self.inferred_modules,
            "resolved_entities": self.resolved_entities,
            "detected_metrics": self.detected_metrics,
            "requested_output_type": self.requested_output_type,
            "extracted_filters": self.extracted_filters,
            "extracted_time_hints": self.extracted_time_hints,
            "extracted_aggregation_hints": self.extracted_aggregation_hints,
            "extracted_sort_hints": self.extracted_sort_hints,
            "possible_ambiguities": self.possible_ambiguities,
            "multi_entity_flag": self.multi_entity_flag,
            "requires_cross_domain_reasoning": self.requires_cross_domain_reasoning,
            "entity_confidence": self.entity_confidence,
            "lookup_hints": self.lookup_hints,
        }


# ---------------------------------------------------------------------------
# Turkish text normalization (diacritic-insensitive)
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Case-fold + strip Turkish diacritics for robust keyword matching."""
    folded = casefold_tr(text)
    return (
        folded.replace("ı", "i")
        .replace("ş", "s")
        .replace("ç", "c")
        .replace("ğ", "g")
        .replace("ö", "o")
        .replace("ü", "u")
    )


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    normed = _norm(text)
    cleaned = _PUNCT_RE.sub(" ", normed)
    return [t for t in cleaned.split() if t]


# ---------------------------------------------------------------------------
# Entity detection is now registry-driven (data/semantic/glossary.jsonl)
#
# _HR_KEYWORDS, _PO_KEYWORDS, _HR_PHRASES, _PO_PHRASES have been REMOVED.
# They are superseded by ``SemanticFoundationRegistry.resolve_term()`` and
# ``resolve_phrases_in_text()`` which cover HR, PO, AP, AR, GL, and INV
# without requiring code changes to add new modules.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Output type detection
# ---------------------------------------------------------------------------

_AGG_KEYWORDS: set[str] = {
    "say", "sayi", "sayisi", "kac", "toplam", "ortalama", "minimum",
    "maksimum", "sum", "count", "avg", "min", "max", "dagilim",
    "dagilimi", "analiz", "analizi", "istatistik", "bazinda", "basina",
    "grupla", "gruplama",
}

_SORT_KEYWORDS: set[str] = {
    "sirala", "siralama", "en son", "en yuksek", "en dusuk", "ilk",
    "son", "order by", "desc", "asc",
}

# ---------------------------------------------------------------------------
# Time hints
# ---------------------------------------------------------------------------

_TIME_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"son (\d+) gun", re.I), "last_n_days"),
    (re.compile(r"son (\d+) ay", re.I), "last_n_months"),
    (re.compile(r"son (\d+) yil", re.I), "last_n_years"),
    (re.compile(r"bugun", re.I), "today"),
    (re.compile(r"bu hafta", re.I), "this_week"),
    (re.compile(r"bu ay", re.I), "this_month"),
    (re.compile(r"gecen ay", re.I), "last_month"),
    (re.compile(r"gecen hafta", re.I), "last_week"),
    (re.compile(r"(\d{4})\s*yili", re.I), "specific_year"),
    (re.compile(r"(\d{4})\s*oncesi", re.I), "before_year"),
    (re.compile(r"(\d+)\s*yil\w*\s*(once|fazla|uzun)", re.I), "n_years_ago"),
]

# ---------------------------------------------------------------------------
# Filter hint extraction (dimension → value hints)
# ---------------------------------------------------------------------------

_LOCATION_RE = re.compile(
    r"\b(istanbul|ankara|izmir|bursa|antalya|adana|konya|gaziantep|kayseri|eskisehir"
    r"|trabzon|samsun|mersin|diyarbakir|sanliurfa|mugla|denizli|sakarya|balikesir"
    r"|malatya|van|elazig|erzurum|manisa|kocaeli|tekirdag|hatay|afyon|usak|bolu"
    r"|aksaray|kirklareli|edirne|canakkale|kutahya|nigde|sivas|tokat|yalova"
    r"|giresun|rize|artvin|ordu|amasya|kastamonu|corum|kirikkale|karaman"
    r"|duzce|burdur|isparta|bilecik|bartin|cankiri|sinop|ardahan|igdir"
    r"|kilis|siirt|bitlis|mus|hakkari|sirnak|tunceli|gumushane|bayburt|agri)\b",
    re.I,
)

_STATUS_KEYWORDS: dict[str, str] = {
    "aktif": "active",
    "pasif": "terminated",
    "kapali": "closed",
    "acik": "open",
    "iptal": "cancelled",
    "approved": "approved",
    "onay bekleyen": "pending_approval",
}

# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------


def analyze_query(
    question: str,
    *,
    registry: "SemanticFoundationRegistry | None" = None,
) -> QueryUnderstanding:
    """Produce a structured understanding of *question* using rules only.

    Parameters
    ----------
    registry:
        Optional ``SemanticFoundationRegistry`` override.  When omitted the
        application singleton is used.  Pass a custom registry in tests.
    """
    normed = _norm(question)
    tokens = _tokenize(question)
    token_set = set(tokens)

    qu = QueryUnderstanding(
        original_question=question,
        normalized_question=normed,
    )

    # --- Entity / module detection via semantic registry ---
    try:
        if registry is None:
            from app.semantic.registry import get_registry as _get_registry
            reg = _get_registry()
        else:
            reg = registry
        _registry_available = True
    except Exception as exc:  # pragma: no cover
        logger.warning("[query-understanding] Registry unavailable: %s", exc)
        reg = None
        _registry_available = False

    entity_scores: dict[str, int] = {}  # entity_id → accumulated score

    if _registry_available and reg is not None:
        # Phrase-level substring matching (stronger signal: +3 each)
        for entry in reg.resolve_phrases_in_text(normed):
            canonical = entry.canonical
            if not canonical.startswith(("filter:", "metric:")):
                entity_scores[canonical] = entity_scores.get(canonical, 0) + 3

        # Token-level exact matching (+1 each)
        for tok in tokens:
            for entry in reg.resolve_term(tok):
                canonical = entry.canonical
                if canonical.startswith("metric:"):
                    metric_id = canonical[len("metric:"):]
                    if metric_id not in qu.detected_metrics:
                        qu.detected_metrics.append(metric_id)
                elif not canonical.startswith("filter:"):
                    entity_scores[canonical] = entity_scores.get(canonical, 0) + 1

        # Populate QU from scored entities (unique, stable order)
        for entity_id, _score in sorted(
            entity_scores.items(), key=lambda kv: kv[1], reverse=True
        ):
            entity = reg.get_entity(entity_id)
            if entity is None:
                continue
            if entity.display_name not in qu.detected_entities:
                qu.detected_entities.append(entity.display_name)
            if entity.module not in qu.inferred_modules:
                qu.inferred_modules.append(entity.module)
            if entity_id not in qu.resolved_entities:
                qu.resolved_entities.append(entity_id)

    # Confidence / multi-entity flags
    if len(qu.inferred_modules) > 1:
        qu.multi_entity_flag = True
        qu.requires_cross_domain_reasoning = True
        qu.entity_confidence = "medium"
    elif qu.inferred_modules:
        dominant = max(entity_scores.values()) if entity_scores else 0
        qu.entity_confidence = "high" if dominant >= 3 else "medium"
    else:
        qu.entity_confidence = "low"
        qu.possible_ambiguities.append("no_domain_signal")

    # --- Output type ---
    agg_hits = token_set & _AGG_KEYWORDS
    if agg_hits:
        qu.requested_output_type = "aggregation"
        qu.extracted_aggregation_hints = sorted(agg_hits)
    else:
        qu.requested_output_type = "list"

    # --- Sort hints ---
    sort_hits = token_set & _SORT_KEYWORDS
    if sort_hits:
        qu.extracted_sort_hints = sorted(sort_hits)

    # --- Time hints ---
    for pat, hint_type in _TIME_PATTERNS:
        m = pat.search(normed)
        if m:
            qu.extracted_time_hints.append(hint_type)

    # --- Filter extraction ---
    # Location
    loc_match = _LOCATION_RE.search(normed)
    if loc_match:
        qu.extracted_filters.append({
            "dimension": "location",
            "value": loc_match.group(1),
            "column_hint": "LOCATION_ADI",
        })

    # Status
    for keyword, status_val in _STATUS_KEYWORDS.items():
        if keyword in normed:
            qu.extracted_filters.append({
                "dimension": "status",
                "value": status_val,
                "column_hint": keyword,
            })

    # Specific value filters (e.g. "vendor_id 501", "BT-01")
    vendor_id_match = re.search(r"tedarikci\w*\s*(?:id|no|numarasi?)?\s*(\d+)", normed)
    if vendor_id_match:
        qu.extracted_filters.append({
            "dimension": "vendor",
            "value": vendor_id_match.group(1),
            "column_hint": "VENDOR_ID",
        })

    # --- Ambiguity detection ---
    if len(tokens) <= 2 and not qu.detected_entities:
        qu.possible_ambiguities.append("too_short_no_entity")
    if not qu.detected_entities and not qu.extracted_filters:
        qu.possible_ambiguities.append("no_entity_no_filter")

    logger.debug(
        "[query-understanding] entities=%s modules=%s output=%s confidence=%s",
        qu.detected_entities,
        qu.inferred_modules,
        qu.requested_output_type,
        qu.entity_confidence,
    )

    return qu
