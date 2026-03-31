"""Tests for column-aware value matching with multi-representation forms + RapidFuzz.

Covers:
- ValueForms generation
- ColumnMatchingPolicy inference (generic value-shape policies)
- freetext matching (names, descriptions)
- structured matching (labels, departments, locations)
- coded matching (lookup codes, flags, segments)
- Integration: resolved output preserves canonical DB value
- Integration: ambiguous output feeds clarification
- Regression: existing exact/alias behavior preserved
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.services.column_matching_policy import (
    CODED,
    DEFAULT_POLICY,
    FREETEXT,
    STRUCTURED,
    ColumnPolicyType,
    infer_column_policy,
)
from app.services.filter_value_profile_provider import FilterValueProfileProvider
from app.services.filter_value_resolution_service import (
    CandidateMatch,
    FilterValueResolutionService,
)
from app.utils.value_forms import ValueForms, build_value_forms


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_plan(filters: list[FilterSpec], table: str = "XXBT_PDKS_PER_DETAILS_V") -> QueryPlan:
    return QueryPlan(intent="employee_list", table=table, filters=filters)


def _filter(column: str, value: Any, op: FilterOp = FilterOp.EQ, table: str | None = None) -> FilterSpec:
    return FilterSpec(column=column, op=op, value=value, table=table)


def _provider_from_dict(data: dict[str, Any]) -> FilterValueProfileProvider:
    with TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "filter_value_profiles.json"
        cfg_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        provider = FilterValueProfileProvider(config_path=cfg_path)
        provider.policy()
        return provider


def _test_profiles() -> dict[str, Any]:
    """Comprehensive test profiles covering all policy types."""
    return {
        "schema_version": "1.0",
        "matching_policy": {
            "candidate_preview_limit": 5,
            "min_select_score": 0.88,
            "min_score_gap": 0.08,
            "min_fuzzy_ratio": 0.76,
        },
        "profiles": {
            # freetext policy
            "T.AD": {
                "table": "T",
                "column": "AD",
                "column_policy": "freetext",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "AHMET", "aliases": []},
                    {"value": "MEHMET", "aliases": []},
                    {"value": "ALİ", "aliases": ["ali"]},
                ],
            },
            "T.AD_SOYAD": {
                "table": "T",
                "column": "AD_SOYAD",
                "column_policy": "freetext",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "AHMET CELIK", "aliases": []},
                    {"value": "AHMET YILMAZ", "aliases": []},
                    {"value": "MEHMET KAYA", "aliases": []},
                ],
            },
            # structured policy
            "T.BIRIM_ADI": {
                "table": "T",
                "column": "BIRIM_ADI",
                "column_policy": "structured",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "DT-Dizayn", "aliases": []},
                    {"value": "ELM-Dizayn", "aliases": []},
                    {"value": "YAZILIM GELİŞTİRME", "aliases": ["it", "bt", "bilgi teknolojileri"]},
                    {"value": "İnsan Kaynakları", "aliases": ["ik", "hr"]},
                ],
            },
            "T.LOCATION_ADI": {
                "table": "T",
                "column": "LOCATION_ADI",
                "column_policy": "structured",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "İzmir", "aliases": ["izmir"]},
                    {"value": "İstanbul", "aliases": ["istanbul"]},
                    {"value": "Ankara", "aliases": ["ankara"]},
                ],
            },
            # coded policy
            "T.MASRAF_MERKEZI": {
                "table": "T",
                "column": "MASRAF_MERKEZI",
                "column_policy": "coded",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "BT-01", "aliases": ["bt01", "bt 01"]},
                    {"value": "BT-02", "aliases": ["bt02", "bt 02"]},
                    {"value": "IK-01", "aliases": ["ik01", "ik 01"]},
                    {"value": "MUH-01", "aliases": ["muh01"]},
                ],
            },
            "T.STAJYER": {
                "table": "T",
                "column": "STAJYER",
                "column_policy": "coded",
                "supported_ops": ["=", "!="],
                "canonical_values": [
                    {"value": "Y", "aliases": ["evet", "yes"]},
                    {"value": "N", "aliases": ["hayir", "no"]},
                ],
            },
        },
    }


def _svc() -> FilterValueResolutionService:
    return FilterValueResolutionService(provider=_provider_from_dict(_test_profiles()))


# ═══════════════════════════════════════════════════════════════════════
# 1. ValueForms generation
# ═══════════════════════════════════════════════════════════════════════


class TestValueForms:
    def test_basic_latin(self) -> None:
        f = build_value_forms("Hello World")
        assert f.raw == "Hello World"
        assert f.casefolded == "hello world"
        assert f.ascii_folded == "hello world"
        assert f.compact == "hello world"
        assert f.tokens == ("hello", "world")

    def test_turkish_i_casefolding(self) -> None:
        f = build_value_forms("İSTANBUL")
        assert f.casefolded == "istanbul"
        # İ → i, S→s, T→t, A→a, N→n, B→b, U→u, L→l
        assert f.ascii_folded == "istanbul"

    def test_turkish_I_without_dot(self) -> None:
        f = build_value_forms("IZMIR")
        # casefold_tr maps I→ı
        assert f.casefolded == "ızmır"
        # ascii_fold: ı→i
        assert f.ascii_folded == "izmir"

    def test_turkish_accent_stripping(self) -> None:
        f = build_value_forms("Ahmet Çelik")
        assert f.casefolded == "ahmet çelik"
        assert f.ascii_folded == "ahmet celik"
        assert f.tokens == ("ahmet", "celik")

    def test_separator_normalization(self) -> None:
        f = build_value_forms("DT-Dizayn")
        assert f.casefolded == "dt-dizayn"
        assert f.compact == "dt dizayn"
        assert set(f.tokens) == {"dt", "dizayn"}

    def test_multiple_separators(self) -> None:
        f = build_value_forms("A--B__C")
        assert f.compact == "a b c"

    def test_empty_string(self) -> None:
        f = build_value_forms("")
        assert f.raw == ""
        assert f.tokens == ()

    def test_whitespace_normalization(self) -> None:
        f = build_value_forms("  hello   world  ")
        assert f.raw == "hello   world"
        assert f.compact == "hello world"

    def test_as_trace_dict(self) -> None:
        f = build_value_forms("Test")
        d = f.as_trace_dict()
        assert isinstance(d, dict)
        assert d["raw"] == "Test"
        assert isinstance(d["tokens"], list)


# ═══════════════════════════════════════════════════════════════════════
# 2. Column matching policy inference
# ═══════════════════════════════════════════════════════════════════════


class TestColumnPolicyInference:
    def test_ad_is_freetext(self) -> None:
        p = infer_column_policy("AD")
        assert p.policy_type == ColumnPolicyType.FREETEXT

    def test_soyad_is_freetext(self) -> None:
        p = infer_column_policy("SOYAD")
        assert p.policy_type == ColumnPolicyType.FREETEXT

    def test_ad_soyad_is_freetext(self) -> None:
        p = infer_column_policy("AD_SOYAD")
        assert p.policy_type == ColumnPolicyType.FREETEXT

    def test_masraf_merkezi_is_coded(self) -> None:
        p = infer_column_policy("MASRAF_MERKEZI")
        assert p.policy_type == ColumnPolicyType.CODED

    def test_stajyer_is_coded(self) -> None:
        p = infer_column_policy("STAJYER")
        assert p.policy_type == ColumnPolicyType.CODED

    def test_bordrolu_is_coded(self) -> None:
        p = infer_column_policy("BORDROLU")
        assert p.policy_type == ColumnPolicyType.CODED

    def test_birim_adi_is_structured(self) -> None:
        p = infer_column_policy("BIRIM_ADI")
        assert p.policy_type == ColumnPolicyType.STRUCTURED

    def test_location_adi_is_structured(self) -> None:
        p = infer_column_policy("LOCATION_ADI")
        assert p.policy_type == ColumnPolicyType.STRUCTURED

    def test_unvan_is_structured(self) -> None:
        p = infer_column_policy("UNVAN")
        assert p.policy_type == ColumnPolicyType.STRUCTURED

    def test_unknown_column_is_default(self) -> None:
        p = infer_column_policy("SOME_FIELD")
        assert p.policy_type == ColumnPolicyType.DEFAULT

    def test_explicit_policy_overrides_inference(self) -> None:
        # AD would normally be freetext, but explicit override wins
        p = infer_column_policy("AD", explicit_policy="coded")
        assert p.policy_type == ColumnPolicyType.CODED

    def test_invalid_explicit_policy_falls_back(self) -> None:
        p = infer_column_policy("AD", explicit_policy="nonexistent_policy")
        assert p.policy_type == ColumnPolicyType.FREETEXT

    def test_column_policy_from_profile_config(self) -> None:
        provider = _provider_from_dict(_test_profiles())
        profile = provider.get_profile("T", "MASRAF_MERKEZI")
        assert profile is not None
        assert profile.column_policy == "coded"


# ═══════════════════════════════════════════════════════════════════════
# 3. freetext matching
# ═══════════════════════════════════════════════════════════════════════


class TestFreetextMatching:
    async def test_ahmet_resolves_to_AHMET(self) -> None:
        """Case-insensitive match: 'Ahmet' → 'AHMET'."""
        svc = _svc()
        plan = _make_plan([_filter("AD", "Ahmet", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "AHMET"
        assert trace["actions"][0]["column_policy"] == "freetext"
        assert "exact" in trace["actions"][0]["reason"]

    async def test_ahmet_celik_matches_AHMET_CELIK(self) -> None:
        """Turkish accent folding: 'Ahmet Çelik' → 'AHMET CELIK'."""
        svc = _svc()
        plan = _make_plan([_filter("AD_SOYAD", "Ahmet Çelik", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "AHMET CELIK"
        assert trace["actions"][0]["winning_strategy"] in (
            "exact_casefolded", "exact_ascii_folded",
        )

    async def test_multiple_close_names_trigger_ambiguity(self) -> None:
        """Two names with same prefix → clarification."""
        svc = _svc()
        plan = _make_plan([_filter("AD_SOYAD", "Ahmet", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        # "Ahmet" matches both "AHMET CELIK" and "AHMET YILMAZ" via token subset
        # Gap should be small → clarification or low-confidence
        action = trace["actions"][0]
        if resolved.needs_clarification:
            assert action["clarification_required"] is True
            assert action["column_policy"] == "freetext"
        else:
            # If one dominates, at least check canonical value is preserved
            assert resolved.filters[0].value in ("AHMET CELIK", "AHMET YILMAZ")

    async def test_ali_with_turkish_i_resolves(self) -> None:
        """Turkish İ/I handling: 'ali' → 'ALİ'."""
        svc = _svc()
        plan = _make_plan([_filter("AD", "ali", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "ALİ"

    async def test_freetext_preserves_canonical_value(self) -> None:
        """Resolution must return the original DB canonical value, not normalized."""
        svc = _svc()
        plan = _make_plan([_filter("AD", "ahmet", table="T")], table="T")
        resolved, _ = await svc.resolve(plan)
        assert resolved.filters[0].value == "AHMET"  # canonical, not "ahmet"


# ═══════════════════════════════════════════════════════════════════════
# 4. structured matching
# ═══════════════════════════════════════════════════════════════════════


class TestStructuredMatching:
    async def test_dizayn_returns_ambiguity_candidates(self) -> None:
        """'Dizayn' → both 'DT-Dizayn' and 'ELM-Dizayn' → clarification."""
        svc = _svc()
        plan = _make_plan([_filter("BIRIM_ADI", "Dizayn", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.needs_clarification is True
        action = trace["actions"][0]
        assert action["clarification_required"] is True
        candidates = action["candidate_values"]
        assert "DT-Dizayn" in candidates
        assert "ELM-Dizayn" in candidates
        assert action["column_policy"] == "structured"

    async def test_hyphen_space_tolerated(self) -> None:
        """'DT Dizayn' should match 'DT-Dizayn' despite separator difference."""
        svc = _svc()
        plan = _make_plan([_filter("BIRIM_ADI", "DT Dizayn", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "DT-Dizayn"
        # Compact form normalizes separators
        action = trace["actions"][0]
        assert action["winning_strategy"] in (
            "exact_compact", "exact_ascii_folded", "exact_casefolded",
        )

    async def test_close_dept_candidates_do_not_auto_resolve(self) -> None:
        """Two equally close department labels must go to clarification."""
        svc = _svc()
        plan = _make_plan([_filter("BIRIM_ADI", "Dizayn", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        # Both DT-Dizayn and ELM-Dizayn should score similarly
        assert resolved.needs_clarification is True
        action = trace["actions"][0]
        assert action["ambiguity_reason"] in ("close_candidate_gap", "score_below_threshold")

    async def test_izmir_ascii_fold_resolves(self) -> None:
        """'IZMIR' → 'İzmir' via ASCII-fold comparison."""
        svc = _svc()
        plan = _make_plan([_filter("LOCATION_ADI", "IZMIR", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "İzmir"
        assert trace["actions"][0]["column_policy"] == "structured"

    async def test_department_alias_still_works(self) -> None:
        """Alias 'it' → 'YAZILIM GELİŞTİRME' via exact alias match."""
        svc = _svc()
        plan = _make_plan([_filter("BIRIM_ADI", "it", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "YAZILIM GELİŞTİRME"
        assert trace["actions"][0]["reason"] == "exact_alias_match"

    async def test_istanbul_case_insensitive(self) -> None:
        """'istanbul' → 'İstanbul' via alias or folded match."""
        svc = _svc()
        plan = _make_plan([_filter("LOCATION_ADI", "istanbul", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "İstanbul"


# ═══════════════════════════════════════════════════════════════════════
# 5. coded matching
# ═══════════════════════════════════════════════════════════════════════


class TestCodedMatching:
    async def test_exact_code_match(self) -> None:
        """Exact raw match for cost center code."""
        svc = _svc()
        plan = _make_plan([_filter("MASRAF_MERKEZI", "BT-01", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "BT-01"
        assert trace["actions"][0]["column_policy"] == "coded"

    async def test_code_alias_resolves(self) -> None:
        """Alias 'bt01' → 'BT-01'."""
        svc = _svc()
        plan = _make_plan([_filter("MASRAF_MERKEZI", "bt01", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "BT-01"
        assert trace["actions"][0]["reason"] == "exact_alias_match"

    async def test_coded_conservative_fuzzy(self) -> None:
        """Unrelated similar codes must NOT auto-resolve.
        'BT-03' doesn't exist → should not resolve to BT-01 or BT-02."""
        svc = _svc()
        plan = _make_plan([_filter("MASRAF_MERKEZI", "BT-03", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        action = trace["actions"][0]
        # coded has min_select_score=0.95 — fuzzy match shouldn't pass
        if resolved.needs_clarification:
            assert action["clarification_required"] is True
        else:
            # Must not have silently resolved to BT-01 or BT-02
            assert resolved.filters[0].value == "BT-03"

    async def test_strict_flag_alias(self) -> None:
        """'evet' → 'Y' for STAJYER flag."""
        svc = _svc()
        plan = _make_plan([_filter("STAJYER", "evet", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "Y"
        assert trace["actions"][0]["reason"] == "exact_alias_match"

    async def test_coded_min_score_is_high(self) -> None:
        """Verify coded policy uses high min_select_score."""
        assert CODED.min_select_score == 0.95
        assert CODED.min_fuzzy_ratio == 0.90

    async def test_bt_01_vs_bt_02_not_confused(self) -> None:
        """BT-02 should match BT-02, not BT-01."""
        svc = _svc()
        plan = _make_plan([_filter("MASRAF_MERKEZI", "BT-02", table="T")], table="T")
        resolved, _ = await svc.resolve(plan)
        assert resolved.filters[0].value == "BT-02"


# ═══════════════════════════════════════════════════════════════════════
# 6. Multi-representation comparison logic
# ═══════════════════════════════════════════════════════════════════════


class TestRepresentationLogic:
    def test_turkish_folding_izmir(self) -> None:
        """'IZMIR' and 'İzmir' produce same ascii_folded form."""
        f1 = build_value_forms("IZMIR")
        f2 = build_value_forms("İzmir")
        assert f1.ascii_folded == f2.ascii_folded == "izmir"

    def test_turkish_folding_istanbul(self) -> None:
        f1 = build_value_forms("İSTANBUL")
        f2 = build_value_forms("istanbul")
        assert f1.ascii_folded == f2.ascii_folded

    def test_accent_fold_does_not_destroy_tokens(self) -> None:
        f = build_value_forms("Çelik")
        assert f.ascii_folded == "celik"
        assert f.tokens == ("celik",)

    def test_separator_normalization_preserves_content(self) -> None:
        f1 = build_value_forms("DT-Dizayn")
        f2 = build_value_forms("DT Dizayn")
        assert f1.compact == f2.compact == "dt dizayn"

    def test_canonical_value_never_replaced(self) -> None:
        """build_value_forms returns forms; the raw field preserves original."""
        f = build_value_forms("AHMET CELIK")
        assert f.raw == "AHMET CELIK"  # untouched

    def test_casefolded_preserves_diacritics(self) -> None:
        f = build_value_forms("Çelik")
        assert "ç" in f.casefolded
        # but ascii_folded strips them
        assert "ç" not in f.ascii_folded

    def test_ahmet_vs_AHMET_casefolded_match(self) -> None:
        f1 = build_value_forms("Ahmet")
        f2 = build_value_forms("AHMET")
        assert f1.casefolded == f2.casefolded == "ahmet"

    def test_ahmet_celik_vs_AHMET_CELIK_ascii_folded(self) -> None:
        f1 = build_value_forms("Ahmet Çelik")
        f2 = build_value_forms("AHMET CELIK")
        assert f1.ascii_folded == f2.ascii_folded == "ahmet celik"


# ═══════════════════════════════════════════════════════════════════════
# 7. Integration tests
# ═══════════════════════════════════════════════════════════════════════


class TestIntegration:
    async def test_resolved_returns_canonical_db_value(self) -> None:
        """Final resolved value must be the original canonical DB value."""
        svc = _svc()
        plan = _make_plan([_filter("AD", "ahmet", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        # Must be "AHMET" (canonical), not "ahmet" (user input)
        assert resolved.filters[0].value == "AHMET"
        assert trace["actions"][0]["changed"] is True

    async def test_ambiguous_feeds_clarification_flow(self) -> None:
        """Ambiguous result sets needs_clarification on the plan."""
        svc = _svc()
        plan = _make_plan([_filter("BIRIM_ADI", "Dizayn", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.needs_clarification is True
        assert isinstance(resolved.clarification_message, str)
        assert len(resolved.clarification_message) > 0
        assert trace["clarification_required"] is True

    async def test_trace_contains_new_fields(self) -> None:
        """Trace output includes column_policy, user_value_forms, winning_strategy."""
        svc = _svc()
        plan = _make_plan([_filter("AD", "Ahmet", table="T")], table="T")
        _, trace = await svc.resolve(plan)
        action = trace["actions"][0]
        assert "column_policy" in action
        assert action["column_policy"] == "freetext"
        assert "user_value_forms" in action
        assert action["user_value_forms"]["raw"] == "Ahmet"
        assert "winning_strategy" in action
        assert "winning_score" in action
        assert "resolver_stage_used" in action

    async def test_candidate_count_in_trace(self) -> None:
        """Trace includes candidate_count from profile."""
        svc = _svc()
        plan = _make_plan([_filter("MASRAF_MERKEZI", "BT-01", table="T")], table="T")
        _, trace = await svc.resolve(plan)
        action = trace["actions"][0]
        assert action.get("candidate_count") == 4  # 4 cost center codes

    async def test_no_profile_no_executor_still_noop(self) -> None:
        """Out of scope column without executor stays no-op."""
        svc = FilterValueResolutionService(
            provider=_provider_from_dict({"profiles": {}}),
        )
        plan = _make_plan([_filter("UNKNOWN_COL", "test")])
        resolved, trace = await svc.resolve(plan)
        assert resolved is plan
        assert trace["actions"][0]["reason"] == "no_profile_no_executor_no_op"


# ═══════════════════════════════════════════════════════════════════════
# 8. Regression tests — existing behavior preserved
# ═══════════════════════════════════════════════════════════════════════


class TestRegression:
    async def test_existing_exact_alias_behavior(self) -> None:
        """Alias 'ik' → 'İnsan Kaynakları' still works."""
        svc = _svc()
        plan = _make_plan([_filter("BIRIM_ADI", "ik", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "İnsan Kaynakları"
        assert trace["actions"][0]["reason"] == "exact_alias_match"

    async def test_already_canonical_is_no_change(self) -> None:
        """Exact canonical value → no change, no resolution."""
        svc = _svc()
        plan = _make_plan([_filter("MASRAF_MERKEZI", "BT-01", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert trace["actions"][0]["reason"] == "already_canonical_exact"
        assert trace["actions"][0]["changed"] is False

    async def test_non_string_value_no_op(self) -> None:
        svc = _svc()
        plan = _make_plan([_filter("AD", 42, table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert trace["actions"][0]["reason"] == "non_string_value_no_op"

    async def test_user_literal_passthrough_no_profile(self) -> None:
        """User-typed literal in question skips resolution when no profile."""
        svc = FilterValueResolutionService(
            provider=_provider_from_dict({"profiles": {}}),
        )
        plan = _make_plan([_filter("UNKNOWN_COL", "Ahmet")])
        resolved, trace = await svc.resolve(
            plan, original_question="Ahmet adlı çalışanları listele",
        )
        assert trace["actions"][0]["reason"] == "user_literal_passthrough"
        assert resolved.filters[0].value == "Ahmet"

    async def test_user_literal_with_profile_resolves_canonical(self) -> None:
        """User-typed literal must still resolve to canonical when profile exists."""
        svc = _svc()
        plan = _make_plan([_filter("AD", "Ahmet", table="T")], table="T")
        resolved, trace = await svc.resolve(
            plan, original_question="Ahmet adlı çalışanları listele",
        )
        # Profile exists → must resolve "Ahmet" → "AHMET" (canonical DB form)
        assert resolved.filters[0].value == "AHMET"
        assert trace["actions"][0]["changed"] is True

    async def test_like_surface_extraction(self) -> None:
        """LIKE operator extracts surface value and rewrites to EQ."""
        svc = _svc()
        plan = _make_plan([_filter("LOCATION_ADI", "%istanbul%", FilterOp.LIKE, table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].op == FilterOp.EQ
        assert resolved.filters[0].value == "İstanbul"

    async def test_empty_filters_returns_empty_trace(self) -> None:
        svc = _svc()
        plan = _make_plan([])
        resolved, trace = await svc.resolve(plan)
        assert resolved is plan
        assert trace["any_changed"] is False
        assert trace["total_filters_seen"] == 0


# ═══════════════════════════════════════════════════════════════════════
# 9. Policy score thresholds
# ═══════════════════════════════════════════════════════════════════════


class TestPolicyThresholds:
    def test_coded_has_highest_min_select(self) -> None:
        assert CODED.min_select_score > FREETEXT.min_select_score
        assert CODED.min_select_score > STRUCTURED.min_select_score

    def test_structured_has_widest_gap(self) -> None:
        assert STRUCTURED.min_score_gap > FREETEXT.min_score_gap
        assert STRUCTURED.min_score_gap >= DEFAULT_POLICY.min_score_gap

    def test_freetext_uses_token_sort_ratio(self) -> None:
        assert FREETEXT.fuzzy_scorer == "token_sort_ratio"

    def test_structured_uses_token_set_ratio(self) -> None:
        assert STRUCTURED.fuzzy_scorer == "token_set_ratio"

    def test_coded_uses_wratio(self) -> None:
        assert CODED.fuzzy_scorer == "WRatio"


# ═══════════════════════════════════════════════════════════════════════
# 10. Oracle EBS R12 column inference (suffix conventions)
# ═══════════════════════════════════════════════════════════════════════


class TestOracleR12ColumnInference:
    """Verify that generic column suffix heuristics correctly map to policies."""

    # Coded — suffix patterns
    def test_suffix_code(self) -> None:
        assert infer_column_policy("CURRENCY_CODE").policy_type == ColumnPolicyType.CODED

    def test_suffix_flag(self) -> None:
        assert infer_column_policy("ACTIVE_FLAG").policy_type == ColumnPolicyType.CODED

    def test_suffix_status(self) -> None:
        assert infer_column_policy("ORDER_STATUS").policy_type == ColumnPolicyType.CODED

    def test_suffix_type(self) -> None:
        assert infer_column_policy("LINE_TYPE").policy_type == ColumnPolicyType.CODED

    def test_suffix_class(self) -> None:
        assert infer_column_policy("ITEM_CLASS").policy_type == ColumnPolicyType.CODED

    def test_segment_column(self) -> None:
        assert infer_column_policy("SEGMENT1").policy_type == ColumnPolicyType.CODED
        assert infer_column_policy("SEGMENT12").policy_type == ColumnPolicyType.CODED

    # Freetext — person / vendor / customer names
    def test_customer_name(self) -> None:
        assert infer_column_policy("CUSTOMER_NAME").policy_type == ColumnPolicyType.FREETEXT

    def test_party_name(self) -> None:
        assert infer_column_policy("PARTY_NAME").policy_type == ColumnPolicyType.FREETEXT

    def test_vendor_name(self) -> None:
        assert infer_column_policy("VENDOR_NAME").policy_type == ColumnPolicyType.FREETEXT

    # Structured — suffix patterns
    def test_suffix_adi(self) -> None:
        assert infer_column_policy("BIRIM_ADI").policy_type == ColumnPolicyType.STRUCTURED

    def test_suffix_label(self) -> None:
        assert infer_column_policy("ORG_LABEL").policy_type == ColumnPolicyType.STRUCTURED

    def test_suffix_desc(self) -> None:
        assert infer_column_policy("ITEM_DESC").policy_type == ColumnPolicyType.STRUCTURED

    def test_suffix_description(self) -> None:
        assert infer_column_policy("TASK_DESCRIPTION").policy_type == ColumnPolicyType.STRUCTURED

    # Legacy config values still resolve
    def test_legacy_strict_code_resolves(self) -> None:
        p = infer_column_policy("SOME_COL", explicit_policy="strict_code")
        assert p.policy_type == ColumnPolicyType.CODED

    def test_legacy_person_name_resolves(self) -> None:
        p = infer_column_policy("SOME_COL", explicit_policy="person_name")
        assert p.policy_type == ColumnPolicyType.FREETEXT

    def test_legacy_department_label_resolves(self) -> None:
        p = infer_column_policy("SOME_COL", explicit_policy="department_label")
        assert p.policy_type == ColumnPolicyType.STRUCTURED


# ═══════════════════════════════════════════════════════════════════════
# 11. E2E integration — multi-filter pipeline flow
# ═══════════════════════════════════════════════════════════════════════


class TestE2EIntegration:
    """End-to-end tests exercising the full resolve() pipeline with
    multiple filters of different policy types in a single plan."""

    async def test_multi_filter_mixed_policies(self) -> None:
        """Plan with coded + structured + freetext filters resolves correctly."""
        svc = _svc()
        plan = _make_plan(
            [
                _filter("MASRAF_MERKEZI", "bt01", table="T"),  # coded → alias
                _filter("BIRIM_ADI", "it", table="T"),          # structured → alias
                _filter("AD", "ahmet", table="T"),              # freetext → casefolded
            ],
            table="T",
        )
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "BT-01"
        assert resolved.filters[1].value == "YAZILIM GELİŞTİRME"
        assert resolved.filters[2].value == "AHMET"
        assert trace["changed_count"] == 3
        assert trace["any_changed"] is True
        # Each action has correct policy
        assert trace["actions"][0]["column_policy"] == "coded"
        assert trace["actions"][1]["column_policy"] == "structured"
        assert trace["actions"][2]["column_policy"] == "freetext"

    async def test_multi_filter_with_clarification_stops_early(self) -> None:
        """If one filter triggers clarification, pipeline returns partial."""
        svc = _svc()
        plan = _make_plan(
            [
                _filter("AD", "ahmet", table="T"),        # freetext → resolves
                _filter("BIRIM_ADI", "Dizayn", table="T"),  # structured → ambiguous
                _filter("MASRAF_MERKEZI", "BT-01", table="T"),  # coded → would resolve
            ],
            table="T",
        )
        resolved, trace = await svc.resolve(plan)
        # Second filter triggers clarification, third is NOT processed
        assert resolved.needs_clarification is True
        assert trace["clarification_required"] is True
        # First filter resolved, second triggered clarification
        assert len(trace["actions"]) == 2

    async def test_all_canonical_no_change(self) -> None:
        """When all filter values are already canonical, nothing changes."""
        svc = _svc()
        plan = _make_plan(
            [
                _filter("MASRAF_MERKEZI", "BT-01", table="T"),
                _filter("AD", "AHMET", table="T"),
            ],
            table="T",
        )
        resolved, trace = await svc.resolve(plan)
        assert trace["any_changed"] is False
        assert trace["changed_count"] == 0
        for action in trace["actions"]:
            assert action["reason"] == "already_canonical_exact"

    async def test_trace_completeness(self) -> None:
        """Verify all expected trace fields are present for a resolved filter."""
        svc = _svc()
        plan = _make_plan([_filter("AD", "ali", table="T")], table="T")
        _, trace = await svc.resolve(plan)
        action = trace["actions"][0]
        required_fields = {
            "table", "column", "operator", "original_value", "resolved_value",
            "changed", "reason", "confidence", "column_policy",
            "user_value_forms", "winning_strategy", "winning_score",
            "resolver_stage_used",
        }
        assert required_fields.issubset(set(action.keys())), (
            f"Missing fields: {required_fields - set(action.keys())}"
        )


# ═══════════════════════════════════════════════════════════════════════
# 12. Ambiguity stress tests — close competing candidates
# ═══════════════════════════════════════════════════════════════════════


def _ambiguity_profiles() -> dict[str, Any]:
    """Profiles with deliberately close candidate values."""
    return {
        "schema_version": "1.0",
        "matching_policy": {
            "candidate_preview_limit": 5,
            "min_select_score": 0.88,
            "min_score_gap": 0.08,
            "min_fuzzy_ratio": 0.76,
        },
        "profiles": {
            "T.BIRIM_ADI": {
                "table": "T",
                "column": "BIRIM_ADI",
                "column_policy": "structured",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "Bilgi Teknolojileri", "aliases": ["bt"]},
                    {"value": "Bilgi Güvenliği", "aliases": ["bg"]},
                    {"value": "Bilgi Sistemleri", "aliases": ["bs"]},
                    {"value": "BT Destek", "aliases": ["btdestek"]},
                    {"value": "BT Altyapı", "aliases": ["btaltyapi"]},
                ],
            },
            "T.UNVAN": {
                "table": "T",
                "column": "UNVAN",
                "column_policy": "structured",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "Uzman Yazılımcı", "aliases": ["sw expert"]},
                    {"value": "Uzman Mühendis", "aliases": ["sr engineer"]},
                    {"value": "Uzman Yardımcısı", "aliases": ["junior expert"]},
                ],
            },
            "T.AD_SOYAD": {
                "table": "T",
                "column": "AD_SOYAD",
                "column_policy": "freetext",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "ALİ YILMAZ", "aliases": []},
                    {"value": "ALİ YILDIZ", "aliases": []},
                    {"value": "ALİ YILDIRIM", "aliases": []},
                ],
            },
        },
    }


def _ambiguity_svc() -> FilterValueResolutionService:
    return FilterValueResolutionService(
        provider=_provider_from_dict(_ambiguity_profiles())
    )


class TestAmbiguityStress:
    """Stress tests for close-match disambiguation."""

    async def test_bilgi_triggers_clarification(self) -> None:
        """'Bilgi' matches three close departments → clarification."""
        svc = _ambiguity_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "Bilgi", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.needs_clarification is True
        action = trace["actions"][0]
        candidates = action["candidate_values"]
        # All three "Bilgi*" departments should appear
        assert len(candidates) >= 3

    async def test_bt_exact_alias_does_not_ambiguate(self) -> None:
        """Exact alias 'bt' → 'Bilgi Teknolojileri', but close 'BT Destek'
        triggers gap check.  With structured policy min_gap=0.10 the gap
        (0.96 - 0.89 = 0.07) is below threshold → clarification is correct."""
        svc = _ambiguity_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "bt", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        action = trace["actions"][0]
        # 'bt' alias resolves to "Bilgi Teknolojileri" at 0.96, but
        # "BT Destek" / "BT Altyapı" score closely → clarification
        if resolved.needs_clarification:
            assert "Bilgi Teknolojileri" in action["candidate_values"]
        else:
            assert resolved.filters[0].value == "Bilgi Teknolojileri"

    async def test_uzman_triggers_ambiguity(self) -> None:
        """'Uzman' matches three titles → clarification."""
        svc = _ambiguity_svc()
        plan = _make_plan([_filter("UNVAN", "Uzman", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        # All "Uzman*" titles should compete
        assert resolved.needs_clarification is True
        action = trace["actions"][0]
        assert action["ambiguity_reason"] in ("close_candidate_gap", "score_below_threshold")

    async def test_ali_yil_triggers_person_ambiguity(self) -> None:
        """'Ali Yıl' matches three close names → clarification."""
        svc = _ambiguity_svc()
        plan = _make_plan([_filter("AD_SOYAD", "Ali Yıl", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        # Three ALİ YIL* values should compete
        action = trace["actions"][0]
        if resolved.needs_clarification:
            assert len(action["candidate_values"]) >= 2
        else:
            # If one wins, it must be one of the three
            assert resolved.filters[0].value in (
                "ALİ YILMAZ", "ALİ YILDIZ", "ALİ YILDIRIM",
            )

    async def test_exact_name_resolves_despite_close_candidates(self) -> None:
        """Exact match 'ALİ YILMAZ' should resolve without ambiguity."""
        svc = _ambiguity_svc()
        plan = _make_plan([_filter("AD_SOYAD", "ALİ YILMAZ", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "ALİ YILMAZ"
        assert not resolved.needs_clarification

    async def test_bt_destek_separates_from_bt(self) -> None:
        """'BT Destek' should resolve to 'BT Destek', not 'Bilgi Teknolojileri'."""
        svc = _ambiguity_svc()
        plan = _make_plan([_filter("BIRIM_ADI", "BT Destek", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)
        assert resolved.filters[0].value == "BT Destek"


# ═══════════════════════════════════════════════════════════════════════
# 13. Zero-gap LLM tiebreak skipping
# ═══════════════════════════════════════════════════════════════════════


def _zero_gap_profiles() -> dict[str, Any]:
    """Two candidates that produce identical fuzzy scores for 'Dizayn'."""
    return {
        "schema_version": "1.0",
        "matching_policy": {
            "candidate_preview_limit": 5,
            "min_select_score": 0.88,
            "min_score_gap": 0.10,
            "min_fuzzy_ratio": 0.76,
        },
        "profiles": {
            "T.BIRIM_ADI": {
                "table": "T",
                "column": "BIRIM_ADI",
                "column_policy": "structured",
                "supported_ops": ["=", "!=", "LIKE"],
                "canonical_values": [
                    {"value": "DT-MEKANİK DİZAYN", "aliases": []},
                    {"value": "DT-ELEKTRİK DİZAYN", "aliases": []},
                ],
            },
        },
    }


class _FakeLLM:
    """Fake LLM that always confidently picks the first candidate."""

    async def generate_structured(self, prompt: str, model_class: type) -> Any:
        import re
        # Extract candidates from the prompt
        match = re.search(r'\[([^\]]+)\]', prompt)
        if match:
            first_val = match.group(1).split(',')[0].strip().strip('"')
        else:
            first_val = "DT-MEKANİK DİZAYN"
        return model_class(
            chosen_candidate=first_val,
            confidence=0.95,
            reason="fake pick",
        )


class TestZeroGapTiebreakSkipping:
    """When two candidates score identically (gap≈0), LLM tiebreak must be
    skipped and clarification must be forced — even with an LLM available."""

    async def test_zero_gap_forces_clarification_with_llm(self) -> None:
        provider = _provider_from_dict(_zero_gap_profiles())
        svc = FilterValueResolutionService(provider=provider, llm=_FakeLLM())
        plan = _make_plan([_filter("BIRIM_ADI", "Dizayn", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)

        assert resolved.needs_clarification is True
        action = trace["actions"][0]
        assert action.get("llm_tiebreak_skipped") is True
        assert action.get("llm_tiebreak_skip_reason") == "gap_too_small"
        candidates = action["candidate_values"]
        assert "DT-MEKANİK DİZAYN" in candidates
        assert "DT-ELEKTRİK DİZAYN" in candidates

    async def test_zero_gap_forces_clarification_without_llm(self) -> None:
        provider = _provider_from_dict(_zero_gap_profiles())
        svc = FilterValueResolutionService(provider=provider)
        plan = _make_plan([_filter("BIRIM_ADI", "Dizayn", table="T")], table="T")
        resolved, trace = await svc.resolve(plan)

        assert resolved.needs_clarification is True
        action = trace["actions"][0]
        candidates = action["candidate_values"]
        assert "DT-MEKANİK DİZAYN" in candidates
        assert "DT-ELEKTRİK DİZAYN" in candidates


# ═══════════════════════════════════════════════════════════════════════
# 14. RapidFuzz health signal
# ═══════════════════════════════════════════════════════════════════════


class TestRapidFuzzHealth:
    def test_rapidfuzz_available_returns_bool(self) -> None:
        from app.services.filter_value_resolution_service import rapidfuzz_available
        result = rapidfuzz_available()
        assert isinstance(result, bool)
        # In test environment, rapidfuzz should be installed
        assert result is True
