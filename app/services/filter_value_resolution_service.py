from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.core.logging import get_logger
from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.providers.executor.base import ExecutorProvider
from app.providers.llm.base import LLMProvider
from app.services.clarification_state_manager import (
    ClarificationCandidate,
    ClarificationStateManager,
    PendingClarification,
)
from app.services.filter_value_profile_provider import (
    CanonicalValueEntry,
    FilterValueProfile,
    FilterValueProfileProvider,
    ValueMatchingPolicy,
)
from app.utils.turkish import normalize_for_matching


logger = get_logger(__name__)

_DB_DISTINCT_LIMIT = 200


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token for token in normalize_for_matching(value).split() if token)


@dataclass(frozen=True)
class CandidateMatch:
    canonical_value: str
    score: float
    reason: str


class FilterValueResolutionService:
    def __init__(
        self,
        provider: FilterValueProfileProvider | None = None,
        llm: LLMProvider | None = None,
        clarification_manager: ClarificationStateManager | None = None,
        executor: ExecutorProvider | None = None,
    ) -> None:
        self._provider = provider or FilterValueProfileProvider()
        self._llm = llm
        self._clarification_manager = clarification_manager
        self._executor = executor
        self._db_value_cache: dict[str, list[str]] = {}

    async def resolve(
        self,
        plan: QueryPlan,
        *,
        session_id: str | None = None,
        original_question: str | None = None,
    ) -> tuple[QueryPlan, dict[str, Any]]:
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
                "llm_tiebreak_used": False,
                "pending_clarification": None,
            }

        for filter_spec in plan.filters:
            action, next_filter, changed, clarification_info = await self._resolve_filter(
                filter_spec, policy, plan, session_id=session_id, original_question=original_question,
            )
            actions.append(action)
            updated_filters.append(next_filter)
            any_changed = any_changed or changed
            changed_count += int(changed)
            if clarification_info is not None:
                clarified = plan.model_copy(
                    update={
                        "filters": updated_filters + list(plan.filters[len(updated_filters) :]),
                        "needs_clarification": True,
                        "clarification_message": clarification_info["message"],
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
                    "llm_tiebreak_used": any(a.get("llm_tiebreak_used") for a in actions),
                    "pending_clarification": clarification_info.get("pending_trace"),
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
            "llm_tiebreak_used": any(a.get("llm_tiebreak_used") for a in actions),
            "pending_clarification": None,
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

    async def _resolve_filter(
        self,
        filter_spec: FilterSpec,
        policy: ValueMatchingPolicy,
        plan: QueryPlan,
        *,
        session_id: str | None = None,
        original_question: str | None = None,
    ) -> tuple[dict[str, Any], FilterSpec, bool, dict[str, Any] | None]:
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
            "llm_tiebreak_used": False,
            "llm_tiebreak_result": None,
            "ranking_scores": [],
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

        # ── LIKE surface-value extraction for in-scope columns ──
        like_input = False
        like_surface_value: str | None = None
        effective_value = original_value

        if filter_spec.op == FilterOp.LIKE:
            like_input = True
            like_surface_value = self._extract_like_surface_value(original_value)
            base_action["like_input"] = True
            base_action["like_surface_value_extracted"] = like_surface_value
            if not like_surface_value:
                base_action.update({
                    "reason": "like_surface_extraction_failed",
                    "source": "config_profile",
                    "no_op": True,
                })
                return base_action, filter_spec, False, None
            effective_value = like_surface_value

        normalized_value = normalize_for_matching(effective_value)
        matches = self._match_candidates(profile, normalized_value, policy)
        preview = [candidate.canonical_value for candidate in matches[: policy.candidate_preview_limit]]
        ranking_scores = [
            {"value": m.canonical_value, "score": round(m.score, 3), "reason": m.reason}
            for m in matches[: policy.candidate_preview_limit]
        ]
        base_action.update({
            "source": "config_profile",
            "candidate_values": preview,
            "ranking_scores": ranking_scores,
        })

        if not matches:
            # ── DB fallback: fetch DISTINCT values from the actual database ──
            resolve_table = filter_spec.table or plan.table
            db_values = await self._fetch_db_distinct_values(resolve_table, filter_spec.column)
            if db_values:
                db_profile = self._build_dynamic_profile(
                    resolve_table, filter_spec.column, db_values, profile.supported_ops,
                )
                db_matches = self._match_candidates(db_profile, normalized_value, policy)
                if db_matches:
                    # Re-run same resolution logic with DB-sourced candidates
                    base_action["db_fallback_used"] = True
                    base_action["source"] = "db_distinct"
                    base_action["db_distinct_count"] = len(db_values)
                    matches = db_matches
                    preview = [c.canonical_value for c in matches[: policy.candidate_preview_limit]]
                    ranking_scores = [
                        {"value": m.canonical_value, "score": round(m.score, 3), "reason": m.reason}
                        for m in matches[: policy.candidate_preview_limit]
                    ]
                    base_action["candidate_values"] = preview
                    base_action["ranking_scores"] = ranking_scores
                    # Fall through to normal top-candidate logic below
                else:
                    # DB values exist but none scored — present all DB values for clarification
                    db_candidate_values = db_values[: policy.candidate_preview_limit]
                    db_as_candidates = [
                        CandidateMatch(canonical_value=v, score=0.0, reason="db_canonical_fallback")
                        for v in db_candidate_values
                    ]
                    display_value = effective_value if like_input else original_value
                    clarification_info = self._create_clarification(
                        filter_spec, display_value, db_candidate_values, plan,
                        session_id=session_id, original_question=original_question,
                        reason="no_confident_candidate_clarification",
                        candidates_for_state=db_as_candidates,
                    )
                    base_action.update({
                        "reason": "no_confident_candidate_clarification",
                        "clarification_required": True,
                        "no_op": True,
                        "candidate_values": db_candidate_values,
                        "db_fallback_used": True,
                        "source": "db_distinct",
                        "db_distinct_count": len(db_values),
                    })
                    return base_action, filter_spec, False, clarification_info
            else:
                # No DB values available either — present static profile values
                all_profile_values = [e.value for e in profile.canonical_values[: policy.candidate_preview_limit]]
                all_as_candidates: list[CandidateMatch] | None = (
                    [
                        CandidateMatch(canonical_value=e.value, score=0.0, reason="profile_canonical_fallback")
                        for e in profile.canonical_values[: policy.candidate_preview_limit]
                    ]
                    if profile.canonical_values
                    else None
                )
                display_value = effective_value if like_input else original_value
                clarification_info = self._create_clarification(
                    filter_spec, display_value, all_profile_values, plan,
                    session_id=session_id, original_question=original_question,
                    reason="no_confident_candidate_clarification",
                    candidates_for_state=all_as_candidates,
                )
                base_action.update({
                    "reason": "no_confident_candidate_clarification",
                    "clarification_required": True,
                    "no_op": True,
                    "candidate_values": all_profile_values,
                })
                return base_action, filter_spec, False, clarification_info

        top_candidate = matches[0]
        runner_up = matches[1] if len(matches) > 1 else None
        gap = top_candidate.score - runner_up.score if runner_up is not None else 1.0

        if top_candidate.score < policy.min_select_score:
            clarification_info = self._create_clarification(
                filter_spec, original_value, preview, plan,
                session_id=session_id, original_question=original_question,
                reason="low_confidence_clarification",
                candidates_for_state=matches[:policy.candidate_preview_limit],
                top_candidate_value=top_candidate.canonical_value,
                top_score=top_candidate.score,
            )
            base_action.update(
                {
                    "reason": "low_confidence_clarification",
                    "confidence": round(top_candidate.score, 3),
                    "clarification_required": True,
                    "no_op": True,
                }
            )
            return base_action, filter_spec, False, clarification_info

        if runner_up is not None and gap < policy.min_score_gap:
            # Attempt LLM tie-break if available — narrow set only (top 3 max)
            tiebreak_candidates = matches[:3]
            llm_result = await self._llm_tiebreak(
                original_value, filter_spec.column, tiebreak_candidates,
                original_question=original_question,
            )
            if llm_result is not None:
                base_action["llm_tiebreak_used"] = True
                base_action["llm_tiebreak_result"] = llm_result
                chosen = llm_result.get("chosen_candidate")
                llm_confidence = llm_result.get("confidence", 0.0)
                if chosen and llm_confidence >= policy.min_select_score:
                    # LLM resolved the tie
                    changed = chosen != original_value or like_input
                    llm_update: dict[str, Any] = {"value": chosen}
                    if like_input:
                        llm_update["op"] = FilterOp.EQ
                        base_action["operator_rewritten"] = True
                        base_action["original_operator"] = FilterOp.LIKE.value
                    next_filter = (
                        filter_spec.model_copy(update=llm_update)
                        if changed else filter_spec
                    )
                    base_action.update({
                        "resolved_value": chosen,
                        "reason": "llm_tiebreak_resolved",
                        "confidence": round(llm_confidence, 3),
                        "changed": changed,
                    })
                    return base_action, next_filter, changed, None

            # LLM unavailable or still not confident → structured clarification
            clarification_info = self._create_clarification(
                filter_spec, original_value, preview, plan,
                session_id=session_id, original_question=original_question,
                reason="ambiguous_candidate_clarification",
                candidates_for_state=tiebreak_candidates,
                top_candidate_value=top_candidate.canonical_value,
                top_score=top_candidate.score,
            )
            base_action.update(
                {
                    "reason": "ambiguous_candidate_clarification",
                    "confidence": round(top_candidate.score, 3),
                    "clarification_required": True,
                    "no_op": True,
                }
            )
            return base_action, filter_spec, False, clarification_info

        resolved_value = top_candidate.canonical_value
        changed = resolved_value != original_value or like_input
        update_fields: dict[str, Any] = {"value": resolved_value}
        if like_input:
            update_fields["op"] = FilterOp.EQ
            base_action["operator_rewritten"] = True
            base_action["original_operator"] = FilterOp.LIKE.value
        next_filter = filter_spec if not changed else filter_spec.model_copy(update=update_fields)
        base_action.update(
            {
                "resolved_value": resolved_value,
                "reason": top_candidate.reason if changed else "already_canonical_exact",
                "confidence": round(top_candidate.score, 3),
                "changed": changed,
            }
        )
        return base_action, next_filter, changed, None

    def _create_clarification(
        self,
        filter_spec: FilterSpec,
        original_value: str,
        preview: list[str],
        plan: QueryPlan,
        *,
        session_id: str | None = None,
        original_question: str | None = None,
        reason: str,
        candidates_for_state: list[CandidateMatch] | None = None,
        top_candidate_value: str | None = None,
        top_score: float = 0.0,
    ) -> dict[str, Any]:
        """Create a structured clarification with optional state persistence."""
        mgr = self._clarification_manager
        if mgr is not None and session_id and candidates_for_state:
            clar_candidates = [
                ClarificationCandidate(value=c.canonical_value, score=c.score, reason=c.reason)
                for c in candidates_for_state
            ]
            pending = mgr.create_pending(
                session_id=session_id,
                original_question=original_question or "",
                target_column=filter_spec.column or "",
                target_table=filter_spec.table,
                original_filter_value=original_value,
                candidates=clar_candidates,
                top_candidate=top_candidate_value or (candidates_for_state[0].canonical_value if candidates_for_state else ""),
                top_score=top_score,
                partial_grounded_plan_json=plan.model_dump(mode="json"),
            )
            message = mgr.build_clarification_message(pending)
            return {
                "message": message,
                "pending_trace": mgr.as_trace_dict(pending),
            }

        # Fallback: no state manager available
        message = self._build_clarification_message(
            filter_spec.column, original_value, preview,
            no_match=(reason == "no_confident_candidate_clarification"),
        )
        return {
            "message": message,
            "pending_trace": None,
        }

    async def _llm_tiebreak(
        self,
        user_value: str,
        column: str | None,
        candidates: list[CandidateMatch],
        *,
        original_question: str | None = None,
    ) -> dict[str, Any] | None:
        """Use LLM to break tie among narrowed candidates. Returns None if LLM unavailable."""
        if self._llm is None or not candidates:
            return None
        candidate_list = [c.canonical_value for c in candidates]
        prompt = (
            f"Kullanici sorusu: {original_question or '(bilinmiyor)'}\n"
            f"Filtre kolonu: {column or '(bilinmiyor)'}\n"
            f"Kullanicinin girdigi deger: '{user_value}'\n"
            f"Aday kanonik degerler: {json.dumps(candidate_list, ensure_ascii=False)}\n\n"
            "Yukaridaki adaylardan kullanicinin kastettigine en yakin olani sec.\n"
            "Sonucu SADECE asagidaki JSON formatinda dondur, baska bir sey yazma:\n"
            '{"chosen_candidate": "...", "confidence": 0.0-1.0, "reason": "..."}'
        )
        try:
            from pydantic import BaseModel as _BM

            class _TieBreakResult(_BM):
                chosen_candidate: str
                confidence: float
                reason: str

            result = await self._llm.generate_structured(prompt, _TieBreakResult)
            if result.chosen_candidate in candidate_list:
                return {
                    "chosen_candidate": result.chosen_candidate,
                    "confidence": result.confidence,
                    "reason": result.reason,
                }
            return None
        except Exception:
            return None

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
        *,
        no_match: bool = False,
    ) -> str:
        label = column or "filtre"
        if candidate_values:
            options = ", ".join(candidate_values)
            if no_match:
                return (
                    f"{label} icin '{original_value}' ile eslesen bir deger bulunamadi. "
                    f"Mevcut degerler: {options}. Bunlardan birini mi kastettiginizi belirtir misiniz?"
                )
            return (
                f"{label} için '{original_value}' ifadesi birden fazla değere yakın. "
                f"Lutfen su seceneklerden hangisini kastettiginizi netlestirin: {options}."
            )
        return (
            f"{label} için '{original_value}' ifadesini kanonik bir degerle eslestiremedim. "
            "Lutfen daha net bir deger belirtin."
        )

    async def _fetch_db_distinct_values(self, table: str | None, column: str | None) -> list[str]:
        """Fetch DISTINCT values from the database for a given table.column."""
        if not self._executor or not table or not column:
            return []
        cache_key = f"{table}.{column}"
        if cache_key in self._db_value_cache:
            return self._db_value_cache[cache_key]
        try:
            from app.domain.execution_models import CompiledQuery
            sql = (
                f"SELECT DISTINCT {column} FROM {table} "
                f"WHERE {column} IS NOT NULL AND ROWNUM <= :p1"
            )
            cq = CompiledQuery(sql=sql, params={"p1": _DB_DISTINCT_LIMIT}, table=table)
            result = await self._executor.execute(cq)
            if result.status.value == "success" and result.rows:
                col_key = column.lower()
                values = [str(row.get(col_key, "")) for row in result.rows if row.get(col_key)]
                self._db_value_cache[cache_key] = values
                logger.info(
                    "[filter-value-resolution] DB distinct fetch: %s.%s → %d values",
                    table, column, len(values),
                )
                return values
        except Exception:
            logger.warning(
                "[filter-value-resolution] DB distinct fetch failed for %s.%s",
                table, column, exc_info=True,
            )
        self._db_value_cache[cache_key] = []
        return []

    @staticmethod
    def _build_dynamic_profile(
        table: str | None,
        column: str,
        db_values: list[str],
        supported_ops: frozenset[str],
    ) -> FilterValueProfile:
        """Build a transient FilterValueProfile from DB distinct values."""
        entries = tuple(
            CanonicalValueEntry(
                value=v,
                aliases=(),
                normalized_value=normalize_for_matching(v),
                normalized_aliases=(),
            )
            for v in db_values
        )
        return FilterValueProfile(
            table=table,
            column=column,
            supported_ops=supported_ops,
            canonical_values=entries,
        )

    @staticmethod
    def _extract_like_surface_value(raw_value: str) -> str | None:
        """Strip leading/trailing SQL wildcards from a LIKE pattern.

        Returns the cleaned surface value, or ``None`` if nothing meaningful
        remains after stripping (e.g. bare ``%`` or ``%%``).

        Examples::

            "%dizayn%"  → "dizayn"
            "%Istanbul%" → "Istanbul"
            "IT%"       → "IT"
            "%BT"       → "BT"
            "%%"        → None
            "%"         → None
        """
        stripped = raw_value.strip().strip("%").strip()
        return stripped if stripped else None
