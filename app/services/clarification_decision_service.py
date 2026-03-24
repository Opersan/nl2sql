"""Clarification decision stage for planner output."""

from __future__ import annotations

import re

from app.domain.query_plan import QueryPlan
from app.services.intent_guard import (
    build_filter_loss_guard_decision,
    derive_clarification_diagnostics,
    derive_confidence_band,
)
from app.services.planning_models import ClarificationDecision, ClarificationDecisionResult
from app.utils.turkish import casefold_tr


class ClarificationDecisionService:
    """Apply deterministic clarification decisions after semantic resolution."""

    def apply(
        self,
        user_message: str,
        planner_plan_snapshot: QueryPlan,
        resolved_plan: QueryPlan,
    ) -> ClarificationDecisionResult:
        plan = self.enforce_aggregation_intent_guard(resolved_plan, user_message)

        guard = build_filter_loss_guard_decision(
            user_message=user_message,
            planner_plan=planner_plan_snapshot,
            final_plan=plan,
        )
        if guard["success_blocked_by_filter_loss"]:
            missing_dims = list(dict.fromkeys(guard["clarification_missing_dimensions"]))
            dim_label = ", ".join(missing_dims) if missing_dims else "filtre boyutu"
            plan = plan.model_copy(
                update={
                    "intent": "clarification_required",
                    "needs_clarification": True,
                    "clarification_message": (
                        f"Sorguda {dim_label} net ama plan bu filtreyi güvenilir şekilde koruyamadı. "
                        "Lütfen ilgili filtreyi açıkça belirtin."
                    ),
                    "clarification_missing_dimensions": missing_dims,
                    "select_columns": [],
                    "filters": [],
                    "aggregations": [],
                    "group_by": [],
                    "order_by": [],
                }
            )

        clarification_diag = derive_clarification_diagnostics(
            planner_plan=planner_plan_snapshot,
            final_plan=plan,
            guard_decision=guard,
        )

        plan_band, plan_conf = derive_confidence_band(
            needs_clarification=planner_plan_snapshot.needs_clarification,
            requested_signals=guard["requested_filter_signals"],
            coverage=guard["planner_filter_coverage"],
        )
        semantic_band, semantic_conf = derive_confidence_band(
            needs_clarification=plan.needs_clarification,
            requested_signals=guard["requested_filter_signals"],
            coverage=guard["final_filter_coverage"],
        )

        return ClarificationDecisionResult(
            plan=plan,
            decision=ClarificationDecision(
                requested_filter_signals=list(guard["requested_filter_signals"]),
                planner_filter_coverage=dict(guard["planner_filter_coverage"]),
                final_filter_coverage=dict(guard["final_filter_coverage"]),
                false_success_risk=guard["false_success_risk"],
                success_blocked_by_filter_loss=guard["success_blocked_by_filter_loss"],
                clarification_reason_code=clarification_diag["clarification_reason_code"],
                clarification_missing_dimensions=list(clarification_diag["clarification_missing_dimensions"]),
                clarification_was_avoidable=clarification_diag["clarification_was_avoidable"],
                plan_confidence=plan_conf,
                semantic_confidence=semantic_conf,
                confidence_band=semantic_band,
                plan_confidence_band=plan_band,
            ),
        )

    def enforce_aggregation_intent_guard(
        self,
        plan: QueryPlan,
        user_message: str,
    ) -> QueryPlan:
        if plan.needs_clarification or plan.aggregations or plan.filters or plan.group_by:
            return plan

        folded = casefold_tr(user_message or "")
        tokens = [token for token in re.split(r"\s+", folded.strip()) if token]

        domain_tokens = ("calisan", "çalışan", "personel", "siparis", "sipariş", "po")
        if len(tokens) <= 2 and any(token in folded for token in domain_tokens):
            return plan

        filter_or_date_tokens = (
            "daki", "deki", "ndaki", "indeki", "olan", "içeren", "iceren",
            "açık", "acik", "aktif", "onay", "bekleyen",
            "bugün", "bugun", "dün", "dun", "tarih", "gün", "gun", "hafta", "yıl", "yil",
        )
        if any(token in folded for token in filter_or_date_tokens):
            return plan

        broad_tokens = (
            "kaç", "kac", "sayı", "sayi", "adedi", "toplam", "ortalama",
            "dağılım", "dagilim", "bazında", "bazinda", "count", "sum", "avg",
        )
        measure_tokens = (
            "kaç", "kac", "sayısı", "sayisi", "adedi", "count",
            "toplam", "sum", "ortalama", "average", "avg", "miktar", "quantity",
        )
        if not any(token in folded for token in broad_tokens) or not any(token in folded for token in measure_tokens):
            return plan

        return plan.model_copy(
            update={
                "intent": "clarification_required",
                "needs_clarification": True,
                "clarification_message": "Bu sorgu bir toplama/hesaplama isteği gibi görünüyor. Hangi metrik ve hangi kırılımı istediğinizi netleştirir misiniz?",
                "select_columns": [],
                "filters": [],
                "aggregations": [],
                "group_by": [],
                "order_by": [],
            },
        )