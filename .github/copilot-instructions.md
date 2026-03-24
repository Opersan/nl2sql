# Project Guidelines (Production NL2SQL System)

## Core Philosophy

This system is a **deterministic, plan-driven NL2SQL pipeline** designed for **Oracle EBS R12 enterprise analytics**.

DO NOT treat this as a direct text-to-SQL system.

The LLM is NOT responsible for generating executable SQL directly.

Instead:

User Query → QueryPlan → Validation → Compilation → Execution → Interpretation

---

## Mandatory Architectural Principles

### 1. Plan-First Execution (Non-Negotiable)

- Always generate a structured `QueryPlan` before any SQL exists.
- SQL MUST be derived from the plan, never directly from natural language.
- Never allow raw LLM SQL to reach execution.

Violations of this rule are considered critical defects.

---

### 2. Multi-Stage Decomposition (Agentic Pattern)

The pipeline MUST logically separate:

1. Intent classification (domain, KPI, operation type)
2. Schema retrieval (tables, relationships)
3. Table selection
4. Column pruning
5. Query planning
6. SQL compilation

Do NOT collapse these into a single step.

---

### 3. Metadata-First Retrieval (RAG Required)

SQL generation MUST be grounded using:

- schema metadata (tables, columns)
- relationship graph (joins)
- business glossary (TR/EN mappings)
- KPI/metric definitions
- example queries (few-shot retrieval)

Never rely on implicit model knowledge.

---

### 4. Semantic Layer is Mandatory

All reasoning must pass through a semantic abstraction layer:

- Business entities (Invoice, PO, Item, Ledger, etc.)
- Synonyms (Turkish/English normalization)
- KPI definitions (centralized)
- Relationship graph (curated joins, not inferred blindly)

Do NOT expose raw database schema directly to the model.

---

### 5. Oracle EBS Constraints (Critical)

The system MUST respect:

- Multi-Org context (OU, ledger, inventory org)
- Flexfields (KFF/DFF structures)
- Lookup resolution (code → meaning)
- Non-enforced FK relationships (join graph must not rely only on DB constraints)

---

## SQL Generation Rules

- SQL must be generated ONLY via compiler layer
- Use bind variables (no string interpolation)
- No `SELECT *`
- Fully qualified column references required
- Oracle-compatible syntax only
- Row limiting must use `ROWNUM` or equivalent

---

## Validation Layer (Mandatory)

Every query MUST pass deterministic validation:

- SQL must be SELECT-only (no DML/DDL)
- Allowed tables/columns must be enforced (RBAC)
- Query must be syntactically valid
- Query must be semantically consistent with QueryPlan

Optional but strongly recommended:

- SQL AST validation (e.g. SQLGlot-style)
- Join path verification against relationship graph

---

## Execution Guard (Oracle-Specific)

Before execution:

- Run EXPLAIN PLAN (DBMS_XPLAN)
- Detect:
  - full table scans
  - large joins
  - high cost queries

If risk detected:
- degrade query OR
- require user confirmation OR
- redirect to aggregated query

---

## Execution Layer

- Read-only connections only
- Prefer reporting replica (never overload OLTP EBS)
- Enforce:
  - row limits
  - timeouts
  - pagination
  - safe parameter binding

---

## Narration / Interpretation Layer

The system MUST transform results into business insights:

Supported output types:
- scalar metrics
- grouped aggregates
- listings
- empty results

Narration must include:
- business meaning
- filters applied
- key metrics
- optional trend hints

Avoid generic outputs like:
> "Toplam X kayıt bulundu"

---

## Observability & Tracing (Mandatory)

Every request MUST produce a trace including:

- user query
- QueryPlan
- selected tables/columns
- generated SQL
- execution metadata
- final response

Never remove raw outputs.

---

## Evaluation Requirements

The system MUST support:

- golden question set
- SQL execution accuracy tracking
- schema selection accuracy
- permission boundary tests
- failure classification

---

## Code Organization

- `app/api` → FastAPI endpoints
- `app/services` → orchestration logic
- `app/domain` → Pydantic contracts
- `app/providers` → DB / external integrations
- `app/semantic` → glossary + schema + relationships

Do not violate these boundaries.

---

## Code Style

- Async-first (`async def`)
- No blocking I/O
- Pydantic v2 only
- Use domain exceptions (no raw Exception)

---

## Anti-Patterns (STRICTLY FORBIDDEN)

- Direct NL → SQL generation
- Feeding full schema to LLM
- Blind join inference from DB constraints
- Ignoring multi-org context
- Returning raw codes instead of business meanings
- Executing SQL without validation
- Lack of traceability

---

## References

- docs/pipeline_contract.md
- docs/refactor_boundary_sprint_c2.md
- README.md
