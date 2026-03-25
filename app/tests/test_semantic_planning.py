"""Tests for metadata-driven semantic planning layer."""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.catalog_models import CatalogSnapshot, ColumnMetadata, ColumnType, TableMetadata
from app.domain.query_plan import AggregateFn, AggregationSpec, FilterOp, FilterSpec, QueryPlan
from app.domain.semantic_models import RegistryLookupSpec, SemanticRegistry
from app.services.semantic_planning import apply_semantic_normalization
from app.services.query_understanding import QueryUnderstanding


def _snapshot() -> CatalogSnapshot:
    return CatalogSnapshot(
        tables=[
            TableMetadata(
                name="PO_HEADERS_ALL",
                columns=[
                    ColumnMetadata(name="po_header_id", data_type=ColumnType.NUMBER, nullable=False),
                    ColumnMetadata(name="segment1", data_type=ColumnType.VARCHAR2),
                    ColumnMetadata(name="authorization_status", data_type=ColumnType.VARCHAR2),
                    ColumnMetadata(name="cancel_flag", data_type=ColumnType.VARCHAR2),
                    ColumnMetadata(name="closed_code", data_type=ColumnType.VARCHAR2),
                    ColumnMetadata(name="vendor_id", data_type=ColumnType.NUMBER),
                ],
            ),
            TableMetadata(
                name="PO_LINES_ALL",
                columns=[
                    ColumnMetadata(name="po_line_id", data_type=ColumnType.NUMBER, nullable=False),
                    ColumnMetadata(name="po_header_id", data_type=ColumnType.NUMBER, nullable=False),
                    ColumnMetadata(name="quantity", data_type=ColumnType.NUMBER),
                ],
            ),
            TableMetadata(
                name="PO_LINE_LOCATIONS_ALL",
                columns=[
                    ColumnMetadata(name="line_location_id", data_type=ColumnType.NUMBER, nullable=False),
                    ColumnMetadata(name="po_line_id", data_type=ColumnType.NUMBER, nullable=False),
                ],
            ),
            TableMetadata(
                name="PO_DISTRIBUTIONS_ALL",
                columns=[
                    ColumnMetadata(name="line_location_id", data_type=ColumnType.NUMBER, nullable=False),
                    ColumnMetadata(name="code_combination_id", data_type=ColumnType.NUMBER),
                ],
            ),
            TableMetadata(
                name="MTL_SYSTEM_ITEMS_B",
                columns=[
                    ColumnMetadata(name="inventory_item_id", data_type=ColumnType.NUMBER, nullable=False),
                    ColumnMetadata(name="segment1", data_type=ColumnType.VARCHAR2),
                ],
            ),
            TableMetadata(
                name="XXBT_PDKS_PER_DETAILS_V",
                columns=[
                    ColumnMetadata(name="SICIL_NO", data_type=ColumnType.VARCHAR2),
                    ColumnMetadata(name="BIRIM_ADI", data_type=ColumnType.VARCHAR2),
                    ColumnMetadata(name="LOCATION_ADI", data_type=ColumnType.VARCHAR2),
                    ColumnMetadata(name="UNVAN", data_type=ColumnType.VARCHAR2),
                    ColumnMetadata(name="CIKIS_TARIHI", data_type=ColumnType.DATE),
                    ColumnMetadata(name="BORDROLU", data_type=ColumnType.NUMBER),
                    ColumnMetadata(name="STAJYER", data_type=ColumnType.NUMBER),
                ],
            ),
        ]
    )


def test_rebase_line_quantity_to_root() -> None:
    plan = QueryPlan(
        intent="Kalem bazında sipariş miktarını göster",
        table="PO_LINES_ALL",
        select_columns=["line_num", "item_description"],
        aggregations=[
            AggregationSpec(function=AggregateFn.SUM, column="quantity", table="PO_LINES_ALL")
        ],
        group_by=["line_num", "item_description"],
    )
    out = apply_semantic_normalization(plan, "Kalem bazında sipariş miktarını göster", _snapshot())
    assert out.root_entity == "PO_PURCHASING"
    assert out.table == "PO_HEADERS_ALL"
    assert out.join_path_id == "po.header_lines"
    assert len(out.joins) == 1
    assert out.joins[0].right_table == "PO_LINES_ALL"


def test_distribution_path_selected() -> None:
    plan = QueryPlan(
        intent="Dağıtım bazında tutar analizi",
        table="PO_DISTRIBUTIONS_ALL",
        select_columns=["code_combination_id"],
        aggregations=[
            AggregationSpec(function=AggregateFn.SUM, column="quantity_ordered", table="PO_DISTRIBUTIONS_ALL")
        ],
        group_by=["code_combination_id"],
    )
    out = apply_semantic_normalization(plan, "Dağıtım bazında tutar analizi yap", _snapshot())
    assert out.table == "PO_HEADERS_ALL"
    assert out.join_path_id == "po.header_lines_shipments_distributions"
    assert len(out.joins) == 3


