"""Tests for semantic intent-defaults select_columns propagation.

Covers
------
* IntentDefaults.select_columns model field
* _apply_intent_defaults applies select_columns for listing intents
* _apply_intent_defaults does NOT override select_columns for agg intents
* _apply_intent_defaults does NOT override non-empty plan.select_columns
* po_open_orders / po_unapproved / po_last_30_days semantic normalization
* q_101 / q_102 / q_103 / q_109 full-compile smoke (synthetic catalog)
"""
from __future__ import annotations

import pytest

from app.core.exceptions import CompilationError
from app.domain.catalog_models import ColumnMetadata, ColumnType, TableMetadata
from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    QueryPlan,
)
from app.domain.semantic_models import (
    BusinessEntitySemantic,
    IntentDefaults,
    IntentRule,
    RegistryAggregationSpec,
    RegistryFilterSpec,
    SemanticRegistry,
)
from app.services.semantic_planning import apply_semantic_normalization
from app.services.sql_compiler import SQLCompiler


# ---------------------------------------------------------------------------
# Helpers — minimal synthetic catalog
# ---------------------------------------------------------------------------

def _col(name: str, dtype: ColumnType = ColumnType.NUMBER) -> ColumnMetadata:
    return ColumnMetadata(name=name, data_type=dtype)


def _po_headers_table() -> TableMetadata:
    return TableMetadata(
        name="PO_HEADERS_ALL",
        columns=[
            _col("po_header_id"),
            _col("vendor_id"),
            _col("creation_date", ColumnType.DATE),
            _col("authorization_status", ColumnType.VARCHAR),
        ],
    )


# ---------------------------------------------------------------------------
# Helpers — minimal synthetic registry
# ---------------------------------------------------------------------------

def _listing_registry(intent: str, select_cols: list[str], filters: list[RegistryFilterSpec] | None = None) -> SemanticRegistry:
    """Registry with a single entity + one listing intent."""
    return SemanticRegistry(
        version="1.0",
        entities=[
            BusinessEntitySemantic(
                entity_id="PO_PURCHASING",
                root_table="PO_HEADERS_ALL",
                keywords=["po", "satınalma"],
                intent_rules=[IntentRule(intent=intent, any_of=["po"])],
                default_intent=intent,
                intent_defaults={
                    intent: IntentDefaults(
                        stable=True,
                        select_columns=select_cols,
                        filters=filters or [],
                    )
                },
            )
        ],
    )


def _agg_registry() -> SemanticRegistry:
    """Registry with a vendor_count aggregation intent."""
    return SemanticRegistry(
        version="1.0",
        entities=[
            BusinessEntitySemantic(
                entity_id="PO_PURCHASING",
                root_table="PO_HEADERS_ALL",
                keywords=["tedarikçi"],
                intent_rules=[IntentRule(intent="po_vendor_count", any_of=["tedarikçi"])],
                default_intent="po_vendor_count",
                intent_defaults={
                    "po_vendor_count": IntentDefaults(
                        stable=True,
                        select_columns=["vendor_id"],  # should be IGNORED for agg intents
                        group_by=["vendor_id"],
                        aggregations=[
                            RegistryAggregationSpec(function="COUNT", column="*", alias="po_count")
                        ],
                    )
                },
            )
        ],
    )


def _real_po_registry() -> SemanticRegistry:
    """Load the actual semantic_registry.json (cached)."""
    from app.services.semantic_planning import _load_registry
    return _load_registry()


# ---------------------------------------------------------------------------
# 1. IntentDefaults model field
# ---------------------------------------------------------------------------

class TestIntentDefaultsModel:
    def test_select_columns_field_defaults_empty(self) -> None:
        d = IntentDefaults(stable=True)
        assert d.select_columns == []

    def test_select_columns_field_accepted(self) -> None:
        d = IntentDefaults(
            stable=True,
            select_columns=["po_header_id", "creation_date"],
        )
        assert d.select_columns == ["po_header_id", "creation_date"]

    def test_select_columns_independent_of_filters(self) -> None:
        d = IntentDefaults(
            stable=True,
            select_columns=["authorization_status"],
            filters=[RegistryFilterSpec(column="authorization_status", op="!=", value="CLOSED")],
        )
        assert len(d.select_columns) == 1
        assert len(d.filters) == 1


