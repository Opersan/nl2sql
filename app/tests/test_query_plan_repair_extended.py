from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.catalog_models import ColumnMetadata, ColumnType, TableMetadata
from app.domain.query_plan import (
    FilterOp,
    FilterSpec,
    JoinCondition,
    JoinSpec,
    JoinType,
    QueryPlan,
)
from app.providers.executor.oracle_executor import _classify_oracle_error
from app.services.query_plan_repair import QueryPlanRepairEngine
from app.services.sql_compiler import SQLCompiler
from scripts.e2e_real_provider_eval import _bucket_execution_error, _bucket_wrong_plan


@pytest.fixture()
def engine() -> QueryPlanRepairEngine:
    return QueryPlanRepairEngine()


def _plan(**kwargs) -> QueryPlan:
    kwargs.setdefault("intent", "test")
    kwargs.setdefault("table", "PO_HEADERS_ALL")
    return QueryPlan(**kwargs)


def test_registry_anchor_from_child_table(engine: QueryPlanRepairEngine) -> None:
    repaired, _ = engine.repair(_plan(table="PO_DISTRIBUTIONS_ALL"), "calisan raporu")
    assert repaired.table == "PO_HEADERS_ALL"


def test_registry_semantic_join_path_enforced(engine: QueryPlanRepairEngine) -> None:
    p = _plan(table="PO_HEADERS_ALL", semantic_intent="po_item_line_count", joins=[])
    repaired, _ = engine.repair(p, "ignored")
    assert repaired.join_path_id == "po.header_lines_items"
    assert len(repaired.joins) == 2


def test_filter_column_not_fuzzy_repaired(engine: QueryPlanRepairEngine) -> None:
    p = _plan(table="XXBT_PDKS_PER_DETAILS_V", filters=[FilterSpec(column="MAIL", op=FilterOp.IS_NOT_NULL)])
    repaired, _ = engine.repair(p, "maili olan çalışanlar")
    assert repaired.filters[0].column == "MAIL"


def test_no_message_based_cross_domain_reroute(engine: QueryPlanRepairEngine) -> None:
    repaired, _ = engine.repair(_plan(table="XXBT_PDKS_PER_DETAILS_V"), "satinalma siparis dagitim")
    assert repaired.table == "XXBT_PDKS_PER_DETAILS_V"


def _mk_table(name: str, cols: list[str]) -> TableMetadata:
    return TableMetadata(
        name=name,
        description=name,
        columns=[ColumnMetadata(name=c, data_type=ColumnType.NUMBER) for c in cols],
    )


def test_sql_compiler_multi_ambiguous_column_raises() -> None:
    compiler = SQLCompiler()
    p = QueryPlan(
        intent="x",
        table="A",
        joins=[
            JoinSpec(
                left_table="A",
                right_table="B",
                join_type=JoinType.INNER,
                on=[JoinCondition(left_table="A", left_column="id", right_table="B", right_column="id")],
            )
        ],
        select_columns=["id"],
    )
    with pytest.raises(Exception):
        compiler.compile(
            p,
            _mk_table("A", ["id"]),
            extra_tables={"B": _mk_table("B", ["id"])},
        )


@pytest.mark.parametrize(
    "err,expected",
    [
        ("ORA-00904: invalid identifier", "invalid_identifier"),
        ("ORA-00918: column ambiguously defined", "ambiguous_column"),
        ("ORA-00979: not a group by expression", "expression_rendering_issue"),
        ("ORA-01008: not all variables bound", "mis_shaped_params"),
        ("ORA-01858: non-numeric where numeric expected", "invalid_date_value"),
    ],
)
def test_oracle_error_classifier(err: str, expected: str) -> None:
    assert _classify_oracle_error(err) == expected


def _res(**kwargs):
    base = dict(
        wrong_plan=False,
        wrong_plan_reasons=[],
        expected_table=None,
        predicted_tables=[],
        join_path=[],
        raw_status="success",
        execution_error_subtype=None,
        error_detail=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "r,expected",
    [
        (_res(wrong_plan=True, wrong_plan_reasons=["wrong_table"], expected_table="PO_HEADERS_ALL", predicted_tables=["XXBT_PDKS_PER_DETAILS_V"]), {"wrong_domain_entity"}),
        (_res(wrong_plan=True, wrong_plan_reasons=["wrong_table"], expected_table="PO_HEADERS_ALL", predicted_tables=["PO_LINES_ALL"]), {"wrong_root_table"}),
        (_res(wrong_plan=True, wrong_plan_reasons=["wrong_join"], join_path=[]), {"missing_join"}),
        (_res(wrong_plan=True, wrong_plan_reasons=["wrong_join"], join_path=["x"]), {"wrong_join_path"}),
        (_res(wrong_plan=True, wrong_plan_reasons=["wrong_aggregation"]), {"missing_aggregation"}),
        (_res(wrong_plan=True, wrong_plan_reasons=["wrong_filter_column"]), {"wrong_filter_column"}),
        (_res(wrong_plan=True, wrong_plan_reasons=["semantically_incorrect_result"]), {"unnecessary_clarification_disguised_as_success"}),
    ],
)
def test_wrong_plan_bucketing(r, expected) -> None:
    assert set(_bucket_wrong_plan(r)) == expected


@pytest.mark.parametrize(
    "r,expected",
    [
        (_res(raw_status="execution_error", execution_error_subtype="invalid_date_value"), "oracle_date_type_error"),
        (_res(raw_status="execution_error", execution_error_subtype="invalid_identifier"), "invalid_identifier"),
        (_res(raw_status="execution_error", execution_error_subtype="ambiguous_column"), "ambiguous_column"),
        (_res(raw_status="execution_error", execution_error_subtype="expression_rendering_issue"), "expression_rendering_issue"),
        (_res(raw_status="execution_error", execution_error_subtype="mis_shaped_params"), "runtime_mis_shaped_params"),
        (_res(raw_status="execution_error", execution_error_subtype="timeout"), "timeout_heavy_join"),
        (_res(raw_status="execution_error", execution_error_subtype="unknown_execution_error"), "data_specific_edge_case_or_unknown"),
    ],
)
def test_execution_error_bucketing(r, expected: str) -> None:
    assert _bucket_execution_error(r) == expected