def test_item_path_selected() -> None:
    plan = QueryPlan(
        intent="Ürün bazında PO satır sayısını göster",
        table="PO_LINES_ALL",
        select_columns=["segment1"],
    )
    out = apply_semantic_normalization(plan, "Ürün bazında PO satır sayısını göster", _snapshot())
    assert out.table == "PO_HEADERS_ALL"
    assert out.join_path_id == "po.header_lines_items"
    assert any(j.right_table == "MTL_SYSTEM_ITEMS_B" for j in out.joins)


def test_pending_delivery_filter_canonicalized() -> None:
    plan = QueryPlan(
        intent="Teslim bekleyen satırları getir",
        table="PO_LINE_LOCATIONS_ALL",
        filters=[],
    )
    out = apply_semantic_normalization(plan, "Teslim bekleyen satırları getir", _snapshot())
    assert out.table == "PO_HEADERS_ALL"
    assert out.join_path_id == "po.header_lines_shipments"
    assert len(out.filters) == 1
    f = out.filters[0]
    assert f.table == "PO_LINE_LOCATIONS_ALL"
    assert f.column == "quantity_received"
    assert f.op.value == "<"
    assert f.value == "__COLUMN_REF__quantity"


def test_line_quantity_group_by_canonicalized() -> None:
    plan = QueryPlan(
        intent="Kalem bazında sipariş miktarını göster",
        table="PO_LINES_ALL",
        group_by=["item_id"],
    )
    out = apply_semantic_normalization(plan, "Kalem bazında sipariş miktarını göster", _snapshot())
    assert out.group_by == ["line_num", "item_description"]
    assert len(out.aggregations) == 1
    assert out.aggregations[0].function.value == "SUM"
    assert out.aggregations[0].column == "quantity"


def test_distribution_intent_recovers_from_clarification() -> None:
    plan = QueryPlan(
        intent="Dağıtım bazında tutar analizi",
        table="PO_DISTRIBUTIONS_ALL",
        needs_clarification=True,
        clarification_message="Hangi tablo?",
    )
    out = apply_semantic_normalization(plan, "Dağıtım bazında tutar analizi yap", _snapshot())
    assert out.needs_clarification is False
    assert out.clarification_message is None
    assert out.table == "PO_HEADERS_ALL"
    assert out.join_path_id == "po.header_lines_shipments_distributions"
    assert out.group_by == ["code_combination_id"]
    assert len(out.aggregations) == 2


def test_wrong_entity_correction_prefers_query_understanding_and_retrieval_root() -> None:
    plan = QueryPlan(
        intent="Açık satınalma siparişlerini listele",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO"],
    )

    out = apply_semantic_normalization(
        plan,
        "Açık satınalma siparişlerini listele",
        _snapshot(),
        query_understanding=QueryUnderstanding(
            original_question="Açık satınalma siparişlerini listele",
            normalized_question="acik satinalma siparislerini listele",
            inferred_modules=["PO"],
            resolved_entities=["PO_PURCHASING"],
            requested_output_type="list",
            entity_confidence="high",
        ),
        retrieval_diagnostics=SimpleNamespace(
            root_table_name="PO_HEADERS_ALL",
            dominant_domain_match=True,
        ),
    )

    assert out.table == "PO_HEADERS_ALL"
    assert out.root_entity == "PO_PURCHASING"
    assert apply_semantic_normalization.last_diagnostics["override_applied"] is True
    assert "query_understanding_alignment" in apply_semantic_normalization.last_diagnostics["decision_reasons"]
    assert "retrieval_domain_alignment" in apply_semantic_normalization.last_diagnostics["decision_reasons"]


def test_filter_ownership_preserved_when_root_table_changes() -> None:
    plan = QueryPlan(
        intent="Teslim bekleyen satırları getir",
        table="PO_LINE_LOCATIONS_ALL",
        filters=[FilterSpec(column="line_location_id", op=FilterOp.GT, value=1000)],
    )

    out = apply_semantic_normalization(
        plan,
        "Teslim bekleyen satırları getir",
        _snapshot(),
        query_understanding=QueryUnderstanding(
            original_question="Teslim bekleyen satırları getir",
            normalized_question="teslim bekleyen satirlari getir",
            inferred_modules=["PO"],
            resolved_entities=["PO_PURCHASING"],
            requested_output_type="list",
            entity_confidence="high",
        ),
        retrieval_diagnostics=SimpleNamespace(root_table_name="PO_HEADERS_ALL"),
    )

    assert out.table == "PO_HEADERS_ALL"
    assert any(filter_spec.table == "PO_LINE_LOCATIONS_ALL" for filter_spec in out.filters)
    assert apply_semantic_normalization.last_diagnostics["protected_filter_preserved"] is True


