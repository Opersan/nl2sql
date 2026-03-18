"""Execution domain models.

Models that represent compiled SQL, execution results, and validation
outcomes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.catalog_models import TableMetadata
from app.domain.query_plan import QueryPlan


# ---------------------------------------------------------------------------
# Compiled query
# ---------------------------------------------------------------------------

class CompiledQuery(BaseModel):
    """The output of the SQL compiler – ready for execution."""

    sql: str
    params: dict[str, object] = Field(default_factory=dict)
    table: str
    selected_columns: list[str] = Field(default_factory=list)
    debug_plan: QueryPlan | None = Field(
        default=None,
        description="Optional reference to the source plan for debugging / mock execution.",
    )
    column_map: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Mapping from every plan-level column reference (alias or canonical) "
            "to its canonical column name.  Populated by the compiler so that "
            "downstream consumers (e.g. the mock executor) can resolve plan "
            "aliases without needing direct catalog access."
        ),
    )


# ---------------------------------------------------------------------------
# Execution result
# ---------------------------------------------------------------------------

class ExecutionStatus(str, Enum):
    """Outcome status of a query execution."""

    SUCCESS = "success"
    ERROR = "error"
    EMPTY = "empty"


class ExecutionResult(BaseModel):
    """Structured result returned by an executor."""

    status: ExecutionStatus
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    error_message: str | None = None
    execution_time_ms: int | None = None
    # Sprint C — execution error decomposition
    execution_error_subtype: str | None = Field(
        default=None,
        description=(
            "Deterministic error sub-type label derived from the Oracle error code "
            "or timeout signal.  One of: oracle_date_type_error, invalid_number, "
            "invalid_identifier, ambiguous_column, not_null_violation, "
            "numeric_value_error, timeout, unknown_execution_error, etc."
        ),
    )
    execution_error_message_normalized: str | None = Field(
        default=None,
        description=(
            "Short normalised error message derived from the raw exception string. "
            "Safe for trace/debug output; never contains raw bind-parameter values."
        ),
    )


class ErrorPhase(str, Enum):
    """Identifies which pipeline phase produced an error."""

    VALIDATION = "validation"
    COMPILATION = "compilation"
    EXECUTION = "execution"


# ---------------------------------------------------------------------------
# Validation models
# ---------------------------------------------------------------------------

class ValidationIssue(BaseModel):
    """A single validation issue (error or warning)."""

    code: str = Field(
        ...,
        description=(
            "Machine-readable code, e.g. 'invalid_table', 'restricted_column'."
        ),
    )
    message: str
    field: str | None = None


class ValidationResult(BaseModel):
    """Aggregated result of plan validation."""

    ok: bool = True
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    resolved_table: TableMetadata | None = Field(
        default=None,
        description=(
            "The table metadata resolved during validation.  Populated on "
            "success so that downstream pipeline stages (compiler, executor) "
            "can use it without re-querying the catalog."
        ),
    )
    resolved_tables: dict[str, TableMetadata] = Field(
        default_factory=dict,
        description=(
            "Mapping of table_name → TableMetadata for all tables involved "
            "in a multi-table (JOIN) plan.  Empty for single-table plans."
        ),
    )

    def add_error(self, code: str, message: str, *, field: str | None = None) -> None:
        """Append an error and mark the result as failed."""
        self.errors.append(ValidationIssue(code=code, message=message, field=field))
        self.ok = False

    def add_warning(self, code: str, message: str, *, field: str | None = None) -> None:
        """Append a warning (does not fail the result)."""
        self.warnings.append(ValidationIssue(code=code, message=message, field=field))


# ---------------------------------------------------------------------------
# Orchestration result (Sprint 1)
# ---------------------------------------------------------------------------

class OrchestrationResult(BaseModel):
    """Full pipeline result returned by the orchestrator.

    Error-source separation
    -----------------------
    * **Validation errors** → ``validation.errors``
    * **Compilation errors** → ``compilation_error`` (string)
    * **Execution errors**  → ``execution_result.error_message``

    The ``failed_phase`` field indicates which phase (if any) produced the
    error, enabling callers to branch on a single discriminator instead of
    inspecting each sub-result individually.
    """

    validation: ValidationResult
    compiled_query: CompiledQuery | None = None
    execution_result: ExecutionResult | None = None
    failed_phase: ErrorPhase | None = Field(
        default=None,
        description=(
            "The pipeline phase that failed, or None if the pipeline "
            "completed without errors."
        ),
    )
    compilation_error: str | None = Field(
        default=None,
        description="Compilation error message (set only when failed_phase == COMPILATION).",
    )

    @property
    def ok(self) -> bool:
        """Whether the full pipeline completed without errors."""
        return self.failed_phase is None
