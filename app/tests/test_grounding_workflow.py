"""Tests for the full grounding workflow — Sprint 2 expansion.

Covers:
1. Canonical candidate generation for scoped columns
2. Deterministic ranking works before LLM tie-break
3. LLM tie-break only receives narrowed candidate list
4. Clarification is generated when ambiguity remains
5. User selects a specific option
6. User says "sen karar ver"
7. Too-low-confidence top candidate does NOT auto-resolve on "sen karar ver"
8. Out-of-scope flag/date/null filters remain unchanged
9. Pipeline Live View shows grounding workflow stages
10. No inline hardcoded value dicts
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.services import filter_value_resolution_service as fvr_module
from app.services.clarification_state_manager import (
    ClarificationCandidate,
    ClarificationStateManager,
    ClarificationStatus,
    PendingClarification,
)
from app.services.filter_value_profile_provider import FilterValueProfileProvider
from app.services.filter_value_resolution_service import (
    CandidateMatch,
    FilterValueResolutionService,
)
from app.services.trace_serializer import build_filter_value_resolution_payload


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_plan(filters: list[FilterSpec]) -> QueryPlan:
    return QueryPlan(
        intent="employee_list",
        table="XXBT_PDKS_PER_DETAILS_V",
        filters=filters,
    )


def _filter(column: str, value: Any, op: FilterOp = FilterOp.EQ) -> FilterSpec:
    return FilterSpec(column=column, op=op, value=value)


def _profiles_json() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "matching_policy": {
            "candidate_preview_limit": 5,
            "min_select_score": 0.88,
            "min_score_gap": 0.08,
            "min_fuzzy_ratio": 0.76,
            "min_auto_resolve_score": 0.80,
            "exact_canonical_score": 1.0,
            "exact_alias_score": 0.96,
            "token_subset_score": 0.86,
            "token_overlap_score": 0.8,
            "fuzzy_score_base": 0.7,
            "fuzzy_score_scale": 0.25,
        },
        "profiles": {
            "XXBT_PDKS_PER_DETAILS_V.BIRIM_ADI": {
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "column": "BIRIM_ADI",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "Bilgi Teknolojileri", "aliases": ["it", "bt", "bilgi teknolojileri"]},
                    {"value": "İnsan Kaynakları", "aliases": ["ik", "insan kaynaklari"]},
                    {"value": "Muhasebe", "aliases": ["muhasebe", "accounting"]},
                    {"value": "ELEKTRIK DIZAYN", "aliases": ["elektrik dizayn"]},
                    {"value": "MEKANIK DIZAYN", "aliases": ["mekanik dizayn"]},
                ],
            },
            "XXBT_PDKS_PER_DETAILS_V.LOCATION_ADI": {
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "column": "LOCATION_ADI",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "ISTANBUL BURO", "aliases": ["istanbul", "ist"]},
                    {"value": "Ankara", "aliases": ["ankara"]},
                ],
            },
            "XXBT_PDKS_PER_DETAILS_V.UNVAN": {
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "column": "UNVAN",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "Proje Yöneticisi", "aliases": ["yonetici", "manager"]},
                    {"value": "Sistem Yöneticisi", "aliases": ["yonetici", "sysadmin"]},
                    {"value": "Yazılım Uzmanı", "aliases": ["yazilim uzmani", "uzman"]},
                ],
            },
            "XXBT_PDKS_PER_DETAILS_V.MASRAF_MERKEZI": {
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "column": "MASRAF_MERKEZI",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "BT-01", "aliases": ["bt01", "bt 01"]},
                    {"value": "BT-02", "aliases": ["bt02", "bt 02"]},
                ],
            },
            "XXBT_PDKS_PER_DETAILS_V.ORGANIZATION_ADI": {
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "column": "ORGANIZATION_ADI",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "Genel Müdürlük", "aliases": ["genel mudurluk", "merkez"]},
                ],
            },
        },
    }


def _provider_from_dict(data: dict[str, Any]) -> FilterValueProfileProvider:
    with TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "filter_value_profiles.json"
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        provider = FilterValueProfileProvider(config_path=cfg_path)
        provider.policy()
        return provider


def _build_svc(
    *,
    llm: Any | None = None,
    clarification_manager: ClarificationStateManager | None = None,
    executor: Any | None = None,
) -> FilterValueResolutionService:
    return FilterValueResolutionService(
        provider=_provider_from_dict(_profiles_json()),
        llm=llm,
        clarification_manager=clarification_manager,
        executor=executor,
    )


def _make_mock_executor(column: str, values: list[str]) -> AsyncMock:
    """Create an AsyncMock executor that returns DISTINCT-style rows.

    Column keys are **lowercased** to match real OracleExecutor behaviour.
    """
    from app.domain.execution_models import ExecutionResult, ExecutionStatus
    mock = AsyncMock()
    rows = [{column.lower(): v} for v in values]
    mock.execute.return_value = ExecutionResult(
        status=ExecutionStatus.SUCCESS,
        columns=[column.lower()],
        rows=rows,
        row_count=len(rows),
        execution_time_ms=5,
    )
    return mock


# ── 1. Canonical candidate generation ────────────────────────────────

class TestCandidateGeneration:
    async def test_birim_adi_generates_candidates_from_central_source(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "IT")])

        resolved, trace = await svc.resolve(plan)

        assert resolved.filters[0].value == "Bilgi Teknolojileri"
        assert trace["actions"][0]["source"] == "config_profile"
        assert "Bilgi Teknolojileri" in trace["actions"][0]["candidate_values"]

    async def test_location_adi_generates_candidates(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("LOCATION_ADI", "istanbul")])

        resolved, trace = await svc.resolve(plan)

        assert resolved.filters[0].value == "ISTANBUL BURO"
        assert trace["actions"][0]["source"] == "config_profile"

    async def test_organization_adi_generates_candidates(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("ORGANIZATION_ADI", "merkez")])

        resolved, trace = await svc.resolve(plan)

        assert resolved.filters[0].value == "Genel Müdürlük"

    async def test_masraf_merkezi_generates_candidates(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("MASRAF_MERKEZI", "bt01")])

        resolved, trace = await svc.resolve(plan)

        assert resolved.filters[0].value == "BT-01"


# ── 2. Deterministic ranking before LLM tie-break ────────────────────

class TestDeterministicRanking:
    async def test_candidates_ranked_by_score(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "IT")])

        _, trace = await svc.resolve(plan)

        scores = trace["actions"][0].get("ranking_scores", [])
        assert len(scores) >= 1
        assert scores[0]["value"] == "Bilgi Teknolojileri"
        assert scores[0]["score"] >= 0.9

    async def test_exact_alias_has_higher_score_than_fuzzy(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "muhasebe")])

        resolved, trace = await svc.resolve(plan)

        assert resolved.filters[0].value == "Muhasebe"
        scores = trace["actions"][0].get("ranking_scores", [])
        assert scores[0]["score"] >= 0.95


# ── 3. LLM tie-break receives only narrowed candidates ──────────────

class TestLLMTieBreak:
    async def test_llm_tiebreak_called_only_for_ambiguous(self) -> None:
        """When top 2 candidates are too close, LLM tie-break is attempted."""
        mock_llm = AsyncMock()
        from pydantic import BaseModel

        class FakeResult(BaseModel):
            chosen_candidate: str = "Proje Yöneticisi"
            confidence: float = 0.92
            reason: str = "match"

        mock_llm.generate_structured = AsyncMock(return_value=FakeResult())

        svc = _build_svc(llm=mock_llm)
        plan = _make_plan([_filter("UNVAN", "yonetici")])

        resolved, trace = await svc.resolve(plan)

        # LLM was called since "yonetici" matches both Proje+ and Sistem+
        assert trace["actions"][0]["llm_tiebreak_used"] is True
        # LLM resolved the tie → exact match
        assert resolved.filters[0].value == "Proje Yöneticisi"
        assert resolved.needs_clarification is False

    async def test_llm_tiebreak_not_called_for_clear_winner(self) -> None:
        """When there's a clear winner, LLM is not invoked."""
        mock_llm = AsyncMock()
        svc = _build_svc(llm=mock_llm)
        plan = _make_plan([_filter("BIRIM_ADI", "IT")])

        resolved, trace = await svc.resolve(plan)

        assert trace["actions"][0].get("llm_tiebreak_used") is False
        mock_llm.generate_structured.assert_not_called()

    async def test_llm_failure_falls_back_to_clarification(self) -> None:
        """If LLM fails, system falls back to asking the user."""
        mock_llm = AsyncMock()
        mock_llm.generate_structured = AsyncMock(side_effect=Exception("LLM down"))

        mgr = ClarificationStateManager()
        svc = _build_svc(llm=mock_llm, clarification_manager=mgr)
        plan = _make_plan([_filter("UNVAN", "yonetici")])

        resolved, trace = await svc.resolve(
            plan, session_id="test-sess", original_question="yonetici unvanli calisanlar",
        )

        assert resolved.needs_clarification is True
        assert trace["clarification_required"] is True


