"""Follow-up context merge stage — hybrid LLM interpreter + deterministic reducer.

Architecture (3 components)
===========================

A. **ConversationStateStore** (deterministic)
   - Tracks ``active_entities`` (primary subject from filters like AD/SOYAD)
   - Stores ``last_successful_query_snapshot`` per session
   - Stores ``answer_preview`` for context

B. **ContinuationInterpreter** (LLM, closed-schema JSON)
   - Given: current message, state context
   - Returns: structured classification (``message_type``, ``references``,
     ``intent_delta``)
   - Message types: ``fresh_query``, ``followup_refinement``,
     ``reference_question``, ``comparison_request``

C. **StateReducer** (deterministic)
   - Takes interpreter output + state → merged plan
   - Operations: preserve, patch, reset

OPENWEBUI / HELPER SAFETY
--------------------------
``record_success`` is called ONLY from ``ChatOrchestrator.handle_message`` and
``ChatOrchestrator._handle_clarification_resume`` on the real success path.
Helper/title/tag requests never reach those paths, so they cannot overwrite
the snapshot.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.domain.query_plan import (
    AggregationSpec,
    FilterOp,
    FilterSpec,
    OrderSpec,
    QueryPlan,
)
from app.providers.llm.base import LLMProvider

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# A. Conversation State Store (deterministic)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ActiveEntity:
    """A resolved entity (person, item, etc.) active in conversation."""

    surface_form: str                   # e.g. "FURKAN KİRAZ"
    entity_type: str                    # e.g. "person"
    filter_columns: dict[str, str]      # e.g. {"AD": "FURKAN", "SOYAD": "KİRAZ"}


@dataclass(frozen=True)
class SuccessfulTurnSnapshot:
    """Compact snapshot of the last successful DATA-turn structured query."""

    session_id: str
    table: str | None
    filters: tuple[FilterSpec, ...]
    select_columns: tuple[str, ...]
    order_by: tuple[OrderSpec, ...]
    aggregations: tuple[AggregationSpec, ...]
    group_by: tuple[str, ...]
    limit: int
    semantic_intent: str | None
    active_entities: tuple[ActiveEntity, ...] = ()
    answer_preview: str = ""
    partition_by: tuple[str, ...] = ()
    rank_limit: int = 1


def _extract_entities_from_plan(plan: QueryPlan) -> tuple[ActiveEntity, ...]:
    """Extract active entities from plan filters (deterministic, generic).

    Collects ALL equality string filters as a single composite entity.
    No domain-specific column knowledge is embedded here.
    """
    eq_parts: dict[str, str] = {}
    for f in plan.filters:
        if f.op == FilterOp.EQ and isinstance(f.value, str):
            eq_parts[f.column.upper()] = f.value

    if not eq_parts:
        return ()

    surface = " ".join(eq_parts.values()).strip()
    return (ActiveEntity(
        surface_form=surface,
        entity_type="generic",
        filter_columns=eq_parts,
    ),)


# ═══════════════════════════════════════════════════════════════════════════
# B. Continuation Interpreter (LLM, closed-schema)
# ═══════════════════════════════════════════════════════════════════════════


_CONTINUATION_PROMPT = """\
Sen bir konuşma devamı sınıflandırıcısın. Kullanıcının yeni mesajını analiz et.

## Mevcut bağlam
Önceki başarılı sorgu: {previous_query_summary}
Aktif varlıklar: {active_entities}
Önceki yanıt özeti: {answer_preview}

## Yeni mesaj
{current_message}

## Görev
Aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:

{{"message_type": "<tip>", "references": [{{"surface": "<mesajdaki ifade>", "resolved_entity": "<çözümlenen varlık adı>", "confidence": "<high|medium|low>"}}], "preserve_previous_filters": <true|false>, "comparison_entities": ["<isim1>", "<isim2>"]}}

message_type seçenekleri:
- "fresh_query": tamamen yeni, bağımsız bir sorgu
- "followup_refinement": önceki sorguyu daraltma/genişletme (ama, hariç, sadece, ayrıca...)
- "reference_question": önceki bağlamdaki bir varlığa referansla yeni soru ("bu kişi", "onun", "bu çalışanın"...)
- "comparison_request": iki veya daha fazla varlığı karşılaştırma ("ikisi arasında", "farkı", "ile ... karşılaştır")
- "narrative_correction": kullanıcı önceki yanıtın YORUMUNU/HESAPLAMASINI düzeltiyor, veri sorgusunu değil ("emin misin", "yanlış hesapladın", "nasıl doğmamış oluyor", "tekrar bak")

