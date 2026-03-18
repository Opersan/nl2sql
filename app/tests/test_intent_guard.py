from __future__ import annotations

from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.services.intent_guard import (
    build_filter_loss_guard_decision,
    derive_confidence_band,
    extract_filter_signals,
)


def test_extract_filter_signals_detects_strong_status_and_date() -> None:
    msg = "Onay bekleyen siparişleri son 30 gün için listele"
    signals = extract_filter_signals(msg)

    codes = {s["code"] for s in signals}
    assert "status_pending" in codes
    assert "date_window" in codes


def test_filter_loss_guard_blocks_false_success_when_strong_signal_missing() -> None:
    msg = "Onay bekleyen siparişleri listele"
    planner_plan = QueryPlan(
        intent="Onay bekleyen siparişleri listele",
        table="PO_HEADERS_ALL",
        filters=[FilterSpec(column="authorization_status", op=FilterOp.NEQ, value="APPROVED")],
        select_columns=["po_header_id"],
    )
    final_plan = QueryPlan(
        intent="Onay bekleyen siparişleri listele",
        table="PO_HEADERS_ALL",
        filters=[],
        select_columns=["po_header_id"],
    )

    decision = build_filter_loss_guard_decision(
        user_message=msg,
        planner_plan=planner_plan,
        final_plan=final_plan,
    )

    assert decision["false_success_risk"] is True
    assert decision["success_blocked_by_filter_loss"] is True
    assert decision["clarification_reason_code"] == "filter_intent_missing"


def test_weak_wording_does_not_trigger_filter_loss_guard() -> None:
    msg = "Çalışan sayısı nedir"
    planner_plan = QueryPlan(
        intent="Çalışan sayısı",
        table="XXBT_PDKS_PER_DETAILS_V",
        aggregations=[],
        filters=[],
        select_columns=["SICIL_NO"],
    )

    decision = build_filter_loss_guard_decision(
        user_message=msg,
        planner_plan=planner_plan,
        final_plan=planner_plan,
    )

    assert decision["requested_filter_signals"] == []
    assert decision["false_success_risk"] is False
    assert decision["success_blocked_by_filter_loss"] is False


def test_confidence_band_is_rule_derived_not_pseudo_probability() -> None:
    band, confidence = derive_confidence_band(
        needs_clarification=False,
        requested_signals=[{"code": "status_pending", "strength": "strong", "dimension": "status"}],
        coverage={
            "covered_signal_codes": ["status_pending"],
            "missing_signal_codes": [],
            "coverage_ratio": 1.0,
            "strong_signal_count": 1,
        },
    )

    assert band == "high"
    assert confidence == "rule_high"
