"""Tests for multi-table (JOIN) support — Sprint 5.

Covers:
* Domain model extensions (JoinType, JoinSpec, JoinCondition, QualifiedColumn)
* Catalog metadata (ForeignKeyMetadata, RelationshipMetadata, CatalogSnapshot)
* MockLLMProvider multi-table plan generation
* Plan normalizer JOIN handling
* SQL compiler JOIN clause generation
* Validation service multi-table column resolution
* InMemoryRetriever relation-aware expansion
* Planner prompt relationship block
* Metadata ingestion FK/relationship mapping
* sample_metadata.json multi-table validation
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    ForeignKeyMetadata,
    RelationshipMetadata,
    TableMetadata,
)
from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    JoinCondition,
    JoinSpec,
    JoinType,
    OrderSpec,
    QualifiedColumn,
    QueryPlan,
    SortDirection,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


# =====================================================================
# Fixtures
# =====================================================================

def _employee_table() -> TableMetadata:
    return TableMetadata(
        name="XXBT_PDKS_PER_DETAILS_V",
        description="Çalışan tablosu",
        primary_key=["person_id"],
        foreign_keys=[
            ForeignKeyMetadata(
                column="department_id",
                referenced_table="DEPARTMENT",
                referenced_column="department_id",
            ),
        ],
        columns=[
            ColumnMetadata(name="person_id", data_type=ColumnType.NUMBER, nullable=False),
            ColumnMetadata(name="first_name", data_type=ColumnType.VARCHAR),
            ColumnMetadata(name="last_name", data_type=ColumnType.VARCHAR),
            ColumnMetadata(name="department_id", data_type=ColumnType.NUMBER),
            ColumnMetadata(name="position_id", data_type=ColumnType.NUMBER),
            ColumnMetadata(name="location_id", data_type=ColumnType.NUMBER),
            ColumnMetadata(name="quit_date", data_type=ColumnType.DATE),
            ColumnMetadata(name="reg_no", data_type=ColumnType.VARCHAR, nullable=False),
        ],
    )


def _department_table() -> TableMetadata:
    return TableMetadata(
        name="DEPARTMENT",
        description="Departman tablosu",
        aliases=["departman"],
        primary_key=["department_id"],
        columns=[
            ColumnMetadata(name="department_id", data_type=ColumnType.NUMBER, nullable=False),
            ColumnMetadata(name="department_name", data_type=ColumnType.VARCHAR),
            ColumnMetadata(name="department_code", data_type=ColumnType.VARCHAR),
        ],
    )


def _position_table() -> TableMetadata:
    return TableMetadata(
        name="POSITION",
        description="Pozisyon tablosu",
        aliases=["pozisyon"],
        primary_key=["position_id"],
        columns=[
            ColumnMetadata(name="position_id", data_type=ColumnType.NUMBER, nullable=False),
            ColumnMetadata(name="position_name", data_type=ColumnType.VARCHAR),
        ],
    )


def _location_table() -> TableMetadata:
    return TableMetadata(
        name="LOCATION",
        description="Lokasyon tablosu",
        aliases=["lokasyon"],
        primary_key=["location_id"],
        columns=[
            ColumnMetadata(name="location_id", data_type=ColumnType.NUMBER, nullable=False),
            ColumnMetadata(name="location_name", data_type=ColumnType.VARCHAR),
            ColumnMetadata(name="city", data_type=ColumnType.VARCHAR),
        ],
    )


def _relationships() -> list[RelationshipMetadata]:
    return [
        RelationshipMetadata(
            from_table="XXBT_PDKS_PER_DETAILS_V",
            from_column="department_id",
            to_table="DEPARTMENT",
            to_column="department_id",
        ),
        RelationshipMetadata(
            from_table="XXBT_PDKS_PER_DETAILS_V",
            from_column="position_id",
            to_table="POSITION",
            to_column="position_id",
        ),
        RelationshipMetadata(
            from_table="XXBT_PDKS_PER_DETAILS_V",
            from_column="location_id",
            to_table="LOCATION",
            to_column="location_id",
        ),
    ]


def _multi_snapshot() -> CatalogSnapshot:
    return CatalogSnapshot(
        tables=[_employee_table(), _department_table(), _position_table(), _location_table()],
        relationships=_relationships(),
    )


# =====================================================================
# 1. Domain model tests
# =====================================================================

class TestJoinModels:
    """JoinType, JoinCondition, JoinSpec, QualifiedColumn."""

    def test_join_type_values(self) -> None:
        assert JoinType.INNER == "INNER"
        assert JoinType.LEFT == "LEFT"
        assert JoinType.RIGHT == "RIGHT"

    def test_join_condition_frozen(self) -> None:
        cond = JoinCondition(
            left_table="XXBT_PDKS_PER_DETAILS_V",
            left_column="department_id",
            right_table="DEPARTMENT",
            right_column="department_id",
        )
        assert cond.left_table == "XXBT_PDKS_PER_DETAILS_V"
        with pytest.raises(Exception):
            cond.left_table = "X"  # type: ignore[misc]

    def test_join_spec_creation(self) -> None:
        spec = JoinSpec(
            left_table="XXBT_PDKS_PER_DETAILS_V",
            right_table="DEPARTMENT",
            join_type=JoinType.INNER,
            on=[
                JoinCondition(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    left_column="department_id",
                    right_table="DEPARTMENT",
                    right_column="department_id",
                ),
            ],
        )
        assert spec.join_type == JoinType.INNER
        assert len(spec.on) == 1

    def test_qualified_column_str(self) -> None:
        qc = QualifiedColumn(column="department_id", table="XXBT_PDKS_PER_DETAILS_V")
        assert str(qc) == "XXBT_PDKS_PER_DETAILS_V.department_id"

    def test_qualified_column_no_table(self) -> None:
        qc = QualifiedColumn(column="first_name")
        assert str(qc) == "first_name"


class TestQueryPlanMultiTable:
    """QueryPlan multi-table properties."""

    def test_single_table_not_multi(self) -> None:
        plan = QueryPlan(intent="test", table="XXBT_PDKS_PER_DETAILS_V")
        assert plan.is_multi_table is False
        assert set(plan.all_tables) == {"XXBT_PDKS_PER_DETAILS_V"}

    def test_multi_table_with_joins(self) -> None:
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
            ],
        )
        assert plan.is_multi_table is True
        assert set(plan.all_tables) == {"XXBT_PDKS_PER_DETAILS_V", "DEPARTMENT"}

    def test_filter_with_table_qualifier(self) -> None:
        filt = FilterSpec(column="quit_date", op=FilterOp.IS_NULL, table="XXBT_PDKS_PER_DETAILS_V")
        assert filt.table == "XXBT_PDKS_PER_DETAILS_V"

    def test_filter_without_table_qualifier(self) -> None:
        filt = FilterSpec(column="quit_date", op=FilterOp.IS_NULL)
        assert filt.table is None

    def test_backward_compat_no_joins(self) -> None:
        """Old single-table plan dict should still parse."""
        raw = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "select_columns": ["first_name"],
        }
        plan = QueryPlan.model_validate(raw)
        assert plan.joins == []
        assert plan.is_multi_table is False


# =====================================================================
# 2. Catalog metadata tests
# =====================================================================

class TestForeignKeyMetadata:
    def test_fk_creation(self) -> None:
        fk = ForeignKeyMetadata(
            column="department_id",
            referenced_table="DEPARTMENT",
            referenced_column="department_id",
        )
        assert fk.referenced_table == "DEPARTMENT"

    def test_table_with_fks(self) -> None:
        table = _employee_table()
        assert len(table.foreign_keys) == 1
        assert table.foreign_keys[0].referenced_table == "DEPARTMENT"


class TestRelationshipMetadata:
    def test_relationship_creation(self) -> None:
        rel = RelationshipMetadata(
            from_table="XXBT_PDKS_PER_DETAILS_V",
            from_column="department_id",
            to_table="DEPARTMENT",
            to_column="department_id",
        )
        assert rel.relationship_type == "many_to_one"

    def test_snapshot_relationships(self) -> None:
        snap = _multi_snapshot()
        assert len(snap.relationships) == 3

    def test_get_relationships_for(self) -> None:
        snap = _multi_snapshot()
        rels = snap.get_relationships_for("XXBT_PDKS_PER_DETAILS_V")
        assert len(rels) == 3

    def test_get_relationships_for_department(self) -> None:
        snap = _multi_snapshot()
        rels = snap.get_relationships_for("DEPARTMENT")
        assert len(rels) == 1  # EMPLOYEE → DEPARTMENT

    def test_get_join_path(self) -> None:
        snap = _multi_snapshot()
        rel = snap.get_join_path("XXBT_PDKS_PER_DETAILS_V", "DEPARTMENT")
        assert rel is not None
        assert rel.from_column == "department_id"

    def test_get_join_path_not_found(self) -> None:
        snap = _multi_snapshot()
        rel = snap.get_join_path("DEPARTMENT", "LOCATION")
        assert rel is None


# =====================================================================
# 3. MockLLMProvider multi-table tests
# =====================================================================

class TestMockLLMMultiTable:
    @pytest.mark.asyncio
    async def test_dept_count(self) -> None:
        from app.providers.llm.mock_llm import MockLLMProvider

        provider = MockLLMProvider()
        plan = await provider.generate_structured(
            "Kullanıcı sorusu: Departman bazında aktif çalışan sayısı",
            QueryPlan,
        )
        assert plan.is_multi_table
        assert len(plan.joins) == 1
        assert plan.joins[0].right_table == "DEPARTMENT"
        assert any(a.function == AggregateFn.COUNT for a in plan.aggregations)

    @pytest.mark.asyncio
    async def test_position_distribution(self) -> None:
        from app.providers.llm.mock_llm import MockLLMProvider

        provider = MockLLMProvider()
        plan = await provider.generate_structured(
            "Kullanıcı sorusu: Pozisyona göre aktif çalışan dağılımı",
            QueryPlan,
        )
        assert plan.is_multi_table
        assert plan.joins[0].right_table == "POSITION"

    @pytest.mark.asyncio
    async def test_city_count(self) -> None:
        from app.providers.llm.mock_llm import MockLLMProvider

        provider = MockLLMProvider()
        plan = await provider.generate_structured(
            "Kullanıcı sorusu: Şehir bazında aktif çalışan sayısını göster",
            QueryPlan,
        )
        assert plan.is_multi_table
        assert plan.joins[0].right_table == "LOCATION"
        assert "city" in plan.group_by

    @pytest.mark.asyncio
    async def test_dept_position_matrix(self) -> None:
        from app.providers.llm.mock_llm import MockLLMProvider

        provider = MockLLMProvider()
        plan = await provider.generate_structured(
            "Kullanıcı sorusu: Departman bazında pozisyon bazında çalışan sayısı",
            QueryPlan,
        )
        assert plan.is_multi_table
        assert len(plan.joins) == 2

    @pytest.mark.asyncio
    async def test_assignment_history(self) -> None:
        from app.providers.llm.mock_llm import MockLLMProvider

        provider = MockLLMProvider()
        plan = await provider.generate_structured(
            "Kullanıcı sorusu: Çalışanın atama geçmişini göster",
            QueryPlan,
        )
        assert plan.is_multi_table
        assert plan.table == "ASSIGNMENT"
        assert len(plan.joins) == 2

    @pytest.mark.asyncio
    async def test_single_table_still_works(self) -> None:
        """Backward compat: single-table queries must NOT produce JOINs."""
        from app.providers.llm.mock_llm import MockLLMProvider

        provider = MockLLMProvider()
        plan = await provider.generate_structured(
            "Kullanıcı sorusu: Aktif çalışanları listele",
            QueryPlan,
        )
        assert plan.is_multi_table is False
        assert plan.joins == []


# =====================================================================
# 4. Plan normalizer tests
# =====================================================================

class TestNormalizerMultiTable:
    def test_join_table_uppercasing(self) -> None:
        from app.services.plan_normalizer import normalize_raw_plan

        raw: dict[str, Any] = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "joins": [
                {
                    "left_table": "XXBT_PDKS_PER_DETAILS_V",
                    "right_table": "department",
                    "join_type": "inner",
                    "on": [
                        {
                            "left_table": "XXBT_PDKS_PER_DETAILS_V",
                            "left_column": "department_id",
                            "right_table": "department",
                            "right_column": "department_id",
                        }
                    ],
                }
            ],
        }
        result = normalize_raw_plan(raw)
        assert result["table"] == "XXBT_PDKS_PER_DETAILS_V"
        assert result["joins"][0]["left_table"] == "XXBT_PDKS_PER_DETAILS_V"
        assert result["joins"][0]["right_table"] == "DEPARTMENT"
        assert result["joins"][0]["join_type"] == "INNER"
        assert result["joins"][0]["on"][0]["left_table"] == "XXBT_PDKS_PER_DETAILS_V"

    def test_filter_table_uppercasing(self) -> None:
        from app.services.plan_normalizer import normalize_raw_plan

        raw: dict[str, Any] = {
            "intent": "test",
            "table": "XXBT_PDKS_PER_DETAILS_V",
            "filters": [
                {"column": "quit_date", "op": "IS_NULL", "table": "XXBT_PDKS_PER_DETAILS_V"}
            ],
        }
        result = normalize_raw_plan(raw)
        assert result["filters"][0]["table"] == "XXBT_PDKS_PER_DETAILS_V"

    def test_canonicalize_with_multi_table(self) -> None:
        from app.services.plan_normalizer import canonicalize_columns

        emp = _employee_table()
        dept = _department_table()
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["department_name"],
            group_by=["department_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="cnt"),
            ],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
            ],
        )
        result = canonicalize_columns(
            plan,
            emp,
            table_meta_map={"DEPARTMENT": dept},
        )
        # department_name should resolve to DEPARTMENT table's column
        assert "department_name" in result.select_columns


# =====================================================================
# 5. SQL compiler tests
# =====================================================================

class TestSQLCompilerMultiTable:
    def test_single_table_unchanged(self) -> None:
        from app.services.sql_compiler import SQLCompiler

        compiler = SQLCompiler()
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["first_name", "last_name"],
        )
        compiled = compiler.compile(plan, _employee_table())
        assert "JOIN" not in compiled.sql
        assert "FROM XXBT_PDKS_PER_DETAILS_V" in compiled.sql

    def test_inner_join(self) -> None:
        from app.services.sql_compiler import SQLCompiler

        compiler = SQLCompiler()
        plan = QueryPlan(
            intent="Departman bazında çalışan sayısı",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["department_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="cnt"),
            ],
            group_by=["department_name"],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
            ],
        )
        compiled = compiler.compile(
            plan,
            _employee_table(),
            extra_tables={"DEPARTMENT": _department_table()},
        )
        assert "INNER JOIN DEPARTMENT" in compiled.sql
        assert "COUNT(*) AS cnt" in compiled.sql
        assert "GROUP BY" in compiled.sql

    def test_left_join(self) -> None:
        from app.services.sql_compiler import SQLCompiler

        compiler = SQLCompiler()
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["first_name", "department_name"],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.LEFT,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
            ],
        )
        compiled = compiler.compile(
            plan,
            _employee_table(),
            extra_tables={"DEPARTMENT": _department_table()},
        )
        assert "LEFT JOIN DEPARTMENT" in compiled.sql

    def test_triple_join(self) -> None:
        from app.services.sql_compiler import SQLCompiler

        compiler = SQLCompiler()
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["department_name", "position_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="cnt"),
            ],
            group_by=["department_name", "position_name"],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="POSITION",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="position_id",
                            right_table="POSITION",
                            right_column="position_id",
                        ),
                    ],
                ),
            ],
        )
        compiled = compiler.compile(
            plan,
            _employee_table(),
            extra_tables={
                "DEPARTMENT": _department_table(),
                "POSITION": _position_table(),
            },
        )
        assert "INNER JOIN DEPARTMENT" in compiled.sql
        assert "INNER JOIN POSITION" in compiled.sql
        assert compiled.sql.count("INNER JOIN") == 2

    def test_table_aliases_in_join(self) -> None:
        from app.services.sql_compiler import SQLCompiler

        compiler = SQLCompiler()
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["first_name", "department_name"],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
            ],
        )
        compiled = compiler.compile(
            plan,
            _employee_table(),
            extra_tables={"DEPARTMENT": _department_table()},
        )
        # Should have table aliases like e.first_name, d.department_name
        assert "e." in compiled.sql or "d." in compiled.sql

    def test_where_with_table_qualifier(self) -> None:
        from app.services.sql_compiler import SQLCompiler

        compiler = SQLCompiler()
        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["department_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="cnt"),
            ],
            filters=[
                FilterSpec(column="quit_date", op=FilterOp.IS_NULL, table="XXBT_PDKS_PER_DETAILS_V"),
            ],
            group_by=["department_name"],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
            ],
        )
        compiled = compiler.compile(
            plan,
            _employee_table(),
            extra_tables={"DEPARTMENT": _department_table()},
        )
        assert "IS NULL" in compiled.sql


# =====================================================================
# 6. Validation service tests
# =====================================================================

class TestValidationMultiTable:
    @pytest.mark.asyncio
    async def test_multi_table_valid(self) -> None:
        from app.providers.catalog.in_memory import InMemoryCatalogProvider
        from app.services.catalog_service import CatalogService
        from app.services.validation_service import ValidationService

        provider = InMemoryCatalogProvider()
        provider._snapshot = _multi_snapshot()
        service = CatalogService(provider)
        validator = ValidationService(service)

        plan = QueryPlan(
            intent="Departman bazında çalışan sayısı",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["department_name"],
            aggregations=[
                AggregationSpec(function=AggregateFn.COUNT, column="*", alias="cnt"),
            ],
            group_by=["department_name"],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="DEPARTMENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="department_id",
                            right_table="DEPARTMENT",
                            right_column="department_id",
                        ),
                    ],
                ),
            ],
        )
        result = await validator.validate(plan)
        assert result.ok, f"Validation failed: {result.errors}"
        assert "DEPARTMENT" in result.resolved_tables

    @pytest.mark.asyncio
    async def test_invalid_join_table(self) -> None:
        from app.providers.catalog.in_memory import InMemoryCatalogProvider
        from app.services.catalog_service import CatalogService
        from app.services.validation_service import ValidationService

        snap = CatalogSnapshot(tables=[_employee_table()])
        provider = InMemoryCatalogProvider()
        provider._snapshot = snap
        service = CatalogService(provider)
        validator = ValidationService(service)

        plan = QueryPlan(
            intent="test",
            table="XXBT_PDKS_PER_DETAILS_V",
            select_columns=["first_name"],
            joins=[
                JoinSpec(
                    left_table="XXBT_PDKS_PER_DETAILS_V",
                    right_table="NONEXISTENT",
                    join_type=JoinType.INNER,
                    on=[
                        JoinCondition(
                            left_table="XXBT_PDKS_PER_DETAILS_V",
                            left_column="id",
                            right_table="NONEXISTENT",
                            right_column="id",
                        ),
                    ],
                ),
            ],
        )
        result = await validator.validate(plan)
        assert not result.ok
        assert any("NONEXISTENT" in e.message for e in result.errors)


# =====================================================================
# 7. InMemoryRetriever relation-aware tests
# =====================================================================

class TestRetrieverRelationAware:
    @pytest.mark.asyncio
    async def test_related_tables_expanded(self) -> None:
        from app.providers.catalog.in_memory import InMemoryCatalogProvider
        from app.providers.retrieval.in_memory_retriever import InMemoryRetriever

        snap = _multi_snapshot()
        provider = InMemoryCatalogProvider()
        provider._snapshot = snap
        retriever = InMemoryRetriever(provider)

        result = await retriever.retrieve("employee listele", top_k=5)
        # Should include EMPLOYEE and potentially related tables
        table_names = {t.name for t in result.tables}
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names

    @pytest.mark.asyncio
    async def test_relationships_filtered(self) -> None:
        from app.providers.catalog.in_memory import InMemoryCatalogProvider
        from app.providers.retrieval.in_memory_retriever import InMemoryRetriever

        snap = _multi_snapshot()
        provider = InMemoryCatalogProvider()
        provider._snapshot = snap
        retriever = InMemoryRetriever(provider)

        result = await retriever.retrieve("departman çalışan", top_k=5)
        # Relationships in the result should only include tables in the result
        result_names = {t.name.upper() for t in result.tables}
        for rel in result.relationships:
            assert rel.from_table.upper() in result_names
            assert rel.to_table.upper() in result_names


# =====================================================================
# 8. Prompt relationship block tests
# =====================================================================

class TestPromptRelationshipBlock:
    def test_empty_when_no_relationships(self) -> None:
        from app.providers.llm.prompts import build_relationship_block

        snap = CatalogSnapshot(tables=[_employee_table()])
        block = build_relationship_block(snap)
        assert block == ""

    def test_has_content_with_relationships(self) -> None:
        from app.providers.llm.prompts import build_relationship_block

        snap = _multi_snapshot()
        block = build_relationship_block(snap)
        assert "XXBT_PDKS_PER_DETAILS_V" in block
        assert "DEPARTMENT" in block
        assert "→" in block

    def test_catalog_summary_includes_fk(self) -> None:
        from app.providers.llm.prompts import build_catalog_summary

        snap = _multi_snapshot()
        summary = build_catalog_summary(snap)
        assert "FK:" in summary
        assert "department_id → DEPARTMENT.department_id" in summary

    def test_multi_table_prompt_includes_join_rules(self) -> None:
        from app.providers.llm.prompts import build_planner_prompt

        snap = _multi_snapshot()
        prompt = build_planner_prompt("test", snap)
        assert "joins" in prompt
        assert "join_type" in prompt


# =====================================================================
# 9. Metadata file validation
# =====================================================================

class TestSampleMetadataMultiTable:
    def test_metadata_has_tables(self) -> None:
        path = DATA_DIR / "sample_metadata.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        table_names = {t["name"] for t in data["tables"]}
        assert "XXBT_PDKS_PER_DETAILS_V" in table_names
        assert len(table_names) >= 2  # at least XXBT_PDKS_PER_DETAILS_V + others

    def test_metadata_has_relationships(self) -> None:
        path = DATA_DIR / "sample_metadata.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data.get("relationships", [])) >= 1

    def test_employee_has_no_ghost_fks(self) -> None:
        """XXBT_PDKS_PER_DETAILS_V FK'leri ghost tablolara işaret etmemeli."""
        path = DATA_DIR / "sample_metadata.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        emp = next(t for t in data["tables"] if t["name"] == "XXBT_PDKS_PER_DETAILS_V")
        fks = emp.get("foreign_keys", [])
        # Ghost tables (DEPARTMENT, POSITION, LOCATION) not in metadata → FKs removed
        all_table_names = {t["name"] for t in data["tables"]}
        for fk in fks:
            assert fk["referenced_table"] in all_table_names, (
                f"FK {fk['column']} → {fk['referenced_table']} references ghost table"
            )

    def test_po_fk_directions_canonical(self) -> None:
        """All PO FKs must point child → parent (never parent → child)."""
        path = DATA_DIR / "sample_metadata.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        # Parent tables should NOT have FKs to their children
        headers = next(t for t in data["tables"] if t["name"] == "PO_HEADERS_ALL")
        assert len(headers.get("foreign_keys", [])) == 0, "PO_HEADERS_ALL must not have child FKs"

        mtl = next(t for t in data["tables"] if t["name"] == "MTL_SYSTEM_ITEMS_B")
        assert len(mtl.get("foreign_keys", [])) == 0, "MTL_SYSTEM_ITEMS_B must not have reverse FK"

        # Child tables must have correct parent references
        lines = next(t for t in data["tables"] if t["name"] == "PO_LINES_ALL")
        lines_fk_refs = {fk["referenced_table"] for fk in lines.get("foreign_keys", [])}
        assert "PO_HEADERS_ALL" in lines_fk_refs
        assert "MTL_SYSTEM_ITEMS_B" in lines_fk_refs

        locs = next(t for t in data["tables"] if t["name"] == "PO_LINE_LOCATIONS_ALL")
        locs_fk_refs = {fk["referenced_table"] for fk in locs.get("foreign_keys", [])}
        assert "PO_HEADERS_ALL" in locs_fk_refs
        assert "PO_LINES_ALL" in locs_fk_refs

        dists = next(t for t in data["tables"] if t["name"] == "PO_DISTRIBUTIONS_ALL")
        dists_fk_refs = {fk["referenced_table"] for fk in dists.get("foreign_keys", [])}
        assert "PO_HEADERS_ALL" in dists_fk_refs
        assert "PO_LINES_ALL" in dists_fk_refs
        assert "PO_LINE_LOCATIONS_ALL" in dists_fk_refs


