"""Dataset-driven session evaluation tests backed by ``data/questions.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.domain.execution_models import (
    CompiledQuery,
    ExecutionResult,
    ExecutionStatus,
    OrchestrationResult,
    ValidationResult,
)
from app.domain.query_plan import (
    AggregateFn,
    AggregationSpec,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
    SortDirection,
)
from app.services.clarification_state_manager import (
    ClarificationCandidate,
    ClarificationReply,
    ClarificationStateManager,
)
from app.services.followup_context_merge import FollowupContextMergeService, MergeResult
from app.services.narrator_service import NarratorService
from app.services.session_service import SessionService

ROOT_DIR = Path(__file__).resolve().parents[2]
QUESTIONS_PATH = ROOT_DIR / "data" / "questions.json"

HR_TABLE = "XXBT_PDKS_PER_DETAILS_V"
PO_TABLE = "PO_HEADERS_ALL"


@dataclass(frozen=True)
class TurnCase:
    session_id: str
    session_description: str
    turn_number: int
    user_message: str
    expect: dict[str, Any]
    turns: tuple[dict[str, Any], ...]

    @property
    def case_id(self) -> str:
        return f"{self.session_id}-t{self.turn_number:02d}"


@dataclass
class ReconstructedState:
    current_plan: QueryPlan | None
    pending_question: str | None = None
    pending_target_column: str | None = None
    pending_original_value: str | None = None
    pending_candidates: tuple[str, ...] = ()


KNOWN_GAPS: dict[tuple[str, int], str] = {
    ("session_01_followup_negation", 2): "Negation-only refinement phrasing is not classified as follow-up.",
    ("session_01_followup_negation", 3): "Sort-only follow-ups are not detected without an explicit refinement cue.",
    ("session_01_followup_negation", 4): "Limit-only follow-ups are not detected as follow-up.",
    ("session_02_clarification_flow", 3): "Bare removal phrasing like 'stajyerleri cikar' is not classified as refinement.",
    ("session_02_clarification_flow", 4): "Elliptic follow-ups like 'aktif olanlar' are treated as fresh queries.",
    ("session_03_correction_vs_new", 2): "Short correction phrases like 'yok istanbuldakiler' are not recognized as correction.",
    ("session_03_correction_vs_new", 4): "Conversation reset is not implemented as a message-driven runtime behavior.",
    ("session_05_aggregate_drilldown", 2): "Aggregate refinements without an explicit cue are not detected as follow-up.",
    ("session_05_aggregate_drilldown", 3): "Shape-shift prompts like 'bunlari listele' are not detected as follow-up.",
    ("session_05_aggregate_drilldown", 4): "Ranking/list refinements without a cue are not detected as follow-up.",
    ("session_06_temporal_refinement", 2): "Bare removal phrasing like 'stajyerleri cikar' is not classified as follow-up.",
    ("session_06_temporal_refinement", 3): "Temporal correction phrasing like 'simdi son 3 aya indir' is not recognized as correction.",
    ("session_07_clarification_chain", 3): "Post-clarification elliptic follow-ups are treated as fresh queries.",
    ("session_07_clarification_chain", 4): "Limit-only follow-ups are not detected as follow-up.",
    ("session_08_contradiction", 2): "Observation-style record turns are not classified as follow-up/reference questions.",
    ("session_08_contradiction", 3): "Re-check turns are not classified as reference follow-ups.",
    ("session_08_contradiction", 4): "Contradiction-report detection is not implemented as a dedicated runtime turn type.",
    ("session_09_negation_variants", 2): "Sentence-final 'getirme' negation is not matched by current refinement rules.",
    ("session_09_negation_variants", 3): "Equivalent negation phrasing is not classified as follow-up.",
    ("session_10_pagination", 2): "Sort-only follow-ups are not detected without an explicit refinement cue.",
    ("session_10_pagination", 3): "Pagination is not represented in QueryPlan and is not handled by follow-up merge.",
    ("session_10_pagination", 4): "Sort-only follow-ups are not detected without an explicit refinement cue.",
    ("session_10_pagination", 5): "Pagination is not represented in QueryPlan and is not handled by follow-up merge.",
    ("session_11_shape_shift", 2): "Aggregate shape-shift prompts are not detected as follow-up.",
    ("session_11_shape_shift", 3): "Bare filter follow-ups without a cue are treated as fresh queries.",
    ("session_11_shape_shift", 4): "Grouped distribution prompts without an explicit refinement cue are treated as fresh queries.",
    ("session_12_reset", 2): "Bare removal phrasing like 'stajyerleri cikar' is not classified as follow-up.",
    ("session_12_reset", 3): "Conversation reset is not implemented as a message-driven runtime behavior.",
    # P0: Filter removal
    ("session_13_filter_removal", 2): "Filter removal phrasing 'filtresini kaldir' is not in refinement signals.",
    ("session_13_filter_removal", 3): "Filter removal phrasing 'da kaldir' is not in refinement signals.",
    # P0: Confirmation turns
    ("session_15_confirmation_turns", 2): "Standalone 'evet' is not classified as confirmation; treated as fresh query.",
    ("session_15_confirmation_turns", 3): "Standalone 'tamam' is not classified as confirmation; treated as fresh query.",
    # P1: Clarification rejection
    ("session_16_clarification_rejection", 2): "Clarification rejection phrasing is not a recognized turn type.",
    # P1: Entity swap
    ("session_17_entity_swap", 3): "Entity swap phrasing 'aynisini X icin yap' is not in refinement signals.",
}


def _load_dataset() -> dict[str, Any]:
    return json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))


def _load_cases() -> list[TurnCase]:
    data = _load_dataset()
    cases: list[TurnCase] = []
    for session in data["sessions"]:
        turns = tuple(session["turns"])
        for turn in turns:
            cases.append(
                TurnCase(
                    session_id=session["session_id"],
                    session_description=session["description"],
                    turn_number=int(turn["turn"]),
                    user_message=str(turn["user"]),
                    expect=dict(turn["expect"]),
                    turns=turns,
                )
            )
    return cases


ALL_CASES = _load_cases()


def _param(case: TurnCase) -> Any:
    gap = KNOWN_GAPS.get((case.session_id, case.turn_number))
    if gap is None:
        return pytest.param(case, id=case.case_id)
    return pytest.param(case, id=case.case_id, marks=pytest.mark.xfail(reason=gap, strict=False))


PARAM_CASES = [_param(case) for case in ALL_CASES]


def _domain_to_table(domain: str | None) -> str:
    return PO_TABLE if (domain or "").upper() == "PO" else HR_TABLE


def _default_select_columns(table: str | None) -> list[str]:
    if (table or "").upper() == PO_TABLE:
        return ["PO_HEADER_ID", "VENDOR_ID", "CREATION_DATE"]
    return ["SICIL_NO", "AD", "SOYAD", "BIRIM_ADI"]


def _looks_po(message: str) -> bool:
    lowered = message.casefold()
    return any(token in lowered for token in ("satin alma", "satinalma", "siparis", "sipariş", "po"))


def _normalize_string(value: str) -> str:
    return value.casefold().replace("ı", "i").replace("İ", "i")


def _parse_filter(text: str, *, table: str | None = None) -> FilterSpec:
    raw = str(text).strip()
    if raw.endswith(" IS NULL"):
        return FilterSpec(column=raw.removesuffix(" IS NULL").strip(), table=table, op=FilterOp.IS_NULL)
    if raw.endswith(" IS NOT NULL"):
        return FilterSpec(column=raw.removesuffix(" IS NOT NULL").strip(), table=table, op=FilterOp.IS_NOT_NULL)
    for marker, op in (("!=", FilterOp.NEQ), (">=", FilterOp.GTE), ("<=", FilterOp.LTE), ("=", FilterOp.EQ)):
        if marker in raw:
            column, value = raw.split(marker, 1)
            return FilterSpec(column=column.strip(), table=table, op=op, value=value.strip())
    raise AssertionError(f"Unsupported filter expression in dataset: {raw}")


def _parse_filters(values: list[str] | None, *, table: str | None = None) -> list[FilterSpec]:
    return [_parse_filter(value, table=table) for value in values or []]


def _parse_order(values: list[str] | None, *, table: str | None = None) -> list[OrderSpec]:
    items: list[OrderSpec] = []
    for value in values or []:
        parts = str(value).split()
        direction = SortDirection.DESC if len(parts) > 1 and parts[1].upper() == "DESC" else SortDirection.ASC
        items.append(OrderSpec(column=parts[0], table=table, direction=direction))
    return items


def _make_aggregations(expect: dict[str, Any]) -> list[AggregationSpec]:
    if expect.get("aggregation") == "COUNT" or expect.get("shape") == "AGGREGATE":
        return [AggregationSpec(function=AggregateFn.COUNT, column="*", alias="count_value")]
    return []


def _make_group_by(expect: dict[str, Any], previous: QueryPlan | None) -> list[str]:
    if expect.get("group_by") is True:
        return list(previous.group_by) if previous and previous.group_by else ["BIRIM_ADI"]
    if isinstance(expect.get("group_by"), list):
        return [str(item) for item in expect["group_by"]]
    return []


def _infer_table(expect: dict[str, Any], previous: QueryPlan | None, message: str) -> str:
    if expect.get("domain"):
        return _domain_to_table(str(expect["domain"]))
    if expect.get("turn_type") == "DOMAIN_SWITCH_OR_CLARIFICATION" and _looks_po(message):
        return PO_TABLE
    if previous and previous.table and expect.get("turn_type") != "RESET":
        return previous.table
    if _looks_po(message):
        return PO_TABLE
    return HR_TABLE


def _infer_filters_from_message(message: str, *, table: str) -> list[FilterSpec]:
    lowered = _normalize_string(message)
    filters: list[FilterSpec] = []

    if "aktif" in lowered:
        filters.append(FilterSpec(column="CIKIS_TARIHI", table=table, op=FilterOp.IS_NULL))

    if "ankara" in lowered:
        filters.append(FilterSpec(column="LOCATION_ADI", table=table, op=FilterOp.EQ, value="ANKARA"))
    if "istanbul" in lowered:
        filters.append(FilterSpec(column="LOCATION_ADI", table=table, op=FilterOp.EQ, value="ISTANBUL"))

    if "bordrolu" in lowered:
        filters.append(FilterSpec(column="BORDROLU", table=table, op=FilterOp.EQ, value="Y"))

    if "stajyer" in lowered:
        if any(token in lowered for token in ("olmayan", "haric", "hariç", "getirme", "cikar", "çıkar")):
            filters.append(FilterSpec(column="STAJYER", table=table, op=FilterOp.NEQ, value="Y"))
        elif any(token in lowered for token in ("yalniz stajyer", "yalnız stajyer")):
            filters.append(FilterSpec(column="STAJYER", table=table, op=FilterOp.EQ, value="Y"))
        else:
            filters.append(FilterSpec(column="STAJYER", table=table, op=FilterOp.EQ, value="Y"))

    return filters


def _inject_clarification_placeholder(
    plan: QueryPlan,
    *,
    message: str,
    candidate_hint: str | None = None,
) -> QueryPlan:
    if not plan.needs_clarification or plan.filters:
        return plan
    value = "dizayn" if "dizayn" in _normalize_string(message) else (candidate_hint or "?")
    return plan.model_copy(
        update={
            "filters": [FilterSpec(column="BIRIM_ADI", table=plan.table, op=FilterOp.EQ, value=value)],
        }
    )


def _merge_filter_state(previous: QueryPlan, new_filters: list[FilterSpec]) -> list[FilterSpec]:
    new_cols = {flt.column.upper() for flt in new_filters}
    kept = [flt for flt in previous.filters if flt.column.upper() not in new_cols]
    return kept + new_filters


def _build_base_plan(
    expect: dict[str, Any],
    message: str,
    previous: QueryPlan | None,
    *,
    candidate_hint: str | None = None,
) -> QueryPlan:
    table = _infer_table(expect, previous, message)
    filters = _parse_filters(expect.get("filters"), table=table)
    if not filters:
        filters = _infer_filters_from_message(message, table=table)
    plan = QueryPlan(
        intent=f"dataset:{expect.get('turn_type', 'UNKNOWN')}",
        table=table,
        select_columns=[] if _make_aggregations(expect) else _default_select_columns(table),
        filters=filters,
        aggregations=_make_aggregations(expect),
        group_by=_make_group_by(expect, previous),
        order_by=_parse_order(expect.get("order_by"), table=table),
        limit=int(expect.get("limit", previous.limit if previous else 100)),
        needs_clarification=bool(expect.get("clarification")),
        clarification_message="Clarification required." if expect.get("clarification") else None,
    )
    if expect.get("shape_change") == "AGG_TO_LIST":
        plan = plan.model_copy(
            update={
                "aggregations": [],
                "group_by": [],
                "select_columns": _default_select_columns(table),
            }
        )
    return _inject_clarification_placeholder(plan, message=message, candidate_hint=candidate_hint)


def _apply_expected_turn(
    previous: QueryPlan | None,
    turn: dict[str, Any],
    next_turn: dict[str, Any] | None = None,
) -> ReconstructedState:
    expect = turn["expect"]
    message = str(turn["user"])
    turn_type = str(expect["turn_type"])
    next_message = str(next_turn["user"]) if next_turn is not None else None

    if turn_type == "RESET":
        return ReconstructedState(current_plan=None)

    if previous is None or turn_type == "NEW_QUERY":
        plan = _build_base_plan(expect, message, previous, candidate_hint=next_message)
        if plan.needs_clarification:
            explicit_candidates = expect.get("clarification_candidates")
            if explicit_candidates:
                candidate_1 = explicit_candidates[0]
                candidate_2 = explicit_candidates[1] if len(explicit_candidates) > 1 else "SECENEK_2"
            else:
                candidate_1 = next_message or "SECENEK_1"
                candidate_2 = "ELM-Dizayn" if candidate_1 != "ELM-Dizayn" else "DT-Dizayn"
            return ReconstructedState(
                current_plan=plan,
                pending_question=message,
                pending_target_column=plan.filters[0].column,
                pending_original_value=str(plan.filters[0].value),
                pending_candidates=(candidate_1, candidate_2),
            )
        return ReconstructedState(current_plan=plan)

    if turn_type == "CLARIFICATION_ANSWER":
        add_filters = _parse_filters(expect.get("add_filters"), table=previous.table)
        if not add_filters:
            add_filters = [
                FilterSpec(column=previous.filters[0].column, table=previous.table, op=FilterOp.EQ, value=message),
            ]
        return ReconstructedState(
            current_plan=previous.model_copy(
                update={
                    "filters": _merge_filter_state(previous, add_filters),
                    "needs_clarification": False,
                    "clarification_message": None,
                }
            )
        )

    if turn_type == "CONTRADICTION_REPORT":
        return ReconstructedState(current_plan=previous)

    if turn_type == "CONFIRMATION":
        return ReconstructedState(current_plan=previous)

    if turn_type == "CLARIFICATION_REJECTION":
        removed_cols = {col.upper() for col in expect.get("remove_filters", [])}
        return ReconstructedState(
            current_plan=previous.model_copy(
                update={
                    "filters": [f for f in previous.filters if f.column.upper() not in removed_cols],
                    "needs_clarification": False,
                    "clarification_message": None,
                }
            )
        )

    table = _infer_table(expect, previous, message)
    if turn_type == "DOMAIN_SWITCH_OR_CLARIFICATION" and previous.table and table != previous.table:
        return ReconstructedState(current_plan=_build_base_plan(expect, message, None))
    plan = previous.model_copy(update={"table": table, "limit": int(expect.get("limit", previous.limit))})

    new_filters = _parse_filters(expect.get("replace_filters"), table=table)
    if not new_filters:
        new_filters = _parse_filters(expect.get("add_filters"), table=table)
    if not new_filters:
        new_filters = _parse_filters(expect.get("filters"), table=table)
    if not new_filters:
        new_filters = _infer_filters_from_message(message, table=table)
    if new_filters:
        plan = plan.model_copy(update={"filters": _merge_filter_state(plan, new_filters)})

    if "order_by" in expect:
        plan = plan.model_copy(update={"order_by": _parse_order(expect.get("order_by"), table=table)})

    if expect.get("aggregation") == "COUNT" or expect.get("shape") == "AGGREGATE":
        plan = plan.model_copy(
            update={
                "aggregations": [AggregationSpec(function=AggregateFn.COUNT, column="*", alias="count_value")],
                "group_by": _make_group_by(expect, previous),
                "select_columns": [],
            }
        )

    if expect.get("shape_change") == "AGG_TO_LIST":
        plan = plan.model_copy(
            update={
                "aggregations": [],
                "group_by": [],
                "select_columns": _default_select_columns(table),
            }
        )

    if expect.get("group_by") is True:
        plan = plan.model_copy(update={"group_by": _make_group_by(expect, previous)})

    if turn_type == "DOMAIN_SWITCH_OR_CLARIFICATION" and expect.get("domain"):
        plan = _build_base_plan(expect, message, previous)

    if expect.get("remove_filters"):
        removed_cols = {col.upper() for col in expect["remove_filters"]}
        plan = plan.model_copy(
            update={"filters": [f for f in plan.filters if f.column.upper() not in removed_cols]}
        )

    return ReconstructedState(current_plan=plan)


def _reconstruct_state(case: TurnCase) -> ReconstructedState:
    state = ReconstructedState(current_plan=None)
    for index, turn in enumerate(case.turns, start=1):
        if index >= case.turn_number:
            break
        next_turn = case.turns[index] if index < len(case.turns) else None
        state = _apply_expected_turn(state.current_plan, turn, next_turn)
    return state


def _setup_runtime(
    case: TurnCase,
    state: ReconstructedState,
) -> tuple[SessionService, FollowupContextMergeService, ClarificationStateManager]:
    sessions = SessionService(max_history=20)
    followup = FollowupContextMergeService()
    clarifications = ClarificationStateManager()

    if state.current_plan is not None:
        sessions.set_last_plan(case.session_id, state.current_plan)
        followup.record_success(case.session_id, state.current_plan, answer_preview="previous")

    if state.current_plan is not None and state.pending_question:
        clarifications.create_pending(
            session_id=case.session_id,
            original_question=state.pending_question,
            target_column=state.pending_target_column or "BIRIM_ADI",
            target_table=state.current_plan.table,
            original_filter_value=state.pending_original_value or "?",
            candidates=[
                ClarificationCandidate(value=value, score=0.91 - (idx * 0.05), reason="dataset")
                for idx, value in enumerate(state.pending_candidates)
            ],
            top_candidate=state.pending_candidates[0],
            top_score=0.91,
            partial_grounded_plan_json=state.current_plan.model_dump(mode="json"),
        )

    return sessions, followup, clarifications


def _build_turn_delta(expect: dict[str, Any], message: str, previous: QueryPlan | None) -> QueryPlan:
    table = _infer_table(expect, previous, message)
    if expect.get("remove_filters"):
        filters: list[FilterSpec] = []
    else:
        filters = _parse_filters(expect.get("replace_filters"), table=table)
        if not filters:
            filters = _parse_filters(expect.get("add_filters"), table=table)
        if not filters:
            filters = _parse_filters(expect.get("filters"), table=table)
        if not filters:
            filters = _infer_filters_from_message(message, table=table)

    explicit_new = "NEW_QUERY" in str(expect.get("turn_type", ""))
    is_fresh = previous is None or explicit_new or (previous.table and table and previous.table.upper() != table.upper())
    plan = QueryPlan(
        intent=f"delta:{expect.get('turn_type', 'UNKNOWN')}",
        table=table,
        select_columns=[] if _make_aggregations(expect) else (_default_select_columns(table) if is_fresh else []),
        filters=filters,
        aggregations=_make_aggregations(expect),
        group_by=_make_group_by(expect, previous),
        order_by=_parse_order(expect.get("order_by"), table=table),
        limit=int(expect.get("limit", previous.limit if previous else 100)),
        needs_clarification=bool(expect.get("clarification")),
        clarification_message="Clarification required." if expect.get("clarification") else None,
    )
    if expect.get("shape_change") == "AGG_TO_LIST":
        plan = plan.model_copy(
            update={
                "aggregations": [],
                "group_by": [],
                "select_columns": _default_select_columns(table),
            }
        )
    return _inject_clarification_placeholder(plan, message=message)


def _resume_from_reply(reply: ClarificationReply) -> QueryPlan:
    partial_plan = QueryPlan(**reply.partial_grounded_plan_json)
    updated_filters: list[FilterSpec] = []
    applied = False
    for flt in partial_plan.filters:
        if flt.column == reply.target_column and not applied:
            updated_filters.append(flt.model_copy(update={"value": reply.chosen_value}))
            applied = True
        else:
            updated_filters.append(flt)
    if not applied:
        updated_filters.append(
            FilterSpec(column=reply.target_column, table=reply.target_table, op=FilterOp.EQ, value=reply.chosen_value)
        )
    return partial_plan.model_copy(
        update={
            "filters": updated_filters,
            "needs_clarification": False,
            "clarification_message": None,
        }
    )


def _filter_signature(flt: FilterSpec) -> str:
    if flt.op == FilterOp.IS_NULL:
        return f"{flt.column} IS NULL"
    if flt.op == FilterOp.IS_NOT_NULL:
        return f"{flt.column} IS NOT NULL"
    value: Any = flt.value
    if isinstance(value, str):
        value = _normalize_string(value)
    return f"{flt.column} {flt.op.value} {value}"


def _order_signature(order: OrderSpec) -> str:
    return f"{order.column} {order.direction.value}"


def _plan_signature(plan: QueryPlan | None) -> dict[str, Any]:
    if plan is None:
        return {"table": None, "select_columns": (), "filters": (), "order_by": (), "limit": None, "aggregations": (), "group_by": (), "clarification": None}
    return {
        "table": plan.table,
        "select_columns": tuple(sorted(plan.select_columns)),
        "filters": tuple(sorted(_filter_signature(flt) for flt in plan.filters)),
        "order_by": tuple(_order_signature(order) for order in plan.order_by),
        "limit": plan.limit,
        "aggregations": tuple(f"{agg.function.value}:{agg.column}" for agg in plan.aggregations),
        "group_by": tuple(plan.group_by),
        "clarification": plan.needs_clarification,
    }


def _derive_actual_turn_type(
    *,
    previous: QueryPlan | None,
    delta: QueryPlan,
    merge_result: MergeResult | None,
    clarification_reply: ClarificationReply | None,
    user_message: str,
) -> str:
    if clarification_reply is not None:
        return "CLARIFICATION_ANSWER"
    if previous is None or merge_result is None:
        return "NEW_QUERY"
    if previous.table and delta.table and previous.table.upper() != delta.table.upper() and merge_result.merge_strategy == "none":
        return "DOMAIN_SWITCH_OR_CLARIFICATION"
    if merge_result.message_type == "narrative_correction":
        lowered = _normalize_string(user_message)
        return "CONTRADICTION_REPORT" if "dedin" in lowered or "diyorsun" in lowered else "CORRECTION"
    if merge_result.message_type in {"reference_question", "comparison_request"}:
        return "FOLLOW_UP"
    if merge_result.message_type == "followup_refinement":
        lowered = _normalize_string(user_message)
        if "demistim" in lowered or "demiştim" in lowered:
            return "CORRECTION"
        has_rejection = any(w in lowered for w in ("hayir", " yok "))
        if has_rejection and delta.filters:
            return "CORRECTION"
        if not delta.order_by and delta.limit == previous.limit and not delta.aggregations and not delta.group_by:
            return "FILTER_UPDATE"
        if delta.filters and any(flt.column.upper() in {prev.column.upper() for prev in previous.filters} for flt in delta.filters):
            return "CORRECTION"
        return "FOLLOW_UP"
    return "NEW_QUERY"


def _allowed_turn_types(expect_type: str) -> set[str]:
    if "_OR_" in expect_type:
        return {expect_type, *expect_type.split("_OR_")}
    return {expect_type}


def _assert_must_not(expect: dict[str, Any], actual_turn_type: str, final_plan: QueryPlan) -> None:
    signatures = {_filter_signature(flt) for flt in final_plan.filters}
    for item in expect.get("must_not", []):
        lowered = str(item).casefold()
        if lowered in {"new_query", "reset_query"}:
            assert actual_turn_type.casefold() != "new_query"
            continue
        if lowered == "mix_hr_po":
            all_tables = final_plan.all_tables
            has_hr = any(t == HR_TABLE for t in all_tables)
            has_po = any(t == PO_TABLE for t in all_tables)
            assert not (has_hr and has_po), "Plan mixes HR and PO tables"
            continue
        assert _normalize_string(str(item)) not in {_normalize_string(sig) for sig in signatures}


def _assert_narration_summary_consistent(plan: QueryPlan) -> None:
    result = OrchestrationResult(
        validation=ValidationResult(),
        compiled_query=CompiledQuery(
            sql="SELECT 1 FROM DUAL",
            params={},
            table=plan.table or HR_TABLE,
            selected_columns=list(plan.select_columns),
            debug_plan=plan,
        ),
        execution_result=ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            columns=list(plan.select_columns),
            rows=[{}] if not plan.aggregations else [{"count_value": 1}],
            row_count=1,
        ),
    )
    summary = NarratorService._build_success_summary(result)  # noqa: SLF001
    assert f"uygulanan_limit={plan.limit}" in summary
    for flt in plan.filters:
        if flt.op == FilterOp.IS_NULL:
            assert f"{flt.column} IS_NULL" in summary
        elif flt.op == FilterOp.IS_NOT_NULL:
            assert f"{flt.column} IS_NOT_NULL" in summary
        else:
            assert f"{flt.column} {flt.op.value}" in summary
    for order in plan.order_by:
        assert f"{order.column} {order.direction.value}" in summary


@pytest.fixture(scope="module")
def dataset() -> dict[str, Any]:
    return _load_dataset()


def test_questions_dataset_schema(dataset: dict[str, Any]) -> None:
    sessions = dataset["sessions"]
    assert len(sessions) == 18
    session_ids = [session["session_id"] for session in sessions]
    assert len(session_ids) == len(set(session_ids))
    for session in sessions:
        turns = session["turns"]
        assert [turn["turn"] for turn in turns] == list(range(1, len(turns) + 1))
        for turn in turns:
            assert "user" in turn
            assert "expect" in turn
            assert "turn_type" in turn["expect"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", PARAM_CASES)
async def test_questions_turn_state_eval(case: TurnCase) -> None:
    state = _reconstruct_state(case)
    previous = state.current_plan
    _sessions, followup, clarifications = _setup_runtime(case, state)

    clarification_reply = clarifications.interpret_reply(case.session_id, case.user_message, min_auto_resolve_score=0.80)
    if clarification_reply is not None:
        actual_turn_type = "CLARIFICATION_ANSWER"
        final_plan = _resume_from_reply(clarification_reply)
    else:
        delta = _build_turn_delta(case.expect, case.user_message, previous)
        if previous is None:
            merge_result = None
            actual_turn_type = "NEW_QUERY"
            final_plan = delta
        else:
            merge_result = await followup.process(case.session_id, case.user_message, delta)
            final_plan = merge_result.merged_plan if merge_result.merged_plan is not None else delta
            actual_turn_type = _derive_actual_turn_type(
                previous=previous,
                delta=delta,
                merge_result=merge_result,
                clarification_reply=None,
                user_message=case.user_message,
            )

    expected_plan = _apply_expected_turn(
        previous,
        {"user": case.user_message, "expect": case.expect},
        None,
    ).current_plan

    assert actual_turn_type in _allowed_turn_types(str(case.expect["turn_type"]))
    assert _plan_signature(final_plan) == _plan_signature(expected_plan)

    if case.expect.get("domain"):
        assert final_plan.table == _domain_to_table(str(case.expect["domain"]))
    if case.expect.get("clarification") is True:
        assert final_plan.needs_clarification is True
    if case.expect.get("clarification") is False:
        assert final_plan.needs_clarification is False
    if case.expect.get("must_not"):
        _assert_must_not(case.expect, actual_turn_type, final_plan)

    if previous is not None and case.expect.get("inherits_filters"):
        assert {_filter_signature(flt) for flt in previous.filters} <= {_filter_signature(flt) for flt in final_plan.filters}

    if previous is not None and case.expect.get("state_should_remain"):
        assert {_filter_signature(flt) for flt in previous.filters} <= {_filter_signature(flt) for flt in final_plan.filters}

    if not final_plan.needs_clarification:
        _assert_narration_summary_consistent(final_plan)


# ═══════════════════════════════════════════════════════════════════════════
# P1: Narration variant tests — empty result, scalar metric, grouped aggregate
# ═══════════════════════════════════════════════════════════════════════════


def test_narration_empty_result() -> None:
    """Narration summary must reflect shape=empty_result when zero rows returned."""
    plan = QueryPlan(
        intent="test_empty",
        table=HR_TABLE,
        select_columns=["SICIL_NO", "AD", "SOYAD"],
        filters=[FilterSpec(column="LOCATION_ADI", table=HR_TABLE, op=FilterOp.EQ, value="NONEXISTENT")],
        limit=100,
    )
    result = OrchestrationResult(
        validation=ValidationResult(),
        compiled_query=CompiledQuery(
            sql="SELECT SICIL_NO, AD, SOYAD FROM XXBT_PDKS_PER_DETAILS_V WHERE 1=0",
            params={},
            table=HR_TABLE,
            selected_columns=list(plan.select_columns),
            debug_plan=plan,
        ),
        execution_result=ExecutionResult(
            status=ExecutionStatus.EMPTY,
            columns=list(plan.select_columns),
            rows=[],
            row_count=0,
        ),
    )
    summary = NarratorService._build_success_summary(result)  # noqa: SLF001
    assert "shape=empty_result" in summary
    assert "satır_sayısı=0" in summary
    assert "uygulanan_limit=100" in summary
    assert "LOCATION_ADI" in summary


def test_narration_scalar_metric() -> None:
    """Narration summary must reflect shape=scalar_metric for single aggregate."""
    plan = QueryPlan(
        intent="test_scalar",
        table=HR_TABLE,
        aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="*", alias="count_value")],
        filters=[FilterSpec(column="BIRIM_ADI", table=HR_TABLE, op=FilterOp.EQ, value="ELEKTRIK DIZAYN")],
        limit=100,
    )
    result = OrchestrationResult(
        validation=ValidationResult(),
        compiled_query=CompiledQuery(
            sql="SELECT COUNT(*) AS count_value FROM XXBT_PDKS_PER_DETAILS_V",
            params={},
            table=HR_TABLE,
            selected_columns=["count_value"],
            debug_plan=plan,
        ),
        execution_result=ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            columns=["count_value"],
            rows=[{"count_value": 42}],
            row_count=1,
        ),
    )
    summary = NarratorService._build_success_summary(result)  # noqa: SLF001
    assert "shape=scalar_metric" in summary
    assert "BIRIM_ADI" in summary
    assert "uygulanan_limit=100" in summary


def test_narration_grouped_aggregate() -> None:
    """Narration summary must reflect shape=grouped_aggregate with group_by_hint."""
    plan = QueryPlan(
        intent="test_grouped",
        table=HR_TABLE,
        aggregations=[AggregationSpec(function=AggregateFn.COUNT, column="*", alias="count_value")],
        group_by=["BIRIM_ADI"],
        filters=[FilterSpec(column="CIKIS_TARIHI", table=HR_TABLE, op=FilterOp.IS_NULL)],
        limit=100,
    )
    result = OrchestrationResult(
        validation=ValidationResult(),
        compiled_query=CompiledQuery(
            sql="SELECT BIRIM_ADI, COUNT(*) AS count_value FROM XXBT_PDKS_PER_DETAILS_V GROUP BY BIRIM_ADI",
            params={},
            table=HR_TABLE,
            selected_columns=["BIRIM_ADI", "count_value"],
            debug_plan=plan,
        ),
        execution_result=ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            columns=["BIRIM_ADI", "count_value"],
            rows=[
                {"birim_adi": "ELEKTRIK DIZAYN", "count_value": 25},
                {"birim_adi": "MEKANIK DIZAYN", "count_value": 18},
            ],
            row_count=2,
        ),
    )
    summary = NarratorService._build_success_summary(result)  # noqa: SLF001
    assert "shape=grouped_aggregate" in summary
    assert "CIKIS_TARIHI IS_NULL" in summary
    assert "group_by_hint=BIRIM_ADI" in summary
    assert "uygulanan_limit=100" in summary
    assert "top_group_label=ELEKTRIK DIZAYN" in summary
