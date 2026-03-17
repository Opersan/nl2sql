"""Tests for the validation service."""

from __future__ import annotations

import pytest

from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
    SortDirection,
)
from app.domain.semantic_models import ColumnAliases, SemanticRegistry
from app.providers.catalog.in_memory import InMemoryCatalogProvider
from app.services.catalog_service import CatalogService
from app.services import validation_service as validation_module
from app.services.validation_service import ValidationService


@pytest.fixture
def validator() -> ValidationService:
    provider = InMemoryCatalogProvider()
    catalog = CatalogService(provider)
    return ValidationService(catalog)


# ---------------------------------------------------------------------------
# Valid plans
# ---------------------------------------------------------------------------


class TestValidPlans:
    @pytest.mark.asyncio
    async def test_simple_valid_plan(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="list employees",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name", "last_name"],
        )
        result = await validator.validate(plan)

        assert result.ok is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_valid_plan_with_alias(self, validator: ValidationService) -> None:
        """Table alias 'personnel' and column alias 'sicil_no' should resolve."""
        plan = QueryPlan(
            intent="alias test",
            table="personnel",
            select_columns=["sicil_no", "department"],
        )
        result = await validator.validate(plan)

        assert result.ok is True

    @pytest.mark.asyncio
    async def test_valid_plan_single_candidate(self, validator: ValidationService) -> None:
        """When table is None but candidate_tables has one entry, it should pass."""
        plan = QueryPlan(
            intent="single candidate",
            candidate_tables=["XXBT_PDKS_PER_DETAILS_V"],
            select_columns=["reg_no", "first_name"],
        )
        result = await validator.validate(plan)

        assert result.ok is True


# ---------------------------------------------------------------------------
# Invalid table
# ---------------------------------------------------------------------------


class TestInvalidTable:
    @pytest.mark.asyncio
    async def test_nonexistent_table(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="bad table",
            table="nonexistent",
            select_columns=["a"],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "invalid_table" for e in result.errors)

    @pytest.mark.asyncio
    async def test_no_table_no_candidates(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="no table",
            select_columns=["a"],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "invalid_table" for e in result.errors)


# ---------------------------------------------------------------------------
# Ambiguous table
# ---------------------------------------------------------------------------


class TestAmbiguousTable:
    @pytest.mark.asyncio
    async def test_multiple_candidates(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="ambiguous",
            candidate_tables=["XXBT_PDKS_PER_DETAILS_V", "departments"],
            select_columns=["a"],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "ambiguous_table" for e in result.errors)


# ---------------------------------------------------------------------------
# Invalid columns
# ---------------------------------------------------------------------------


class TestInvalidColumns:
    @pytest.mark.asyncio
    async def test_invalid_select_column(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="bad column",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "nonexistent_col"],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "invalid_column" for e in result.errors)

    @pytest.mark.asyncio
    async def test_invalid_filter_column(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="bad filter",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            filters=[FilterSpec(column="foo", op=FilterOp.EQ, value="x")],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "invalid_column" for e in result.errors)


# ---------------------------------------------------------------------------
# SELECT *
# ---------------------------------------------------------------------------


class TestSelectStar:
    @pytest.mark.asyncio
    async def test_explicit_star(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="star",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["*"],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "select_star_not_allowed" for e in result.errors)

    @pytest.mark.asyncio
    async def test_empty_select_no_agg(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="empty select",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=[],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "no_columns" for e in result.errors)


# ---------------------------------------------------------------------------
# Restricted columns
# ---------------------------------------------------------------------------


class TestRestrictedColumns:
    @pytest.mark.asyncio
    async def test_restricted_column_in_select(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="birth_date query",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "birth_date"],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "restricted_column" for e in result.errors)

    @pytest.mark.asyncio
    async def test_restricted_column_in_filter(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="filter by birth date",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            filters=[FilterSpec(column="birth_date", op=FilterOp.IS_NOT_NULL)],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "restricted_column" for e in result.errors)

    @pytest.mark.asyncio
    async def test_restricted_column_via_alias(self, validator: ValidationService) -> None:
        plan = QueryPlan(
            intent="birth_date via alias",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "dogum_tarihi"],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "restricted_column" for e in result.errors)


