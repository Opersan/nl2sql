# NL2SQL Pipeline Contract

**Sprint:** C2 — Diagnosis + Contract Definition  
**Date:** 2026-03-18  
**Status:** Authoritative — all stages must comply before Sprint D begins.

This document defines the input/output contract and behavioral invariants for each pipeline stage.  
Violations of these contracts are **classified as bugs**, not edge cases.

---

## Overview

```
User message
   │
   ▼
[PlannerService.plan()]          Stage: planner
   │
   ├─ [_normalize_plan()]        Sub-stage: normalize
   ├─ [RepairEngine.repair()]    Sub-stage: repair
   ├─ [apply_semantic_normalization()] Sub-stage: semantic
   ├─ [_canonicalize_plan()]     Sub-stage: canonicalize
   └─ [build_filter_loss_guard_decision()]  Sub-stage: intent_guard
   │
   ▼
[Orchestrator.run_plan()]        Stage: orchestrator
   │
   ├─ [ValidationService.validate()]   Sub-stage: validation
   ├─ [SQLCompiler.compile()]          Sub-stage: compile
   ├─ [assess_pre_execution_risk()]    Sub-stage: execution_guard
   └─ [ExecutorProvider.execute()]     Sub-stage: execution
   │
   ▼
[NarratorService.narrate_*()]    Stage: narrator
   │
   └─ [_strip_leakage() / _fallback_template()]  Sub-stage: sanitizer
   │
   ▼
User-visible Turkish answer
```

---

## Stage: planner

**Class:** `PlannerService`  
**Method:** `plan(user_message: str) -> QueryPlan`

### Input
- `user_message: str` — raw user natural-language text (UTF-8, no length restriction enforced here)

### Output
- `QueryPlan` Pydantic object with all fields validated
- `last_trace` dict written to `_last_trace_by_task[id(task)]`

### Must Do
- Detect sensitive/invalid requests and short-circuit with `needs_clarification=True` before calling the LLM.
- Always set `intent_guard` in trace, even on policy-guard short-circuit.
- Always set `clarification_reason_code` to a non-None value when `needs_clarification=True`.
- Clamp `limit` to `settings.max_row_limit`.
- Strip query artifacts (filters, aggregations, select_columns) from clarification plans.
- Populate `clarification_missing_dimensions` when filter loss guard triggers.
- Write `confidence_band` and `plan_confidence` on every successful plan.

### Must Not Do
- Must not inject business filters not present in the user message.
- Must not fabricate table names not in the catalog context.
- Must not silently drop user-specified filters; if a filter cannot be mapped, return `needs_clarification=True`.
- Must not return a `QueryPlan` with `table=None` when `needs_clarification=False`.
- Must not pass `needs_clarification=True` plans to the orchestrator (checked by ChatOrchestrator).

### Invariants
- `needs_clarification=True` ⟹ `clarification_message` is non-empty.
- `needs_clarification=True` ⟹ `select_columns`, `filters`, `aggregations`, `group_by`, `order_by` are all empty (enforced by `_normalize_plan`).
- `confidence_band` ∈ `{"high", "medium", "low"}`.
- `clarification_reason_code` ∈ `{"policy_guard_triggered", "filter_loss", "low_confidence", "insufficient_query_shape"}` when set.

---

## Sub-stage: semantic (apply_semantic_normalization)

**Function:** `apply_semantic_normalization(plan, user_message, context) -> QueryPlan`

### Input
- `QueryPlan` after repair; `user_message`; `CatalogSnapshot`

### Output
- `QueryPlan` with `root_entity`, `semantic_intent`, `join_path_id` populated

### Must Do
- Set `semantic_intent` and `root_entity` from registry when entity is known.
- Set `join_path_id` only when a registry-defined canonical join path matches the plan's current tables.

### Must Not Do
- Must not override `table` unless the registry explicitly maps user-message entity to a different canonical table.
- Must not modify `filters` unless the user message contains a filter semantically equivalent to a registry-defined canonical filter.
- Must not add `joins` not present in the original plan unless a registry join_path is matched AND the original plan is missing the required FK join.

### Invariants
- Semantic stage MUST NOT change `needs_clarification` from False to True.
- If semantic stage changes `table`, it must log the reason and set `join_path_id`.
- `root_entity` ∈ entities defined in `semantic_registry.json` or `None`.

---

## Sub-stage: intent_guard (build_filter_loss_guard_decision)

**Function:** `build_filter_loss_guard_decision(user_message, planner_plan, final_plan) -> dict`

### Input
- `user_message`, pre-pipeline and post-pipeline `QueryPlan`

