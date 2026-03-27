"""Filter Column Resolution Stage.

Sprint 1 — Grounding Hardening: Correct Filter Column Resolution.

This stage deterministically corrects filter column bindings where the planner
LLM has mapped a business dimension to the wrong DB column.

Example
-------
Question: "IT departmanindaki calisanlari goster"
Planner plan: ORGANIZATION_ADI = 'IT'
After this stage: BIRIM_ADI = 'IT'  (value unchanged, only column corrected)

Design invariants
-----------------
- No LLM calls — purely rule-based.
- No value changes (Sprint 1 only fixes columns; Sprint 2 handles value
  canonicalization).
- No retrieval changes.
- Only operates on columns in the known "confusable set" for a dimension.
- If evidence is insufficient → no-op (safe passthrough).
- Non-dimension columns (flags, timestamps, IDs) are never remapped.

Configuration
-------------
All business-semantic data (dimension keywords, preferred columns, confusable
column sets, non-dimension column list) is read from:

    data/config/filter_grounding.json

via ``GroundingConfigProvider``.  Do NOT add hardcoded constant maps here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.domain.query_plan import FilterSpec, QueryPlan
from app.utils.turkish import normalize_for_matching

if TYPE_CHECKING:
    from app.services.grounding_config_provider import GroundingConfigProvider
    from app.services.query_understanding import QueryUnderstanding

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Text normalisation helpers
# (public so they can be re-used / tested independently)
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Case-fold + strip Turkish diacritics for robust keyword matching.

    Delegates to ``normalize_for_matching`` from the shared Turkish utility
    module.  Kept as a module-level function for backward compatibility with
    existing tests.
    """
    return normalize_for_matching(text)


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    cleaned = _PUNCT_RE.sub(" ", _norm(text))
    return [t for t in cleaned.split() if t]


# ---------------------------------------------------------------------------
# Result data-classes
# ---------------------------------------------------------------------------

@dataclass
class FilterResolutionAction:
    """Trace record for a single filter column resolution decision."""

    filter_index: int
    original_column: str
    resolved_column: str
    changed: bool
    dimension: str | None
    confidence: str          # "high" | "low"
    reason: str              # machine-readable reason code
    original_table: str | None = None
    resolved_table: str | None = None
    operator: str | None = None
    original_value: Any = None
    resolved_value: Any = None


@dataclass
class FilterColumnResolutionResult:
    """Output of the filter column resolution stage."""

    resolved_plan: QueryPlan
    actions: list[FilterResolutionAction] = field(default_factory=list)
    any_changed: bool = False

    @staticmethod
    def _serialize_filter(filter_spec: FilterSpec) -> dict[str, Any]:
        return {
            "table": filter_spec.table,
            "column": filter_spec.column,
            "operator": filter_spec.op.value,
            "value": filter_spec.value,
        }

    def as_trace_dict(self) -> dict[str, Any]:
        total_filters_seen = len(self.actions)
        changed_count = sum(1 for a in self.actions if a.changed)
        skipped_count = total_filters_seen - changed_count
        skip_reasons: dict[str, int] = {}
        changed_items: list[dict[str, Any]] = []
        original_filters: list[dict[str, Any]] = []
        for action in self.actions:
            original_filters.append({
                "table": action.original_table,
                "column": action.original_column,
                "operator": action.operator,
                "value": action.original_value,
            })
            if action.changed:
                changed_items.append({
                    "filter_index": action.filter_index,
                    "original_column": action.original_column,
                    "resolved_column": action.resolved_column,
                    "reason": action.reason,
                })
            else:
                skip_reasons[action.reason] = skip_reasons.get(action.reason, 0) + 1

        return {
            "any_changed": self.any_changed,
            "total_filters": total_filters_seen,
            "total_filters_seen": total_filters_seen,
            "processed_filters": total_filters_seen,
            "changed_count": changed_count,
            "changed_filters": changed_count,
            "skipped_filters": skipped_count,
            "skip_reasons": skip_reasons,
            "changed_items": changed_items,
            "original_filters": original_filters,
            "final_filters": [self._serialize_filter(filter_spec) for filter_spec in self.resolved_plan.filters],
            "actions": [
                {
                    "filter_index": a.filter_index,
                    "original_column": a.original_column,
                    "resolved_column": a.resolved_column,
                    "changed": a.changed,
                    "dimension": a.dimension,
                    "confidence": a.confidence,
                    "reason": a.reason,
                    "original_table": a.original_table,
                    "resolved_table": a.resolved_table,
                    "operator": a.operator,
                    "original_value": a.original_value,
                    "resolved_value": a.resolved_value,
                }
                for a in self.actions
            ],
        }


# ---------------------------------------------------------------------------
# Core dimension detection
# (takes GroundingConfigProvider so it is testable in isolation)
# ---------------------------------------------------------------------------

