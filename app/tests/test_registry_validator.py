"""Tests for the semantic registry ↔ catalog metadata validator.

Strategy
--------
All tests build a synthetic :class:`~app.domain.catalog_models.CatalogSnapshot`
that exactly mirrors the PO entity definition in *semantic_registry.json*.
This makes every "happy path" test pass against the real registry and every
"sad path" test trivially reproducible by removing one table or column.

Four categories
~~~~~~~~~~~~~~~
1. valid_registry_pass        — full PO catalog matches registry: 0 issues
2. unknown_table_fail         — root table absent from catalog
3. unknown_column_fail        — join-key column absent from table
4. broken_join_path_fail      — join-step references wrong table name
5. intent_defaults checks     — group_by / aggregation / filter columns
6. assert_registry_valid      — raises RegistryValidationError on issues
"""
from __future__ import annotations

import pytest

from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    TableMetadata,
)
from app.domain.semantic_models import (
    BusinessEntitySemantic,
    CanonicalJoinPath,
    CanonicalJoinStep,
    IntentDefaults,
    IntentRule,
    RegistryAggregationSpec,
    RegistryFilterSpec,
    SemanticRegistry,
)
from app.services.registry_validator import (
    RegistryValidationError,
    RegistryIssue,
    assert_registry_valid,
    validate_registry_against_catalog,
)


# ---------------------------------------------------------------------------
# Catalog snapshot helpers
# ---------------------------------------------------------------------------

def _col(name: str, dtype: ColumnType = ColumnType.NUMBER) -> ColumnMetadata:
    return ColumnMetadata(name=name, data_type=dtype)


def _po_headers(**overrides) -> TableMetadata:
    cols = [
        _col("po_header_id"),
        _col("vendor_id"),
        _col("creation_date", ColumnType.DATE),
        _col("authorization_status", ColumnType.VARCHAR),
    ]
    kwargs = {"name": "PO_HEADERS_ALL", "columns": cols}
    kwargs.update(overrides)
    return TableMetadata(**kwargs)


def _po_lines(**overrides) -> TableMetadata:
    cols = [
        _col("po_line_id"),
        _col("po_header_id"),
        _col("item_id"),
        _col("line_num"),
        _col("item_description", ColumnType.VARCHAR),
        _col("quantity"),
        _col("unit_price"),
    ]
    kwargs = {"name": "PO_LINES_ALL", "columns": cols}
    kwargs.update(overrides)
    return TableMetadata(**kwargs)


def _po_shipments(**overrides) -> TableMetadata:
    cols = [
        _col("line_location_id"),
        _col("po_line_id"),
        _col("quantity_received"),
    ]
    kwargs = {"name": "PO_LINE_LOCATIONS_ALL", "columns": cols}
    kwargs.update(overrides)
    return TableMetadata(**kwargs)


def _po_distributions(**overrides) -> TableMetadata:
    cols = [
        _col("po_distribution_id"),
        _col("line_location_id"),
        _col("quantity_ordered"),
        _col("code_combination_id"),
    ]
    kwargs = {"name": "PO_DISTRIBUTIONS_ALL", "columns": cols}
    kwargs.update(overrides)
    return TableMetadata(**kwargs)


def _mtl_items(**overrides) -> TableMetadata:
    cols = [
        _col("inventory_item_id"),
        _col("segment1", ColumnType.VARCHAR),
        _col("description", ColumnType.VARCHAR),
    ]
    kwargs = {"name": "MTL_SYSTEM_ITEMS_B", "columns": cols}
    kwargs.update(overrides)
    return TableMetadata(**kwargs)


def _full_po_snapshot() -> CatalogSnapshot:
    """CatalogSnapshot containing all PO tables with all referenced columns."""
    return CatalogSnapshot(tables=[
        _po_headers(),
        _po_lines(),
        _po_shipments(),
        _po_distributions(),
        _mtl_items(),
    ])


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def _load_real_po_registry() -> SemanticRegistry:
    from app.services.semantic_planning import _load_registry
    return _load_registry()