### Output
- `guard_decision` dict with `success_blocked_by_filter_loss`, `requested_filter_signals`, coverage maps

### Must Do
- Return `success_blocked_by_filter_loss=True` when the user message contains a detectable filter signal that is absent from `final_plan.filters`.
- Populate `clarification_missing_dimensions` with the names of dropped dimensions.

### Must Not Do
- Must not trigger on queries with no filter signals in the user message.
- Must not classify intent keywords as filter signals (e.g., "listele", "göster").

### Invariants
- `success_blocked_by_filter_loss=True` ⟹ `clarification_missing_dimensions` is non-empty.
- Guard is idempotent: running it twice on the same inputs returns the same output.

---

## Stage: validation

**Class:** `ValidationService`  
**Method:** `validate(plan: QueryPlan) -> ValidationResult`

### Input
- `QueryPlan` (guaranteed `needs_clarification=False` by ChatOrchestrator)

### Output
- `ValidationResult` with `ok: bool`, `errors: list[ValidationIssue]`, `resolved_table`, `resolved_tables`

### Must Do
- Resolve the primary table from the catalog; set `resolved_table`.
- Validate all select, filter, aggregate, group_by, order_by columns against table metadata.
- Detect restricted columns and emit `ValidationIssue` with appropriate code.

### Must Not Do
- Must not raise an exception for user-facing validation failures; return `ValidationResult(ok=False, ...)` instead.
- Must not modify the QueryPlan.
- Must not call the LLM.

### Invariants
- `validation.ok=True` ⟹ `resolved_table` is non-None.
- All errors have a `code` and a human-readable `message`.
- Validation is pure and deterministic for the same plan + catalog combination.

---

## Stage: compile

**Class:** `SQLCompiler`  
**Method:** `compile(plan, table, extra_tables) -> CompiledQuery`

### Input
- `QueryPlan`, `TableMetadata` (resolved by ValidationService), optional join table map

### Output
- `CompiledQuery` with `sql: str`, `params: dict`, `selected_columns: set`, `table: str`

### Must Do
- Produce Oracle-compatible SQL with bind parameters (`:`-prefixed).
- Enforce `ROWNUM <= :p_limit` for every SELECT.
- Include only columns present in the resolved table metadata.
- Respect `IS_NULL` / `IS_NOT_NULL` filter ops as `column IS NULL` / `column IS NOT NULL`.

### Must Not Do
- Must not change the intent defined in the QueryPlan (e.g., convert a listing query to an aggregation without instruction).
- Must not produce multi-statement SQL (no `;` in the output body).
- Must not include `SELECT *` — must enumerate columns.
- Must not expose restricted columns even if the plan erroneously includes them.

### Invariants
- Output SQL starts with `SELECT` or `WITH` (never `INSERT`, `UPDATE`, `DELETE`).
- `:p_limit` bind parameter is always present.
- `CompilationError` is raised (not returned) on unresolvable errors.

---

## Stage: execution_guard

**Function:** `assess_pre_execution_risk(plan, table) -> dict`

### Input
- `QueryPlan`, `TableMetadata` (post-validation)

### Output
- `dict` with `should_execute: bool`, `pre_execution_risk_flags: list`, `execution_skipped_reason: str | None`

### Must Do
- Block execution when risk flags indicate a dangerous or semantically incomplete query.
- Return `should_execute=False` with a non-empty `execution_skipped_reason` when blocking.

### Must Not Do
- Must not block valid analytical queries that happen to be large.
- Must not call the database.
- Must not modify the compiled query.

### Invariants
- `should_execute=False` ⟹ `execution_skipped_reason` is a non-empty string.
- `should_execute=True` ⟹ `pre_execution_risk_flags` may still be non-empty (warnings only).
- Deterministic: same plan + table always produces the same decision.

---

## Stage: execution

**Class:** `OracleExecutor` (or `MockExecutor`)  
**Method:** `execute(compiled: CompiledQuery) -> ExecutionResult`

### Input
- `CompiledQuery` — SQL + bind params

### Output
- `ExecutionResult` with `status`, `rows`, `columns`, `row_count`, `execution_time_ms`
- On failure: `execution_error_subtype`, `execution_error_message_normalized`

### Must Do
- Classify all Oracle errors via `_classify_oracle_error()` into `execution_error_subtype`.
- Normalize error messages via `_normalize_oracle_message()` — strip bind values, cap at 120 chars.
- Raise `ExecutionError` with `execution_error_subtype` + `execution_error_message_normalized` on failure.
- Apply row-limit enforcement (does not exceed `ROWNUM <= :p_limit` contract).