def test_override_suppressed_for_low_confidence_child_table_plan() -> None:
    plan = QueryPlan(
        intent="sipariş kaydı",
        table="PO_LINES_ALL",
        select_columns=["po_line_id"],
    )

    out = apply_semantic_normalization(
        plan,
        "sipariş kaydı",
        _snapshot(),
        query_understanding=QueryUnderstanding(
            original_question="sipariş kaydı",
            normalized_question="siparis kaydi",
            inferred_modules=["PO"],
            requested_output_type="list",
            entity_confidence="medium",
        ),
    )

    assert out.table == "PO_LINES_ALL"
    assert apply_semantic_normalization.last_diagnostics["override_applied"] is False
    assert "override_suppressed_due_to_low_confidence" in apply_semantic_normalization.last_diagnostics["decision_reasons"]


def test_missing_pending_approval_filter_is_injected_from_documented_values() -> None:
    plan = QueryPlan(
        intent="Onay bekleyen satın alma siparişlerini listele",
        table="PO_HEADERS_ALL",
        select_columns=["segment1", "authorization_status"],
        filters=[],
    )

    out = apply_semantic_normalization(
        plan,
        "Onay bekleyen satın alma siparişlerini listele",
        _snapshot(),
        query_understanding=QueryUnderstanding(
            original_question="Onay bekleyen satın alma siparişlerini listele",
            normalized_question="onay bekleyen satin alma siparislerini listele",
            inferred_modules=["PO"],
            resolved_entities=["PO_HEADERS"],
            requested_output_type="list",
            entity_confidence="high",
            extracted_filters=[
                {"dimension": "status", "value": "IN PROCESS", "column_hint": "AUTHORIZATION_STATUS"},
                {"dimension": "status", "value": "INCOMPLETE", "column_hint": "AUTHORIZATION_STATUS"},
                {"dimension": "status", "value": "PRE-APPROVED", "column_hint": "AUTHORIZATION_STATUS"},
            ],
        ),
        retrieval_diagnostics=SimpleNamespace(root_table_name="PO_HEADERS_ALL", dominant_domain_match=True),
    )

    assert any(f.column == "AUTHORIZATION_STATUS" and f.op == FilterOp.IN for f in out.filters)
    assert apply_semantic_normalization.last_diagnostics["missing_filter"] is False


def test_weak_flag_mapping_repaired_and_empty_result_hint_marked() -> None:
    plan = QueryPlan(
        intent="Stajyer çalışanları göster",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "UNVAN"],
        filters=[FilterSpec(column="STAJYER", op=FilterOp.EQ, value="stajyer")],
    )

    out = apply_semantic_normalization(
        plan,
        "Stajyer çalışanları göster",
        _snapshot(),
        query_understanding=QueryUnderstanding(
            original_question="Stajyer çalışanları göster",
            normalized_question="stajyer calisanlari goster",
            inferred_modules=["HR"],
            resolved_entities=["HR_EMPLOYEES"],
            requested_output_type="list",
            entity_confidence="high",
            extracted_filters=[
                {"dimension": "employment_type", "value": "1", "column_hint": "STAJYER"},
            ],
        ),
        retrieval_diagnostics=SimpleNamespace(root_table_name="XXBT_PDKS_PER_DETAILS_V", dominant_domain_match=True),
    )

    assert any(f.column == "STAJYER" and f.value == 1 for f in out.filters)
    assert apply_semantic_normalization.last_diagnostics["weak_filter_mapping"] is True
    assert apply_semantic_normalization.last_diagnostics["empty_result_diagnosis_hint"] == "value_encoding_mismatch"


def test_missing_lookup_definition_skips_weak_filter_rewrite() -> None:
    base_registry = SemanticRegistry.model_validate(apply_semantic_normalization.__globals__["_load_registry"]().model_dump())
    base_registry.lookups = []

    plan = QueryPlan(
        intent="Stajyer çalışanları göster",
        table="XXBT_PDKS_PER_DETAILS_V",
        filters=[FilterSpec(column="STAJYER", op=FilterOp.EQ, value="stajyer")],
    )

    out = apply_semantic_normalization(
        plan,
        "Stajyer çalışanları göster",
        _snapshot(),
        registry=base_registry,
        query_understanding=QueryUnderstanding(
            original_question="Stajyer çalışanları göster",
            normalized_question="stajyer calisanlari goster",
            inferred_modules=["HR"],
            resolved_entities=["HR_EMPLOYEES"],
            requested_output_type="list",
            entity_confidence="high",
            extracted_filters=[
                {"dimension": "employment_type", "value": "1", "column_hint": "STAJYER"},
            ],
        ),
    )

    assert any(f.column == "STAJYER" and f.value == "stajyer" for f in out.filters)
    assert apply_semantic_normalization.last_diagnostics["weak_filter_mapping"] is False