Kurallar:
- "bu kişi", "bu çalışan", "o", "onun" gibi zamirler varsa → önceki aktif varlığa çözümle
- Yeni bir isim + önceki bağlam referansı → comparison_request
- Kullanıcı önceki cevabın yanlış YORUMLANDIĞINI söylüyorsa → narrative_correction
- Yalnızca JSON yaz, açıklama ekleme
- Şüphe durumunda reference_question tercih et
"""

# How we classify for trace/logging
_VALID_MESSAGE_TYPES = frozenset({
    "fresh_query",
    "followup_refinement",
    "reference_question",
    "comparison_request",
    "narrative_correction",
})


@dataclass
class ContinuationClassification:
    """Structured output of the continuation interpreter."""

    message_type: str
    references: list[dict[str, str]]
    preserve_previous_filters: bool
    comparison_entities: list[str]
    raw_response: str = ""
    llm_used: bool = False
    user_message: str = ""


async def _classify_continuation(
    llm: LLMProvider | None,
    message: str,
    snapshot: SuccessfulTurnSnapshot,
) -> ContinuationClassification:
    """Call LLM to classify the continuation type.

    Falls back to heuristic classification if LLM is unavailable or fails.
    """
    if llm is None:
        result = _heuristic_classify(message, snapshot)
        result.user_message = message
        return result

    # Build compact context
    previous_query = (
        f"table={snapshot.table}, "
        f"filters={_summarize_filters(snapshot.filters)}, "
        f"columns={list(snapshot.select_columns)}"
    )
    entities_str = (
        ", ".join(e.surface_form for e in snapshot.active_entities) or "(yok)"
    )
    answer_preview = (snapshot.answer_preview or "")[:200]

    prompt = _CONTINUATION_PROMPT.format(
        previous_query_summary=previous_query,
        active_entities=entities_str,
        answer_preview=answer_preview,
        current_message=message.strip()[:300],
    )

    try:
        raw = await asyncio.wait_for(
            llm.generate_text(prompt, disable_thinking=True),
            timeout=50.0,
        )
        parsed = _parse_classification_response(raw)
        if parsed is not None:
            parsed.raw_response = raw
            parsed.llm_used = True
            parsed.user_message = message
            return parsed
        logger.warning("[continuation] LLM response parse failed, using heuristic")
    except Exception:
        logger.warning("[continuation] LLM classification failed, using heuristic")

    result = _heuristic_classify(message, snapshot)
    result.user_message = message
    return result


def _parse_classification_response(raw: str) -> ContinuationClassification | None:
    """Parse the LLM JSON response into a ContinuationClassification."""
    if not raw or not raw.strip():
        return None
    # Extract JSON from response (may have markdown fences)
    text = raw.strip()
    json_match = re.search(r"\{[\s\S]*\}", text)
    if not json_match:
        return None
    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        return None

    msg_type = data.get("message_type", "")
    if msg_type not in _VALID_MESSAGE_TYPES:
        return None

    references = data.get("references", [])
    if not isinstance(references, list):
        references = []

    return ContinuationClassification(
        message_type=msg_type,
        references=[r for r in references if isinstance(r, dict)],
        preserve_previous_filters=bool(data.get("preserve_previous_filters", False)),
        comparison_entities=[
            str(e) for e in (data.get("comparison_entities") or [])
            if isinstance(e, str)
        ],
    )


def _heuristic_classify(
    message: str,
    snapshot: SuccessfulTurnSnapshot,
) -> ContinuationClassification:
    """Rule-based fallback when LLM is unavailable."""
    msg_lower = message.lower().strip()

    _REFERENCE_SIGNALS = (
        "bu kişi", "bu çalışan", "bu çalışanın", "bu kişinin",
        "onun ", "ona ", "onu ",
    )
    _COMPARE_SIGNALS = (
        " arasındaki", " arasında", " farkı", " fark ", "karşılaştır",
        " ile ", "ikisi",
    )
    _REFINEMENT_SIGNALS = (
        "tamam ama", "tamam, ama", "ama ", "fakat ", "lakin ",
        "hariç", " dışında", "sadece ", "yalnızca ", "yalnız ",
        "ayrıca ", "bir de ", "ek olarak",
        "bunları da ekle", "bunu da ekle",
        "bunları çıkar", "bunu çıkar",
        "da getir", "de getir", "da göster", "de göster",
        "da ekle", "de ekle",
        "getirme ", "gösterme ",
        "peki ", "peki,",
    )
    _CORRECTION_SIGNALS = (
        "emin misin", "emin değilim",
        "yanlış cevap", "doğru değil", "yanlış hesap",
        "nasıl doğmamış", "nasıl olur", "nasıl oluyor",
        "tekrar söyle", "yeniden dene", "tekrar bak",
        "yanlış söyledin", "yanlış hesapladın",
        "yanılıyorsun", "hatalı",
    )

    has_compare = any(s in msg_lower for s in _COMPARE_SIGNALS)
    if has_compare and snapshot.active_entities:
        # Extract comparison entity names from message (uppercase word groups)
        comp_entities: list[str] = [
            m.group(1).strip()
            for m in _UPPERCASE_NAME_RE.finditer(message)
        ]
        return ContinuationClassification(
            message_type="comparison_request",
            references=[{
                "surface": snapshot.active_entities[0].surface_form,
                "resolved_entity": snapshot.active_entities[0].surface_form,
                "confidence": "medium",
            }],
            preserve_previous_filters=True,
            comparison_entities=comp_entities,
        )

    has_reference = any(s in msg_lower for s in _REFERENCE_SIGNALS)
    if has_reference and snapshot.active_entities:
        return ContinuationClassification(
            message_type="reference_question",
            references=[{
                "surface": next(
                    (s for s in _REFERENCE_SIGNALS if s in msg_lower), ""
                ),
                "resolved_entity": snapshot.active_entities[0].surface_form,
                "confidence": "high",
            }],
            preserve_previous_filters=True,
            comparison_entities=[],
        )

    has_refinement = any(s in msg_lower for s in _REFINEMENT_SIGNALS)
    if has_refinement:
        return ContinuationClassification(
            message_type="followup_refinement",
            references=[],
            preserve_previous_filters=True,
            comparison_entities=[],
        )

    # Narrative correction: user is correcting the system's interpretation
    has_correction = any(s in msg_lower for s in _CORRECTION_SIGNALS)
    if has_correction:
        return ContinuationClassification(
            message_type="narrative_correction",
            references=[],
            preserve_previous_filters=True,
            comparison_entities=[],
        )

    return ContinuationClassification(
        message_type="fresh_query",
        references=[],
        preserve_previous_filters=False,
        comparison_entities=[],
    )


def _summarize_filters(filters: tuple[FilterSpec, ...]) -> str:
    """Compact text summary of filters for the LLM prompt."""
    parts: list[str] = []
    for f in filters:
        if f.op == FilterOp.IS_NULL:
            parts.append(f"{f.column} IS NULL")
        elif f.op == FilterOp.IS_NOT_NULL:
            parts.append(f"{f.column} IS NOT NULL")
        else:
            parts.append(f"{f.column}{f.op.value}{f.value!r}")
        if len(parts) >= 5:
            parts.append("...")
            break
    return ", ".join(parts) if parts else "(yok)"


# ═══════════════════════════════════════════════════════════════════════════
# C. State Reducer (deterministic)
# ═══════════════════════════════════════════════════════════════════════════

# Consecutive uppercase Turkish words — matches names like "AHMET UYGUN"
_UPPERCASE_NAME_RE = re.compile(
    r"([A-ZÇĞİÖŞÜ]{2,}(?:\s+[A-ZÇĞİÖŞÜ]{2,})+)"
)


def _extract_new_comparison_entity(
    message: str,
    classification: ContinuationClassification,
    snapshot: SuccessfulTurnSnapshot,
) -> str | None:
    """Find the NEW entity name that is different from the snapshot entity.

    Sources (in priority order):
    1. ``classification.comparison_entities`` (from LLM)
    2. Consecutive uppercase word groups in the message text
    """
    if not snapshot.active_entities:
        return None

    ref_upper = snapshot.active_entities[0].surface_form.upper().strip()

    # 1. LLM comparison_entities
    for name in classification.comparison_entities:
        name_clean = name.strip()
        if name_clean and name_clean.upper() != ref_upper:
            return name_clean

    # 2. Uppercase word groups in message
    for m in _UPPERCASE_NAME_RE.finditer(message):
        candidate = m.group(1).strip()
        if candidate.upper() != ref_upper:
            return candidate

    return None


def _synthesize_comparison_filters(
    snapshot: SuccessfulTurnSnapshot,
    new_plan: QueryPlan,
    new_entity_name: str,
) -> QueryPlan:
    """Create EQ filters for *new_entity_name* using the snapshot's column mapping.

    Generic — relies on ``active_entities[0].filter_columns`` for the column
    order, not on hardcoded column names.
    """
    ref_entity = snapshot.active_entities[0]
    col_order = list(ref_entity.filter_columns.keys())  # e.g. ["AD", "SOYAD"]
    parts = new_entity_name.split()

    if len(parts) != len(col_order):
        return new_plan  # can't map safely

    synth_filters = list(new_plan.filters)
    for col, val in zip(col_order, parts):
        # Inherit table from the corresponding snapshot filter
        table: str | None = None
        for f in snapshot.filters:
            if f.column.upper() == col.upper():
                table = f.table
                break
        synth_filters.append(FilterSpec(
            column=col, table=table, op=FilterOp.EQ, value=val.upper(),
        ))

    return new_plan.model_copy(update={"filters": synth_filters})


def _reduce_reference_question(
    snapshot: SuccessfulTurnSnapshot,
    new_plan: QueryPlan,
    classification: ContinuationClassification,
) -> tuple[QueryPlan, list[str], list[str], list[str], bool]:
    """Carry forward person filters from the previous turn, merge projection."""
    return _merge_preserve_filters(snapshot, new_plan)


def _reduce_followup_refinement(
    snapshot: SuccessfulTurnSnapshot,
    new_plan: QueryPlan,
    classification: ContinuationClassification,
) -> tuple[QueryPlan, list[str], list[str], list[str], bool]:
    """Preserve previous context + apply delta."""
    return _merge_preserve_filters(snapshot, new_plan)


def _reduce_narrative_correction(
    snapshot: SuccessfulTurnSnapshot,
    new_plan: QueryPlan,
    classification: ContinuationClassification,
) -> tuple[QueryPlan, list[str], list[str], list[str], bool]:
    """Replay the exact previous plan — user is correcting the *interpretation*, not the data query."""
    preserved = [f.column for f in snapshot.filters]
    rebuilt = new_plan.model_copy(
        update={
            "table": snapshot.table,
            "filters": list(snapshot.filters),
            "select_columns": list(snapshot.select_columns),
            "order_by": list(snapshot.order_by),
            "aggregations": list(snapshot.aggregations),
            "group_by": list(snapshot.group_by),
            "partition_by": list(snapshot.partition_by),
            "rank_limit": snapshot.rank_limit,
            "limit": snapshot.limit,
            "semantic_intent": snapshot.semantic_intent,
            "needs_clarification": False,
            "clarification_message": None,
        }
    )
    return rebuilt, preserved, [], [], True


def _reduce_comparison_request(
    snapshot: SuccessfulTurnSnapshot,
    new_plan: QueryPlan,
    classification: ContinuationClassification,
) -> tuple[QueryPlan, list[str], list[str], list[str], bool]:
    """Handle comparison: build IN-filter for columns that differ between turns.

    Generic — no hardcoded column names.  Any EQ string filter whose value
    differs between the snapshot and the new plan is merged into an IN filter.

    When the planner produces a plan without entity filters (e.g. it set
    ``needs_clarification=True`` with empty filters), the reducer synthesises
    filters for the new comparison entity using the snapshot's column
    structure + the entity name from ``classification.comparison_entities``
    or from the user message text.
    """
    # If the new plan has no EQ-string filters, try to synthesise them
    has_eq_string = any(
        f.op == FilterOp.EQ and isinstance(f.value, str)
        for f in new_plan.filters
    )
    working_plan = new_plan
    if not has_eq_string and snapshot.active_entities:
        new_entity = _extract_new_comparison_entity(
            classification.user_message,
            classification,
            snapshot,
        )
        if new_entity:
            working_plan = _synthesize_comparison_filters(
                snapshot, new_plan, new_entity,
            )

    new_filter_cols: set[str] = {f.column.upper() for f in working_plan.filters}
    prev_eq_filters: dict[str, FilterSpec] = {
        f.column.upper(): f for f in snapshot.filters
        if f.op == FilterOp.EQ and isinstance(f.value, str)
    }

    merged_filters: list[FilterSpec] = []
    comparison_applied = False
    in_merged_cols: set[str] = set()  # columns already merged via IN

    for f in working_plan.filters:
        col_up = f.column.upper()
        if f.op == FilterOp.EQ and isinstance(f.value, str):
            prev_f = prev_eq_filters.get(col_up)
            if (
                prev_f
                and isinstance(prev_f.value, str)
                and prev_f.value.upper() != f.value.upper()
            ):
                # Two different values on same column → IN filter
                merged_filters.append(FilterSpec(
                    column=f.column,
                    table=f.table,
                    op=FilterOp.IN,
                    value=[prev_f.value, f.value],
                ))
                comparison_applied = True
                in_merged_cols.add(col_up)
                continue
        merged_filters.append(f)

    # Carry forward non-overridden, non-IN-merged filters from snapshot
    preserved: list[str] = []
    added: list[str] = [f.column for f in working_plan.filters]

    for prev_filter in snapshot.filters:
        col_upper = prev_filter.column.upper()
        if col_upper in in_merged_cols:
            continue  # already merged via IN above
        if col_upper not in new_filter_cols:
            merged_filters.append(prev_filter)
            preserved.append(prev_filter.column)

    new_limit = max(new_plan.limit, 10) if comparison_applied else new_plan.limit

    # Projection: union
    prev_cols = list(snapshot.select_columns)
    new_cols = list(new_plan.select_columns)
    preserved_proj = False
    if prev_cols:
        prev_upper = {c.upper() for c in prev_cols}
        extra = [c for c in new_cols if c.upper() not in prev_upper]
        merged_cols = prev_cols + extra
        preserved_proj = True
    else:
        merged_cols = new_cols

    merged_plan = working_plan.model_copy(
        update={
            "table": working_plan.table or snapshot.table,
            "filters": merged_filters,
            "select_columns": merged_cols,
            "limit": new_limit,
            "needs_clarification": False,
            "clarification_message": None,
        }
    )
    return merged_plan, preserved, added, [], preserved_proj


def _merge_preserve_filters(
    snapshot: SuccessfulTurnSnapshot,
    new_plan: QueryPlan,
) -> tuple[QueryPlan, list[str], list[str], list[str], bool]:
    """Standard merge: carry forward previous filters not overridden by new plan."""
    new_filter_cols: set[str] = {f.column.upper() for f in new_plan.filters}

    preserved: list[str] = []
    added: list[str] = [f.column for f in new_plan.filters]

    merged_filters: list[FilterSpec] = list(new_plan.filters)
    for prev_filter in snapshot.filters:
        col_upper = prev_filter.column.upper()
        if col_upper not in new_filter_cols:
            merged_filters.append(prev_filter)
            preserved.append(prev_filter.column)

    # When the new plan introduces aggregations/group_by that the snapshot
    # didn't have, the query shape changed fundamentally (e.g. listing →
    # aggregation).  Do NOT carry forward the old projection — those columns
    # would conflict with the analytical query.
    new_has_analytics = bool(new_plan.aggregations or new_plan.group_by or new_plan.computed_measures)
    prev_had_analytics = bool(snapshot.aggregations or snapshot.group_by)
    shape_changed = new_has_analytics and not prev_had_analytics

    prev_cols = list(snapshot.select_columns)
    new_cols = list(new_plan.select_columns)
    preserved_proj = False
    if prev_cols and not shape_changed:
        prev_upper = {c.upper() for c in prev_cols}
        extra_cols = [c for c in new_cols if c.upper() not in prev_upper]
        merged_cols = prev_cols + extra_cols
        preserved_proj = True
    else:
        merged_cols = new_cols

    # Carry forward analytic / structural fields when new plan lacks them
    update: dict[str, object] = {
        "table": new_plan.table or snapshot.table,
        "filters": merged_filters,
        "select_columns": merged_cols,
        "needs_clarification": False,
        "clarification_message": None,
    }
    if not new_plan.partition_by and snapshot.partition_by:
        update["partition_by"] = list(snapshot.partition_by)
        update["rank_limit"] = snapshot.rank_limit
    if not new_plan.order_by and snapshot.order_by:
        update["order_by"] = list(snapshot.order_by)

    merged_plan = new_plan.model_copy(update=update)
    return merged_plan, preserved, added, [], preserved_proj


# ═══════════════════════════════════════════════════════════════════════════
# Public interface (MergeResult + Service)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class MergeResult:
    """Result of the ``followup_context_merge`` pipeline stage."""

    followup_detected: bool
    followup_confidence: str        # "high" | "medium" | "low"
    merge_strategy: str             # "patch" | "none"
    merged_plan: QueryPlan | None
    preserved_filters: list[str]
    added_filters: list[str]
    dropped_filters: list[str]
    preserved_projection: bool
    reason_codes: list[str]
    previous_snapshot_found: bool
    previous_snapshot_status: str   # "success" | "none"
    message_type: str = "unknown"

    def to_trace_payload(self) -> dict[str, Any]:
        """Serialize to the ``followup_context_merge`` trace stage payload."""
        return {
            "previous_snapshot_found": self.previous_snapshot_found,
            "previous_snapshot_status": self.previous_snapshot_status,
            "followup_detected": self.followup_detected,
            "followup_confidence": self.followup_confidence,
            "merge_strategy": self.merge_strategy,
            "message_type": self.message_type,
            "preserved_filters": self.preserved_filters,
            "added_filters": self.added_filters,
            "dropped_filters": self.dropped_filters,
            "preserved_projection": self.preserved_projection,
            "reason_codes": self.reason_codes,
        }


_NO_MERGE = MergeResult(
    followup_detected=False,
    followup_confidence="low",
    merge_strategy="none",
    merged_plan=None,
    preserved_filters=[],
    added_filters=[],
    dropped_filters=[],
    preserved_projection=False,
    reason_codes=[],
    previous_snapshot_found=False,
    previous_snapshot_status="none",
    message_type="unknown",
)


class FollowupContextMergeService:
    """Hybrid LLM interpreter + deterministic reducer for follow-up handling.

    Usage (inside ChatOrchestrator)
    --------------------------------
    1. After every successful real DATA turn::

           self._followup_merge.record_success(session_id, plan, answer)

    2. After planning (before clarification gate)::

           merge = await self._followup_merge.process(session_id, message, new_plan)
           if merge.merge_strategy == "patch":
               plan = merge.merged_plan

    3. Emit ``merge.to_trace_payload()`` as the ``followup_context_merge``
       trace stage.
    """

    def __init__(self, llm: LLMProvider | None = None) -> None:
        self._llm = llm
        self._snapshots: dict[str, SuccessfulTurnSnapshot] = {}

    # ------------------------------------------------------------------
    # Snapshot management
    # ------------------------------------------------------------------

    def record_success(
        self,
        session_id: str,
        plan: QueryPlan,
        answer_preview: str = "",
    ) -> None:
        """Store a compact snapshot after a successful real DATA turn."""
        entities = _extract_entities_from_plan(plan)
        self._snapshots[session_id] = SuccessfulTurnSnapshot(
            session_id=session_id,
            table=plan.table,
            filters=tuple(plan.filters),
            select_columns=tuple(plan.select_columns),
            order_by=tuple(plan.order_by),
            aggregations=tuple(plan.aggregations),
            group_by=tuple(plan.group_by),
            limit=plan.limit,
            semantic_intent=plan.semantic_intent,
            active_entities=entities,
            answer_preview=answer_preview[:300] if answer_preview else "",
            partition_by=tuple(plan.partition_by),
            rank_limit=plan.rank_limit,
        )
        logger.debug(
            "[followup] snapshot recorded: session=%s table=%s filters=%d entities=%s",
            session_id,
            plan.table,
            len(plan.filters),
            [e.surface_form for e in entities],
        )

    def get_snapshot(self, session_id: str) -> SuccessfulTurnSnapshot | None:
        """Return the stored snapshot for a session, or None."""
        return self._snapshots.get(session_id)

    def clear_snapshot(self, session_id: str) -> None:
        """Remove the snapshot for a session."""
        self._snapshots.pop(session_id, None)

    # ------------------------------------------------------------------
    # Main pipeline entry point
    # ------------------------------------------------------------------

    async def process(
        self,
        session_id: str,
        message: str,
        new_plan: QueryPlan,
    ) -> MergeResult:
        """Detect follow-up via LLM interpreter + apply deterministic reducer."""
        snapshot = self._snapshots.get(session_id)

        if snapshot is None:
            return MergeResult(
                followup_detected=False,
                followup_confidence="low",
                merge_strategy="none",
                merged_plan=None,
                preserved_filters=[],
                added_filters=[],
                dropped_filters=[],
                preserved_projection=False,
                reason_codes=["no_previous_snapshot"],
                previous_snapshot_found=False,
                previous_snapshot_status="none",
                message_type="fresh_query",
            )

        # Table change → fresh query; skip LLM call
        if (
            new_plan.table
            and snapshot.table
            and new_plan.table.upper() != snapshot.table.upper()
        ):
            return MergeResult(
                followup_detected=False,
                followup_confidence="low",
                merge_strategy="none",
                merged_plan=None,
                preserved_filters=[],
                added_filters=[],
                dropped_filters=[],
                preserved_projection=False,
                reason_codes=["table_changed"],
                previous_snapshot_found=True,
                previous_snapshot_status="success",
                message_type="fresh_query",
            )

        # B. Continuation Interpreter (LLM or heuristic fallback)
        classification = await _classify_continuation(self._llm, message, snapshot)

        # Query-shape change guard: when the new plan introduces aggregations
        # that the previous turn didn't have (e.g. listing → turnover rate),
        # force fresh_query even if the LLM classified it as a refinement.
        # Filters may still be carried forward via the reducer's filter merge,
        # but the projection must not be overwritten.
        new_has_analytics = bool(
            new_plan.aggregations or new_plan.group_by or new_plan.computed_measures
        )
        prev_had_analytics = bool(snapshot.aggregations or snapshot.group_by)
        if (
            new_has_analytics
            and not prev_had_analytics
            and classification.message_type in ("followup_refinement", "narrative_correction")
        ):
            logger.info(
                "[followup] query shape changed (listing→aggregation): "
                "overriding %s → fresh_query for session=%s",
                classification.message_type,
                session_id,
            )
            return MergeResult(
                followup_detected=False,
                followup_confidence="low",
                merge_strategy="none",
                merged_plan=None,
                preserved_filters=[],
                added_filters=[],
                dropped_filters=[],
                preserved_projection=False,
                reason_codes=["query_shape_changed_to_aggregation"],
                previous_snapshot_found=True,
                previous_snapshot_status="success",
                message_type="fresh_query",
            )

        logger.info(
            "[followup] classification: session=%s type=%s llm=%s refs=%s",
            session_id,
            classification.message_type,
            classification.llm_used,
            [r.get("resolved_entity", "?") for r in classification.references],
        )

        # fresh_query → no merge
        if classification.message_type == "fresh_query":
            return MergeResult(
                followup_detected=False,
                followup_confidence="low",
                merge_strategy="none",
                merged_plan=None,
                preserved_filters=[],
                added_filters=[],
                dropped_filters=[],
                preserved_projection=False,
                reason_codes=[f"classified:{classification.message_type}"],
                previous_snapshot_found=True,
                previous_snapshot_status="success",
                message_type=classification.message_type,
            )

        # C. State Reducer (deterministic)
        _reducers = {
            "reference_question": _reduce_reference_question,
            "followup_refinement": _reduce_followup_refinement,
            "comparison_request": _reduce_comparison_request,
            "narrative_correction": _reduce_narrative_correction,
        }
        reducer = _reducers.get(
            classification.message_type, _reduce_reference_question
        )
        merged_plan, preserved, added, dropped, preserved_proj = reducer(
            snapshot, new_plan, classification,
        )

        confidence = "high" if classification.llm_used else "medium"

        logger.info(
            "[followup] merge applied: session=%s type=%s confidence=%s "
            "preserved=%s added=%s",
            session_id,
            classification.message_type,
            confidence,
            preserved,
            added,
        )

        return MergeResult(
            followup_detected=True,
            followup_confidence=confidence,
            merge_strategy="patch",
            merged_plan=merged_plan,
            preserved_filters=preserved,
            added_filters=added,
            dropped_filters=dropped,
            preserved_projection=preserved_proj,
            reason_codes=[f"classified:{classification.message_type}"],
            previous_snapshot_found=True,
            previous_snapshot_status="success",
            message_type=classification.message_type,
        )