# =====================================================================
# 10. Ingestion service FK/relationship mapping tests
# =====================================================================

class TestIngestionMultiTable:
    @pytest.mark.asyncio
    async def test_ingestion_maps_relationships(self) -> None:
        from app.providers.metadata.file_loader import JSONMetadataLoader
        from app.services.metadata_ingestion_service import MetadataIngestionService

        loader = JSONMetadataLoader()
        service = MetadataIngestionService(loader)
        snap = await service.ingest(DATA_DIR / "sample_metadata.json")

        assert len(snap.tables) >= 2
        assert len(snap.relationships) >= 1

    @pytest.mark.asyncio
    async def test_ingestion_maps_foreign_keys(self) -> None:
        from app.providers.metadata.file_loader import JSONMetadataLoader
        from app.services.metadata_ingestion_service import MetadataIngestionService

        loader = JSONMetadataLoader()
        service = MetadataIngestionService(loader)
        snap = await service.ingest(DATA_DIR / "sample_metadata.json")

        lines = next(t for t in snap.tables if t.name == "PO_LINES_ALL")
        assert len(lines.foreign_keys) == 2
        ref_tables = {fk.referenced_table for fk in lines.foreign_keys}
        assert "PO_HEADERS_ALL" in ref_tables
        assert "MTL_SYSTEM_ITEMS_B" in ref_tables


