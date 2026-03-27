"""Tests for the Filter Value Resolution stage.

Sprint 2 — Correct Filter Value Resolution.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.services import filter_value_resolution_service as fvr_module
from app.services.filter_value_profile_provider import FilterValueProfileProvider
from app.services.filter_value_resolution_service import FilterValueResolutionService
from app.services.trace_serializer import build_filter_value_resolution_payload


def _make_plan(filters: list[FilterSpec]) -> QueryPlan:
    return QueryPlan(
        intent="employee_list",
        table="XXBT_PDKS_PER_DETAILS_V",
        filters=filters,
    )


def _filter(column: str, value: Any, op: FilterOp = FilterOp.EQ) -> FilterSpec:
    return FilterSpec(column=column, op=op, value=value)


def _minimal_value_profiles_json() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "matching_policy": {
            "candidate_preview_limit": 3,
            "min_select_score": 0.88,
            "min_score_gap": 0.08,
            "min_fuzzy_ratio": 0.76,
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
                "supported_ops": ["=", "!="],
                "canonical_values": [
                    {"value": "Bilgi Teknolojileri", "aliases": ["it", "bt", "bilgi teknolojileri"]},
                    {"value": "İnsan Kaynakları", "aliases": ["ik", "insan kaynaklari"]},
                ],
            },
            "XXBT_PDKS_PER_DETAILS_V.LOCATION_ADI": {
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "column": "LOCATION_ADI",
                "supported_ops": ["=", "!="],
                "canonical_values": [
                    {"value": "İstanbul", "aliases": ["istanbul", "istanbul buro"]},
                    {"value": "Ankara", "aliases": ["ankara"]},
                ],
            },
            "XXBT_PDKS_PER_DETAILS_V.UNVAN": {
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "column": "UNVAN",
                "supported_ops": ["=", "!="],
                "canonical_values": [
                    {"value": "Proje Yöneticisi", "aliases": ["yonetici", "manager"]},
                    {"value": "Sistem Yöneticisi", "aliases": ["yonetici", "sysadmin"]},
                    {"value": "Yazılım Uzmanı", "aliases": ["yazilim uzmani", "uzman"]},
                ],
            },
            "XXBT_PDKS_PER_DETAILS_V.MASRAF_MERKEZI": {
                "table": "XXBT_PDKS_PER_DETAILS_V",
                "column": "MASRAF_MERKEZI",
                "supported_ops": ["=", "!="],
                "canonical_values": [
                    {"value": "BT-01", "aliases": ["bt01", "bt 01"]},
                    {"value": "BT-02", "aliases": ["bt02", "bt 02"]},
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


class TestFilterValueProfileProvider:
    def test_loads_profile_from_centralized_config(self) -> None:
        provider = _provider_from_dict(_minimal_value_profiles_json())

        profile = provider.get_profile("XXBT_PDKS_PER_DETAILS_V", "BIRIM_ADI")

        assert profile is not None
        assert profile.column == "BIRIM_ADI"
        assert profile.table == "XXBT_PDKS_PER_DETAILS_V"
        assert profile.canonical_values[0].value == "Bilgi Teknolojileri"

    def test_default_config_path_stays_under_existing_data_config_family(self) -> None:
        provider = FilterValueProfileProvider()

        assert provider.config_path.as_posix().endswith("data/config/filter_value_profiles.json")


class TestFilterValueResolutionService:
    def setup_method(self) -> None:
        self.svc = FilterValueResolutionService(provider=_provider_from_dict(_minimal_value_profiles_json()))

    def test_department_alias_resolves_to_canonical_value(self) -> None:
        plan = _make_plan([_filter("BIRIM_ADI", "IT")])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan.filters[0].value == "Bilgi Teknolojileri"
        assert trace["any_changed"] is True
        assert trace["changed_count"] == 1
        assert trace["total_filters_seen"] == 1
        assert trace["processed_filters"] == 1
        assert trace["changed_filters"] == 1
        assert trace["actions"][0]["reason"] == "exact_alias_match"

    def test_location_surface_form_resolves_to_canonical_spelling(self) -> None:
        plan = _make_plan([_filter("LOCATION_ADI", "Istanbul")])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan.filters[0].value == "İstanbul"
        assert trace["actions"][0]["resolved_value"] == "İstanbul"
        assert resolved_plan.filters[0].op == FilterOp.EQ

    def test_already_canonical_value_is_no_change(self) -> None:
        plan = _make_plan([_filter("BIRIM_ADI", "Bilgi Teknolojileri")])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan is plan
        assert trace["any_changed"] is False
        assert trace["actions"][0]["reason"] == "already_canonical_exact"

    def test_ambiguous_title_requests_clarification(self) -> None:
        plan = _make_plan([_filter("UNVAN", "yonetici")])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan.needs_clarification is True
        assert trace["clarification_required"] is True
        assert trace["actions"][0]["reason"] == "ambiguous_candidate_clarification"
        assert "Proje Yöneticisi" in (resolved_plan.clarification_message or "")
        assert "Sistem Yöneticisi" in (resolved_plan.clarification_message or "")

    def test_unknown_value_requests_clarification(self) -> None:
        plan = _make_plan([_filter("BIRIM_ADI", "Satis")])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan.needs_clarification is True
        assert trace["actions"][0]["reason"] == "no_confident_candidate_clarification"

    def test_out_of_scope_column_is_no_op(self) -> None:
        plan = _make_plan([_filter("BORDROLU", "E")])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan is plan
        assert trace["actions"][0]["reason"] == "out_of_scope_column_no_op"
        assert trace["actions"][0]["no_op"] is True

    def test_non_string_value_is_no_op(self) -> None:
        plan = _make_plan([_filter("PERSON_ID", 10)])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan is plan
        assert trace["actions"][0]["reason"] == "non_string_value_no_op"

    def test_null_check_is_no_op(self) -> None:
        plan = _make_plan([FilterSpec(column="CIKIS_TARIHI", op=FilterOp.IS_NULL)])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan is plan
        assert trace["actions"][0]["reason"] == "non_string_value_no_op"

    def test_unsupported_operator_never_converts_to_like(self) -> None:
        plan = _make_plan([_filter("LOCATION_ADI", "Ist", FilterOp.LIKE)])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan is plan
        assert trace["actions"][0]["reason"] == "unsupported_operator_no_op"
        assert trace["total_filters_seen"] == 1
        assert trace["processed_filters"] == 1
        assert trace["skipped_filters"] == 1
        assert trace["actions"][0]["resolved_value"] == "Ist"
        assert plan.filters[0].op == FilterOp.LIKE

    def test_cost_center_alias_resolves_without_broad_fallback(self) -> None:
        plan = _make_plan([_filter("MASRAF_MERKEZI", "bt01")])

        resolved_plan, trace = self.svc.resolve(plan)

        assert resolved_plan.filters[0].value == "BT-01"
        assert trace["actions"][0]["reason"] == "exact_alias_match"


class TestServiceModuleArchitecture:
    def test_service_module_has_no_inline_business_value_dictionaries(self) -> None:
        forbidden_names = [
            "_CANONICAL_VALUES_BY_COLUMN",
            "_COLUMN_VALUE_ALIASES",
            "_FILTER_VALUE_MAP",
        ]
        for name in forbidden_names:
            assert hasattr(fvr_module, name) is False, f"inline constant still present: {name}"


class TestBuildFilterValueResolutionPayload:
    def test_payload_with_clarification(self) -> None:
        trace = {
            "any_changed": False,
            "total_filters_seen": 1,
            "processed_filters": 1,
            "skipped_filters": 1,
            "changed_filters": 0,
            "changed_count": 0,
            "total_filters": 1,
            "clarification_required": True,
            "skip_reasons": {"ambiguous_candidate_clarification": 1},
            "changed_items": [],
            "original_filters": [{"column": "UNVAN", "operator": "=", "value": "yonetici", "table": None}],
            "final_filters": [{"column": "UNVAN", "operator": "=", "value": "yonetici", "table": None}],
            "actions": [
                {
                    "column": "UNVAN",
                    "operator": "=",
                    "original_value": "yonetici",
                    "resolved_value": "yonetici",
                    "changed": False,
                    "clarification_required": True,
                    "reason": "ambiguous_candidate_clarification",
                    "confidence": 0.96,
                    "candidate_values": ["Proje Yöneticisi", "Sistem Yöneticisi"],
                }
            ],
        }

        payload = build_filter_value_resolution_payload(trace)

        assert payload["clarification_required"] is True
        assert payload["total_filters"] == 1
        assert payload["total_filters_seen"] == 1
        assert payload["processed_filters"] == 1
        assert payload["changed_count"] == 0
        assert payload["actions"][0]["original_value"] == "yonetici"
        assert payload["actions"][0]["candidate_values"] == ["Proje Yöneticisi", "Sistem Yöneticisi"]

    def test_payload_with_change(self) -> None:
        trace = {
            "any_changed": True,
            "total_filters_seen": 1,
            "processed_filters": 1,
            "skipped_filters": 0,
            "changed_filters": 1,
            "changed_count": 1,
            "total_filters": 1,
            "clarification_required": False,
            "skip_reasons": {},
            "changed_items": [{"column": "BIRIM_ADI", "original_value": "IT", "resolved_value": "Bilgi Teknolojileri", "reason": "exact_alias_match"}],
            "original_filters": [{"column": "BIRIM_ADI", "operator": "=", "value": "IT", "table": None}],
            "final_filters": [{"column": "BIRIM_ADI", "operator": "=", "value": "Bilgi Teknolojileri", "table": None}],
            "actions": [
                {
                    "column": "BIRIM_ADI",
                    "operator": "=",
                    "original_value": "IT",
                    "resolved_value": "Bilgi Teknolojileri",
                    "changed": True,
                    "clarification_required": False,
                    "reason": "exact_alias_match",
                    "confidence": 0.96,
                    "candidate_values": ["Bilgi Teknolojileri"],
                }
            ],
        }

        payload = build_filter_value_resolution_payload(trace)

        assert payload["any_changed"] is True
        assert payload["changed_count"] == 1
        assert payload["processed_filters"] == 1
        assert payload["actions"][0]["resolved_value"] == "Bilgi Teknolojileri"