# ---------------------------------------------------------------------------
# 2. _apply_intent_defaults — select_columns propagation rules
# ---------------------------------------------------------------------------

class TestApplySelectColumnsRule:
    def _normalize(self, plan: QueryPlan, registry: SemanticRegistry, msg: str = "po sorgusu") -> QueryPlan:
        from app.domain.catalog_models import CatalogSnapshot
        return apply_semantic_normalization(plan, msg, CatalogSnapshot(), registry=registry)

    def test_select_columns_applied_when_plan_empty(self) -> None:
        """Default select_columns fill in when plan has nothing selected."""
        reg = _listing_registry("po_open_orders", ["po_header_id", "vendor_id", "creation_date"])
        plan = QueryPlan(intent="açık po", table="PO_HEADERS_ALL", select_columns=[])
        result = self._normalize(plan, reg)
        assert result.select_columns == ["po_header_id", "vendor_id", "creation_date"]

    def test_select_columns_not_overridden_when_plan_has_cols(self) -> None:
        """Plan's own select_columns must NOT be overwritten by defaults."""
        reg = _listing_registry("po_open_orders", ["po_header_id", "vendor_id", "creation_date"])
        plan = QueryPlan(intent="açık po", table="PO_HEADERS_ALL", select_columns=["authorization_status"])
        result = self._normalize(plan, reg)
        assert result.select_columns == ["authorization_status"]

    def test_select_columns_not_applied_for_agg_intent(self) -> None:
        """select_columns default must be ignored when aggregations are present."""
        reg = _agg_registry()
        plan = QueryPlan(intent="tedarikçi sayısı", table="PO_HEADERS_ALL", select_columns=[])
        result = self._normalize(plan, reg, msg="tedarikçi sayısı")
        # The query has aggregations — select_columns should remain empty
        assert result.select_columns == []
        # But aggregations should be set
        assert len(result.aggregations) == 1
        assert result.aggregations[0].alias == "po_count"

    def test_stable_true_clears_clarification(self) -> None:
        reg = _listing_registry("po_open_orders", ["po_header_id"])
        plan = QueryPlan(
            intent="po",
            table="PO_HEADERS_ALL",
            needs_clarification=True,
            clarification_message="Hangi PO?",
            select_columns=[],
        )
        result = self._normalize(plan, reg)
        assert result.needs_clarification is False
        assert result.clarification_message is None
        assert result.select_columns == ["po_header_id"]


# ---------------------------------------------------------------------------
# 3. Real registry — semantic normalization for specific intents
# ---------------------------------------------------------------------------

class TestRealRegistryNormalization:
    def _normalize(self, msg: str, plan: QueryPlan | None = None) -> QueryPlan:
        from app.domain.catalog_models import CatalogSnapshot
        reg = _real_po_registry()
        if plan is None:
            plan = QueryPlan(intent=msg, table="PO_HEADERS_ALL", select_columns=[])
        return apply_semantic_normalization(plan, msg, CatalogSnapshot(), registry=reg)

    def test_po_open_orders_select_columns(self) -> None:
        result = self._normalize("Açık satınalma siparişlerini listele")
        assert result.semantic_intent == "po_open_orders"
        assert "po_header_id" in result.select_columns
        assert result.select_columns  # non-empty

    def test_po_unapproved_select_columns(self) -> None:
        result = self._normalize("Onaysız bekleyen PO'ları getir")
        assert result.semantic_intent == "po_unapproved"
        assert "po_header_id" in result.select_columns
        assert result.select_columns

    def test_po_last_30_days_select_columns(self) -> None:
        result = self._normalize("Son 30 günde açılan PO'lar")
        assert result.semantic_intent == "po_last_30_days"
        assert "po_header_id" in result.select_columns
        assert result.select_columns

    def test_po_vendor_count_select_columns_empty(self) -> None:
        """po_vendor_count is an agg intent — select_columns must remain empty."""
        result = self._normalize("Tedarikçiye göre PO sayısı")
        assert result.semantic_intent == "po_vendor_count"
        assert result.select_columns == []
        assert len(result.aggregations) >= 1

    def test_filter_still_applied_alongside_select_columns(self) -> None:
        """select_columns and filters co-exist in the same plan."""
        result = self._normalize("Kapatılmamış PO'ları göster")
        assert result.semantic_intent == "po_open_orders"
        assert result.select_columns  # has defaults
        assert any(f.value == "CLOSED" for f in result.filters)