def _detect_intended_dimension(
    normalized_question: str,
    provider: "GroundingConfigProvider | None" = None,
) -> str | None:
    """Return the single most-specific business dimension detected in the question.

    Detection strategy (in order of specificity):
    1. Phrase-level substring match (e.g. "masraf merkezi", "birimindeki").
    2. Token-level substring match: each token in the question is compared
       to each keyword via substring containment (both directions) for tokens
       of length >= 4 to handle Turkish morphological suffixes.
    3. Priority ordering from config (lower priority number = more specific).

    Uses ``GroundingConfigProvider`` for all business-semantic data.
    Falls back to safe no-op (returns None) when the provider is unavailable
    or has no dimensions loaded.

    Parameters
    ----------
    normalized_question:
        The question text after ``_norm()`` (diacritic-stripped, casefold).
    provider:
        Optional provider override.  When ``None``, a default
        ``GroundingConfigProvider`` is used (safe for production calls).
    """
    if provider is None:
        from app.services.grounding_config_provider import GroundingConfigProvider
        provider = GroundingConfigProvider()

    dims_by_priority = provider.get_dimension_priority_order()
    if not dims_by_priority:
        return None

    # 1. Phrase-level (multi-word, most specific) — check in priority order
    for dim in dims_by_priority:
        for phrase in dim.phrases:
            if phrase in normalized_question:
                return dim.name

    # 2. Token-level root matching
    tokens = _tokenize(normalized_question)
    scores: dict[str, int] = {}
    for dim in dims_by_priority:
        for root in dim.keywords:
            for tok in tokens:
                if len(tok) < 4 and len(root) < 4:
                    # Both short → require exact match to avoid noise
                    if tok == root:
                        scores[dim.name] = scores.get(dim.name, 0) + 1
                        break
                else:
                    # At least one is long enough → allow substring
                    if root in tok or tok in root:
                        scores[dim.name] = scores.get(dim.name, 0) + 1
                        break

    if not scores:
        return None

    best_score = max(scores.values())

    # 3. Priority tiebreak (lower number = more specific = wins)
    for dim in dims_by_priority:
        if scores.get(dim.name, 0) == best_score:
            return dim.name

    return None  # unreachable but satisfies type checker


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class FilterColumnResolutionService:
    """Deterministic pre-validation filter column correction stage.

    Inspects each filter in a QueryPlan, infers the intended business
    dimension from the user question (via config-driven detection), and
    remaps the filter column when strong deterministic evidence is available.

    This service is called **after** semantic canonicalization and **before**
    the validation stage so that downstream validation and compilation receive
    the correctly-mapped column.

    Invariants
    ----------
    - Never changes filter values (Sprint 1 only).
    - Never changes filter operators.
    - Never touches non-dimension columns (flags, timestamps, IDs) as defined
      in ``data/config/filter_grounding.json``.
    - Only remaps columns within the declared confusable set for a dimension.
    - No-op when evidence is insufficient; returns original plan unchanged.

    Configuration
    -------------
    All dimension/column semantics come from ``GroundingConfigProvider``.
    Inject a custom provider for unit tests.
    """

    def __init__(
        self,
        provider: "GroundingConfigProvider | None" = None,
    ) -> None:
        if provider is None:
            from app.services.grounding_config_provider import GroundingConfigProvider
            provider = GroundingConfigProvider()
        self._provider = provider

        if not self._provider.loaded_ok:
            logger.warning(
                "[filter_column_resolution] GroundingConfigProvider failed to load — stage will no-op."
            )

    def resolve(
        self,
        plan: QueryPlan,
        user_question: str,
        *,
        query_understanding: "QueryUnderstanding | None" = None,
    ) -> FilterColumnResolutionResult:
        """Apply filter column resolution to *plan*.

        Parameters
        ----------
        plan:
            The QueryPlan produced by upstream planner/semantic stages.
        user_question:
            The original (or normalized) user question text.
        query_understanding:
            Optional QueryUnderstanding from the QU pre-pass. Currently used
            for trace enrichment; reserved for future confidence boosting.

        Returns
        -------
        FilterColumnResolutionResult
            Contains the (potentially corrected) plan and per-filter trace
            actions.  When no corrections are needed, ``resolved_plan`` is the
            same object as ``plan`` and ``any_changed`` is ``False``.
        """
        _no_change = FilterColumnResolutionResult(resolved_plan=plan)

        if not plan.filters:
            return _no_change

        if plan.needs_clarification:
            return FilterColumnResolutionResult(
                resolved_plan=plan,
                actions=[
                    FilterResolutionAction(
                        filter_index=idx,
                        original_column=fspec.column,
                        resolved_column=fspec.column,
                        changed=False,
                        dimension=None,
                        confidence="low",
                        reason="clarification_plan_no_op",
                        original_table=fspec.table,
                        resolved_table=fspec.table,
                        operator=fspec.op.value,
                        original_value=fspec.value,
                        resolved_value=fspec.value,
                    )
                    for idx, fspec in enumerate(plan.filters)
                ],
                any_changed=False,
            )

        if not self._provider.loaded_ok:
            return FilterColumnResolutionResult(
                resolved_plan=plan,
                actions=[
                    FilterResolutionAction(
                        filter_index=idx,
                        original_column=fspec.column,
                        resolved_column=fspec.column,
                        changed=False,
                        dimension=None,
                        confidence="low",
                        reason="grounding_config_unavailable_no_op",
                        original_table=fspec.table,
                        resolved_table=fspec.table,
                        operator=fspec.op.value,
                        original_value=fspec.value,
                        resolved_value=fspec.value,
                    )
                    for idx, fspec in enumerate(plan.filters)
                ],
                any_changed=False,
            )

        normalized_q = _norm(user_question)
        intended_dimension = _detect_intended_dimension(normalized_q, provider=self._provider)

        actions: list[FilterResolutionAction] = []
        new_filters: list[FilterSpec] = []
        any_changed = False

        for idx, fspec in enumerate(plan.filters):
            col_upper = fspec.column.upper()

            # 1. Non-dimension columns — never remap.
            if self._provider.is_non_dimension_column(col_upper):
                actions.append(FilterResolutionAction(
                    filter_index=idx,
                    original_column=fspec.column,
                    resolved_column=fspec.column,
                    changed=False,
                    dimension=None,
                    confidence="high",
                    reason="protected_column_no_op",
                    original_table=fspec.table,
                    resolved_table=fspec.table,
                    operator=fspec.op.value,
                    original_value=fspec.value,
                    resolved_value=fspec.value,
                ))
                new_filters.append(fspec)
                continue

            # 2. No dimension detected in the question → no-op.
            if intended_dimension is None:
                actions.append(FilterResolutionAction(
                    filter_index=idx,
                    original_column=fspec.column,
                    resolved_column=fspec.column,
                    changed=False,
                    dimension=None,
                    confidence="low",
                    reason="no_dimension_signal_in_question",
                    original_table=fspec.table,
                    resolved_table=fspec.table,
                    operator=fspec.op.value,
                    original_value=fspec.value,
                    resolved_value=fspec.value,
                ))
                new_filters.append(fspec)
                continue

            dim_cfg = self._provider.get_dimension_config(intended_dimension)
            if dim_cfg is None:
                # Dimension detected but no config entry — safe no-op.
                actions.append(FilterResolutionAction(
                    filter_index=idx,
                    original_column=fspec.column,
                    resolved_column=fspec.column,
                    changed=False,
                    dimension=intended_dimension,
                    confidence="low",
                    reason="dimension_config_missing",
                    original_table=fspec.table,
                    resolved_table=fspec.table,
                    operator=fspec.op.value,
                    original_value=fspec.value,
                    resolved_value=fspec.value,
                ))
                new_filters.append(fspec)
                continue

            preferred_col = dim_cfg.preferred_column

            # 3. Planner column not in confusable set for this dimension → no-op.
            if col_upper not in dim_cfg.confusable_columns:
                actions.append(FilterResolutionAction(
                    filter_index=idx,
                    original_column=fspec.column,
                    resolved_column=fspec.column,
                    changed=False,
                    dimension=intended_dimension,
                    confidence="high",
                    reason=f"column_not_confusable_for_{intended_dimension}",
                    original_table=fspec.table,
                    resolved_table=fspec.table,
                    operator=fspec.op.value,
                    original_value=fspec.value,
                    resolved_value=fspec.value,
                ))
                new_filters.append(fspec)
                continue

            # 4. Column is already the correct preferred column → no-op.
            if col_upper == preferred_col.upper():
                actions.append(FilterResolutionAction(
                    filter_index=idx,
                    original_column=fspec.column,
                    resolved_column=fspec.column,
                    changed=False,
                    dimension=intended_dimension,
                    confidence="high",
                    reason="already_correct_column",
                    original_table=fspec.table,
                    resolved_table=fspec.table,
                    operator=fspec.op.value,
                    original_value=fspec.value,
                    resolved_value=fspec.value,
                ))
                new_filters.append(fspec)
                continue

            # 5. Apply correction: rewrite column, preserve op + value + table.
            corrected = fspec.model_copy(update={"column": preferred_col})
            any_changed = True
            actions.append(FilterResolutionAction(
                filter_index=idx,
                original_column=fspec.column,
                resolved_column=preferred_col,
                changed=True,
                dimension=intended_dimension,
                confidence="high",
                reason=f"dimension_{intended_dimension}_keyword_detected_in_question",
                original_table=fspec.table,
                resolved_table=fspec.table,
                operator=fspec.op.value,
                original_value=fspec.value,
                resolved_value=fspec.value,
            ))
            new_filters.append(corrected)
            logger.info(
                "[filter_column_resolution] filter[%d]: %s → %s (dimension=%s, question=%r)",
                idx,
                fspec.column,
                preferred_col,
                intended_dimension,
                user_question[:80],
            )

        if not any_changed:
            return FilterColumnResolutionResult(resolved_plan=plan, actions=actions, any_changed=False)

        new_plan = plan.model_copy(update={"filters": new_filters})
        return FilterColumnResolutionResult(
            resolved_plan=new_plan,
            actions=actions,
            any_changed=True,
        )