# =====================================================================
# 11. PO relationship graph tests
# =====================================================================


def _load_po_snapshot() -> CatalogSnapshot:
    """Load the real sample_metadata.json as a CatalogSnapshot."""
    import json as _json
    path = DATA_DIR / "sample_metadata.json"
    data = _json.loads(path.read_text(encoding="utf-8"))
    return CatalogSnapshot.model_validate(data)


class TestPORelationshipGraph:
    """CatalogSnapshot.get_join_path / get_relationships_for on real PO data."""

    def test_join_path_headers_to_lines(self) -> None:
        snap = _load_po_snapshot()
        rel = snap.get_join_path("PO_HEADERS_ALL", "PO_LINES_ALL")
        assert rel is not None
        assert rel.from_table == "PO_LINES_ALL"
        assert rel.to_table == "PO_HEADERS_ALL"
        assert rel.from_column == "PO_HEADER_ID"

    def test_join_path_lines_to_locations(self) -> None:
        snap = _load_po_snapshot()
        rel = snap.get_join_path("PO_LINES_ALL", "PO_LINE_LOCATIONS_ALL")
        assert rel is not None
        assert rel.from_column == "PO_LINE_ID"

    def test_join_path_locations_to_distributions(self) -> None:
        snap = _load_po_snapshot()
        rel = snap.get_join_path("PO_LINE_LOCATIONS_ALL", "PO_DISTRIBUTIONS_ALL")
        assert rel is not None
        assert rel.from_column == "LINE_LOCATION_ID"

    def test_join_path_headers_to_distributions(self) -> None:
        """Direct relationship exists (skip-level shortcut)."""
        snap = _load_po_snapshot()
        rel = snap.get_join_path("PO_HEADERS_ALL", "PO_DISTRIBUTIONS_ALL")
        assert rel is not None
        assert rel.from_table == "PO_DISTRIBUTIONS_ALL"
        assert rel.from_column == "PO_HEADER_ID"

    def test_join_path_lines_to_item_master(self) -> None:
        snap = _load_po_snapshot()
        rel = snap.get_join_path("PO_LINES_ALL", "MTL_SYSTEM_ITEMS_B")
        assert rel is not None
        assert rel.from_column == "ITEM_ID"
        assert rel.to_column == "INVENTORY_ITEM_ID"

    def test_join_path_headers_to_item_master(self) -> None:
        """No direct relationship between PO_HEADERS_ALL and MTL_SYSTEM_ITEMS_B."""
        snap = _load_po_snapshot()
        rel = snap.get_join_path("PO_HEADERS_ALL", "MTL_SYSTEM_ITEMS_B")
        assert rel is None

    def test_join_path_employee_to_po(self) -> None:
        """No relationship between EMPLOYEE and PO domains."""
        snap = _load_po_snapshot()
        rel = snap.get_join_path("XXBT_PDKS_PER_DETAILS_V", "PO_HEADERS_ALL")
        assert rel is None

    def test_join_path_nonexistent_table(self) -> None:
        snap = _load_po_snapshot()
        rel = snap.get_join_path("PO_HEADERS_ALL", "NONEXISTENT")
        assert rel is None

    def test_relationships_for_headers(self) -> None:
        """PO_HEADERS_ALL is referenced by LINES, LOCATIONS, DISTRIBUTIONS."""
        snap = _load_po_snapshot()
        rels = snap.get_relationships_for("PO_HEADERS_ALL")
        from_tables = {r.from_table for r in rels}
        assert "PO_LINES_ALL" in from_tables
        assert "PO_LINE_LOCATIONS_ALL" in from_tables
        assert "PO_DISTRIBUTIONS_ALL" in from_tables

    def test_relationships_for_lines(self) -> None:
        snap = _load_po_snapshot()
        rels = snap.get_relationships_for("PO_LINES_ALL")
        assert len(rels) >= 4  # to headers, from locations, from dists, to items

    def test_all_relationships_many_to_one(self) -> None:
        snap = _load_po_snapshot()
        for rel in snap.relationships:
            assert rel.relationship_type == "many_to_one", (
                f"{rel.from_table} → {rel.to_table} should be many_to_one"
            )

    def test_canonical_po_chain(self) -> None:
        """Verify the complete canonical chain direction."""
        snap = _load_po_snapshot()
        chain = [
            ("PO_LINES_ALL", "PO_HEADER_ID", "PO_HEADERS_ALL", "PO_HEADER_ID"),
            ("PO_LINE_LOCATIONS_ALL", "PO_LINE_ID", "PO_LINES_ALL", "PO_LINE_ID"),
            ("PO_LINE_LOCATIONS_ALL", "PO_HEADER_ID", "PO_HEADERS_ALL", "PO_HEADER_ID"),
            ("PO_DISTRIBUTIONS_ALL", "LINE_LOCATION_ID", "PO_LINE_LOCATIONS_ALL", "LINE_LOCATION_ID"),
            ("PO_DISTRIBUTIONS_ALL", "PO_LINE_ID", "PO_LINES_ALL", "PO_LINE_ID"),
            ("PO_DISTRIBUTIONS_ALL", "PO_HEADER_ID", "PO_HEADERS_ALL", "PO_HEADER_ID"),
            ("PO_LINES_ALL", "ITEM_ID", "MTL_SYSTEM_ITEMS_B", "INVENTORY_ITEM_ID"),
        ]
        for from_t, from_c, to_t, to_c in chain:
            match = next(
                (r for r in snap.relationships
                 if r.from_table == from_t and r.from_column == from_c
                 and r.to_table == to_t and r.to_column == to_c),
                None,
            )
            assert match is not None, (
                f"Missing relationship: {from_t}.{from_c} → {to_t}.{to_c}"
            )