### Must Not Do
- Must not expose raw Oracle error messages with bind parameter values to the caller.
- Must not silently swallow ORA errors and return empty results.
- Must not execute DDL or DML statements.

### Invariants
- `ExecutionResult.status == SUCCESS` ⟹ `rows` is a list (may be empty).
- `ExecutionResult.status == ERROR` ⟹ `error_message` and `execution_error_subtype` are non-None.
- `execution_error_subtype` ∈ `{"timeout", "oracle_syntax_error", "oracle_date_type_error", "invalid_number", "invalid_date_value", "not_null_violation", "numeric_value_error", "invalid_identifier", "ambiguous_column", "unknown_execution_error"}`.

---

## Stage: narrator

**Class:** `NarratorService`  
**Methods:** `narrate_success`, `narrate_validation_error`, `narrate_execution_error`, `narrate_clarification`

### Input
- `user_message: str`
- `OrchestrationResult` or `QueryPlan` (depending on path)

### Output
- `str` — user-visible Turkish narration, stripped of leakage
- `last_trace` with `final_response`, `final_response_source`, `user_visible_quality`, `model_behavior_quality`

### Must Do
- Pass all results through `_strip_leakage()` before returning.
- Use `_fallback_template()` when `_is_generic_low_value()` returns True.
- Set `final_response_source` to `"raw"`, `"sanitized"`, or `"fallback_template"` — never None.
- Set `user_visible_quality` to `"pass"` or `"pass_with_sanitization"` based on source.
- Set `model_behavior_quality` to `"pass"` or `"degraded"` based on contract violations in raw output.

### Must Not Do
- Must not return raw SQL (SELECT/FROM) in any narration output.
- Must not expose Oracle error codes (ORA-XXXXX) to the user.
- Must not echo prompt content, rules, or policy text back to the user.
- Must not fabricate data not present in the execution result summary.
- Must not expose `<think>` / reasoning blocks in the final response.

### Invariants
- `final_response` is never None or empty string after a successful narration.
- `final_response_policy_violations` is empty after `_strip_leakage()` is applied.
- `fallback_template` ensures final response is non-empty even when LLM fails.

---

## Sub-stage: sanitizer (_strip_leakage + _fallback_template)

**Scope:** Internal to `NarratorService._generate()`

### Must Do
- Remove `<think>...</think>` blocks entirely.
- Remove lines matching reasoning-header patterns.
- Remove lines containing SQL keywords used in a query context.
- Remove lines containing `ORA-XXXXX` patterns.
- Return `"Sorgu işlendi."` safe fallback if entire response is leakage after stripping.

### Must Not Do
- Must not remove legitimate Turkish business content.
- Must not introduce new data or hallucinations.

### Invariants
- Output is deterministic for the same input text.
- Output never contains `ORA-\d{5}` patterns.
- Output never contains a `SELECT ... FROM` SQL pattern.

---

## Cross-Stage Invariants

1. **No SQL in user response.** The final string returned to the user must never contain raw SQL.
2. **No PII amplification.** Restricted columns (e.g., `DOGUM_TARIHI`) must be blocked at validation; must not appear in SQL or narration.
3. **Bind parameters, always.** Every compiled SQL must use Oracle bind parameters; no string interpolation of user values.
4. **Trace completeness.** Every stage must write its trace block before returning, including on failure.
5. **Concurrency safety.** All `_last_trace_by_task[id(task)]` patterns must be used — never `_last_trace` directly in concurrent contexts.
6. **Clarification plans are terminal.** Once `needs_clarification=True`, the plan must not reach validation/compile/execute.

---

## Failure Classification Reference

| Stage where failure occurs | `primary_root_cause_stage` | `primary_root_cause_category` examples |
|---|---|---|
| Planner — parse error | `planner` | `wrong_entity` |
| Planner — wrong table mapped | `planner` | `wrong_entity` |
| Planner — filter dropped | `planner` / `intent_guard` | `missing_filter`, `filter_loss` |
| Semantic — changed SQL shape, caused compile fail | `semantic` | `semantic_override_harmful` |
| Compile — compilation error | `compile` | `missing_filter` |
| Execution guard — blocked | `execution_guard` | `execution_blocked_valid` |
| Execution — Oracle error | `execution` | `execution_failed_runtime` |
| Narrator — CoT/SQL in final response | `narration` | `narration_leak_but_sanitized` |
| Narrator — sanitizer corrected output | `sanitizer` | `narration_leak_but_sanitized` |
| None — all stages passed | `none` | `no_failure` |