def _minimal_po_entity() -> BusinessEntitySemantic:
    """PO entity with all join paths and intent_defaults fully populated."""
    return BusinessEntitySemantic(
        entity_id="PO_PURCHASING",
        root_table="PO_HEADERS_ALL",
        child_tables=["PO_LINES_ALL", "PO_LINE_LOCATIONS_ALL", "PO_DISTRIBUTIONS_ALL", "MTL_SYSTEM_ITEMS_B"],
        join_paths=[
            CanonicalJoinPath(
                path_id="po.header_lines",
                steps=[CanonicalJoinStep(
                    left_table="PO_HEADERS_ALL", left_column="po_header_id",
                    right_table="PO_LINES_ALL", right_column="po_header_id",
                )],
            ),
        ],
        intent_defaults={
            "po_line_quantity": IntentDefaults(
                stable=True,
                group_by=["line_num", "item_description"],
                aggregations=[RegistryAggregationSpec(function="SUM", column="quantity", table="PO_LINES_ALL", alias="total_qty")],
            ),
            "po_pending_delivery": IntentDefaults(
                stable=True,
                filters=[RegistryFilterSpec(column="quantity_received", table="PO_LINE_LOCATIONS_ALL", op="<", value="__COLUMN_REF__quantity")],
            ),
            "po_distribution_amount": IntentDefaults(
                stable=True,
                group_by=["code_combination_id"],
                aggregations=[
                    RegistryAggregationSpec(function="SUM", column="quantity_ordered", table="PO_DISTRIBUTIONS_ALL", alias="ordered_qty"),
                    RegistryAggregationSpec(function="SUM", column="unit_price", table="PO_LINES_ALL", alias="price_sum"),
                ],
            ),
            "po_item_line_count": IntentDefaults(
                stable=True,
                group_by=["segment1", "description"],
                aggregations=[RegistryAggregationSpec(function="COUNT", column="*", alias="line_count")],
            ),
        },
    )


def _registry_with(entity: BusinessEntitySemantic) -> SemanticRegistry:
    return SemanticRegistry(entities=[entity], intent_join_paths={})


# ===========================================================================
# 1. Valid registry — 0 issues
# ===========================================================================

class TestValidRegistry:

    def test_full_po_catalog_against_real_registry(self):
        """The real registry + a full PO catalog snapshot must produce 0 issues."""
        registry = _load_real_po_registry()
        snapshot = _full_po_snapshot()
        issues = validate_registry_against_catalog(registry, snapshot)
        po_issues = [i for i in issues if i.entity_id == "PO_PURCHASING"]
        assert po_issues == [], f"Unexpected PO issues: {po_issues}"

    def test_minimal_po_entity_passes(self):
        registry = _registry_with(_minimal_po_entity())
        snapshot = _full_po_snapshot()
        assert validate_registry_against_catalog(registry, snapshot) == []

    def test_empty_registry_passes(self):
        assert validate_registry_against_catalog(SemanticRegistry(), CatalogSnapshot()) == []

    def test_entity_with_no_join_paths_passes(self):
        entity = BusinessEntitySemantic(entity_id="AP_PAYABLES", root_table="AP_INVOICES_ALL")
        snapshot = CatalogSnapshot(tables=[TableMetadata(name="AP_INVOICES_ALL")])
        assert validate_registry_against_catalog(_registry_with(entity), snapshot) == []

    def test_count_wildcard_is_skipped(self):
        """COUNT(*) must not trigger a 'column not found' issue."""
        entity = _minimal_po_entity()
        registry = _registry_with(entity)
        snapshot = _full_po_snapshot()
        issues = validate_registry_against_catalog(registry, snapshot)
        assert not any("*" in i.detail for i in issues)

    def test_column_ref_filter_value_is_not_checked(self):
        """__COLUMN_REF__ filter values are runtime references, not catalog columns."""
        entity = _minimal_po_entity()
        registry = _registry_with(entity)
        snapshot = _full_po_snapshot()
        issues = validate_registry_against_catalog(registry, snapshot)
        assert issues == []


# ===========================================================================
# 2. Unknown table — must produce issues
# ===========================================================================

