"""Tests for the Filter Column Resolution Stage.

Sprint 1 — Grounding Hardening: Correct Filter Column Resolution.

Coverage:
1. Department dimension -> BIRIM_ADI correction
2. Location dimension -> LOCATION_ADI no-op (already correct)
3. Title dimension -> UNVAN no-op (already correct)
4. Organization dimension -> ORGANIZATION_ADI stays correct
5. No dimension signal -> no-op passthrough
6. Non-dimension columns (BORDROLU, STAJYER, CIKIS_TARIHI) -> never remapped
7. IS_NULL / IS_NOT_NULL operators on non-confusable columns -> no-op
8. Clarification plans -> no-op passthrough
9. Plans without filters -> no-op passthrough
10. Multi-filter plan -> each filter evaluated independently
11. Cost-center phrase detection
12. Pipeline Live View trace payload shape
13. Turkish morphological suffix handling
14. Cross-domain PO filters -> not remapped by HR dimension logic
15. Provider loads from centralized config, not inline constants
16. Shared semantic metadata overlays non-dimension columns
17. Service safe no-op when config unavailable
18. Inline hardcoded business maps are absent from the service module
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.query_plan import FilterOp, FilterSpec, QueryPlan
from app.services import filter_column_resolution_service as fcr_module
from app.services.filter_column_resolution_service import (
    FilterColumnResolutionService,
    _detect_intended_dimension,
    _norm,
)
from app.services.grounding_config_provider import GroundingConfigProvider


def _make_plan(filters: list[FilterSpec], *, needs_clarification: bool = False) -> QueryPlan:
    return QueryPlan(
        intent="employee_list",
        table="XXBT_PDKS_PER_DETAILS_V",
        filters=filters,
        needs_clarification=needs_clarification,
    )


def _filter(column: str, op: FilterOp = FilterOp.EQ, value: Any = "test") -> FilterSpec:
    return FilterSpec(column=column, op=op, value=value)


def _minimal_grounding_json() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "dimensions": {
            "cost_center": {
                "preferred_column": "MASRAF_MERKEZI",
                "priority": 1,
                "keywords": ["masraf", "maliyet"],
                "phrases": ["masraf merkezi", "cost center"],
                "confusable_columns": ["MASRAF_MERKEZI", "COST_CENTER", "CC_NAME"],
            },
            "department": {
                "preferred_column": "BIRIM_ADI",
                "priority": 2,
                "keywords": ["departman", "birim", "bolum", "department"],
                "phrases": [
                    "birimindeki",
                    "biriminde",
                    "bolumundeki",
                    "bolumunde",
                    "departmanindaki",
                    "departmaninda",
                ],
                "confusable_columns": [
                    "BIRIM_ADI",
                    "ORGANIZATION_ADI",
                    "DEPARTMENT_NAME",
                    "DEPT_NAME",
                    "DEPARTMENT",
                    "BOLUM",
                ],
            },
            "location": {
                "preferred_column": "LOCATION_ADI",
                "priority": 3,
                "keywords": ["lokasyon", "sehir", "ofis", "location", "city"],
                "phrases": [],
                "confusable_columns": ["LOCATION_ADI", "LOKASYON_ADI", "LOCATION", "CITY", "SEHIR"],
            },
            "title": {
                "preferred_column": "UNVAN",
                "priority": 4,
                "keywords": ["unvan", "title", "gorev", "pozisyon"],
                "phrases": ["unvanli", "unvanina sahip", "gorev tanimi"],
                "confusable_columns": ["UNVAN", "GOREV_TANIMI", "JOB_TITLE", "POSITION", "POZISYON"],
            },
            "organization": {
                "preferred_column": "ORGANIZATION_ADI",
                "priority": 5,
                "keywords": ["organizasyon", "organization"],
                "phrases": [],
                "confusable_columns": ["ORGANIZATION_ADI", "BIRIM_ADI", "ORG_NAME"],
            },
        },
        "non_dimension_columns": [
            "BORDROLU",
            "STAJYER",
            "CALISAN_TIPI",
            "CIKIS_TARIHI",
            "ISE_GIRIS_TARIHI",
            "EMAIL",
            "USER_NAME",
            "SICIL_NO",
            "PERSON_ID",
            "FULL_NAME",
            "ORG_ID",
            "AUTHORIZATION_STATUS",
            "APPROVED_FLAG",
            "CANCEL_FLAG",
            "CLOSED_CODE",
            "TYPE_LOOKUP_CODE",
            "VENDOR_ID",
            "PO_HEADER_ID",
            "PO_LINE_ID",
        ],
    }


def _provider_from_dict(data: dict[str, Any]) -> GroundingConfigProvider:
    with TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "filter_grounding.json"
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        return GroundingConfigProvider(config_path=cfg_path)


class TestNorm:
    def test_removes_turkish_diacritics(self) -> None:
        assert _norm("bölüm") == "bolum"
        assert _norm("ünvan") == "unvan"
        assert _norm("şehir") == "sehir"
        assert _norm("lokasyon") == "lokasyon"

    def test_handles_uppercase(self) -> None:
        assert _norm("DEPARTMAN") == "departman"
        assert _norm("BİRİM") == "birim"


class TestDetectIntendedDimension:
    def test_department_via_departman_token(self) -> None:
        assert _detect_intended_dimension("it departmanindaki calisanlar") == "department"

    def test_department_via_birim_token(self) -> None:
        assert _detect_intended_dimension("it birimindeki calisanlar") == "department"

    def test_department_via_bolum_token(self) -> None:
        assert _detect_intended_dimension("yazilim bolumundeki calisanlar") == "department"

    def test_organization_when_keyword_present(self) -> None:
        assert _detect_intended_dimension("organizasyon bazli rapor") == "organization"

    def test_location_via_sehir(self) -> None:
        assert _detect_intended_dimension("ankara sehirindeki calisanlar") == "location"

    def test_location_via_lokasyon(self) -> None:
        assert _detect_intended_dimension("lokasyon bazli listeleme") == "location"

    def test_location_via_ofis(self) -> None:
        assert _detect_intended_dimension("istanbul ofisindeki calisanlar") == "location"

    def test_title_via_unvan(self) -> None:
        assert _detect_intended_dimension("yonetici unvanli calisanlar") == "title"

    def test_title_via_unvanli_phrase(self) -> None:
        assert _detect_intended_dimension("uzman unvanli personeli goster") == "title"

    def test_cost_center_phrase(self) -> None:
        assert _detect_intended_dimension("masraf merkezi bazli liste") == "cost_center"

    def test_no_signal_returns_none(self) -> None:
        assert _detect_intended_dimension("aktif calisanlari goster") is None

    def test_no_signal_bordrolu(self) -> None:
        assert _detect_intended_dimension("bordrolu calisanlari listele") is None

    def test_no_signal_stajyer(self) -> None:
        assert _detect_intended_dimension("stajyer calisanlari goster") is None

    def test_department_wins_over_organization_when_both_present(self) -> None:
        dim = _detect_intended_dimension("it biriminin organizasyon yapisi")
        assert dim == "department"

    def test_returns_none_with_empty_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "filter_grounding.json"
            cfg_path.write_text(
                json.dumps({"dimensions": {}, "non_dimension_columns": []}),
                encoding="utf-8",
            )
            provider = GroundingConfigProvider(config_path=cfg_path)
            result = _detect_intended_dimension(
                "IT departmanindaki calisanlar",
                provider=provider,
            )
        assert result is None

    def test_provider_override_used_in_detection(self) -> None:
        provider = _provider_from_dict(_minimal_grounding_json())
        assert _detect_intended_dimension(
            "it birimindeki calisanlar",
            provider=provider,
        ) == "department"


class TestFilterColumnResolutionService:
    @pytest.fixture(autouse=True)
    def service(self) -> None:
        self.svc = FilterColumnResolutionService(provider=_provider_from_dict(_minimal_grounding_json()))

    def test_department_corrects_organization_adi_to_birim_adi(self) -> None:
        plan = _make_plan([_filter("ORGANIZATION_ADI", FilterOp.EQ, "IT")])
        result = self.svc.resolve(plan, "IT departmanindaki calisanlari goster")
        assert result.any_changed is True
        assert result.resolved_plan.filters[0].column == "BIRIM_ADI"
        assert result.resolved_plan.filters[0].value == "IT"
        assert result.resolved_plan.filters[0].op == FilterOp.EQ

    def test_department_corrects_turkish_suffix_birimindeki(self) -> None:
        plan = _make_plan([_filter("ORGANIZATION_ADI", FilterOp.EQ, "Finans")])
        result = self.svc.resolve(plan, "Finans birimindeki personeli listele")
        assert result.any_changed is True
        assert result.resolved_plan.filters[0].column == "BIRIM_ADI"

    def test_department_corrects_bolum_keyword(self) -> None:
        plan = _make_plan([_filter("ORGANIZATION_ADI", FilterOp.EQ, "Yazilim")])
        result = self.svc.resolve(plan, "Yazilim bolumundeki calisanlari getir")
        assert result.any_changed is True
        assert result.resolved_plan.filters[0].column == "BIRIM_ADI"

    def test_department_corrects_bolum_column_with_like(self) -> None:
        """LLM picks BOLUM column + LIKE for 'dizayn bölümü' → corrected to BIRIM_ADI."""
        plan = _make_plan([_filter("BOLUM", FilterOp.LIKE, "%dizayn%")])
        result = self.svc.resolve(plan, "dizayn bolumunde calisanlari listele")
        assert result.any_changed is True
        assert result.resolved_plan.filters[0].column == "BIRIM_ADI"
        assert result.resolved_plan.filters[0].op == FilterOp.LIKE
        assert result.resolved_plan.filters[0].value == "%dizayn%"

    def test_location_already_correct_no_change(self) -> None:
        plan = _make_plan([_filter("LOCATION_ADI", FilterOp.EQ, "Istanbul")])
        result = self.svc.resolve(plan, "Istanbul'daki calisanlari getir")
        assert result.any_changed is False
        assert result.resolved_plan is plan

    def test_location_summary_says_already_correct(self) -> None:
        plan = _make_plan([_filter("LOCATION_ADI", FilterOp.EQ, "Ankara")])
        result = self.svc.resolve(plan, "Ankara sehirindeki personel")
        assert result.actions[0].reason == "already_correct_column"

    def test_title_already_correct_no_change(self) -> None:
        plan = _make_plan([_filter("UNVAN", FilterOp.EQ, "Yonetici")])
        result = self.svc.resolve(plan, "Yonetici unvanli calisanlari listele")
        assert result.any_changed is False

    def test_title_confusable_corrected(self) -> None:
        plan = _make_plan([_filter("JOB_TITLE", FilterOp.EQ, "Uzman")])
        result = self.svc.resolve(plan, "Uzman unvanli calisanlari getir")
        assert result.any_changed is True
        assert result.resolved_plan.filters[0].column == "UNVAN"

    def test_bordrolu_protected(self) -> None:
        plan = _make_plan([_filter("BORDROLU", FilterOp.EQ, "1")])
        result = self.svc.resolve(plan, "bordrolu calisanlari listele")
        assert result.any_changed is False
        assert result.actions[0].reason == "protected_column_no_op"

    def test_stajyer_protected(self) -> None:
        plan = _make_plan([_filter("STAJYER", FilterOp.EQ, "1")])
        result = self.svc.resolve(plan, "stajyer calisanlari goster")
        assert result.any_changed is False
        assert result.actions[0].reason == "protected_column_no_op"

    def test_cikis_tarihi_protected(self) -> None:
        plan = _make_plan([_filter("CIKIS_TARIHI", FilterOp.IS_NULL)])
        result = self.svc.resolve(plan, "aktif calisanlari goster")
        assert result.any_changed is False
        assert result.actions[0].reason == "protected_column_no_op"

    def test_email_is_not_null_protected(self) -> None:
        plan = _make_plan([_filter("EMAIL", FilterOp.IS_NOT_NULL)])
        result = self.svc.resolve(plan, "emaili olan calisanlar")
        assert result.any_changed is False
        assert result.actions[0].reason == "protected_column_no_op"

    def test_no_dimension_signal_is_noop(self) -> None:
        plan = _make_plan([_filter("ORGANIZATION_ADI", FilterOp.EQ, "ABC")])
        result = self.svc.resolve(plan, "aktif calisanlari listele")
        assert result.any_changed is False
        assert result.actions[0].reason == "no_dimension_signal_in_question"

    def test_empty_question_is_noop(self) -> None:
        plan = _make_plan([_filter("ORGANIZATION_ADI", FilterOp.EQ, "ABC")])
        result = self.svc.resolve(plan, "")
        assert result.any_changed is False

    def test_non_confusable_column_not_remapped_for_department(self) -> None:
        plan = _make_plan([FilterSpec(column="ISE_YILI", op=FilterOp.EQ, value="2024")])
        result = self.svc.resolve(plan, "IT departmanindaki calisanlar")
        assert result.any_changed is False
        assert "not_confusable" in result.actions[0].reason

    def test_clarification_plan_is_passthrough(self) -> None:
        plan = QueryPlan(
            intent="clarification_required",
            table=None,
            needs_clarification=True,
            clarification_message="Hangi departmani kastediyorsunuz?",
        )
        result = self.svc.resolve(plan, "IT departmanindaki calisanlar")
        assert result.any_changed is False
        assert result.resolved_plan is plan

    def test_no_filters_is_passthrough(self) -> None:
        plan = _make_plan([])
        result = self.svc.resolve(plan, "IT departmanindaki calisanlar")
        assert result.any_changed is False
        assert result.resolved_plan is plan

    def test_multi_filter_only_wrong_column_corrected(self) -> None:
        plan = _make_plan([
            _filter("ORGANIZATION_ADI", FilterOp.EQ, "IT"),
            _filter("CIKIS_TARIHI", FilterOp.IS_NULL),
        ])
        result = self.svc.resolve(plan, "IT departmanindaki aktif calisanlar")
        assert result.any_changed is True
        assert result.resolved_plan.filters[0].column == "BIRIM_ADI"
        assert result.resolved_plan.filters[1].column == "CIKIS_TARIHI"

    def test_multi_filter_all_already_correct(self) -> None:
        plan = _make_plan([
            _filter("BIRIM_ADI", FilterOp.EQ, "IT"),
            _filter("CIKIS_TARIHI", FilterOp.IS_NULL),
        ])
        result = self.svc.resolve(plan, "IT departmanindaki aktif calisanlar")
        assert result.any_changed is False
        assert result.resolved_plan is plan

    def test_cost_center_phrase_detection(self) -> None:
        plan = _make_plan([_filter("COST_CENTER", FilterOp.EQ, "ABC-001")])
        result = self.svc.resolve(plan, "masraf merkezi ABC-001 olan calisanlar")
        assert result.any_changed is True
        assert result.resolved_plan.filters[0].column == "MASRAF_MERKEZI"

    def test_trace_dict_structure_when_changed(self) -> None:
        plan = _make_plan([_filter("ORGANIZATION_ADI", FilterOp.EQ, "IT")])
        result = self.svc.resolve(plan, "IT departmanindaki calisanlar")
        trace = result.as_trace_dict()
        assert trace["any_changed"] is True
        assert trace["changed_count"] == 1
        assert trace["total_filters"] == 1
        action = trace["actions"][0]
        assert action["original_column"] == "ORGANIZATION_ADI"
        assert action["resolved_column"] == "BIRIM_ADI"
        assert action["changed"] is True
        assert action["dimension"] == "department"
        assert action["confidence"] == "high"
        assert "dimension_department" in action["reason"]

    def test_trace_dict_structure_when_no_change(self) -> None:
        plan = _make_plan([_filter("BIRIM_ADI", FilterOp.EQ, "IT")])
        result = self.svc.resolve(plan, "IT departmanindaki calisanlar")
        trace = result.as_trace_dict()
        assert trace["any_changed"] is False
        assert trace["changed_count"] == 0
        assert trace["total_filters"] == 1

    def test_trace_dict_structure_no_filters(self) -> None:
        plan = _make_plan([])
        result = self.svc.resolve(plan, "aktif calisanlar")
        trace = result.as_trace_dict()
        assert trace["any_changed"] is False
        assert trace["total_filters"] == 0

    def test_operator_preserved_on_correction(self) -> None:
        plan = _make_plan([_filter("ORGANIZATION_ADI", FilterOp.NEQ, "Finance")])
        result = self.svc.resolve(plan, "Finance departmaninda olmayan calisanlar")
        assert result.any_changed is True
        assert result.resolved_plan.filters[0].op == FilterOp.NEQ
        assert result.resolved_plan.filters[0].value == "Finance"

    def test_table_qualifier_preserved_on_correction(self) -> None:
        plan = _make_plan([
            FilterSpec(
                column="ORGANIZATION_ADI",
                op=FilterOp.EQ,
                value="IT",
                table="XXBT_PDKS_PER_DETAILS_V",
            )
        ])
        result = self.svc.resolve(plan, "IT departmanindaki calisanlar")
        corrected = result.resolved_plan.filters[0]
        assert corrected.column == "BIRIM_ADI"
        assert corrected.table == "XXBT_PDKS_PER_DETAILS_V"


class TestGroundingConfigProvider:
    def test_loads_from_real_config_file(self) -> None:
        provider = GroundingConfigProvider()
        assert provider.loaded_ok is True

    def test_dimensions_loaded_from_file(self) -> None:
        provider = GroundingConfigProvider()
        dim_names = provider.get_dimension_names_by_priority()
        assert "department" in dim_names
        assert "location" in dim_names
        assert "title" in dim_names
        assert "cost_center" in dim_names
        assert "organization" in dim_names

    def test_dimension_priority_order(self) -> None:
        provider = GroundingConfigProvider()
        cc = provider.get_dimension_config("cost_center")
        dep = provider.get_dimension_config("department")
        loc = provider.get_dimension_config("location")
        org = provider.get_dimension_config("organization")
        assert cc is not None and dep is not None and loc is not None and org is not None
        assert cc.priority < dep.priority < loc.priority < org.priority

    def test_preferred_column_from_config(self) -> None:
        provider = GroundingConfigProvider()
        assert provider.get_dimension_config("department").preferred_column == "BIRIM_ADI"
        assert provider.get_dimension_config("location").preferred_column == "LOCATION_ADI"
        assert provider.get_dimension_config("title").preferred_column == "UNVAN"
        assert provider.get_dimension_config("cost_center").preferred_column == "MASRAF_MERKEZI"
        assert provider.get_dimension_config("organization").preferred_column == "ORGANIZATION_ADI"

    def test_non_dimension_columns_loaded(self) -> None:
        provider = GroundingConfigProvider()
        assert provider.is_non_dimension_column("BORDROLU") is True
        assert provider.is_non_dimension_column("STAJYER") is True
        assert provider.is_non_dimension_column("CIKIS_TARIHI") is True
        assert provider.is_non_dimension_column("EMAIL") is True
        assert provider.is_non_dimension_column("PERSON_ID") is True

    def test_non_dimension_column_lookup_requires_uppercase(self) -> None:
        provider = GroundingConfigProvider()
        assert provider.is_non_dimension_column("BORDROLU") is True
        assert provider.is_non_dimension_column("bordrolu") is False

    def test_dimension_column_not_in_non_dimension(self) -> None:
        provider = GroundingConfigProvider()
        assert provider.is_non_dimension_column("BIRIM_ADI") is False
        assert provider.is_non_dimension_column("UNVAN") is False
        assert provider.is_non_dimension_column("LOCATION_ADI") is False

    def test_unknown_dimension_returns_none(self) -> None:
        provider = GroundingConfigProvider()
        assert provider.get_dimension_config("nonexistent_dim") is None

    def test_missing_config_file_degrades_to_empty(self) -> None:
        provider = GroundingConfigProvider(config_path=Path("/nonexistent/filter_grounding.json"))
        assert provider.loaded_ok is False
        assert provider.get_dimension_priority_order() == []

    def test_invalid_json_degrades_to_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "filter_grounding.json"
            bad_path.write_text("{ this is not valid json }", encoding="utf-8")
            provider = GroundingConfigProvider(config_path=bad_path)
        assert provider.loaded_ok is False

    def test_extra_non_dimension_columns_merged(self) -> None:
        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "filter_grounding.json"
            cfg_path.write_text(
                json.dumps({"dimensions": {}, "non_dimension_columns": ["MY_FLAG"]}),
                encoding="utf-8",
            )
            provider = GroundingConfigProvider(
                config_path=cfg_path,
                extra_non_dimension_columns=frozenset({"EXTRA_COLUMN"}),
            )
        assert provider.is_non_dimension_column("MY_FLAG") is True
        assert provider.is_non_dimension_column("EXTRA_COLUMN") is True

    def test_provider_overlays_shared_semantic_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_registry = SimpleNamespace(
            get_all_entities=lambda: [
                SimpleNamespace(
                    likely_identifiers=["PERSON_ID", "SICIL_NO"],
                    status_filter_column="CIKIS_TARIHI",
                    time_column="ISE_GIRIS_TARIHI",
                )
            ]
        )
        monkeypatch.setattr(
            "app.services.grounding_config_provider.get_registry",
            lambda: fake_registry,
        )

        with TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "filter_grounding.json"
            cfg_path.write_text(
                json.dumps({"dimensions": {}, "non_dimension_columns": []}),
                encoding="utf-8",
            )
            provider = GroundingConfigProvider(config_path=cfg_path)

        assert provider.is_non_dimension_column("PERSON_ID") is True
        assert provider.is_non_dimension_column("SICIL_NO") is True
        assert provider.is_non_dimension_column("CIKIS_TARIHI") is True
        assert provider.is_non_dimension_column("ISE_GIRIS_TARIHI") is True

    def test_service_uses_injected_provider(self) -> None:
        provider = _provider_from_dict(_minimal_grounding_json())
        svc = FilterColumnResolutionService(provider=provider)
        assert svc._provider is provider

    def test_service_noop_when_config_not_loaded(self) -> None:
        bad_provider = GroundingConfigProvider(config_path=Path("/no/such/path.json"))
        svc = FilterColumnResolutionService(provider=bad_provider)
        plan = _make_plan([_filter("ORGANIZATION_ADI", FilterOp.EQ, "IT")])
        result = svc.resolve(plan, "IT departmanindaki calisanlar")
        assert result.any_changed is False
        assert result.resolved_plan is plan

    def test_confusable_columns_loaded_for_department(self) -> None:
        provider = GroundingConfigProvider()
        dep = provider.get_dimension_config("department")
        assert dep is not None
        assert "BIRIM_ADI" in dep.confusable_columns
        assert "ORGANIZATION_ADI" in dep.confusable_columns


class TestServiceModuleArchitecture:
    def test_service_module_has_no_inline_business_maps(self) -> None:
        forbidden_names = [
            "_DIMENSION_PREFERRED_COLUMN",
            "_DIMENSION_ROOT_TOKENS",
            "_DIMENSION_PHRASES",
            "_DIMENSION_PRIORITY",
            "_DIMENSION_CONFUSABLE_COLUMNS",
            "_PROTECTED_COLUMNS",
        ]
        for name in forbidden_names:
            assert hasattr(fcr_module, name) is False, f"inline constant still present: {name}"


class TestBuildFilterColumnResolutionPayload:
    def test_payload_with_changes(self) -> None:
        from app.services.trace_serializer import build_filter_column_resolution_payload

        trace = {
            "any_changed": True,
            "total_filters_seen": 2,
            "processed_filters": 2,
            "skipped_filters": 1,
            "changed_filters": 1,
            "total_filters": 2,
            "changed_count": 1,
            "skip_reasons": {"protected_column_no_op": 1},
            "changed_items": [{"filter_index": 0, "original_column": "ORGANIZATION_ADI", "resolved_column": "BIRIM_ADI", "reason": "dimension_department_keyword_detected_in_question"}],
            "original_filters": [{"column": "ORGANIZATION_ADI", "operator": "=", "value": "IT", "table": None}],
            "final_filters": [{"column": "BIRIM_ADI", "operator": "=", "value": "IT", "table": None}],
            "actions": [
                {
                    "filter_index": 0,
                    "original_table": None,
                    "resolved_table": None,
                    "original_column": "ORGANIZATION_ADI",
                    "resolved_column": "BIRIM_ADI",
                    "operator": "=",
                    "original_value": "IT",
                    "resolved_value": "IT",
                    "changed": True,
                    "dimension": "department",
                    "confidence": "high",
                    "reason": "dimension_department_keyword_detected_in_question",
                },
                {
                    "filter_index": 1,
                    "original_table": None,
                    "resolved_table": None,
                    "original_column": "CIKIS_TARIHI",
                    "resolved_column": "CIKIS_TARIHI",
                    "operator": "IS_NULL",
                    "original_value": None,
                    "resolved_value": None,
                    "changed": False,
                    "dimension": None,
                    "confidence": "high",
                    "reason": "protected_column_no_op",
                },
            ],
        }
        payload = build_filter_column_resolution_payload(trace)
        assert payload["any_changed"] is True
        assert payload["changed_count"] == 1
        assert payload["total_filters"] == 2
        assert payload["total_filters_seen"] == 2
        assert payload["processed_filters"] == 2
        assert payload["skipped_filters"] == 1
        assert len(payload["actions"]) == 2
        assert payload["original_filters"][0]["column"] == "ORGANIZATION_ADI"
        assert payload["final_filters"][0]["column"] == "BIRIM_ADI"
        assert payload["actions"][0]["original_column"] == "ORGANIZATION_ADI"
        assert payload["actions"][0]["resolved_column"] == "BIRIM_ADI"
        assert payload["actions"][0]["changed"] is True

    def test_payload_no_changes(self) -> None:
        from app.services.trace_serializer import build_filter_column_resolution_payload

        trace = {
            "any_changed": False,
            "total_filters": 1,
            "changed_count": 0,
            "actions": [
                {
                    "filter_index": 0,
                    "original_column": "LOCATION_ADI",
                    "resolved_column": "LOCATION_ADI",
                    "changed": False,
                    "dimension": "location",
                    "confidence": "high",
                    "reason": "already_correct_column",
                }
            ],
        }
        payload = build_filter_column_resolution_payload(trace)
        assert payload["any_changed"] is False
        assert payload["changed_count"] == 0
        assert payload["processed_filters"] == 1

    def test_payload_handles_empty_actions(self) -> None:
        from app.services.trace_serializer import build_filter_column_resolution_payload

        trace = {"any_changed": False, "total_filters": 0, "changed_count": 0, "actions": []}
        payload = build_filter_column_resolution_payload(trace)
        assert payload["actions"] == []

    def test_payload_handles_missing_keys_gracefully(self) -> None:
        from app.services.trace_serializer import build_filter_column_resolution_payload

        payload = build_filter_column_resolution_payload({})
        assert payload["any_changed"] is False
        assert payload["total_filters"] == 0
        assert payload["changed_count"] == 0


class TestFilterColumnResolutionAccounting:
    def test_real_filter_is_counted_and_bolum_corrected(self) -> None:
        """BOLUM is now confusable for department → corrected to BIRIM_ADI."""
        service = FilterColumnResolutionService(provider=_provider_from_dict(_minimal_grounding_json()))
        plan = _make_plan([_filter("BOLUM", FilterOp.EQ, "IT")])

        result = service.resolve(plan, "IT departmanindaki calisanlar")
        trace = result.as_trace_dict()

        assert trace["total_filters_seen"] == 1
        assert trace["processed_filters"] == 1
        assert trace["changed_filters"] == 1
        assert trace["skipped_filters"] == 0
        assert trace["actions"][0]["reason"] == "dimension_department_keyword_detected_in_question"
        assert trace["actions"][0]["resolved_column"] == "BIRIM_ADI"

    def test_like_filter_is_counted_and_explained(self) -> None:
        service = FilterColumnResolutionService(provider=_provider_from_dict(_minimal_grounding_json()))
        plan = _make_plan([_filter("BIRIM_ADI", FilterOp.LIKE, "%IT%")])

        result = service.resolve(plan, "IT departmanindaki calisanlar")
        trace = result.as_trace_dict()

        assert trace["total_filters_seen"] == 1
        assert trace["processed_filters"] == 1
        assert trace["actions"][0]["operator"] == "LIKE"
        assert trace["actions"][0]["reason"] == "already_correct_column"