def test_weak_lookup_filter_rewrite_uses_registry_lookups() -> None:
    registry = SemanticRegistry.model_validate(apply_semantic_normalization.__globals__["_load_registry"]().model_dump())
    registry.lookups = [
        RegistryLookupSpec(
            lookup_type="EMPLOYEE_INTERN_FLAG",
            meaning="Stajyer",
            decoded_value="Intern",
            raw_value="YES",
            domain="HR",
            table_ref="XXBT_PDKS_PER_DETAILS_V.STAJYER",
        ),
        RegistryLookupSpec(
            lookup_type="EMPLOYEE_INTERN_FLAG",
            meaning="Stajyer Değil",
            decoded_value="Not Intern",
            raw_value="NO",
            domain="HR",
            table_ref="XXBT_PDKS_PER_DETAILS_V.STAJYER",
        ),
    ]

    plan = QueryPlan(
        intent="Stajyer çalışanları göster",
        table="XXBT_PDKS_PER_DETAILS_V",
        filters=[FilterSpec(column="STAJYER", op=FilterOp.EQ, value="stajyer")],
    )

    out = apply_semantic_normalization(
        plan,
        "Stajyer çalışanları göster",
        _snapshot(),
        registry=registry,
        query_understanding=QueryUnderstanding(
            original_question="Stajyer çalışanları göster",
            normalized_question="stajyer calisanlari goster",
            inferred_modules=["HR"],
            resolved_entities=["HR_EMPLOYEES"],
            requested_output_type="list",
            entity_confidence="high",
            extracted_filters=[
                {"dimension": "employment_type", "value": "YES", "column_hint": "STAJYER"},
            ],
        ),
    )

    assert any(f.column == "STAJYER" and f.value == "YES" for f in out.filters)
    assert apply_semantic_normalization.last_diagnostics["weak_filter_mapping"] is True


def test_title_filter_true_no_data_hint_when_mapping_is_stable() -> None:
    plan = QueryPlan(
        intent="Yönetici unvanlı çalışanları listele",
        table="XXBT_PDKS_PER_DETAILS_V",
        select_columns=["SICIL_NO", "UNVAN"],
        filters=[FilterSpec(column="UNVAN", op=FilterOp.LIKE, value="%yonetici%")],
    )

    out = apply_semantic_normalization(
        plan,
        "Yönetici unvanlı çalışanları listele",
        _snapshot(),
        query_understanding=QueryUnderstanding(
            original_question="Yönetici unvanlı çalışanları listele",
            normalized_question="yonetici unvanli calisanlari listele",
            inferred_modules=["HR"],
            resolved_entities=["HR_EMPLOYEES"],
            requested_output_type="list",
            entity_confidence="high",
            extracted_filters=[
                {"dimension": "title", "value": "yonetici", "column_hint": "UNVAN"},
            ],
        ),
        retrieval_diagnostics=SimpleNamespace(root_table_name="XXBT_PDKS_PER_DETAILS_V", dominant_domain_match=True),
    )

    assert out.table == "XXBT_PDKS_PER_DETAILS_V"
    assert apply_semantic_normalization.last_diagnostics["empty_result_diagnosis_hint"] == "true_no_data"


def test_vendor_filter_ownership_stays_stable_on_po_headers() -> None:
    plan = QueryPlan(
        intent="Tedarikçi ID 501'e ait siparişleri getir",
        table="PO_HEADERS_ALL",
        select_columns=["segment1"],
        filters=[],
    )

    out = apply_semantic_normalization(
        plan,
        "Tedarikçi ID 501'e ait siparişleri getir",
        _snapshot(),
        query_understanding=QueryUnderstanding(
            original_question="Tedarikçi ID 501'e ait siparişleri getir",
            normalized_question="tedarikci id 501e ait siparisleri getir",
            inferred_modules=["PO"],
            resolved_entities=["PO_HEADERS"],
            requested_output_type="list",
            entity_confidence="high",
            extracted_filters=[
                {"dimension": "vendor", "value": "501", "column_hint": "VENDOR_ID"},
            ],
        ),
        retrieval_diagnostics=SimpleNamespace(root_table_name="PO_HEADERS_ALL", dominant_domain_match=True),
    )

    assert any(f.column == "VENDOR_ID" and f.value == 501 for f in out.filters)
    assert apply_semantic_normalization.last_diagnostics["filter_ownership_conflict"] is False