# ── 4. Clarification generated when ambiguity remains ────────────────

class TestClarificationGeneration:
    async def test_ambiguous_triggers_clarification_with_options(self) -> None:
        mgr = ClarificationStateManager()
        svc = _build_svc(clarification_manager=mgr)
        plan = _make_plan([_filter("UNVAN", "yonetici")])

        resolved, trace = await svc.resolve(
            plan, session_id="sess-1", original_question="yonetici unvanli calisanlar",
        )

        assert resolved.needs_clarification is True
        assert "Proje Yöneticisi" in (resolved.clarification_message or "")
        assert "Sistem Yöneticisi" in (resolved.clarification_message or "")
        # Pending state created
        pending = mgr.get_pending("sess-1")
        assert pending is not None
        assert pending.target_column == "UNVAN"
        assert len(pending.candidates) >= 2

    async def test_no_candidates_triggers_clarification(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "Pazarlama")])

        resolved, trace = await svc.resolve(plan)

        assert resolved.needs_clarification is True
        assert trace["actions"][0]["reason"] == "no_confident_candidate_clarification"

    async def test_clarification_has_sen_karar_ver_option(self) -> None:
        mgr = ClarificationStateManager()
        svc = _build_svc(clarification_manager=mgr)
        plan = _make_plan([_filter("UNVAN", "yonetici")])

        resolved, _ = await svc.resolve(
            plan, session_id="sess-2", original_question="test",
        )

        msg = resolved.clarification_message or ""
        assert "Sen karar ver" in msg


