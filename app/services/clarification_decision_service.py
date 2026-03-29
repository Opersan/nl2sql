"""Clarification decision stage for planner output."""

from __future__ import annotations

import re
from typing import Any

from app.domain.catalog_models import CatalogSnapshot
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
        *,
        query_understanding: Any | None = None,
        retrieval_diagnostics: Any | None = None,
        semantic_diagnostics: dict[str, Any] | None = None,
        parse_error_taxonomy: str | None = None,
        salvage_applied: bool = False,
        catalog_snapshot: CatalogSnapshot | None = None,
    ) -> ClarificationDecisionResult:
        plan = self._recover_recoverable_clarification(
            resolved_plan,
            query_understanding=query_understanding,
            retrieval_diagnostics=retrieval_diagnostics,
            semantic_diagnostics=semantic_diagnostics,
            parse_error_taxonomy=parse_error_taxonomy,
            salvage_applied=salvage_applied,
            catalog_snapshot=catalog_snapshot,
        )
        plan = self.enforce_aggregation_intent_guard(
            plan,
            user_message,
            query_understanding=query_understanding,
        )

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
            parse_error_taxonomy=parse_error_taxonomy,
            query_understanding=query_understanding,
            retrieval_diagnostics=retrieval_diagnostics,
            semantic_diagnostics=semantic_diagnostics,
        )

        plan_band, plan_conf = derive_confidence_band(
            needs_clarification=planner_plan_snapshot.needs_clarification,
            requested_signals=guard["requested_filter_signals"],
            coverage=guard["planner_filter_coverage"],
            query_understanding=query_understanding,
            retrieval_diagnostics=retrieval_diagnostics,
            semantic_diagnostics=semantic_diagnostics,
            clarification_reason_code=clarification_diag["clarification_reason_code"],
        )
        semantic_band, semantic_conf = derive_confidence_band(
            needs_clarification=plan.needs_clarification,
            requested_signals=guard["requested_filter_signals"],
            coverage=guard["final_filter_coverage"],
            query_understanding=query_understanding,
            retrieval_diagnostics=retrieval_diagnostics,
            semantic_diagnostics=semantic_diagnostics,
            clarification_reason_code=clarification_diag["clarification_reason_code"],
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
        *,
        query_understanding: Any | None = None,
    ) -> QueryPlan:
        if plan.needs_clarification or plan.aggregations or plan.filters or plan.group_by:
            return plan

        if query_understanding is not None:
            requested_output_type = str(getattr(query_understanding, "requested_output_type", "") or "").lower()
            aggregation_hints = list(getattr(query_understanding, "extracted_aggregation_hints", []) or [])
            detected_metrics = list(getattr(query_understanding, "detected_metrics", []) or [])
            if requested_output_type != "aggregation" and not aggregation_hints and not detected_metrics:
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

    def _recover_recoverable_clarification(
        self,
        plan: QueryPlan,
        *,
        query_understanding: Any | None,
        retrieval_diagnostics: Any | None,
        semantic_diagnostics: dict[str, Any] | None,
        parse_error_taxonomy: str | None,
        salvage_applied: bool,
        catalog_snapshot: CatalogSnapshot | None,
    ) -> QueryPlan:
        # Allow recovery for malformed_json (e.g. LLM returned {}) when
        # query_understanding provides strong context.  Other taxonomies
        # (multi_object_response, free_text_instead_of_json) stay blocked.
        _recoverable_taxonomy = parse_error_taxonomy == "malformed_json"
        if not plan.needs_clarification:
            return plan
        if parse_error_taxonomy and not _recoverable_taxonomy:
            return plan
        if salvage_applied:
            return plan

        # When the planner returned {} the plan has no table.  Fall back to
        # the root table identified by semantic resolution if available.
        effective_table = plan.table
        if not effective_table and semantic_diagnostics:
            effective_table = semantic_diagnostics.get("selected_root_table")
        if not effective_table or catalog_snapshot is None:
            return plan

        if plan.select_columns or plan.filters or plan.aggregations or plan.group_by:
            return plan
        if self._is_restricted_clarification_message(plan.clarification_message):
            return plan
        if not self._has_recoverable_listing_context(
            query_understanding=query_understanding,
            retrieval_diagnostics=retrieval_diagnostics,
            semantic_diagnostics=semantic_diagnostics or {},
        ):
            return plan

        default_projection = self._build_default_projection(effective_table, catalog_snapshot)
        if not default_projection:
            return plan

        intent = plan.intent
        requested_output_type = str(getattr(query_understanding, "requested_output_type", "") or "").lower()
        if intent == "clarification_required" and requested_output_type == "list":
            intent = "listele"

        return plan.model_copy(
            update={
                "intent": intent,
                "table": effective_table,
                "select_columns": default_projection,
                "needs_clarification": False,
                "clarification_message": None,
                "clarification_missing_dimensions": [],
            }
        )

    def _has_recoverable_listing_context(
        self,
        *,
        query_understanding: Any | None,
        retrieval_diagnostics: Any | None,
        semantic_diagnostics: dict[str, Any],
    ) -> bool:
        requested_output_type = str(getattr(query_understanding, "requested_output_type", "") or "").lower()
        ambiguities = set(getattr(query_understanding, "possible_ambiguities", []) or [])
        if requested_output_type not in {"", "list"}:
            return False
        if getattr(query_understanding, "multi_entity_flag", False):
            return False
        if {"too_short_no_entity", "no_entity_no_filter"} & ambiguities:
            return False

        semantic_confidence = str(semantic_diagnostics.get("confidence") or "").lower()
        selected_root_table = semantic_diagnostics.get("selected_root_table")
        selected_score = semantic_diagnostics.get("selected_entity_score")
        runner_up_score = semantic_diagnostics.get("runner_up_score")
        if semantic_confidence not in {"high", "medium"} or not selected_root_table:
            return False
        if isinstance(selected_score, int) and isinstance(runner_up_score, int) and selected_score - runner_up_score < 2:
            return False

        dominant_domain_match = (
            retrieval_diagnostics.get("dominant_domain_match")
            if isinstance(retrieval_diagnostics, dict)
            else getattr(retrieval_diagnostics, "dominant_domain_match", None)
        )
        root_table_confidence = str(
            retrieval_diagnostics.get("root_table_confidence", "")
            if isinstance(retrieval_diagnostics, dict)
            else getattr(retrieval_diagnostics, "root_table_confidence", "")
        ).lower()
        if dominant_domain_match is False:
            return False
        if root_table_confidence and root_table_confidence not in {"high", "medium"}:
            return False
        return True

    def _build_default_projection(
        self,
        table_name: str,
        catalog_snapshot: CatalogSnapshot,
    ) -> list[str]:
        table = catalog_snapshot.get_table(table_name)
        if table is None:
            return []

        preferred = (
            "FULL_NAME",
            "SICIL_NO",
            "AD",
            "SOYAD",
            "UNVAN",
            "GOREV_TANIMI",
            "SEGMENT1",
            "AUTHORIZATION_STATUS",
            "CREATION_DATE",
            "LINE_NUM",
            "ITEM_DESCRIPTION",
            "DESCRIPTION",
        )
        available = [col.name for col in table.columns if not col.restricted]
        selected = [name for name in preferred if name in available]
        if selected:
            return selected[:4]

        business_fallback = [
            name for name in available
            if not name.endswith("_ID") and name not in {"ID", "ROWID"}
        ]
        if business_fallback:
            return business_fallback[:4]
        return available[:3]

    def _is_restricted_clarification_message(self, message: str | None) -> bool:
        folded = casefold_tr(message or "")
        return any(
            token in folded
            for token in ("kisitli", "erişime kapali", "erışıme kapali", "güvenlik", "guvenlik", "yasal")
        )