# ---------------------------------------------------------------------------
# Aggregate validation
# ---------------------------------------------------------------------------


class TestAggregateValidation:
    @pytest.mark.asyncio
    async def test_count_star_valid(self, validator: ValidationService) -> None:
        """COUNT(*) should pass validation without column existence check."""
        plan = QueryPlan(
            intent="total count",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="total"),
            ],
        )
        result = await validator.validate(plan)

        assert result.ok is True

    @pytest.mark.asyncio
    async def test_aggregate_only_plan_valid(self, validator: ValidationService) -> None:
        """Plan with only aggregations and no select_columns should pass."""
        plan = QueryPlan(
            intent="count per unit",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
        )
        result = await validator.validate(plan)

        assert result.ok is True

    @pytest.mark.asyncio
    async def test_invalid_aggregate_column(self, validator: ValidationService) -> None:
        """Aggregate referencing non-existent column should fail."""
        plan = QueryPlan(
            intent="bad aggregate",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.SUM, column="nonexistent"),
            ],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "invalid_column" for e in result.errors)

    @pytest.mark.asyncio
    async def test_restricted_column_in_aggregate(self, validator: ValidationService) -> None:
        """Aggregate on restricted column should fail."""
        plan = QueryPlan(
            intent="count birth_date",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="birth_date"),
            ],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "restricted_column" for e in result.errors)


# ---------------------------------------------------------------------------
# Improvement 1: Warning → Error for agg+select without group_by
# ---------------------------------------------------------------------------


