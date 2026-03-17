from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.domain.query_plan import QueryPlan
from app.utils.turkish import casefold_tr


@dataclass(frozen=True)
class FilterSignalSpec:
    code: str
    label: str
    dimension: str
    keywords: tuple[str, ...]
    strength: str


_SIGNAL_SPECS: tuple[FilterSignalSpec, ...] = (
    FilterSignalSpec("status_active", "aktif durumu", "status", ("aktif", "pasif", "çıkış", "isten ayr", "işten ayr"), "strong"),
    FilterSignalSpec("status_pending", "onay durumu", "status", ("onay bekleyen", "onaysiz", "onaysız", "approved dışı", "approved disi", "bekleyen"), "strong"),
    FilterSignalSpec("date_window", "zaman aralığı", "date", ("tarih aralığı", "between", "date range"), "strong"),
    FilterSignalSpec("org_scope", "organizasyon kapsamı", "org", ("lokasyon", "şehir", "sehir", "birim", "departman", "unvan"), "medium"),
)

_DIMENSION_COLUMN_HINTS: dict[str, tuple[str, ...]] = {
    "status": ("status", "authorization_status", "durum", "cikis_tarihi", "quit_date", "bordrolu", "stajyer"),
    "date": ("date", "tarih", "creation", "effective", "start", "end"),
    "org": ("location", "lokasyon", "birim", "departman", "organization", "unvan"),
}


def _norm(text: str | None) -> str:
    if not text:
        return ""
    folded = casefold_tr(text)
    return (
        folded.replace("ı", "i")
        .replace("ş", "s")
        .replace("ç", "c")
        .replace("ğ", "g")
        .replace("ö", "o")
        .replace("ü", "u")
    )


def extract_filter_signals(user_message: str) -> list[dict[str, str]]:
    folded = _norm(user_message)
    out: list[dict[str, str]] = []
    for spec in _SIGNAL_SPECS:
        if any(k in folded for k in spec.keywords):
            out.append(
                {
                    "code": spec.code,
                    "label": spec.label,
                    "dimension": spec.dimension,
                    "strength": spec.strength,
                }
            )

    # Explicit relative-date windows only; do not trigger on generic words like 'sayi'.
    if re.search(r"\bson\s+\d+\s+(gun|gün|ay|yil|yıl)\b", folded):
        out.append(
            {
                "code": "date_window",
                "label": "zaman aralığı",
                "dimension": "date",
                "strength": "strong",
            }
        )

    # Stable unique list by code
    seen: set[str] = set()
    stable: list[dict[str, str]] = []
    for sig in out:
        code = sig["code"]
        if code in seen:
            continue
        seen.add(code)
        stable.append(sig)
    return stable


def _filter_coverage_for_dimension(plan: QueryPlan, dimension: str) -> bool:
    hints = _DIMENSION_COLUMN_HINTS.get(dimension, ())
    if not hints:
        return False

    for f in plan.filters:
        col = _norm(f.column)
        table = _norm(f.table)
        if any(h in col or (table and h in table) for h in hints):
            return True

    return False


def compute_filter_coverage(plan: QueryPlan, requested_signals: list[dict[str, str]]) -> dict[str, Any]:
    covered: list[str] = []
    missing: list[str] = []

    for sig in requested_signals:
        code = str(sig.get("code") or "")
        dimension = str(sig.get("dimension") or "")
        if _filter_coverage_for_dimension(plan, dimension):
            covered.append(code)
        else:
            missing.append(code)

    strong_count = sum(1 for sig in requested_signals if sig.get("strength") == "strong")
    return {
        "covered_signal_codes": covered,
        "missing_signal_codes": missing,
        "coverage_ratio": (len(covered) / len(requested_signals)) if requested_signals else 1.0,
        "strong_signal_count": strong_count,
    }


def derive_confidence_band(
    *,
    needs_clarification: bool,
    requested_signals: list[dict[str, str]],
    coverage: dict[str, Any],
) -> tuple[str, float]:
    if needs_clarification:
        return "low", 0.35

    strong_count = int(coverage.get("strong_signal_count") or 0)
    missing = len(list(coverage.get("missing_signal_codes") or []))
    ratio = float(coverage.get("coverage_ratio") or 0.0)

    if strong_count >= 1 and missing > 0:
        return "low", 0.4
    if ratio >= 0.8:
        return "high", 0.88
    if requested_signals:
        return "medium", 0.65
    return "high", 0.9


def build_filter_loss_guard_decision(
    *,
    user_message: str,
    planner_plan: QueryPlan,
    final_plan: QueryPlan,
) -> dict[str, Any]:
    requested = extract_filter_signals(user_message)
    planner_cov = compute_filter_coverage(planner_plan, requested)
    final_cov = compute_filter_coverage(final_plan, requested)

    strong_signals = [s for s in requested if s.get("strength") == "strong"]
    false_success_risk = bool(strong_signals and final_cov["missing_signal_codes"])
    blocked = bool(false_success_risk and not final_plan.needs_clarification)

    return {
        "requested_filter_signals": requested,
        "planner_filter_coverage": planner_cov,
        "final_filter_coverage": final_cov,
        "false_success_risk": false_success_risk,
        "success_blocked_by_filter_loss": blocked,
        "clarification_reason_code": "filter_intent_missing" if blocked else None,
        "clarification_missing_dimensions": [
            sig.get("dimension")
            for sig in requested
            if sig.get("code") in set(final_cov["missing_signal_codes"])
        ],
    }
