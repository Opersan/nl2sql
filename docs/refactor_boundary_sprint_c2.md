# Refactor Boundary — Sprint C2

**Sprint:** C2 — Diagnosis + Contract Definition  
**Date:** 2026-03-18  
**Purpose:** Define which responsibilities each class currently carries that violate single-responsibility, what must be kept, simplified, extracted, or moved before Sprint D begins.

This document is a preparation for Sprint D (refactoring sprint).  
It is **not** an implementation document — no code changes are made here.

---

## How to Read This Document

Each section covers one file or class.  
The columns mean:
- **KEEP** — code stays exactly as-is; it is correct and well-bounded
- **SIMPLIFY** — code stays but can be trimmed / cleaned without design change
- **EXTRACT** — logic should move to a new home (utility, sub-service, or domain object)
- **REMOVE** — can be deleted once its caller is updated

---

## 1. `app/services/planner_service.py` — `PlannerService`

### Current Responsibilities (too many)
1. LLM prompt construction (CATALOG CONTEXT, RESPONSE FORMAT, system prompt)
2. Raw LLM response parsing (`_parse_llm_response`)
3. Heuristic pre-parse normalization (`_normalize_raw_json`, guard for `!= null` etc.)
4. QueryPlan validation during planning (field clamping, strip filters from clarification plans)
5. Planner-level intent guard (policy guard + filter loss guard)
6. Semantic normalization call-out (`apply_semantic_normalization`)
7. Repair sub-pipeline (`query_plan_repair.py`)
8. Confidence scoring (`_compute_confidence_band`)
9. Trace assembly (`_last_trace_by_task`)
10. Session-aware plan (session history injection into prompt)