class TestAggSelectNoGroupBy:
    @pytest.mark.asyncio
    async def test_select_with_agg_no_group_by_is_error(
        self, validator: ValidationService
    ) -> None:
        """select_columns + aggregation without group_by must be an error, not a warning."""
        plan = QueryPlan(
            intent="count with select no group",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["unit_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "aggregate_select_mismatch" for e in result.errors)
        # Ensure it is NOT a warning
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_select_with_agg_and_group_by_mismatch(
        self, validator: ValidationService
    ) -> None:
        """select col not in group_by → error with aggregate_select_mismatch."""
        plan = QueryPlan(
            intent="mismatch",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["unit_name", "first_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "aggregate_select_mismatch" for e in result.errors)
        # first_name is not in group_by → error
        err_msgs = [e.message for e in result.errors if e.code == "aggregate_select_mismatch"]
        assert any("first_name" in m for m in err_msgs)

    @pytest.mark.asyncio
    async def test_select_with_agg_and_group_by_match_ok(
        self, validator: ValidationService
    ) -> None:
        """All select columns in group_by → valid."""
        plan = QueryPlan(
            intent="correct group",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["unit_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
        )
        result = await validator.validate(plan)

        assert result.ok is True


# ---------------------------------------------------------------------------
# Improvement 2: Canonical resolution in aggregate consistency
# ---------------------------------------------------------------------------


class TestAggregateCanonicalResolution:
    @pytest.mark.asyncio
    async def test_alias_in_select_canonical_in_group_by(
        self, validator: ValidationService
    ) -> None:
        """select_columns=['birim'], group_by=['unit_name'] → should pass
        because 'birim' resolves canonically to 'UNIT_NAME'."""
        plan = QueryPlan(
            intent="alias group test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["birim"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
        )
        result = await validator.validate(plan)

        assert result.ok is True

    @pytest.mark.asyncio
    async def test_canonical_in_select_alias_in_group_by(
        self, validator: ValidationService
    ) -> None:
        """select_columns=['unit_name'], group_by=['birim'] → should pass."""
        plan = QueryPlan(
            intent="reverse alias group test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["unit_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["birim"],
        )
        result = await validator.validate(plan)

        assert result.ok is True


# ---------------------------------------------------------------------------
# Improvement 3: ORDER BY accepts aggregate aliases
# ---------------------------------------------------------------------------


class TestOrderByAggregateAlias:
    @pytest.mark.asyncio
    async def test_order_by_aggregate_alias_valid(
        self, validator: ValidationService
    ) -> None:
        """ORDER BY on an aggregate alias should be valid."""
        plan = QueryPlan(
            intent="order by count",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["unit_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["unit_name"],
            order_by=[OrderSpec(column="cnt", direction=SortDirection.DESC)],
        )
        result = await validator.validate(plan)

        assert result.ok is True

    @pytest.mark.asyncio
    async def test_order_by_auto_alias_valid(
        self, validator: ValidationService
    ) -> None:
        """ORDER BY on an auto-generated aggregate alias should be valid."""
        plan = QueryPlan(
            intent="order by auto alias",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no"),
            ],
            order_by=[OrderSpec(column="COUNT_REG_NO", direction=SortDirection.ASC)],
        )
        result = await validator.validate(plan)

        assert result.ok is True

    @pytest.mark.asyncio
    async def test_order_by_invalid_alias_rejected(
        self, validator: ValidationService
    ) -> None:
        """ORDER BY on a non-existent column/alias should fail."""
        plan = QueryPlan(
            intent="bad order",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            order_by=[OrderSpec(column="nonexistent_alias", direction=SortDirection.ASC)],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "invalid_column" for e in result.errors)

    @pytest.mark.asyncio
    async def test_order_by_table_column_still_works(
        self, validator: ValidationService
    ) -> None:
        """ORDER BY on a regular table column should still work."""
        plan = QueryPlan(
            intent="order by column",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            order_by=[OrderSpec(column="first_name", direction=SortDirection.ASC)],
        )
        result = await validator.validate(plan)

        assert result.ok is True


# ---------------------------------------------------------------------------
# Improvement 4: Empty select message accuracy
# ---------------------------------------------------------------------------


class TestEmptySelectMessage:
    @pytest.mark.asyncio
    async def test_empty_select_no_agg_uses_no_columns_code(
        self, validator: ValidationService
    ) -> None:
        """Empty select + no aggregations → 'no_columns' error, NOT select_star."""
        plan = QueryPlan(
            intent="empty",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=[],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert any(e.code == "no_columns" for e in result.errors)
        assert not any(e.code == "select_star_not_allowed" for e in result.errors)

    @pytest.mark.asyncio
    async def test_empty_select_with_agg_is_valid(
        self, validator: ValidationService
    ) -> None:
        """Empty select_columns but with aggregation → valid (aggregate-only)."""
        plan = QueryPlan(
            intent="aggregate only",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="total"),
            ],
        )
        result = await validator.validate(plan)

        assert result.ok is True


# ---------------------------------------------------------------------------
# Improvement 5: resolve_column_name consistency
# ---------------------------------------------------------------------------


class TestResolveColumnConsistency:
    @pytest.mark.asyncio
    async def test_filter_via_alias(self, validator: ValidationService) -> None:
        """Filter using alias 'sicil_no' for column 'REG_NO' should resolve."""
        plan = QueryPlan(
            intent="alias filter",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
            filters=[FilterSpec(column="sicil_no", op=FilterOp.EQ, value="123")],
        )
        result = await validator.validate(plan)

        # Should not report invalid_column for 'sicil_no'
        col_errors = [e for e in result.errors if e.code == "invalid_column"]
        assert col_errors == []

    @pytest.mark.asyncio
    async def test_aggregate_via_alias(self, validator: ValidationService) -> None:
        """Aggregate using alias 'sicil_no' should resolve."""
        plan = QueryPlan(
            intent="alias aggregate",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="sicil_no"),
            ],
        )
        result = await validator.validate(plan)

        col_errors = [e for e in result.errors if e.code == "invalid_column"]
        assert col_errors == []

    @pytest.mark.asyncio
    async def test_group_by_via_alias(self, validator: ValidationService) -> None:
        """GROUP BY using alias 'birim' should resolve."""
        plan = QueryPlan(
            intent="alias group by",
            table="XXBT_PDKS_PER_DETAILS_V",
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="reg_no", alias="cnt"),
            ],
            group_by=["birim"],
        )
        result = await validator.validate(plan)

        col_errors = [e for e in result.errors if e.code == "invalid_column"]
        assert col_errors == []

    @pytest.mark.asyncio
    async def test_order_by_via_alias(self, validator: ValidationService) -> None:
        """ORDER BY using alias 'sicil_no' should resolve."""
        plan = QueryPlan(
            intent="alias order by",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no", "first_name"],
            order_by=[OrderSpec(column="sicil_no", direction=SortDirection.ASC)],
        )
        result = await validator.validate(plan)

        col_errors = [e for e in result.errors if e.code == "invalid_column"]
        assert col_errors == []


