# Project Guidelines

## Architecture
- Treat this repo as a deterministic NL2SQL pipeline, not a direct text-to-SQL app: planner produces `QueryPlan`, then validation, Oracle-oriented compilation, execution guard, executor, and narration run in sequence.
- Never bypass the plan-first flow and never introduce paths that execute raw LLM-generated SQL.
- Keep responsibilities aligned with the current package layout: `app/api` for FastAPI wiring, `app/services` for orchestration and business logic, `app/domain` for Pydantic contracts, `app/providers` for external adapters, and `app/semantic` for registry-backed semantic normalization.
- When changing planner, orchestrator, narrator, validation, or compiler behavior, preserve the contracts documented in [docs/pipeline_contract.md](../docs/pipeline_contract.md).

## Build And Test
- Create the environment with `python -m venv .venv` and install dependencies with `pip install -e ".[dev]"` or `pip install -e ".[dev,oracle]"` when Oracle integration is needed.
- Run tests with `.venv\Scripts\python -m pytest` on Windows because `pytest` may not be on `PATH`.
- Use `ruff check app/ --fix` for linting when Python files are changed.
- Prefer targeted tests for the touched area first, then broader suites when the change affects pipeline-wide behavior.

## Code Style
- Follow the existing async service style: service entry points are typically `async def`, FastAPI startup uses lifespan hooks, and new code should not block the event loop.
- Use Pydantic v2 patterns already present in the repo. Do not reintroduce Pydantic v1 configuration styles.
- Reuse the domain exception hierarchy in `app/core/exceptions.py` instead of raising bare exceptions from service code.
- Keep Oracle SQL generation compatible with the current compiler patterns: bind parameters, explicit column lists, and `ROWNUM`-based limiting.

## Conventions
- Preserve `QueryPlan` invariants. Clarification plans must stay stripped of query artifacts, and successful plans must remain valid for deterministic validation and compilation.
- Use Turkish-aware normalization utilities for user-language matching and schema/entity comparisons. Do not rely on plain lowercase comparisons for Turkish text.
- Keep semantic-registry behavior fail-open unless the existing code path explicitly opts into strict handling.
- Avoid module-level side effects for new code, especially around metadata, registry, or provider loading.
- For diagnostics and orchestration changes, keep trace data intact because the evaluation scripts and narrator depend on it.

## Key References
- See [README.md](../README.md) for setup, endpoints, configuration, and repo structure.
- See [docs/pipeline_contract.md](../docs/pipeline_contract.md) before modifying stage behavior or cross-stage data contracts.
- See [docs/refactor_boundary_sprint_c2.md](../docs/refactor_boundary_sprint_c2.md) for the intended responsibility boundaries and current refactor direction.