### KEEP
- `plan()` public API signature — `async def plan(user_message, session) -> QueryPlan`
- Trace assembly pattern: `_last_trace_by_task[id(task)]`
- Policy-guard / clarification short-circuit logic (it is the planner's responsibility to decide to clarify)
- `_normalize_plan()` — clamping + strip-on-clarification is enforced here and nowhere else

### SIMPLIFY
- `_build_system_prompt()` — extract static template strings to a `data/planner_prompt_template.txt` file; remove f-string gymnastics for `PLANNER_PROMPT_MAX_CHARS` truncation into a standalone `_trim_catalog_context()` helper
- `_compute_confidence_band()` — 3 simple conditions, no need for private method; inline into `_parse_llm_response`
- Heuristic normalization loop in `_normalize_raw_json` — consolidate the `!= null` / `= null` replacements into a single `re.sub` call

### EXTRACT
| Logic | Suggested New Home | Reason |
|---|---|---|
| LLM prompt template (CATALOG CONTEXT / RESPONSE FORMAT) | `app/providers/llm/planner_prompt.py` | LLM providers own prompts; today it is mixed into service |
| `apply_semantic_normalization()` callout | `app/services/semantic_planning.py` (already there) | `PlannerService` calls it directly — introduce `SemanticPlanningService.normalize(plan)` as the callable instead of the free function |
| `build_filter_loss_guard_decision()` | `app/services/intent_guard.py` (already there) | Already a separate module, just clean up the import; planner should only call it, not own it |
| Repair sub-pipeline | keep in `app/services/query_plan_repair.py` | Already extracted; `PlannerService` should only hold `if self._repair_engine:` |

### REMOVE
- `_PLAN_FIELDS` local constant — merge into `_normalize_plan` directly
- Dead guard code for`requires_clarification` (old field name before `needs_clarification` rename) — verify no test relies on it before deleting

---

## 2. `app/services/orchestrator.py` — `Orchestrator`

### Current Responsibilities
1. Validation (`ValidationService.validate`)
2. SQL compilation (`SQLCompiler.compile`)
3. Pre-execution risk assessment (`assess_pre_execution_risk`)
4. Execution (`ExecutorProvider.execute`)
5. Execution-result post-processing (row coercion, column typing)
6. Trace assembly and result packaging (`OrchestrationResult`)
7. Error normalization (`_normalize_oracle_message`, `_classify_oracle_error`)

### KEEP
- `run_plan()` public API signature — `async def run_plan(plan: QueryPlan) -> OrchestrationResult`
- Execution trace fields (they are referenced by the diagnosis layer and narrator)
- Oracle error classification logic (it is test-covered and diagnostic-relevant)

### SIMPLIFY
- `run_plan()` method body is ~180 lines; split into private `_validate_and_compile()`, `_guard_and_execute()` to reduce cognitive load without changing signatures
- `execute_raw_sql()` helper method — add a docstring clarifying it bypasses the guard; it is currently indistinguishable from `run_plan` at a glance

### EXTRACT
| Logic | Suggested New Home | Reason |
|---|---|---|
| Oracle error classification (`_classify_oracle_error`) | `app/services/execution_risk.py` | It is risk/diagnostic logic, not orchestration |
| Oracle message normalization (`_normalize_oracle_message`) | `app/utils/oracle_utils.py` | Pure string transformation, no orchestrator state needed |
| Row coercion / column typing loop | `app/domain/execution_models.py` as `ExecutionResult.coerce_rows()` | Domain object should own its own normalization |

### REMOVE
- `_EXEC_STATUS_MAP` — if it only maps two values, inline the condition

---

## 3. `app/services/semantic_planning.py` — Semantic Normalization

### Current Responsibilities
1. Load `semantic_registry.json` from hardcoded path at import time
2. Apply entity-to-table mapping override
3. Apply canonical filter injection from registry
4. Apply join path resolution
5. Build semantic diff (what changed, what fields moved)

### KEEP
- Semantic diff structure — referenced by diagnosis layer for `_SQL_SHAPE_FIELDS` comparison
- `_REGISTRY_PATH` constant — keep it, but inject via `Config` so tests can override
- All mapping rules — they are stable and tested

### SIMPLIFY
- `apply_semantic_normalization()` — currently a 200+ line free function; split into:
  - `_apply_entity_override(plan, registry_entry) -> QueryPlan`
  - `_apply_filter_injection(plan, registry_entry) -> QueryPlan`
  - `_apply_join_path(plan, registry_entry) -> QueryPlan`
  Each is independently testable and adds 0 new public API surface.

### EXTRACT
| Logic | Suggested New Home | Reason |
|---|---|---|
| Registry file loading | `app/providers/metadata/semantic_registry_provider.py` | Registry is a data source; loading should be a provider, not a module-level side effect |
| `_REGISTRY_PATH` hardcoded path | `app/core/config.py` as `semantic_registry_path: Path` | Allows test injection and production override via env var |

### REMOVE
- Module-level `_REGISTRY: dict` that loads at import time — move to lazy load inside a provider class; module-level load blocks import in certain test contexts

---

## 4. `app/services/narrator_service.py` — `NarratorService`

### Current Responsibilities
1. Dispatching to correct narration path (success / clarification / validation_error / execution_error)
2. Prompt assembly (system prompt + data summary + user message)
3. LLM call (`_generate`)
4. Leakage stripping (`_strip_leakage`)
5. Low-value detection (`_is_generic_low_value`)
6. Fallback template generation (`_fallback_template`)
7. User-visible quality classification (`user_visible_quality`)
8. Model-behavior quality classification (`model_behavior_quality`)
9. Policy violation detection (final + raw)
10. Trace assembly

### KEEP
- All quality classifications — they are the primary telemetry output used by the diagnosis layer
- `_strip_leakage()` logic — well-defined, well-tested
- Fallback template coverage — the `no_failure` branch of the diagnosis layer depends on it working correctly

### SIMPLIFY
- `_generate()` — currently mixes LLM call + leakage strip + quality classification; split into:
  - `_call_llm(messages) -> str` (pure LLM call)
  - `_classify_and_strip(raw: str) -> tuple[str, dict]` (classification + strip)
- Reduce duplication between `narrate_success`, `narrate_validation_error`, `narrate_execution_error` — they share ~60% of prompt construction logic; extract `_build_common_prompt()` base

### EXTRACT
| Logic | Suggested New Home | Reason |
|---|---|---|
| Policy violation patterns (regex list) | `app/core/narrator_policy.py` as constants | Policy patterns are domain rules, not service logic |
| Fallback template strings | `data/narrator_templates.json` | Templates are configuration, not code |
| `_is_generic_low_value()` heuristic | `app/utils/response_quality.py` | Pure text classification utility |

### REMOVE
- None at this time — all current methods are test-exercised.

---

## 5. `app/api/routes_chat.py` — `ChatOrchestrator`

### Current Responsibilities
1. Session management (`SessionService.get_or_create`)
2. Clarification plan gate (blocks non-clarification plans from reaching the orchestrator)
3. Planner call + result routing
4. Orchestrator call + result routing
5. Narrator call + response assembly
6. Policy guard telemetry capture
7. Response DTO construction

### KEEP
- Clarification gate — `if plan.needs_clarification: return early` must stay at this layer (ChatOrchestrator is the only cross-cutting integration point for this)
- Session session.add_turn() pattern — it is the authoritative history writer

### SIMPLIFY
- `execute_and_narrate()` private method — currently 120 lines; extract `_route_orchestration_result(result, plan, session)` so the routing conditions are independently readable

### EXTRACT
| Logic | Suggested New Home | Reason |
|---|---|---|
| Policy guard telemetry capture | `planner_service.py` (it already produces intent_guard in trace) | CEO layer should not have to know about intent_guard internals |

### REMOVE
- `_build_context_string()` — if it is only building session history text, move that to `SessionService.build_context(session)` to keep session logic cohesive

---

## 6. `app/services/intent_guard.py`

### Current Responsibilities
1. Policy guard keyword detection
2. Filter loss guard (signal extraction from user message)
3. Filter coverage analysis (pre vs post filter comparison)
4. Guard decision struct construction

### KEEP
- All logic — it is small, focused, and well-tested
- Filter loss guard tuple return (`success_blocked_by_filter_loss`, `requested_filter_signals`, `coverage_maps`)

### SIMPLIFY
- Deduplicate the two `_extract_filter_signals()` call paths (one from planner, one from eval harness — they must produce identical results)

### EXTRACT
- None needed; module is already appropriately scoped.

### REMOVE
- None.

---

## 7. `scripts/e2e_real_provider_eval.py`

### Notes
- This is a script, not production code. Refactoring rules are lighter.
- The Sprint C2 diagnosis layer (`_derive_diagnosis`) was added purely additively.
- No existing eval fields were modified.

### KEEP
- All existing EvalResult / EvalSummary fields — the diagnosis layer is additive
- `_derive_diagnosis()` function — it is the Sprint C2 deliverable
- All existing `_classify_*` helper functions — they are inputs to the diagnosis layer

### SIMPLIFY
- `_evaluate_single_question()` — 400+ lines; consider extracting `_build_narration_trace()` and `_build_planner_trace()` sub-functions for readability
- `_build_single_output_markdown()` — 300+ lines; extract `_render_diagnosis_section()` and `_render_retrieval_section()` 

### EXTRACT
- None for now; script complexity is acceptable.

### REMOVE
- `_mock_llm_*` helper stubs that were replaced by real provider evaluation in Sprint B2 — confirm they are unreferenced before deleting.

---

## Sprint D Pre-Conditions

Before Sprint D (Refactor Sprint) can begin, these must be true:

1. **`docs/pipeline_contract.md` is authoritative** — all stages documented with invariants.
2. **Diagnosis layer is running** — `_derive_diagnosis` produces output for every question.
3. **42 Sprint C tests pass** — no regression introduced by Sprint C2 additions.
4. **`execution_error_subtype` canonical enum is stable** — Sprint D must not rename these values.
5. **`_SQL_SHAPE_FIELDS` frozenset is agreed** — semantic diff comparison depends on it.
6. **No module-level side effects** in production code — registry loading must be lazy before Sprint D begins.

---

## Summary Table

| File | Keep | Simplify | Extract | Remove |
|---|---|---|---|---|
| `planner_service.py` | Public API, trace pattern, normalize | Build prompt, confidence band | LLM prompt → provider, semantic → sub-service | Dead guard code |
| `orchestrator.py` | `run_plan()`, Oracle classifier | Split run_plan body | Oracle utils → utils/, row coerce → domain | `_EXEC_STATUS_MAP` |
| `semantic_planning.py` | Diff structure, all mapping rules | Split into 3 sub-functions | Registry loading → provider, path → config | Module-level load |
| `narrator_service.py` | Quality classifications, leakage strip, fallback | Split `_generate()`, deduplicate prompt | Policy patterns → core/, templates → data/ | None |
| `routes_chat.py` | Clarification gate, session writes | Extract routing helper | Policy telemetry → planner | `_build_context_string()` |
| `intent_guard.py` | All logic | Dedup signal extraction | None | None |
| `e2e_real_provider_eval.py` | All EvalResult fields, `_derive_diagnosis` | Extract sub-render functions | None | Old mock stubs |