class TestRegistryDrivenAliases:
    @pytest.mark.asyncio
    async def test_global_alias_resolves_from_registry(
        self,
        validator: ValidationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reg = SemanticRegistry(
            column_aliases=ColumnAliases(
                global_aliases={"mail": "EMAIL"},
                table_scoped={},
            ),
        )
        monkeypatch.setattr(validation_module, "_load_registry", lambda: reg)

        plan = QueryPlan(
            intent="global alias",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["mail"],
        )
        result = await validator.validate(plan)
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_table_scoped_alias_resolves_from_registry(
        self,
        validator: ValidationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reg = SemanticRegistry(
            column_aliases=ColumnAliases(
                global_aliases={},
                table_scoped={
                    "XXBT_PDKS_PER_DETAILS_V": {"giris_tarihi": "ISE_GIRIS_TARIHI"},
                },
            ),
        )
        monkeypatch.setattr(validation_module, "_load_registry", lambda: reg)

        plan = QueryPlan(
            intent="scoped alias",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["giris_tarihi"],
        )
        result = await validator.validate(plan)
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_table_scoped_alias_overrides_global_alias(
        self,
        validator: ValidationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reg = SemanticRegistry(
            column_aliases=ColumnAliases(
                global_aliases={"iletisim": "EMAIL"},
                table_scoped={
                    "XXBT_PDKS_PER_DETAILS_V": {"iletisim": "DAHILI"},
                },
            ),
        )
        monkeypatch.setattr(validation_module, "_load_registry", lambda: reg)

        normalized = validator._normalize_column_identifier(  # noqa: SLF001
            "iletisim",
            table_name="XXBT_PDKS_PER_DETAILS_V",
        )
        assert normalized == "DAHILI"

    @pytest.mark.asyncio
    async def test_original_column_preserved_when_alias_missing(
        self,
        validator: ValidationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reg = SemanticRegistry(
            column_aliases=ColumnAliases(
                global_aliases={},
                table_scoped={},
            ),
        )
        monkeypatch.setattr(validation_module, "_load_registry", lambda: reg)

        normalized = validator._normalize_column_identifier(  # noqa: SLF001
            "REG_NO",
            table_name="XXBT_PDKS_PER_DETAILS_V",
        )
        assert normalized == "REG_NO"


# ---------------------------------------------------------------------------
# Resolved table contract
# ---------------------------------------------------------------------------


class TestResolvedTable:
    """ValidationResult.resolved_table must be set on success, None on failure."""

    @pytest.mark.asyncio
    async def test_resolved_table_set_on_valid_plan(
        self, validator: ValidationService
    ) -> None:
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["reg_no"],
        )
        result = await validator.validate(plan)

        assert result.ok is True
        assert result.resolved_table is not None
        assert result.resolved_table.name == "XXBT_PDKS_PER_DETAILS_V"

    @pytest.mark.asyncio
    async def test_resolved_table_none_on_bad_table(
        self, validator: ValidationService
    ) -> None:
        plan = QueryPlan(
            intent="test",
            table="nonexistent",
            select_columns=["id"],
        )
        result = await validator.validate(plan)

        assert result.ok is False
        assert result.resolved_table is None

    @pytest.mark.asyncio
    async def test_resolved_table_via_alias(
        self, validator: ValidationService
    ) -> None:
        """Table alias 'personnel' should resolve and populate resolved_table."""
        plan = QueryPlan(
            intent="test",
            table="personnel",
            select_columns=["reg_no"],
        )
        result = await validator.validate(plan)

        assert result.ok is True
        assert result.resolved_table is not None
        assert result.resolved_table.name == "XXBT_PDKS_PER_DETAILS_V"