# ── 5. User selects a specific option ────────────────────────────────

class TestUserSelectsOption:
    def test_user_selects_numeric_option(self) -> None:
        mgr = ClarificationStateManager()
        mgr.create_pending(
            session_id="sess-3",
            original_question="yonetici unvanli calisanlar",
            target_column="UNVAN",
            target_table="XXBT_PDKS_PER_DETAILS_V",
            original_filter_value="yonetici",
            candidates=[
                ClarificationCandidate(value="Proje Yöneticisi", score=0.96, reason="alias"),
                ClarificationCandidate(value="Sistem Yöneticisi", score=0.96, reason="alias"),
            ],
            top_candidate="Proje Yöneticisi",
            top_score=0.96,
            partial_grounded_plan_json=_make_plan([_filter("UNVAN", "yonetici")]).model_dump(mode="json"),
        )

        reply = mgr.interpret_reply("sess-3", "1")

        assert reply is not None
        assert reply.chosen_value == "Proje Yöneticisi"
        assert reply.resolution_method == "user_selected_option"

    def test_user_names_candidate_directly(self) -> None:
        mgr = ClarificationStateManager()
        mgr.create_pending(
            session_id="sess-4",
            original_question="test",
            target_column="UNVAN",
            target_table=None,
            original_filter_value="yonetici",
            candidates=[
                ClarificationCandidate(value="Proje Yöneticisi", score=0.96, reason="alias"),
                ClarificationCandidate(value="Sistem Yöneticisi", score=0.96, reason="alias"),
            ],
            top_candidate="Proje Yöneticisi",
            top_score=0.96,
            partial_grounded_plan_json={},
        )

        reply = mgr.interpret_reply("sess-4", "Sistem Yöneticisi")

        assert reply is not None
        assert reply.chosen_value == "Sistem Yöneticisi"
        assert reply.resolution_method == "user_named_candidate"


# ── 6. User says "sen karar ver" ────────────────────────────────────

class TestSenKararVer:
    def test_sen_karar_ver_uses_top_candidate(self) -> None:
        mgr = ClarificationStateManager()
        mgr.create_pending(
            session_id="sess-5",
            original_question="IT departmani",
            target_column="BIRIM_ADI",
            target_table=None,
            original_filter_value="IT",
            candidates=[
                ClarificationCandidate(value="Bilgi Teknolojileri", score=0.90, reason="alias"),
                ClarificationCandidate(value="İnsan Kaynakları", score=0.85, reason="fuzzy"),
            ],
            top_candidate="Bilgi Teknolojileri",
            top_score=0.90,
            partial_grounded_plan_json=_make_plan([_filter("BIRIM_ADI", "IT")]).model_dump(mode="json"),
        )

        reply = mgr.interpret_reply("sess-5", "sen karar ver")

        assert reply is not None
        assert reply.chosen_value == "Bilgi Teknolojileri"
        assert reply.resolution_method == "user_deferred_to_system"

    def test_sen_karar_ver_records_delegation_status(self) -> None:
        mgr = ClarificationStateManager()
        pending = mgr.create_pending(
            session_id="sess-6",
            original_question="test",
            target_column="BIRIM_ADI",
            target_table=None,
            original_filter_value="IT",
            candidates=[
                ClarificationCandidate(value="BT", score=0.90, reason="alias"),
            ],
            top_candidate="BT",
            top_score=0.90,
            partial_grounded_plan_json={},
        )

        mgr.interpret_reply("sess-6", "sen karar ver")

        assert pending.status == ClarificationStatus.RESOLVED_USER_DEFERRED
        assert pending.resolved_value == "BT"
        assert pending.resolution_method == "user_deferred_to_system"


# ── 7. Too-low-confidence does NOT auto-resolve on "sen karar ver" ───

