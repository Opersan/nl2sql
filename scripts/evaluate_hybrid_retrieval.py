"""Hybrid Retrieval + Planner Evaluation Script.

Loads sample data (metadata, document corpus, eval questions), runs
each question through the full hybrid retrieval → prompt → planner
pipeline, and produces accuracy & safety metrics.

Supports multiple LLM providers and optional side-by-side comparison.

Usage:
    python scripts/evaluate_hybrid_retrieval.py                     # default: mock
    python scripts/evaluate_hybrid_retrieval.py --provider mock
    python scripts/evaluate_hybrid_retrieval.py --provider openai_compatible
    python scripts/evaluate_hybrid_retrieval.py --provider all      # runs both & compares
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# UTF-8 safe console output (Windows terminal fix)
# ---------------------------------------------------------------------------


def _configure_utf8_output() -> None:
    """Reconfigure stdout/stderr to UTF-8 with replace error handling."""
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_configure_utf8_output()

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.domain.catalog_models import CatalogSnapshot  # noqa: E402
from app.providers.catalog.base import CatalogProvider  # noqa: E402
from app.providers.documents.jsonl_loader import JSONLDocumentLoader  # noqa: E402
from app.providers.documents.models import DocumentCorpus  # noqa: E402
from app.providers.llm.base import LLMProvider  # noqa: E402
from app.providers.llm.mock_llm import MockLLMProvider  # noqa: E402
from app.providers.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from app.providers.llm.prompts import build_hybrid_planner_prompt  # noqa: E402
from app.providers.retrieval.base import DocumentRetrievalResult  # noqa: E402
from app.providers.retrieval.in_memory_doc_retriever import InMemoryDocumentRetriever  # noqa: E402
from app.providers.retrieval.in_memory_retriever import InMemoryRetriever  # noqa: E402
from app.services.catalog_service import CatalogService  # noqa: E402
from app.services.document_retrieval_service import DocumentRetrievalService  # noqa: E402
from app.services.planner_service import PlannerService  # noqa: E402
from app.services.schema_retrieval_service import SchemaRetrievalService  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = PROJECT_ROOT / "results"

METADATA_PATH = DATA_DIR / "sample_metadata.json"
CORPUS_PATH = DATA_DIR / "sample_schema_documents.jsonl"
EVAL_CSV_PATH = DATA_DIR / "sample_eval_questions.csv"

# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

_PROVIDER_CHOICES = ("mock", "openai_compatible", "all")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Hybrid Retrieval + Planner Evaluation",
    )
    parser.add_argument(
        "--provider",
        choices=_PROVIDER_CHOICES,
        default="mock",
        help="LLM provider to use (default: mock). "
        "'all' runs both mock and openai_compatible sequentially.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def create_llm_provider(name: str) -> LLMProvider:
    """Create an LLM provider instance by name."""
    if name == "mock":
        return MockLLMProvider()
    if name == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
    raise ValueError(f"Unknown provider: {name!r}")


def provider_display_name(name: str) -> str:
    """Human-readable provider name for outputs."""
    return {"mock": "MockLLMProvider", "openai_compatible": "OpenAICompatibleProvider"}.get(
        name, name,
    )


# ---------------------------------------------------------------------------
# SQL leak detection
# ---------------------------------------------------------------------------

_SQL_LEAK_RE = re.compile(r"\bSELECT\b.*\bFROM\b", re.IGNORECASE | re.DOTALL)


def _has_sql_leak(prompt: str) -> bool:
    """Return True if the prompt contains a raw SQL-like pattern."""
    return bool(_SQL_LEAK_RE.search(prompt))


# ---------------------------------------------------------------------------
# Snapshot-based CatalogProvider (loads from JSON)
# ---------------------------------------------------------------------------


class _SnapshotCatalogProvider(CatalogProvider):
    """CatalogProvider backed by a pre-loaded CatalogSnapshot."""

    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self._snapshot = snapshot

    async def get_snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    async def get_table(self, table_name: str):  # type: ignore[override]
        return self._snapshot.get_table(table_name)

    async def search_tables(self, query: str):  # type: ignore[override]
        return self._snapshot.search_tables(query)


# ---------------------------------------------------------------------------
# Eval row model
# ---------------------------------------------------------------------------


@dataclass
class EvalQuestion:
    """Parsed evaluation question from the CSV."""

    question_id: str
    question_tr: str
    expected_table: str
    expected_columns: list[str]
    expected_filter_hint: str
    difficulty: str
    tags: list[str]
    # Future-compatible optional fields
    expected_clarification: bool | None = None
    expected_aggregations: list[str] = field(default_factory=list)
    expected_filter_columns: list[str] = field(default_factory=list)
    expected_group_by: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    """Result of evaluating a single question."""

    question_id: str
    question: str
    difficulty: str
    expected_table: str
    expected_columns: list[str]
    expected_filter_hint: str
    predicted_table: str
    table_match: bool
    needs_clarification: bool
    retrieved_schema_tables: list[str]
    retrieved_doc_ids: list[str]
    retrieved_example_ids: list[str]
    retrieved_schema_hit: bool
    prompt_length: int
    prompt_has_sql_leak: bool
    prompt_budget_exceeded: bool
    # Planner detail fields
    raw_intent: str = ""
    clarification_message: str | None = None
    predicted_select_columns: list[str] = field(default_factory=list)
    predicted_filter_columns: list[str] = field(default_factory=list)
    predicted_aggregation_functions: list[str] = field(default_factory=list)
    predicted_group_by: list[str] = field(default_factory=list)
    # Normalization / canonicalization stats
    canonicalization_applied_count: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def load_metadata(path: Path) -> CatalogSnapshot:
    """Load and validate metadata JSON into a CatalogSnapshot."""
    if not path.exists():
        raise FileNotFoundError(f"Metadata file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    snapshot = CatalogSnapshot.model_validate(data)
    print(f"  Metadata loaded: {len(snapshot.tables)} table(s)")
    return snapshot


async def load_corpus(path: Path) -> DocumentCorpus:
    """Load document corpus from JSONL."""
    if not path.exists():
        raise FileNotFoundError(f"Corpus file not found: {path}")
    loader = JSONLDocumentLoader(strict=True)
    corpus = await loader.load(str(path))
    print(
        f"  Corpus loaded: {len(corpus.schema_docs)} schema doc(s), "
        f"{len(corpus.examples)} example(s)"
    )
    return corpus


def _parse_optional_bool(value: str) -> bool | None:
    """Parse an optional boolean CSV field; return None if absent/empty."""
    v = value.strip().lower()
    if not v:
        return None
    return v in ("true", "1", "yes", "evet")


def _parse_pipe_list(raw: str) -> list[str]:
    """Split a ``|``-delimited string into a trimmed list, skipping blanks."""
    return [c.strip() for c in raw.split("|") if c.strip()]


def load_eval_questions(path: Path) -> list[EvalQuestion]:
    """Load evaluation questions from CSV.

    Supports optional future columns (expected_clarification,
    expected_aggregations, expected_filter_columns, expected_group_by)
    without breaking if they are absent.
    """
    if not path.exists():
        raise FileNotFoundError(f"Eval CSV not found: {path}")
    questions: list[EvalQuestion] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            questions.append(
                EvalQuestion(
                    question_id=row["question_id"].strip(),
                    question_tr=row["question_tr"].strip(),
                    expected_table=row["expected_table"].strip(),
                    expected_columns=_parse_pipe_list(
                        row.get("expected_columns", ""),
                    ),
                    expected_filter_hint=row.get("expected_filter_hint", "").strip(),
                    difficulty=row.get("difficulty", "").strip(),
                    tags=[
                        t.strip()
                        for t in row.get("tags", "").split(";")
                        if t.strip()
                    ],
                    expected_clarification=_parse_optional_bool(
                        row.get("expected_clarification", ""),
                    ),
                    expected_aggregations=_parse_pipe_list(
                        row.get("expected_aggregations", ""),
                    ),
                    expected_filter_columns=_parse_pipe_list(
                        row.get("expected_filter_columns", ""),
                    ),
                    expected_group_by=_parse_pipe_list(
                        row.get("expected_group_by", ""),
                    ),
                )
            )
    print(f"  Eval questions loaded: {len(questions)} question(s)")
    return questions


# ---------------------------------------------------------------------------
# Wire up services
# ---------------------------------------------------------------------------


def build_services(
    snapshot: CatalogSnapshot,
    corpus: DocumentCorpus,
    llm: LLMProvider | None = None,
) -> tuple[PlannerService, CatalogService, DocumentRetrievalService]:
    """Construct the full service stack for evaluation."""

    # Catalog layer
    catalog_provider = _SnapshotCatalogProvider(snapshot)
    schema_retriever = InMemoryRetriever(catalog_provider)
    schema_retrieval_svc = SchemaRetrievalService(schema_retriever)
    catalog_svc = CatalogService(catalog_provider, retrieval=schema_retrieval_svc)

    # Document layer
    doc_retriever = InMemoryDocumentRetriever(corpus)
    doc_retrieval_svc = DocumentRetrievalService(doc_retriever)

    # LLM — default to MockLLMProvider
    llm_provider = llm or MockLLMProvider()

    # Planner
    planner_svc = PlannerService(
        llm=llm_provider,
        catalog=catalog_svc,
        doc_retrieval=doc_retrieval_svc,
    )

    return planner_svc, catalog_svc, doc_retrieval_svc


# ---------------------------------------------------------------------------
# Single question evaluator
# ---------------------------------------------------------------------------


async def evaluate_question(
    question: EvalQuestion,
    planner: PlannerService,
    catalog_svc: CatalogService,
    doc_retrieval_svc: DocumentRetrievalService,
    max_prompt_chars: int,
) -> EvalResult:
    """Run the full pipeline for a single eval question and return metrics."""

    user_msg = question.question_tr
    error: str | None = None

    # ── Retrieval ────────────────────────────────────────────────
    try:
        schema_context = await catalog_svc.get_relevant_context(user_msg)
        retrieved_tables = [t.name for t in schema_context.tables]
    except Exception as exc:
        retrieved_tables = []
        error = f"schema retrieval error: {exc}"

    try:
        doc_result = await doc_retrieval_svc.retrieve_context(user_msg)
        retrieved_doc_ids = [d.doc_id for d in doc_result.schema_docs]
        retrieved_example_ids = [e.doc_id for e in doc_result.examples]
    except Exception as exc:
        doc_result = DocumentRetrievalResult()
        retrieved_doc_ids = []
        retrieved_example_ids = []
        if error:
            error += f"; doc retrieval error: {exc}"
        else:
            error = f"doc retrieval error: {exc}"

    # ── Prompt assembly ──────────────────────────────────────────
    try:
        prompt = build_hybrid_planner_prompt(
            user_msg,
            schema_context,
            schema_docs=doc_result.schema_docs or None,
            examples=doc_result.examples or None,
            max_prompt_chars=max_prompt_chars,
        )
        prompt_length = len(prompt)
        prompt_budget_exceeded = prompt_length > max_prompt_chars
        prompt_has_sql_leak = _has_sql_leak(prompt)
    except Exception as exc:
        prompt_length = 0
        prompt_budget_exceeded = False
        prompt_has_sql_leak = False
        if error:
            error += f"; prompt error: {exc}"
        else:
            error = f"prompt error: {exc}"

    # ── Planner ──────────────────────────────────────────────────
    predicted_table = ""
    needs_clarification = False
    raw_intent = ""
    clarification_message: str | None = None
    predicted_select_columns: list[str] = []
    predicted_filter_columns: list[str] = []
    predicted_aggregation_functions: list[str] = []
    predicted_group_by: list[str] = []
    try:
        plan = await planner.plan(user_msg)
        predicted_table = plan.table or ""
        needs_clarification = plan.needs_clarification
        raw_intent = plan.intent
        clarification_message = plan.clarification_message
        # Extract detail regardless of clarification status
        predicted_select_columns = list(plan.select_columns)
        predicted_filter_columns = [f.column for f in plan.filters]
        predicted_aggregation_functions = [
            f"{a.function.value}({a.column})" for a in plan.aggregations
        ]
        predicted_group_by = list(plan.group_by)
        # Capture canonicalization stats
        canon_stats = planner.last_canonicalization_stats
        canonicalization_count = canon_stats.column_canonicalized if canon_stats else 0
    except Exception as exc:
        canonicalization_count = 0
        if error:
            error += f"; planner error: {exc}"
        else:
            error = f"planner error: {exc}"

    # ── Table match ──────────────────────────────────────────────
    table_match = (
        predicted_table.strip().upper() == question.expected_table.strip().upper()
    )

    # ── Retrieval hit ────────────────────────────────────────────
    expected_upper = question.expected_table.strip().upper()
    retrieved_schema_hit = any(
        t.strip().upper() == expected_upper for t in retrieved_tables
    )

    return EvalResult(
        question_id=question.question_id,
        question=question.question_tr,
        difficulty=question.difficulty,
        expected_table=question.expected_table,
        expected_columns=question.expected_columns,
        expected_filter_hint=question.expected_filter_hint,
        predicted_table=predicted_table,
        table_match=table_match,
        needs_clarification=needs_clarification,
        retrieved_schema_tables=retrieved_tables,
        retrieved_doc_ids=retrieved_doc_ids,
        retrieved_example_ids=retrieved_example_ids,
        retrieved_schema_hit=retrieved_schema_hit,
        prompt_length=prompt_length,
        prompt_has_sql_leak=prompt_has_sql_leak,
        prompt_budget_exceeded=prompt_budget_exceeded,
        raw_intent=raw_intent,
        clarification_message=clarification_message,
        predicted_select_columns=predicted_select_columns,
        predicted_filter_columns=predicted_filter_columns,
        predicted_aggregation_functions=predicted_aggregation_functions,
        predicted_group_by=predicted_group_by,
        canonicalization_applied_count=canonicalization_count,
        error=error,
    )


# ---------------------------------------------------------------------------
# Precision / Recall helpers
# ---------------------------------------------------------------------------


def _set_upper(items: list[str]) -> set[str]:
    """Normalise a list of strings to an upper-case set."""
    return {s.strip().upper() for s in items if s.strip()}


def _precision_recall(
    predicted: list[str],
    expected: list[str],
) -> tuple[float, float]:
    """Return (precision, recall) for two string lists (case-insensitive).

    Returns (0.0, 0.0) if both are empty, (0.0, 0.0) if expected is empty
    but predicted is not, etc.
    """
    p_set = _set_upper(predicted)
    e_set = _set_upper(expected)
    if not p_set and not e_set:
        return (1.0, 1.0)  # nothing expected, nothing predicted
    if not e_set:
        return (0.0, 1.0)  # nothing expected — recall is vacuously 1
    if not p_set:
        return (0.0, 0.0)
    correct = p_set & e_set
    precision = len(correct) / len(p_set) if p_set else 0.0
    recall = len(correct) / len(e_set) if e_set else 0.0
    return (precision, recall)


def _match_rate(predicted: list[str], expected: list[str]) -> float:
    """Fraction of expected items found in predicted (case-insensitive).

    Returns 1.0 when expected is empty (vacuous truth).
    """
    if not expected:
        return 1.0
    e_set = _set_upper(expected)
    p_set = _set_upper(predicted)
    return len(p_set & e_set) / len(e_set)


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------


def compute_summary(
    results: list[EvalResult],
    questions: list[EvalQuestion],
) -> dict[str, Any]:
    """Compute aggregate metrics from individual results.

    *questions* is used to access expected values for column/filter/agg
    match metrics.
    """
    total = len(results)
    if total == 0:
        return {"total_questions": 0}

    # Basic counts
    table_matches = sum(1 for r in results if r.table_match)
    clarifications = sum(1 for r in results if r.needs_clarification)
    non_clarifications = total - clarifications
    schema_hits = sum(1 for r in results if r.retrieved_schema_hit)
    sql_leaks = sum(1 for r in results if r.prompt_has_sql_leak)
    budget_violations = sum(1 for r in results if r.prompt_budget_exceeded)
    errors = sum(1 for r in results if r.error)

    prompt_lengths = [r.prompt_length for r in results if r.prompt_length > 0]
    prompt_avg = round(sum(prompt_lengths) / len(prompt_lengths)) if prompt_lengths else 0
    prompt_max = max(prompt_lengths) if prompt_lengths else 0

    # Canonicalization aggregate
    total_canonicalizations = sum(r.canonicalization_applied_count for r in results)
    questions_with_canonicalization = sum(
        1 for r in results if r.canonicalization_applied_count > 0
    )

    # ── Column precision / recall ────────────────────────────────
    col_precisions: list[float] = []
    col_recalls: list[float] = []
    filter_col_matches: list[float] = []
    agg_matches: list[float] = []
    group_by_matches: list[float] = []

    q_map = {q.question_id: q for q in questions}
    for r in results:
        q = q_map.get(r.question_id)
        if not q:
            continue

        # Column precision / recall (skip when expected_columns empty)
        if q.expected_columns:
            p, rc = _precision_recall(r.predicted_select_columns, q.expected_columns)
            col_precisions.append(p)
            col_recalls.append(rc)

        # Filter column match rate (skip when expected not provided)
        if q.expected_filter_columns:
            filter_col_matches.append(
                _match_rate(r.predicted_filter_columns, q.expected_filter_columns),
            )

        # Aggregation match rate
        if q.expected_aggregations:
            agg_matches.append(
                _match_rate(r.predicted_aggregation_functions, q.expected_aggregations),
            )

        # Group-by match rate
        if q.expected_group_by:
            group_by_matches.append(
                _match_rate(r.predicted_group_by, q.expected_group_by),
            )

    def _safe_avg(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    # Per-difficulty breakdown
    difficulty_breakdown: dict[str, dict[str, Any]] = {}
    for r in results:
        d = r.difficulty or "unknown"
        if d not in difficulty_breakdown:
            difficulty_breakdown[d] = {
                "total": 0, "table_match": 0, "schema_hit": 0, "clarification": 0,
            }
        difficulty_breakdown[d]["total"] += 1
        if r.table_match:
            difficulty_breakdown[d]["table_match"] += 1
        if r.retrieved_schema_hit:
            difficulty_breakdown[d]["schema_hit"] += 1
        if r.needs_clarification:
            difficulty_breakdown[d]["clarification"] += 1
    for d, stats in difficulty_breakdown.items():
        t = stats["total"]
        stats["table_match_rate"] = round(stats["table_match"] / t, 4) if t else 0.0
        stats["schema_hit_rate"] = round(stats["schema_hit"] / t, 4) if t else 0.0

    return {
        "total_questions": total,
        "table_match_count": table_matches,
        "table_match_rate": round(table_matches / total, 4),
        "retrieved_schema_hit_count": schema_hits,
        "retrieved_schema_hit_rate": round(schema_hits / total, 4),
        "non_clarification_count": non_clarifications,
        "non_clarification_rate": round(non_clarifications / total, 4),
        "clarification_count": clarifications,
        "clarification_rate": round(clarifications / total, 4),
        "column_precision": _safe_avg(col_precisions),
        "column_recall": _safe_avg(col_recalls),
        "filter_column_match_rate": _safe_avg(filter_col_matches),
        "aggregation_match_rate": _safe_avg(agg_matches),
        "group_by_match_rate": _safe_avg(group_by_matches),
        "sql_leak_count": sql_leaks,
        "prompt_budget_violations": budget_violations,
        "prompt_avg_length": prompt_avg,
        "prompt_max_length": prompt_max,
        "canonicalization_applied_total": total_canonicalizations,
        "questions_with_canonicalization": questions_with_canonicalization,
        "error_count": errors,
        "difficulty_breakdown": difficulty_breakdown,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _result_to_dict(r: EvalResult) -> dict[str, Any]:
    """Serialize an EvalResult to a JSON-friendly dictionary."""
    return {
        "question_id": r.question_id,
        "question": r.question,
        "difficulty": r.difficulty,
        "expected_table": r.expected_table,
        "expected_columns": r.expected_columns,
        "expected_filter_hint": r.expected_filter_hint,
        "predicted_table": r.predicted_table,
        "table_match": r.table_match,
        "needs_clarification": r.needs_clarification,
        "raw_intent": r.raw_intent,
        "clarification_message": r.clarification_message,
        "retrieved_schema_tables": r.retrieved_schema_tables,
        "retrieved_schema_hit": r.retrieved_schema_hit,
        "retrieved_doc_ids": r.retrieved_doc_ids,
        "retrieved_example_ids": r.retrieved_example_ids,
        "predicted_select_columns": r.predicted_select_columns,
        "predicted_filter_columns": r.predicted_filter_columns,
        "predicted_aggregation_functions": r.predicted_aggregation_functions,
        "predicted_group_by": r.predicted_group_by,
        "canonicalization_applied_count": r.canonicalization_applied_count,
        "prompt_length": r.prompt_length,
        "prompt_has_sql_leak": r.prompt_has_sql_leak,
        "prompt_budget_exceeded": r.prompt_budget_exceeded,
        "error": r.error,
    }


def _fmt_metric(value: float | None, is_pct: bool = True) -> str:
    """Format a metric value for display; handle None gracefully."""
    if value is None:
        return "  n/a"
    return f"{value:.1%}" if is_pct else f"{value:.4f}"


def print_summary(summary: dict[str, Any], *, label: str = "") -> None:
    """Print a human-readable summary to stdout."""
    total = summary["total_questions"]
    sep = "=" * 66
    header = "  HYBRID RETRIEVAL EVALUATION SUMMARY"
    if label:
        header += f"  [{label}]"
    print(f"\n{sep}")
    print(header)
    print(sep)

    # TOTAL
    print(f"  TOTAL              : {total} questions")
    print()

    # TABLE MATCH
    tm = summary["table_match_count"]
    print(f"  TABLE MATCH        : {tm}/{total}  ({summary['table_match_rate']:.1%})")

    # RETRIEVAL HIT
    rh = summary["retrieved_schema_hit_count"]
    print(f"  RETRIEVAL HIT      : {rh}/{total}  ({summary['retrieved_schema_hit_rate']:.1%})")

    # CLARIFICATION
    cl = summary["clarification_count"]
    nc = summary["non_clarification_count"]
    print(
        f"  CLARIFICATION      : {cl}/{total}  ({summary['clarification_rate']:.1%})"
        f"  |  non-clarification: {nc}  ({summary['non_clarification_rate']:.1%})"
    )

    # COLUMN PRECISION / RECALL
    print(
        f"  COLUMN PRECISION   : {_fmt_metric(summary.get('column_precision'))}"
        f"    COLUMN RECALL: {_fmt_metric(summary.get('column_recall'))}"
    )

    # FILTER / AGG / GROUP_BY MATCH
    print(
        f"  FILTER COL MATCH   : {_fmt_metric(summary.get('filter_column_match_rate'))}"
        f"    AGG MATCH    : {_fmt_metric(summary.get('aggregation_match_rate'))}"
        f"    GROUP_BY     : {_fmt_metric(summary.get('group_by_match_rate'))}"
    )

    # SQL LEAK
    print(f"  SQL LEAK           : {summary['sql_leak_count']}")

    # CANONICALIZATION
    print(
        f"  CANONICALIZATION   : {summary['canonicalization_applied_total']} col(s) "
        f"across {summary['questions_with_canonicalization']} question(s)"
    )

    # PROMPT
    print(
        f"  PROMPT AVG/MAX     : {summary['prompt_avg_length']}ch"
        f" / {summary['prompt_max_length']}ch"
        f"  |  budget violations: {summary['prompt_budget_violations']}"
    )

    # ERRORS
    print(f"  ERRORS             : {summary['error_count']}")

    # Per-difficulty breakdown
    breakdown = summary.get("difficulty_breakdown", {})
    if breakdown:
        print(f"\n  {'DIFFICULTY':<10s}  {'TBL':>5s}  {'RET':>5s}  {'CLR':>5s}  {'TOT':>4s}")
        print(f"  {'-' * 10}  {'-' * 5}  {'-' * 5}  {'-' * 5}  {'-' * 4}")
        for diff in sorted(breakdown):
            s = breakdown[diff]
            print(
                f"  {diff:<10s}"
                f"  {s['table_match_rate']:>5.0%}"
                f"  {s['schema_hit_rate']:>5.0%}"
                f"  {s['clarification']:>5d}"
                f"  {s['total']:>4d}"
            )

    print(sep + "\n")


def save_results(
    results: list[EvalResult],
    summary: dict[str, Any],
    output_dir: Path,
    *,
    suffix: str = "",
) -> tuple[Path, Path]:
    """Write detailed results and summary to JSON files.

    When *suffix* is provided (e.g. ``"mock"``), files are named
    ``eval_results_mock.json`` and ``eval_summary_mock.json``.
    Otherwise the legacy names ``eval_results.json`` / ``eval_summary.json``
    are used.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    tag = f"_{suffix}" if suffix else ""
    results_path = output_dir / f"eval_results{tag}.json"
    summary_path = output_dir / f"eval_summary{tag}.json"

    results_data = [_result_to_dict(r) for r in results]
    results_path.write_text(
        json.dumps(results_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"  Results saved to  : {results_path}")
    print(f"  Summary saved to  : {summary_path}")
    return results_path, summary_path


# ---------------------------------------------------------------------------
# Provider comparison
# ---------------------------------------------------------------------------


def print_comparison(
    summaries: dict[str, dict[str, Any]],
) -> None:
    """Print a side-by-side comparison of two or more provider summaries."""
    names = list(summaries.keys())
    if len(names) < 2:
        return

    sep = "=" * 66
    print(f"\n{sep}")
    print("  PROVIDER COMPARISON")
    print(sep)

    metrics = [
        ("table_match_rate", "TABLE MATCH"),
        ("clarification_rate", "CLARIFICATION"),
        ("column_precision", "COL PRECISION"),
        ("column_recall", "COL RECALL"),
        ("filter_column_match_rate", "FILTER MATCH"),
        ("aggregation_match_rate", "AGG MATCH"),
        ("group_by_match_rate", "GROUP_BY MATCH"),
        ("retrieved_schema_hit_rate", "RETRIEVAL HIT"),
        ("canonicalization_applied_total", "CANONICALIZATIONS"),
        ("sql_leak_count", "SQL LEAKS"),
        ("error_count", "ERRORS"),
    ]

    # Header row
    header = f"  {'METRIC':<18s}"
    for n in names:
        header += f"  {n:>20s}"
    print(header)
    print(f"  {'-' * 18}" + f"  {'-' * 20}" * len(names))

    for key, label in metrics:
        line = f"  {label:<18s}"
        for n in names:
            val = summaries[n].get(key)
            if val is None:
                line += f"  {'n/a':>20s}"
            elif isinstance(val, float) and val <= 1.0:
                line += f"  {val:>19.1%} "
            else:
                line += f"  {val!s:>20s}"
        print(line)

    print(sep + "\n")


# ---------------------------------------------------------------------------
# Per-question progress line
# ---------------------------------------------------------------------------


def _status_char(r: EvalResult) -> str:
    """Single-char status indicator for terminal output."""
    if r.error:
        return "E"
    if r.needs_clarification:
        return "?"
    return "+" if r.table_match else "-"


# ---------------------------------------------------------------------------
# Run evaluation for a single provider
# ---------------------------------------------------------------------------


async def run_evaluation(
    provider_name: str,
    snapshot: CatalogSnapshot,
    corpus: DocumentCorpus,
    questions: list[EvalQuestion],
    max_budget: int,
) -> tuple[list[EvalResult], dict[str, Any]]:
    """Execute the eval pipeline for one provider and return (results, summary)."""

    display = provider_display_name(provider_name)
    print(f"\n[2/4] Wiring services ({display})...")

    llm = create_llm_provider(provider_name)
    planner, catalog_svc, doc_retrieval_svc = build_services(snapshot, corpus, llm)
    print(f"  Prompt budget: {max_budget} chars")

    print(f"\n[3/4] Evaluating questions ({display})...")
    results: list[EvalResult] = []
    start = time.perf_counter()

    for i, q in enumerate(questions, 1):
        result = await evaluate_question(
            q, planner, catalog_svc, doc_retrieval_svc, max_budget,
        )
        results.append(result)
        status = _status_char(result)
        ret_mark = "R" if result.retrieved_schema_hit else "."
        print(
            f"  [{i:>3}/{len(questions)}] {status}{ret_mark}  {q.question_id:<8s}  "
            f"tbl={result.table_match!s:<5s}  "
            f"ret={result.retrieved_schema_hit!s:<5s}  "
            f"prm={result.prompt_length:>5d}ch  "
            f"{q.question_tr:.50s}"
        )

    elapsed = time.perf_counter() - start
    print(f"  Completed in {elapsed:.2f}s")

    print(f"\n[4/4] Computing metrics & saving results ({display})...")
    summary = compute_summary(results, questions)
    summary["elapsed_seconds"] = round(elapsed, 3)
    summary["llm_provider"] = display
    summary["prompt_budget_chars"] = max_budget

    print_summary(summary, label=display)
    save_results(results, summary, RESULTS_DIR, suffix=provider_name)

    return results, summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(argv: list[str] | None = None) -> None:
    """Run the full evaluation pipeline."""

    args = parse_args(argv)

    print("\n[1/4] Loading data...")
    snapshot = load_metadata(METADATA_PATH)
    corpus = await load_corpus(CORPUS_PATH)
    questions = load_eval_questions(EVAL_CSV_PATH)

    if not questions:
        print("  No eval questions found — nothing to evaluate.")
        return

    # Override settings for evaluation
    settings.enable_metadata_retrieval = True
    settings.enable_document_retrieval = True
    max_budget = settings.planner_prompt_max_chars

    # Determine which providers to run
    if args.provider == "all":
        providers = ["mock", "openai_compatible"]
    else:
        providers = [args.provider]

    all_summaries: dict[str, dict[str, Any]] = {}
    worst_rate = 1.0

    for prov in providers:
        _results, summary = await run_evaluation(
            prov, snapshot, corpus, questions, max_budget,
        )
        all_summaries[prov] = summary
        worst_rate = min(worst_rate, summary.get("table_match_rate", 0.0))

    # Side-by-side comparison when multiple providers ran
    if len(all_summaries) >= 2:
        print_comparison(all_summaries)

    # Exit code: non-zero if any provider's table match rate < 50%
    if worst_rate < 0.5:
        print("  WARNING: Table match rate below 50% — check results.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
