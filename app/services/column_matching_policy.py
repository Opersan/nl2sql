"""Column-aware matching policies for filter value resolution.

Each policy type defines matching behaviour, thresholds, and RapidFuzz scorer
preferences appropriate for the *value shape* stored in a column — not for a
specific business domain.  This keeps the system generic and applicable across
all Oracle EBS R12 modules.

Policies (value-shape based)
----------------------------
coded      – Controlled lookup codes, flags, segments, identifiers.
             Near-exact matching; high min_select_score.
freetext   – Free-form text (names, descriptions).  Case / accent
             tolerant; moderate fuzzy with token_sort_ratio.
structured – Structured display labels with separators / prefixes
             (department labels, org names, job titles).
             Token-set-aware; wider ambiguity gap.
default    – Balanced fallback for columns not matched by heuristics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ColumnPolicyType(str, Enum):
    CODED = "coded"
    FREETEXT = "freetext"
    STRUCTURED = "structured"
    DEFAULT = "default"


# Legacy config values → new canonical names (backward compat)
_LEGACY_POLICY_MAP: dict[str, ColumnPolicyType] = {
    "strict_code": ColumnPolicyType.CODED,
    "person_name": ColumnPolicyType.FREETEXT,
    "department_label": ColumnPolicyType.STRUCTURED,
}


@dataclass(frozen=True)
class ColumnPolicy:
    policy_type: ColumnPolicyType
    # Decision thresholds
    min_select_score: float
    min_score_gap: float
    min_fuzzy_ratio: float
    # RapidFuzz scorer preference
    fuzzy_scorer: str  # "WRatio" | "token_sort_ratio" | "token_set_ratio"
    allow_fuzzy: bool
    # Score tiers (deterministic)
    exact_raw_score: float
    exact_folded_score: float
    exact_alias_score: float
    token_match_score: float
    fuzzy_score_base: float
    fuzzy_score_scale: float


# ── Pre-built policy instances ───────────────────────────────────────

CODED = ColumnPolicy(
    policy_type=ColumnPolicyType.CODED,
    min_select_score=0.95,
    min_score_gap=0.10,
    min_fuzzy_ratio=0.90,
    fuzzy_scorer="WRatio",
    allow_fuzzy=True,
    exact_raw_score=1.0,
    exact_folded_score=0.99,
    exact_alias_score=0.97,
    token_match_score=0.88,
    fuzzy_score_base=0.70,
    fuzzy_score_scale=0.25,
)

FREETEXT = ColumnPolicy(
    policy_type=ColumnPolicyType.FREETEXT,
    min_select_score=0.85,
    min_score_gap=0.08,
    min_fuzzy_ratio=0.75,
    fuzzy_scorer="token_sort_ratio",
    allow_fuzzy=True,
    exact_raw_score=1.0,
    exact_folded_score=0.98,
    exact_alias_score=0.96,
    token_match_score=0.90,
    fuzzy_score_base=0.70,
    fuzzy_score_scale=0.25,
)

STRUCTURED = ColumnPolicy(
    policy_type=ColumnPolicyType.STRUCTURED,
    min_select_score=0.85,
    min_score_gap=0.10,
    min_fuzzy_ratio=0.72,
    fuzzy_scorer="token_set_ratio",
    allow_fuzzy=True,
    exact_raw_score=1.0,
    exact_folded_score=0.98,
    exact_alias_score=0.96,
    token_match_score=0.88,
    fuzzy_score_base=0.70,
    fuzzy_score_scale=0.20,
)

DEFAULT_POLICY = ColumnPolicy(
    policy_type=ColumnPolicyType.DEFAULT,
    min_select_score=0.88,
    min_score_gap=0.08,
    min_fuzzy_ratio=0.76,
    fuzzy_scorer="WRatio",
    allow_fuzzy=True,
    exact_raw_score=1.0,
    exact_folded_score=0.98,
    exact_alias_score=0.96,
    token_match_score=0.86,
    fuzzy_score_base=0.70,
    fuzzy_score_scale=0.25,
)

# Legacy aliases for backward compatibility
STRICT_CODE = CODED
PERSON_NAME = FREETEXT
DEPARTMENT_LABEL = STRUCTURED

_POLICIES: dict[ColumnPolicyType, ColumnPolicy] = {
    ColumnPolicyType.CODED: CODED,
    ColumnPolicyType.FREETEXT: FREETEXT,
    ColumnPolicyType.STRUCTURED: STRUCTURED,
    ColumnPolicyType.DEFAULT: DEFAULT_POLICY,
}


# ── Column name → policy inference (Oracle EBS R12 conventions) ──────

# CODED: lookup codes, flags, status, segments, controlled identifiers
_CODED_RE = re.compile(
    r"(_CODE$|_FLAG$|_STATUS$|_TYPE$|_CLASS$|_CURRENCY$|^SEGMENT\d)"
    r"|(MASRAF_MERKEZI|MALIYET_MERKEZI|COST_CENTER|ORG_CODE|ORGANIZATION_CODE"
    r"|ITEM_CODE|STOCK_CODE|STOK_KODU|STAJYER|BORDROLU"
    r"|CURRENCY_CODE|PARA_BIRIMI|STATUS_CODE|DURUM_KODU)",
    re.IGNORECASE,
)

# FREETEXT: person names, vendor/customer/party names, free-form text
_FREETEXT_RE = re.compile(
    r"^(AD|SOYAD|AD_SOYAD|FIRST_NAME|LAST_NAME|FULL_NAME|PERSON_NAME"
    r"|EMPLOYEE_NAME|KISI_ADI|CALISAN_ADI|VENDOR_NAME|SUPPLIER_NAME"
    r"|CUSTOMER_NAME|PARTY_NAME)$",
    re.IGNORECASE,
)

# STRUCTURED: display labels, org names, department titles, locations
_STRUCTURED_RE = re.compile(
    r"(_ADI$|_LABEL$|_DESC$|_DESCRIPTION$)"
    r"|(BIRIM|DEPARTMENT|ORGANIZATION_ADI|ORG_ADI|LOCATION|LOKASYON"
    r"|UNVAN|TITLE|POSITION|GOREV|BOLUM|SUBE)",
    re.IGNORECASE,
)


def get_policy(policy_type: ColumnPolicyType) -> ColumnPolicy:
    """Get the pre-built policy for a given type."""
    return _POLICIES.get(policy_type, DEFAULT_POLICY)


def _resolve_policy_type(raw: str) -> ColumnPolicyType | None:
    """Resolve a raw string to a ColumnPolicyType, supporting legacy names."""
    try:
        return ColumnPolicyType(raw)
    except ValueError:
        return _LEGACY_POLICY_MAP.get(raw)


def infer_column_policy(
    column: str,
    table: str | None = None,
    *,
    explicit_policy: str | None = None,
) -> ColumnPolicy:
    """Determine the matching policy for a column.

    Priority:
    1. Explicit policy from profile config (if provided, includes legacy map)
    2. Column-name pattern matching (Oracle EBS R12 suffix conventions)
    3. Default policy
    """
    # 1. Explicit override from config (supports legacy names)
    if explicit_policy:
        resolved = _resolve_policy_type(explicit_policy)
        if resolved is not None:
            return get_policy(resolved)

    # 2. Column name heuristics (Oracle EBS R12 conventions)
    col = column.strip().upper()
    if _FREETEXT_RE.match(col):
        return FREETEXT
    if _CODED_RE.search(col):
        return CODED
    if _STRUCTURED_RE.search(col):
        return STRUCTURED

    # 3. Default
    return DEFAULT_POLICY