class TestLowConfidenceReject:
    def test_sen_karar_ver_rejected_when_score_too_low(self) -> None:
        mgr = ClarificationStateManager()
        mgr.create_pending(
            session_id="sess-7",
            original_question="test",
            target_column="BIRIM_ADI",
            target_table=None,
            original_filter_value="xyz",
            candidates=[
                ClarificationCandidate(value="Something", score=0.50, reason="fuzzy"),
            ],
            top_candidate="Something",
            top_score=0.50,
            partial_grounded_plan_json={},
        )

        reply = mgr.interpret_reply("sess-7", "sen karar ver", min_auto_resolve_score=0.80)

        assert reply is None  # Rejected — score too low

    def test_pending_stays_pending_after_reject(self) -> None:
        mgr = ClarificationStateManager()
        mgr.create_pending(
            session_id="sess-8",
            original_question="test",
            target_column="X",
            target_table=None,
            original_filter_value="xyz",
            candidates=[
                ClarificationCandidate(value="A", score=0.50, reason="fuzzy"),
            ],
            top_candidate="A",
            top_score=0.50,
            partial_grounded_plan_json={},
        )

        mgr.interpret_reply("sess-8", "sen karar ver", min_auto_resolve_score=0.80)

        # Still pending — not resolved
        pending = mgr.get_pending("sess-8")
        assert pending is not None
        assert pending.status == ClarificationStatus.PENDING


# ── 8. Out-of-scope flag/date/null filters remain unchanged ──────────

class TestOutOfScopeFilters:
    async def test_bordrolu_is_untouched(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("BORDROLU", 1)])
        resolved, trace = await svc.resolve(plan)
        assert resolved is plan
        assert trace["actions"][0]["reason"] == "non_string_value_no_op"

    async def test_stajyer_is_untouched(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("STAJYER", 1)])
        resolved, trace = await svc.resolve(plan)
        assert resolved is plan
        assert trace["actions"][0]["reason"] == "non_string_value_no_op"

    async def test_cikis_tarihi_is_null_untouched(self) -> None:
        svc = _build_svc()
        plan = _make_plan([FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL)])
        resolved, trace = await svc.resolve(plan)
        assert resolved is plan
        assert trace["actions"][0]["reason"] == "non_string_value_no_op"

    async def test_email_is_not_null_untouched(self) -> None:
        svc = _build_svc()
        plan = _make_plan([FilterSpec(column="EMAIL", op=FilterOp.IS_NOT_NULL)])
        resolved, trace = await svc.resolve(plan)
        assert resolved is plan
        assert trace["actions"][0]["reason"] == "non_string_value_no_op"

    async def test_numeric_id_is_untouched(self) -> None:
        svc = _build_svc()
        plan = _make_plan([_filter("PERSON_ID", 12345)])
        resolved, trace = await svc.resolve(plan)
        assert resolved is plan
        assert trace["actions"][0]["reason"] == "non_string_value_no_op"


# ── 8b. LIKE surface-value extraction on in-scope columns ────────────