class TestUnknownTable:

    def test_root_table_missing_produces_issue(self):
        entity = BusinessEntitySemantic(
            entity_id="FAKE_ENTITY",
            root_table="NONEXISTENT_TABLE",
        )
        issues = validate_registry_against_catalog(_registry_with(entity), CatalogSnapshot())
        assert len(issues) == 1
        assert issues[0].entity_id == "FAKE_ENTITY"
        assert issues[0].location == "root_table"
        assert "NONEXISTENT_TABLE" in issues[0].detail

    def test_child_table_missing_produces_issue(self):
        snapshot = CatalogSnapshot(tables=[TableMetadata(name="PO_HEADERS_ALL")])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            child_tables=["GHOST_TABLE"],
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any(i.location == "child_tables" and "GHOST_TABLE" in i.detail for i in issues)

    def test_join_path_left_table_missing(self):
        snapshot = CatalogSnapshot(tables=[TableMetadata(name="PO_HEADERS_ALL")])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            join_paths=[CanonicalJoinPath(
                path_id="bad.path",
                steps=[CanonicalJoinStep(
                    left_table="GHOST_LEFT", left_column="id",
                    right_table="PO_HEADERS_ALL", right_column="po_header_id",
                )],
            )],
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_LEFT" in i.detail and "left table" in i.detail for i in issues)

    def test_join_path_right_table_missing(self):
        snapshot = CatalogSnapshot(tables=[_po_headers()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            join_paths=[CanonicalJoinPath(
                path_id="bad.path",
                steps=[CanonicalJoinStep(
                    left_table="PO_HEADERS_ALL", left_column="po_header_id",
                    right_table="GHOST_RIGHT", right_column="po_header_id",
                )],
            )],
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_RIGHT" in i.detail and "right table" in i.detail for i in issues)

    def test_aggregation_table_missing(self):
        snapshot = CatalogSnapshot(tables=[_po_headers()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            intent_defaults={"my_intent": IntentDefaults(
                aggregations=[RegistryAggregationSpec(function="SUM", column="qty", table="GHOST_AGG_TABLE")],
            )},
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_AGG_TABLE" in i.detail for i in issues)

    def test_filter_table_missing(self):
        snapshot = CatalogSnapshot(tables=[_po_headers()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            intent_defaults={"my_intent": IntentDefaults(
                filters=[RegistryFilterSpec(column="col", table="GHOST_FLT_TABLE", op="<")],
            )},
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_FLT_TABLE" in i.detail for i in issues)


# ===========================================================================
# 3. Unknown column — must produce issues
# ===========================================================================

class TestUnknownColumn:

    def test_join_path_left_column_missing(self):
        snapshot = CatalogSnapshot(tables=[_po_headers(), _po_lines()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            child_tables=["PO_LINES_ALL"],
            join_paths=[CanonicalJoinPath(
                path_id="bad.col.path",
                steps=[CanonicalJoinStep(
                    left_table="PO_HEADERS_ALL", left_column="GHOST_COLUMN",
                    right_table="PO_LINES_ALL", right_column="po_header_id",
                )],
            )],
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_COLUMN" in i.detail and "left column" in i.detail for i in issues)

    def test_join_path_right_column_missing(self):
        snapshot = CatalogSnapshot(tables=[_po_headers(), _po_lines()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            child_tables=["PO_LINES_ALL"],
            join_paths=[CanonicalJoinPath(
                path_id="bad.col.path",
                steps=[CanonicalJoinStep(
                    left_table="PO_HEADERS_ALL", left_column="po_header_id",
                    right_table="PO_LINES_ALL", right_column="GHOST_RIGHT_COL",
                )],
            )],
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_RIGHT_COL" in i.detail and "right column" in i.detail for i in issues)

    def test_group_by_column_missing_in_all_entity_tables(self):
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            child_tables=["PO_LINES_ALL"],
            intent_defaults={"intent_x": IntentDefaults(group_by=["GHOST_GROUP_COL"])},
        )
        snapshot = CatalogSnapshot(tables=[_po_headers(), _po_lines()])
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_GROUP_COL" in i.detail for i in issues)

    def test_aggregation_column_missing_in_explicit_table(self):
        snapshot = CatalogSnapshot(tables=[_po_headers(), _po_lines()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            child_tables=["PO_LINES_ALL"],
            intent_defaults={"intent_x": IntentDefaults(
                aggregations=[RegistryAggregationSpec(function="SUM", column="GHOST_AGG_COL", table="PO_LINES_ALL")],
            )},
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_AGG_COL" in i.detail and "PO_LINES_ALL" in i.detail for i in issues)

    def test_aggregation_column_missing_without_table(self):
        """When no table is specified, the column must exist in at least one entity table."""
        snapshot = CatalogSnapshot(tables=[_po_headers(), _po_lines()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            child_tables=["PO_LINES_ALL"],
            intent_defaults={"intent_x": IntentDefaults(
                aggregations=[RegistryAggregationSpec(function="SUM", column="GHOST_NOTBL_COL")],
            )},
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_NOTBL_COL" in i.detail for i in issues)

    def test_filter_column_missing_in_explicit_table(self):
        snapshot = CatalogSnapshot(tables=[_po_shipments()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_LINE_LOCATIONS_ALL",
            intent_defaults={"intent_x": IntentDefaults(
                filters=[RegistryFilterSpec(column="GHOST_FLT_COL", table="PO_LINE_LOCATIONS_ALL", op="<")],
            )},
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("GHOST_FLT_COL" in i.detail for i in issues)


# ===========================================================================
# 4. Broken join path — combined table + column error
# ===========================================================================

class TestBrokenJoinPath:

    def test_multi_step_path_with_broken_middle_step(self):
        """A 3-step join path with the second step referencing non-existent data."""
        snapshot = CatalogSnapshot(tables=[_po_headers(), _po_lines()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            child_tables=["PO_LINES_ALL"],
            join_paths=[CanonicalJoinPath(
                path_id="bad.multi",
                steps=[
                    CanonicalJoinStep(
                        left_table="PO_HEADERS_ALL", left_column="po_header_id",
                        right_table="PO_LINES_ALL",   right_column="po_header_id",
                    ),
                    # Second step: table exists but column doesn't
                    CanonicalJoinStep(
                        left_table="PO_LINES_ALL",   left_column="NONEXISTENT_LINK_COL",
                        right_table="PO_HEADERS_ALL", right_column="po_header_id",
                    ),
                ],
            )],
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("NONEXISTENT_LINK_COL" in i.detail for i in issues)
        # first step should be clean
        step0_issues = [i for i in issues if "step[0]" in i.location]
        assert step0_issues == []

    def test_issue_location_includes_path_id_and_step_index(self):
        snapshot = CatalogSnapshot(tables=[_po_headers()])
        entity = BusinessEntitySemantic(
            entity_id="PO_PURCHASING",
            root_table="PO_HEADERS_ALL",
            join_paths=[CanonicalJoinPath(
                path_id="po.my_path",
                steps=[CanonicalJoinStep(
                    left_table="PO_HEADERS_ALL", left_column="po_header_id",
                    right_table="MISSING_TABLE", right_column="id",
                )],
            )],
        )
        issues = validate_registry_against_catalog(_registry_with(entity), snapshot)
        assert any("po.my_path" in i.location and "step[0]" in i.location for i in issues)

    def test_error_message_includes_entity_id(self):
        entity = BusinessEntitySemantic(
            entity_id="MY_ENTITY",
            root_table="UNKNOWN_ROOT",
        )
        issues = validate_registry_against_catalog(_registry_with(entity), CatalogSnapshot())
        assert all(i.entity_id == "MY_ENTITY" for i in issues)
        assert "MY_ENTITY" in str(issues[0])


# ===========================================================================
# 5. assert_registry_valid raises on issues
# ===========================================================================

class TestAssertRegistryValid:

    def test_raises_on_missing_root_table(self):
        entity = BusinessEntitySemantic(entity_id="BAD", root_table="GHOST")
        with pytest.raises(RegistryValidationError) as exc_info:
            assert_registry_valid(_registry_with(entity), CatalogSnapshot())
        err = exc_info.value
        assert len(err.issues) >= 1
        assert "GHOST" in str(err)
        assert "BAD" in str(err)

    def test_does_not_raise_on_clean_registry(self):
        entity = _minimal_po_entity()
        assert_registry_valid(_registry_with(entity), _full_po_snapshot())  # no exception

    def test_error_lists_all_issues(self):
        entity = BusinessEntitySemantic(
            entity_id="MULTI_ERR",
            root_table="TABLE_A",
            child_tables=["TABLE_B", "TABLE_C"],
        )
        issues_raised: list[RegistryIssue] = []
        with pytest.raises(RegistryValidationError) as exc_info:
            assert_registry_valid(_registry_with(entity), CatalogSnapshot())
        issues_raised = exc_info.value.issues
        # root_table + 2 child_tables = 3 issues minimum
        assert len(issues_raised) >= 3
        entity_ids = {i.entity_id for i in issues_raised}
        assert entity_ids == {"MULTI_ERR"}