# =====================================================================
# 12. PO corpus tests
# =====================================================================


class TestPOCorpus:
    """Validate PO example entries in sample_schema_documents.jsonl."""

    def _load_examples(self) -> list[dict]:
        import json as _json
        path = DATA_DIR / "sample_schema_documents.jsonl"
        lines = [_json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [l for l in lines if l["doc_type"] == "example"]

    def test_po_example_count(self) -> None:
        examples = self._load_examples()
        po_ex = [e for e in examples if any(
            t.startswith("PO_") or t == "MTL_SYSTEM_ITEMS_B"
            for t in e.get("tables", [])
        )]
        assert len(po_ex) >= 20

    def test_open_orders_example_exists(self) -> None:
        examples = self._load_examples()
        assert any(e["doc_id"] == "ex_po_open_orders" for e in examples)

    def test_vendor_count_example_exists(self) -> None:
        examples = self._load_examples()
        assert any(e["doc_id"] == "ex_po_count_by_vendor" for e in examples)

    def test_item_qty_example_exists(self) -> None:
        examples = self._load_examples()
        assert any(e["doc_id"] == "ex_po_item_qty_summary" for e in examples)

    def test_pending_delivery_example_exists(self) -> None:
        examples = self._load_examples()
        assert any(e["doc_id"] == "ex_po_pending_delivery" for e in examples)

    def test_dist_amount_example_exists(self) -> None:
        examples = self._load_examples()
        assert any(e["doc_id"] == "ex_po_dist_amount_analysis" for e in examples)

    def test_item_line_count_example_exists(self) -> None:
        examples = self._load_examples()
        assert any(e["doc_id"] == "ex_po_item_line_count" for e in examples)

    def test_unapproved_example_exists(self) -> None:
        examples = self._load_examples()
        assert any(e["doc_id"] == "ex_po_unapproved_unclosed" for e in examples)

    def test_last_30_days_example_exists(self) -> None:
        examples = self._load_examples()
        assert any(e["doc_id"] == "ex_po_last_30_days" for e in examples)

    def test_multi_table_examples_have_join_tag(self) -> None:
        examples = self._load_examples()
        multi = [e for e in examples if len(e.get("tables", [])) > 1]
        for e in multi:
            assert "multi_table" in e.get("tags", []) or "join" in e.get("tags", []), (
                f"{e['doc_id']} is multi-table but missing join/multi_table tag"
            )


# =====================================================================
# 13. Table alias generator tests
# =====================================================================

class TestTableAliasGenerator:
    def test_basic_aliases(self) -> None:
        from app.services.sql_compiler import _generate_aliases

        aliases = _generate_aliases(["XXBT_PDKS_PER_DETAILS_V", "DEPARTMENT"])
        assert aliases["XXBT_PDKS_PER_DETAILS_V"] == "x"
        assert aliases["DEPARTMENT"] == "d"

    def test_collision_handling(self) -> None:
        from app.services.sql_compiler import _generate_aliases

        # XXBT_PDKS_PER_DETAILS_V starts with 'x', EXT_TABLE starts with 'e' — no collision
        aliases = _generate_aliases(["XXBT_PDKS_PER_DETAILS_V", "EXT_TABLE"])
        assert aliases["XXBT_PDKS_PER_DETAILS_V"] == "x"
        assert aliases["EXT_TABLE"] == "e"

    def test_many_tables(self) -> None:
        from app.services.sql_compiler import _generate_aliases

        aliases = _generate_aliases(
            ["XXBT_PDKS_PER_DETAILS_V", "DEPARTMENT", "POSITION", "LOCATION"]
        )
        assert len(aliases) == 4
        # All values should be unique
        assert len(set(aliases.values())) == 4