class TestLikeSurfaceValueExtraction:
    """Planner-produced LIKE filters on grounding-sensitive columns are
    treated as surface-value inputs, not silently skipped."""

    async def test_like_surface_value_extracted_and_processed(self) -> None:
        """BIRIM_ADI LIKE '%IT%' → extracted 'IT' → resolved to canonical."""
        svc = _build_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "%IT%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)

        assert resolved.filters[0].value == "Bilgi Teknolojileri"
        assert resolved.filters[0].op == FilterOp.EQ
        assert trace["actions"][0]["like_input"] is True
        assert trace["actions"][0]["like_surface_value_extracted"] == "IT"
        assert trace["actions"][0]["operator_rewritten"] is True
        assert trace["actions"][0]["changed"] is True
        assert trace["actions"][0]["reason"] != "unsupported_operator_no_op"

    async def test_like_ambiguous_causes_clarification_not_silent_noop(self) -> None:
        """BIRIM_ADI LIKE '%dizayn%' → 2 candidates → clarification."""
        svc = _build_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "%dizayn%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)

        assert resolved.needs_clarification is True
        assert trace["clarification_required"] is True
        msg = resolved.clarification_message or ""
        assert "ELEKTRIK DIZAYN" in msg
        assert "MEKANIK DIZAYN" in msg
        assert trace["actions"][0]["like_input"] is True
        assert trace["actions"][0]["like_surface_value_extracted"] == "dizayn"
        assert trace["actions"][0]["reason"] != "unsupported_operator_no_op"

    async def test_like_strong_candidate_rewrites_to_exact_match(self) -> None:
        """LOCATION_ADI LIKE '%istanbul%' → ISTANBUL BURO (strong) → EQ."""
        svc = _build_svc()
        plan = _make_plan([_filter("LOCATION_ADI", "%istanbul%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)

        assert resolved.filters[0].op == FilterOp.EQ
        assert resolved.filters[0].value == "ISTANBUL BURO"
        assert trace["actions"][0]["operator_rewritten"] is True
        assert trace["actions"][0]["confidence"] is not None

    async def test_like_no_candidates_explicit_reason(self) -> None:
        """BIRIM_ADI LIKE '%XYZ_UNKNOWN%' → no plausible candidate → explicit reason."""
        svc = _build_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "%XYZ_UNKNOWN%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)

        assert resolved.needs_clarification is True
        assert trace["actions"][0]["like_input"] is True
        assert trace["actions"][0]["reason"] == "no_confident_candidate_clarification"
        assert trace["actions"][0]["reason"] != "unsupported_operator_no_op"
        # Even with zero score-matches, all canonical values are shown as options
        assert len(trace["actions"][0]["candidate_values"]) > 0
        assert "Bilgi Teknolojileri" in trace["actions"][0]["candidate_values"]

    async def test_like_no_match_clarification_shows_available_values(self) -> None:
        """When no candidates match, clarification should present all canonical values."""
        svc = _build_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "%UNKNOWN%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)

        assert resolved.needs_clarification is True
        msg = resolved.clarification_message or ""
        # Fallback message should mention available values
        assert "Bilgi Teknolojileri" in msg or "eslesen bir deger bulunamadi" in msg
        # Surface value is used in message, not raw LIKE pattern
        assert "%UNKNOWN%" not in msg

    async def test_no_match_clarification_with_state_manager(self) -> None:
        """No-match with clarification manager shows numbered options."""
        mgr = ClarificationStateManager()
        svc = _build_svc(clarification_manager=mgr)
        plan = _make_plan([_filter("BIRIM_ADI", "%QQQ%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(
            plan, session_id="sess-nomatch", original_question="QQQ birimindeki calisanlar",
        )

        assert resolved.needs_clarification is True
        pending = mgr.get_pending("sess-nomatch")
        assert pending is not None
        assert len(pending.candidates) > 0
        # All profile canonical values are shown as options
        candidate_vals = [c.value for c in pending.candidates]
        assert "Bilgi Teknolojileri" in candidate_vals
        # Message should include numbered options
        msg = resolved.clarification_message or ""
        assert "1." in msg

    async def test_like_surface_extraction_strips_wildcards(self) -> None:
        """Various LIKE patterns are correctly stripped."""
        extract = FilterValueResolutionService._extract_like_surface_value
        assert extract("%dizayn%") == "dizayn"
        assert extract("%Istanbul%") == "Istanbul"
        assert extract("IT%") == "IT"
        assert extract("%BT") == "BT"
        assert extract("%%") is None
        assert extract("%") is None
        assert extract("  %  hello  %  ") == "hello"

    async def test_like_out_of_scope_column_remains_noop(self) -> None:
        """LIKE on a non-grounding column stays no-op (out_of_scope)."""
        svc = _build_svc()
        plan = _make_plan([_filter("EMAIL", "%test%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)

        assert resolved is plan
        assert trace["actions"][0]["reason"] == "out_of_scope_column_no_op"

    async def test_like_empty_surface_value_extraction_fails(self) -> None:
        """BIRIM_ADI LIKE '%%' → extraction fails → explicit reason."""
        svc = _build_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "%%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)

        assert resolved is plan
        assert trace["actions"][0]["reason"] == "like_surface_extraction_failed"
        assert trace["actions"][0]["like_input"] is True

    async def test_no_generic_unsupported_operator_noop_for_inscope_like(self) -> None:
        """Verify the exact bad behavior (unsupported_operator_no_op) is gone
        for all 5 approved business-dimension columns with LIKE."""
        svc = _build_svc()
        for col in ["BIRIM_ADI", "LOCATION_ADI", "ORGANIZATION_ADI", "UNVAN", "MASRAF_MERKEZI"]:
            plan = _make_plan([_filter(col, "%test%", FilterOp.LIKE)])
            _, trace = await svc.resolve(plan)
            reason = trace["actions"][0]["reason"]
            assert reason != "unsupported_operator_no_op", (
                f"{col} LIKE still produces unsupported_operator_no_op"
            )


class TestDbFallbackResolution:
    """DB-driven fallback when static profile has no matching candidates."""

    async def test_db_fallback_resolves_when_static_misses(self) -> None:
        """Value not in static profile → DB DISTINCT fetch → match found."""
        executor = _make_mock_executor("BIRIM_ADI", ["FİNANS BİRİMİ", "LOJİSTİK BİRİMİ"])
        svc = _build_svc(executor=executor)
        plan = _make_plan([_filter("BIRIM_ADI", "%finans%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)
        action = trace["actions"][0]

        assert action.get("db_fallback_used") is True
        assert action.get("source") == "db_distinct"
        assert "FİNANS BİRİMİ" in action["candidate_values"]
        executor.execute.assert_called_once()

    async def test_db_fallback_no_match_shows_db_values_as_clarification(self) -> None:
        """Value not in static profile, DB values exist but none score → clarification with DB values."""
        executor = _make_mock_executor("BIRIM_ADI", ["PAZARLAMA", "SATIŞ"])
        svc = _build_svc(executor=executor)
        plan = _make_plan([_filter("BIRIM_ADI", "%zzzzzz%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)
        action = trace["actions"][0]

        assert action["clarification_required"] is True
        assert action.get("db_fallback_used") is True
        assert "PAZARLAMA" in action["candidate_values"]
        assert "SATIŞ" in action["candidate_values"]

    async def test_db_fallback_uses_lowercase_column_keys(self) -> None:
        """OracleExecutor returns lowercase keys; DB fallback must handle this."""
        executor = _make_mock_executor("BIRIM_ADI", ["PLANLAMA BİRİMİ", "KALİTE BİRİMİ"])
        svc = _build_svc(executor=executor)
        plan = _make_plan([_filter("BIRIM_ADI", "%planlama%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)
        action = trace["actions"][0]

        assert action.get("db_fallback_used") is True
        assert len(action["candidate_values"]) >= 1
        assert any("PLANLAMA" in v for v in action["candidate_values"])

    async def test_no_executor_falls_back_to_static_profile_values(self) -> None:
        """Without executor, no DB fallback → static profile values shown."""
        svc = _build_svc(executor=None)
        plan = _make_plan([_filter("BIRIM_ADI", "%finans%", FilterOp.LIKE)])

        resolved, trace = await svc.resolve(plan)
        action = trace["actions"][0]

        assert action.get("db_fallback_used") is None  # not set
        assert action["reason"] == "no_confident_candidate_clarification"
        assert action["clarification_required"] is True


class TestLikeLiveViewPayload:
    """Pipeline Live View must show LIKE extraction diagnostics."""

    def test_payload_contains_like_extraction_fields(self) -> None:
        trace = {
            "any_changed": True,
            "clarification_required": False,
            "total_filters_seen": 1,
            "processed_filters": 1,
            "skipped_filters": 0,
            "changed_filters": 1,
            "changed_count": 1,
            "total_filters": 1,
            "llm_tiebreak_used": False,
            "pending_clarification": None,
            "skip_reasons": {},
            "changed_items": [],
            "original_filters": [],
            "final_filters": [],
            "actions": [
                {
                    "column": "BIRIM_ADI",
                    "operator": "LIKE",
                    "original_value": "%dizayn%",
                    "resolved_value": "ELEKTRIK DIZAYN",
                    "changed": True,
                    "clarification_required": False,
                    "reason": "token_subset_match",
                    "confidence": 0.87,
                    "candidate_values": ["ELEKTRIK DIZAYN", "MEKANIK DIZAYN"],
                    "ranking_scores": [],
                    "like_input": True,
                    "like_surface_value_extracted": "dizayn",
                    "operator_rewritten": True,
                    "original_operator": "LIKE",
                },
            ],
        }

        payload = build_filter_value_resolution_payload(trace)

        action = payload["actions"][0]
        assert action["like_input"] is True
        assert action["like_surface_value_extracted"] == "dizayn"
        assert action["operator_rewritten"] is True
        assert action["original_operator"] == "LIKE"


# ── 9. Pipeline Live View trace integration ──────────────────────────

class TestPipelineLiveViewTrace:
    async def test_resolved_trace_contains_grounding_workflow_data(self) -> None:
        mgr = ClarificationStateManager()
        svc = _build_svc(clarification_manager=mgr)
        plan = _make_plan([_filter("BIRIM_ADI", "IT")])

        _, trace = await svc.resolve(plan, session_id="sess-lv", original_question="IT calisanlar")

        # Must include ranking scores
        assert trace["actions"][0].get("ranking_scores") is not None
        assert len(trace["actions"][0]["ranking_scores"]) >= 1

    async def test_clarification_trace_contains_pending_state(self) -> None:
        mgr = ClarificationStateManager()
        svc = _build_svc(clarification_manager=mgr)
        plan = _make_plan([_filter("UNVAN", "yonetici")])

        _, trace = await svc.resolve(
            plan, session_id="sess-lv2", original_question="yonetici listele",
        )

        assert trace["clarification_required"] is True
        pending_trace = trace.get("pending_clarification")
        assert pending_trace is not None
        assert pending_trace["target_column"] == "UNVAN"
        assert pending_trace["status"] == "pending"
        assert len(pending_trace["candidates"]) >= 2

    def test_payload_builder_includes_new_fields(self) -> None:
        trace = {
            "any_changed": True,
            "clarification_required": False,
            "total_filters_seen": 1,
            "processed_filters": 1,
            "skipped_filters": 0,
            "changed_filters": 1,
            "changed_count": 1,
            "total_filters": 1,
            "llm_tiebreak_used": True,
            "pending_clarification": None,
            "skip_reasons": {},
            "changed_items": [],
            "original_filters": [],
            "final_filters": [],
            "actions": [
                {
                    "column": "UNVAN",
                    "operator": "=",
                    "original_value": "yonetici",
                    "resolved_value": "Proje Yöneticisi",
                    "changed": True,
                    "clarification_required": False,
                    "reason": "llm_tiebreak_resolved",
                    "confidence": 0.92,
                    "candidate_values": ["Proje Yöneticisi", "Sistem Yöneticisi"],
                    "ranking_scores": [
                        {"value": "Proje Yöneticisi", "score": 0.96, "reason": "alias"},
                        {"value": "Sistem Yöneticisi", "score": 0.96, "reason": "alias"},
                    ],
                    "llm_tiebreak_used": True,
                    "llm_tiebreak_result": {
                        "chosen_candidate": "Proje Yöneticisi",
                        "confidence": 0.92,
                        "reason": "closer match",
                    },
                }
            ],
        }

        payload = build_filter_value_resolution_payload(trace)

        assert payload["llm_tiebreak_used"] is True
        assert payload["actions"][0]["llm_tiebreak_used"] is True
        assert payload["actions"][0]["ranking_scores"] is not None


# ── 10. No inline hardcoded value dicts ──────────────────────────────

class TestNoHardcodedValues:
    def test_no_inline_business_value_dictionaries_in_service(self) -> None:
        forbidden = [
            "_CANONICAL_VALUES_BY_COLUMN",
            "_COLUMN_VALUE_ALIASES",
            "_FILTER_VALUE_MAP",
            "_DEPARTMENT_MAP",
            "_LOCATION_MAP",
        ]
        for name in forbidden:
            assert not hasattr(fvr_module, name), f"inline constant found: {name}"

    def test_resolver_depends_on_central_provider(self) -> None:
        svc = FilterValueResolutionService()
        assert hasattr(svc, "_provider")
        assert isinstance(svc._provider, FilterValueProfileProvider)


# ── ClarificationStateManager unit tests ─────────────────────────────

class TestClarificationStateManager:
    def test_create_and_retrieve_pending(self) -> None:
        mgr = ClarificationStateManager()
        pending = mgr.create_pending(
            session_id="s1",
            original_question="q",
            target_column="BIRIM_ADI",
            target_table=None,
            original_filter_value="IT",
            candidates=[ClarificationCandidate(value="BT", score=0.9, reason="alias")],
            top_candidate="BT",
            top_score=0.9,
            partial_grounded_plan_json={},
        )

        retrieved = mgr.get_pending("s1")
        assert retrieved is not None
        assert retrieved.clarification_id == pending.clarification_id

    def test_expired_pending_not_returned(self) -> None:
        mgr = ClarificationStateManager(ttl_seconds=0.0)
        mgr.create_pending(
            session_id="s2",
            original_question="q",
            target_column="X",
            target_table=None,
            original_filter_value="v",
            candidates=[ClarificationCandidate(value="A", score=0.9, reason="r")],
            top_candidate="A",
            top_score=0.9,
            partial_grounded_plan_json={},
        )

        # Already expired (ttl=0)
        import time
        time.sleep(0.01)
        assert mgr.get_pending("s2") is None

    def test_clear_removes_pending(self) -> None:
        mgr = ClarificationStateManager()
        mgr.create_pending(
            session_id="s3",
            original_question="q",
            target_column="X",
            target_table=None,
            original_filter_value="v",
            candidates=[ClarificationCandidate(value="A", score=0.9, reason="r")],
            top_candidate="A",
            top_score=0.9,
            partial_grounded_plan_json={},
        )
        mgr.clear("s3")
        assert mgr.get_pending("s3") is None

    def test_interpret_reply_no_pending(self) -> None:
        mgr = ClarificationStateManager()
        assert mgr.interpret_reply("no-session", "1") is None

    def test_build_clarification_message_format(self) -> None:
        mgr = ClarificationStateManager()
        pending = mgr.create_pending(
            session_id="s-msg",
            original_question="q",
            target_column="BIRIM_ADI",
            target_table=None,
            original_filter_value="IT",
            candidates=[
                ClarificationCandidate(value="Bilgi Tek.", score=0.9, reason="alias"),
                ClarificationCandidate(value="Yazılım", score=0.85, reason="fuzzy"),
            ],
            top_candidate="Bilgi Tek.",
            top_score=0.9,
            partial_grounded_plan_json={},
        )

        msg = mgr.build_clarification_message(pending)

        assert "1. Bilgi Tek." in msg
        assert "2. Yazılım" in msg
        assert "3. Sen karar ver" in msg

    def test_as_trace_dict(self) -> None:
        mgr = ClarificationStateManager()
        pending = mgr.create_pending(
            session_id="s-trace",
            original_question="test question for trace",
            target_column="COL",
            target_table="TBL",
            original_filter_value="val",
            candidates=[ClarificationCandidate(value="V", score=0.9, reason="r")],
            top_candidate="V",
            top_score=0.9,
            partial_grounded_plan_json={"key": "value"},
        )

        trace = mgr.as_trace_dict(pending)

        assert trace["clarification_id"] == pending.clarification_id
        assert trace["target_column"] == "COL"
        assert trace["status"] == "pending"
        assert len(trace["candidates"]) == 1

    def test_multiple_defer_phrases(self) -> None:
        """All Turkish delegation phrases should be recognized."""
        mgr = ClarificationStateManager()
        for phrase in ["sen karar ver", "sen sec", "sen seç", "karar ver", "farketmez"]:
            mgr.create_pending(
                session_id=f"s-{phrase}",
                original_question="q",
                target_column="X",
                target_table=None,
                original_filter_value="v",
                candidates=[ClarificationCandidate(value="A", score=0.9, reason="r")],
                top_candidate="A",
                top_score=0.9,
                partial_grounded_plan_json={},
            )
            reply = mgr.interpret_reply(f"s-{phrase}", phrase, min_auto_resolve_score=0.80)
            assert reply is not None, f"Failed for phrase: {phrase}"
            assert reply.resolution_method == "user_deferred_to_system"

    def test_recently_resolved_replay_via_chat(self) -> None:
        """After a clarification is resolved, re-sending the chosen value via
        /chat should replay instead of starting a new pipeline."""
        mgr = ClarificationStateManager()
        mgr.create_pending(
            session_id="s-replay",
            original_question="dizayn bolumunde calisanlari listele",
            target_column="BIRIM_ADI",
            target_table="XXBT_PDKS_PER_DETAILS_V",
            original_filter_value="dizayn",
            candidates=[
                ClarificationCandidate(value="ELEKTRİK DİZAYN", score=0.85, reason="fuzzy"),
                ClarificationCandidate(value="MEKANİK DİZAYN", score=0.80, reason="fuzzy"),
            ],
            top_candidate="ELEKTRİK DİZAYN",
            top_score=0.85,
            partial_grounded_plan_json={"intent": "test", "table": "T", "filters": []},
        )
        # Resolve via /chat/clarify (numeric selection)
        reply1 = mgr.interpret_reply("s-replay", "1")
        assert reply1 is not None
        assert reply1.chosen_value == "ELEKTRİK DİZAYN"

        # Now the user re-sends the same value via regular /chat
        replay = mgr.interpret_reply("s-replay", "ELEKTRİK DİZAYN")
        assert replay is not None, "Recently resolved value should replay, not start new pipeline"
        assert replay.resolution_method == "replay_resolved"
        assert replay.chosen_value == "ELEKTRİK DİZAYN"
        assert replay.original_question == "dizayn bolumunde calisanlari listele"

    def test_recently_resolved_replay_matches_other_candidate(self) -> None:
        """Re-sending a different candidate value from the same resolved
        clarification should also replay."""
        mgr = ClarificationStateManager()
        mgr.create_pending(
            session_id="s-replay2",
            original_question="q",
            target_column="BIRIM_ADI",
            target_table=None,
            original_filter_value="dizayn",
            candidates=[
                ClarificationCandidate(value="ELEKTRİK DİZAYN", score=0.85, reason="fuzzy"),
                ClarificationCandidate(value="MEKANİK DİZAYN", score=0.80, reason="fuzzy"),
            ],
            top_candidate="ELEKTRİK DİZAYN",
            top_score=0.85,
            partial_grounded_plan_json={},
        )
        # Resolve first
        mgr.interpret_reply("s-replay2", "1")
        # Re-send a different candidate
        replay = mgr.interpret_reply("s-replay2", "MEKANİK DİZAYN")
        assert replay is not None
        assert replay.chosen_value == "MEKANİK DİZAYN"
        assert replay.resolution_method == "replay_resolved"

    def test_recently_resolved_no_replay_for_unrelated_message(self) -> None:
        """An unrelated message should NOT trigger a replay."""
        mgr = ClarificationStateManager()
        mgr.create_pending(
            session_id="s-noreplay",
            original_question="q",
            target_column="X",
            target_table=None,
            original_filter_value="v",
            candidates=[ClarificationCandidate(value="A", score=0.9, reason="r")],
            top_candidate="A",
            top_score=0.9,
            partial_grounded_plan_json={},
        )
        mgr.interpret_reply("s-noreplay", "1")  # resolve
        replay = mgr.interpret_reply("s-noreplay", "something completely different")
        assert replay is None
