from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.domain.query_plan import FilterSpec, QueryPlan
from app.services.filter_value_profile_provider import (
    CanonicalValueEntry,
    FilterValueProfile,
    FilterValueProfileProvider,
    ValueMatchingPolicy,
)
from app.utils.turkish import normalize_for_matching


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token for token in normalize_for_matching(value).split() if token)


@dataclass(frozen=True)
class CandidateMatch:
    canonical_value: str
    score: float
    reason: str


class FilterValueResolutionService:
    def __init__(self, provider: FilterValueProfileProvider | None = None) -> None:
        self._provider = provider or FilterValueProfileProvider()

    def resolve(self, plan: QueryPlan) -> tuple[QueryPlan, dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        updated_filters: list[FilterSpec] = []
        any_changed = False
        changed_count = 0

        policy = self._provider.policy()

        if not plan.filters:
            return plan, {
                "actions": [],
                "any_changed": False,
                "changed_count": 0,
                "changed_filters": 0,
                "processed_filters": 0,
                "skipped_filters": 0,
                "total_filters": 0,
                "total_filters_seen": 0,
                "skip_reasons": {},
                "changed_items": [],
                "original_filters": [],
                "final_filters": [],
                "clarification_required": False,
            }

        for filter_spec in plan.filters:
            action, next_filter, changed, clarification = self._resolve_filter(filter_spec, policy)
            actions.append(action)
            updated_filters.append(next_filter)
            any_changed = any_changed or changed
            changed_count += int(changed)
            if clarification is not None:
                clarified = plan.model_copy(
                    update={
                        "filters": updated_filters + list(plan.filters[len(updated_filters) :]),
                        "needs_clarification": True,
                        "clarification_message": clarification,
                    }
                )
                return clarified, {
                    "actions": actions,
                    "any_changed": any_changed,
                    "changed_count": changed_count,
                    "changed_filters": changed_count,
                    "processed_filters": len(actions),
                    "skipped_filters": len(actions) - changed_count,
                    "total_filters": len(plan.filters),
                    "total_filters_seen": len(plan.filters),
                    "skip_reasons": self._summarize_skip_reasons(actions),
                    "changed_items": self._summarize_changed_items(actions),
                    "original_filters": self._serialize_filters(plan.filters),
                    "final_filters": self._serialize_filters(clarified.filters),
                    "clarification_required": True,
                }

        resolved_plan = plan.model_copy(update={"filters": updated_filters}) if any_changed else plan
        return resolved_plan, {
            "actions": actions,
            "any_changed": any_changed,
            "changed_count": changed_count,
            "changed_filters": changed_count,
            "processed_filters": len(actions),
            "skipped_filters": len(actions) - changed_count,
            "total_filters": len(plan.filters),
            "total_filters_seen": len(plan.filters),
            "skip_reasons": self._summarize_skip_reasons(actions),
            "changed_items": self._summarize_changed_items(actions),
            "original_filters": self._serialize_filters(plan.filters),
            "final_filters": self._serialize_filters(resolved_plan.filters),
            "clarification_required": False,
        }

    @staticmethod
    def _serialize_filters(filters: list[FilterSpec]) -> list[dict[str, Any]]:
        return [
            {
                "table": filter_spec.table,
                "column": filter_spec.column,
                "operator": filter_spec.op.value,
                "value": filter_spec.value,
            }
            for filter_spec in filters
        ]

    @staticmethod
    def _summarize_skip_reasons(actions: list[dict[str, Any]]) -> dict[str, int]:
        reasons: dict[str, int] = {}
        for action in actions:
            if action.get("changed"):
                continue
            reason = str(action.get("reason") or "unknown_no_op")
            reasons[reason] = reasons.get(reason, 0) + 1
        return reasons

    @staticmethod
    def _summarize_changed_items(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for action in actions:
            if not action.get("changed"):
                continue
            items.append(
                {
                    "column": action.get("column"),
                    "original_value": action.get("original_value"),
                    "resolved_value": action.get("resolved_value"),
                    "reason": action.get("reason"),
                }
            )
        return items

    def _resolve_filter(
        self,
        filter_spec: FilterSpec,
        policy: ValueMatchingPolicy,
    ) -> tuple[dict[str, Any], FilterSpec, bool, str | None]:
        original_value = filter_spec.value
        base_action = {
            "table": filter_spec.table,
            "column": filter_spec.column,
            "operator": filter_spec.op.value,
            "original_value": original_value,
            "resolved_value": original_value,
            "normalized_value": normalize_for_matching(original_value) if isinstance(original_value, str) else None,
            "source": None,
            "reason": None,
            "confidence": None,
            "changed": False,
            "no_op": False,
            "clarification_required": False,
            "candidate_values": [],
        }

        if not isinstance(original_value, str) or not original_value.strip():
            base_action.update({"reason": "non_string_value_no_op", "no_op": True})
            return base_action, filter_spec, False, None

        profile = self._provider.get_profile(filter_spec.table, filter_spec.column)
        if profile is None:
            base_action.update({"reason": "out_of_scope_column_no_op", "no_op": True})
            return base_action, filter_spec, False, None

        if filter_spec.op not in profile.supported_ops:
            base_action.update(
                {
                    "reason": "unsupported_operator_no_op",
                    "source": "config_profile",
                    "no_op": True,
                }
            )
            return base_action, filter_spec, False, None

        normalized_value = normalize_for_matching(original_value)
        matches = self._match_candidates(profile, normalized_value, policy)
        preview = [candidate.canonical_value for candidate in matches[: policy.candidate_preview_limit]]
        base_action.update({"source": "config_profile", "candidate_values": preview})

        if not matches:
            clarification = self._build_clarification_message(filter_spec.column, original_value, preview)
            base_action.update(
                {
                    "reason": "no_confident_candidate_clarification",
                    "clarification_required": True,
                    "no_op": True,
                }
            )
            return base_action, filter_spec, False, clarification

        top_candidate = matches[0]
        runner_up = matches[1] if len(matches) > 1 else None
        gap = top_candidate.score - runner_up.score if runner_up is not None else 1.0

        if top_candidate.score < policy.min_select_score:
            clarification = self._build_clarification_message(filter_spec.column, original_value, preview)
            base_action.update(
                {
                    "reason": "low_confidence_clarification",
                    "confidence": round(top_candidate.score, 3),
                    "clarification_required": True,
                    "no_op": True,
                }
            )
            return base_action, filter_spec, False, clarification

        if runner_up is not None and gap < policy.min_score_gap:
            clarification = self._build_clarification_message(filter_spec.column, original_value, preview)
            base_action.update(
                {
                    "reason": "ambiguous_candidate_clarification",
                    "confidence": round(top_candidate.score, 3),
                    "clarification_required": True,
                    "no_op": True,
                }
            )
            return base_action, filter_spec, False, clarification

        resolved_value = top_candidate.canonical_value
        changed = resolved_value != original_value
        next_filter = filter_spec if not changed else filter_spec.model_copy(update={"value": resolved_value})
        base_action.update(
            {
                "resolved_value": resolved_value,
                "reason": top_candidate.reason if changed else "already_canonical_exact",
                "confidence": round(top_candidate.score, 3),
                "changed": changed,
            }
        )
        return base_action, next_filter, changed, None

    def _match_candidates(
        self,
        profile: FilterValueProfile,
        normalized_value: str,
        policy: ValueMatchingPolicy,
    ) -> list[CandidateMatch]:
        matches: list[CandidateMatch] = []
        for entry in profile.canonical_values:
            match = self._score_entry(entry, normalized_value, policy)
            if match is not None:
                matches.append(match)
        return sorted(matches, key=lambda item: (-item.score, item.canonical_value))

    def _score_entry(
        self,
        entry: CanonicalValueEntry,
        normalized_value: str,
        policy: ValueMatchingPolicy,
    ) -> CandidateMatch | None:
        if normalized_value == entry.normalized_value:
            return CandidateMatch(
                canonical_value=entry.value,
                score=policy.exact_canonical_score,
                reason="exact_canonical_match",
            )

        if normalized_value in entry.normalized_aliases:
            return CandidateMatch(
                canonical_value=entry.value,
                score=policy.exact_alias_score,
                reason="exact_alias_match",
            )

        query_tokens = set(_tokenize(normalized_value))
        best_token_score = 0.0
        best_token_reason: str | None = None
        best_fuzzy_ratio = 0.0
        for form in entry.all_normalized_forms:
            form_tokens = set(_tokenize(form))
            if query_tokens and form_tokens:
                if query_tokens.issubset(form_tokens):
                    score = policy.token_subset_score + min(0.05, 0.01 * len(query_tokens))
                    if score > best_token_score:
                        best_token_score = score
                        best_token_reason = "token_subset_match"
                else:
                    overlap = len(query_tokens & form_tokens) / max(len(query_tokens | form_tokens), 1)
                    if overlap >= 0.5:
                        score = policy.token_overlap_score + min(0.05, 0.05 * overlap)
                        if score > best_token_score:
                            best_token_score = score
                            best_token_reason = "token_overlap_match"
            ratio = SequenceMatcher(None, normalized_value, form).ratio()
            if ratio > best_fuzzy_ratio:
                best_fuzzy_ratio = ratio

        fuzzy_score = 0.0
        if best_fuzzy_ratio >= policy.min_fuzzy_ratio:
            fuzzy_score = policy.fuzzy_score_base + (best_fuzzy_ratio * policy.fuzzy_score_scale)

        if best_token_score >= fuzzy_score and best_token_reason is not None:
            return CandidateMatch(
                canonical_value=entry.value,
                score=min(best_token_score, 0.95),
                reason=best_token_reason,
            )

        if fuzzy_score > 0.0:
            return CandidateMatch(
                canonical_value=entry.value,
                score=min(fuzzy_score, 0.95),
                reason="fuzzy_match",
            )

        return None

    def _build_clarification_message(
        self,
        column: str | None,
        original_value: str,
        candidate_values: list[str],
    ) -> str:
        label = column or "filtre"
        if candidate_values:
            options = ", ".join(candidate_values)
            return (
                f"{label} için '{original_value}' ifadesi birden fazla değere yakın. "
                f"Lutfen su seceneklerden hangisini kastettiginizi netlestirin: {options}."
            )
        return (
            f"{label} için '{original_value}' ifadesini kanonik bir degerle eslestiremedim. "
            "Lutfen daha net bir deger belirtin."
        )