# ---------------------------------------------------------------------------
# 4. Compile smoke tests: q_101 / q_102 / q_103 / q_109
# ---------------------------------------------------------------------------

@pytest.fixture
def compiler() -> SQLCompiler:
    return SQLCompiler()


@pytest.fixture
def po_headers() -> TableMetadata:
    return _po_headers_table()


def _normalized_plan(msg: str) -> QueryPlan:
    from app.domain.catalog_models import CatalogSnapshot
    reg = _real_po_registry()
    plan = QueryPlan(intent=msg, table="PO_HEADERS_ALL", select_columns=[])
    return apply_semantic_normalization(plan, msg, CatalogSnapshot(), registry=reg)


class TestQ101Smoke:
    def test_q101_open_orders_compiles(self, compiler: SQLCompiler, po_headers: TableMetadata) -> None:
        """q_101: Açık satınalma siparişlerini listele — must compile without error."""
        plan = _normalized_plan("Açık satınalma siparişlerini listele")
        assert plan.select_columns, "select_columns must be non-empty after normalization"
        result = compiler.compile(plan, po_headers)
        assert "FROM PO_HEADERS_ALL" in result.sql
        assert "WHERE" in result.sql  # authorization_status filter
        assert "authorization_status" in result.sql
        assert result.sql  # non-empty SQL


class TestQ102Smoke:
    def test_q102_unapproved_compiles(self, compiler: SQLCompiler, po_headers: TableMetadata) -> None:
        """q_102: Onaysız bekleyen PO'ları getir — must compile without error."""
        plan = _normalized_plan("Onaysız bekleyen PO'ları getir")
        assert plan.select_columns, "select_columns must be non-empty after normalization"
        result = compiler.compile(plan, po_headers)
        assert "FROM PO_HEADERS_ALL" in result.sql
        assert "authorization_status" in result.sql


class TestQ103Smoke:
    def test_q103_unclosed_compiles(self, compiler: SQLCompiler, po_headers: TableMetadata) -> None:
        """q_103: Kapatılmamış PO'ları göster — maps to po_open_orders."""
        plan = _normalized_plan("Kapatılmamış PO'ları göster")
        assert plan.semantic_intent == "po_open_orders"
        assert plan.select_columns, "select_columns must be non-empty after normalization"
        result = compiler.compile(plan, po_headers)
        assert "authorization_status" in result.sql
        # CLOSED filter value inlined or bound — either way present
        assert "CLOSED" in result.sql or ":p" in result.sql


class TestQ109Smoke:
    def test_q109_last_30_days_compiles(self, compiler: SQLCompiler, po_headers: TableMetadata) -> None:
        """q_109: Son 30 günde açılan PO'lar — __EXPR__ filter + select_columns."""
        plan = _normalized_plan("Son 30 günde açılan PO'lar")
        assert plan.semantic_intent == "po_last_30_days"
        assert plan.select_columns, "select_columns must be non-empty after normalization"
        result = compiler.compile(plan, po_headers)
        assert "FROM PO_HEADERS_ALL" in result.sql
        # __EXPR__ inlined
        assert "TRUNC(SYSDATE)-30" in result.sql
        # Only ROWNUM bind param
        assert set(result.params.keys()) == {"p1"}


class TestCompileDoesNotRaiseForListingIntents:
    """Guard: none of the 4 listing intents may raise CompilationError."""

    QUESTIONS = [
        "Açık satınalma siparişlerini listele",
        "Onaysız bekleyen PO'ları getir",
        "Kapatılmamış PO'ları göster",
        "Son 30 günde açılan PO'lar",
    ]

    @pytest.mark.parametrize("question", QUESTIONS)
    def test_no_compilation_error(self, question: str, compiler: SQLCompiler) -> None:
        po_headers = _po_headers_table()
        plan = _normalized_plan(question)
        # Must not raise
        result = compiler.compile(plan, po_headers)
        assert result.sql
