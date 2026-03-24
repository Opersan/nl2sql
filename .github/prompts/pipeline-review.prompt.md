---
name: "Pipeline Review"
description: "Use when: reviewing planner, orchestrator, validation, compiler, executor, or narrator changes against the NL2SQL pipeline contract"
argument-hint: "diff, files, or change summary"
agent: "agent"
---

Review the provided change, mentioned files, current selection, or current workspace diff as a contract-focused pipeline review for this repository.

Prioritize findings over summary.

Review against these repo-specific requirements:
- Preserve the deterministic flow: planner produces `QueryPlan`, then validation, Oracle-oriented compilation, execution guard, executor, and narration.
- Never allow raw LLM-generated SQL to bypass validation, compilation, or execution safeguards.
- Preserve `QueryPlan` invariants, especially clarification behavior and filter-loss handling.
- Keep validation deterministic and side-effect free.
- Keep Oracle SQL generation aligned with current compiler rules: bind parameters, explicit column lists, and `ROWNUM`-based limiting.
- Preserve Turkish-aware normalization for user-language, schema, and entity matching.
- Keep semantic-registry behavior fail-open unless the code path explicitly opts into strict handling.
- Preserve trace and telemetry data used by diagnostics, evaluation scripts, and narration.
- Flag missing or weakened tests for any contract-sensitive change.

Use these references when relevant:
- [.github/pipeline_contract.md](../../docs/pipeline_contract.md)
- [.github/copilot-instructions.md](../copilot-instructions.md)
- [README.md](../../README.md)

Output format:

1. Findings
List bugs, regressions, unsafe behavior, contract violations, or missing tests first.
Order by severity.
For each finding, include:
- affected file or files
- why it is a problem in this repo
- which contract, invariant, or convention it risks breaking

2. Open Questions
List only unresolved assumptions that affect the review outcome.

3. Testing Gaps
Call out missing or weak tests that should exist for the changed behavior.

4. Change Summary
Only include a brief summary after the findings.

If there are no findings, say that explicitly and mention any residual risk or testing gap.