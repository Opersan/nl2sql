"""Dependency injection helpers for the API layer."""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.catalog.base import CatalogProvider
from app.providers.catalog.in_memory import InMemoryCatalogProvider, catalog_fingerprint
from app.providers.executor.base import ExecutorProvider
from app.providers.executor.mock_executor import MockExecutor
from app.providers.llm.base import LLMProvider
from app.providers.llm.mock_llm import MockLLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleProvider
from app.providers.retrieval.in_memory_retriever import InMemoryRetriever
from app.services.catalog_service import CatalogService
from app.services.document_retrieval_service import DocumentRetrievalService
from app.services.narrator_service import NarratorService
from app.services.orchestrator import ChatOrchestrator, Orchestrator
from app.services.planner_service import PlannerService
from app.services.schema_retrieval_service import SchemaRetrievalService
from app.services.semantic_planning import (
    _REGISTRY_PATH,
    validate_registry_against_catalog,
)
from app.services.session_service import SessionService
from app.services.sql_compiler import SQLCompiler
from app.services.validation_service import ValidationService

logger = get_logger(__name__)


def build_llm_provider() -> LLMProvider:
    """Construct the LLM provider based on application settings."""
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
    return MockLLMProvider()


def _build_catalog_provider() -> CatalogProvider:
    """Construct the catalog provider based on application settings.

    Resolution order
    ----------------
    1. ``metadata_source_type == "json"`` and ``metadata_source_path`` set
       and file exists → ``JsonFileCatalogProvider``.
    2. ``metadata_source_type == "json"`` but file missing → warning, fall
       back to ``InMemoryCatalogProvider``.
    3. All other cases (``"none"``, ``"csv"`` etc.) → ``InMemoryCatalogProvider``.
    """
    if settings.metadata_source_type == "json" and settings.metadata_source_path:
        path = Path(settings.metadata_source_path)
        if not path.is_absolute():
            # Resolve relative to the repository root (two levels up from this file).
            path = Path(__file__).resolve().parents[2] / path
        if path.exists():
            from app.providers.catalog.json_file_catalog import JsonFileCatalogProvider
            return JsonFileCatalogProvider(path)
        logger.warning(
            "[catalog] metadata_source_type='json' but file not found: %s "
            "— falling back to InMemoryCatalogProvider",
            path,
        )
    return InMemoryCatalogProvider()


def _build_executor() -> ExecutorProvider:
    """Construct the executor based on application settings."""
    if settings.enable_oracle_executor:
        from app.providers.executor.oracle_executor import OracleExecutor

        return OracleExecutor(
            dsn=settings.oracle_dsn,
            user=settings.oracle_user,
            password=settings.oracle_password,
            timeout=settings.oracle_timeout,
        )
    return MockExecutor()


async def build_document_retrieval() -> DocumentRetrievalService | None:
    """Construct the document retrieval service if configured.

    **Must be called from an async context** (e.g. the FastAPI lifespan
    handler).  The corpus is loaded with a single ``await`` — no
    ``run_until_complete`` hacks.

    Startup policy: **fail-open**.
    The document layer is auxiliary context for the planner — not the
    source-of-truth (that role belongs to ``CatalogSnapshot``).  If the
    corpus cannot be loaded for any reason the function returns ``None``
    so the planner produces valid plans using the structured catalog alone.

    ``document_loader_strict`` controls **line-level** validation inside
    the JSONL loader (malformed JSON / unknown doc_type / invalid payload).
    It does *not* make the application crash on startup.
    """
    if not settings.enable_document_retrieval:
        logger.info("[doc-retrieval] disabled — skipping corpus load")
        return None

    if not settings.document_corpus_path:
        logger.warning(
            "[doc-retrieval] enabled but document_corpus_path is empty "
            "(strict=%s) — continuing without documents (fail-open)",
            settings.document_loader_strict,
        )
        return None

    corpus_path = Path(settings.document_corpus_path)
    if not corpus_path.exists():
        logger.warning(
            "[doc-retrieval] corpus file not found: %s (strict=%s) "
            "— continuing without documents (fail-open)",
            corpus_path,
            settings.document_loader_strict,
        )
        return None

    # Lazy imports keep the loader pluggable and avoid circular deps.
    from app.providers.documents.jsonl_loader import JSONLDocumentLoader
    from app.providers.retrieval.in_memory_doc_retriever import (
        InMemoryDocumentRetriever,
    )

    strict = settings.document_loader_strict
    logger.info(
        "[doc-retrieval] loading corpus from %s (strict=%s)",
        corpus_path,
        strict,
    )

    try:
        loader = JSONLDocumentLoader(strict=strict)
        corpus = await loader.load(corpus_path)
    except Exception:
        # Fail-open: document layer failure must not crash the app.
        logger.exception(
            "[doc-retrieval] failed to load corpus from %s (strict=%s) "
            "— continuing without documents (fail-open)",
            corpus_path,
            strict,
        )
        return None

    n_docs = len(corpus.schema_docs)
    n_examples = len(corpus.examples)

    if n_docs == 0 and n_examples == 0:
        logger.warning(
            "[doc-retrieval] corpus loaded from %s (strict=%s) but empty "
            "(0 docs, 0 examples) — retrieval will have no effect",
            corpus_path.name,
            strict,
        )
    else:
        logger.info(
            "[doc-retrieval] corpus loaded from %s (strict=%s) "
            "— %d doc(s), %d example(s)",
            corpus_path.name,
            strict,
            n_docs,
            n_examples,
        )

    retriever = InMemoryDocumentRetriever(corpus)
    return DocumentRetrievalService(retriever)


def build_chat_orchestrator(
    *,
    doc_retrieval: DocumentRetrievalService | None = None,
    executor: ExecutorProvider | None = None,
) -> ChatOrchestrator:
    """Wire up the full dependency graph for the chat pipeline.

    Parameters
    ----------
    doc_retrieval:
        Pre-built document retrieval service.  When ``None`` the planner
        falls back to structured catalog context only.  Callers should
        build this via :func:`build_document_retrieval` **before** calling
        this function (typically in the async lifespan handler).
    executor:
        Optional executor override.  When ``None``, the executor is built
        from application settings via ``_build_executor()``.  Pass an
        explicit executor (e.g. a pre-initialised ``OracleExecutor``) for
        eval scripts that need pool lifecycle control.
    """
    llm = build_llm_provider()

    # Catalog provider — single source of truth for API and eval.
    # Build via _build_catalog_provider() so the active source is
    # determined by settings (JSON file → InMemory fallback).
    catalog_provider = _build_catalog_provider()
    snapshot = catalog_provider._snapshot  # type: ignore[attr-defined]

    # ── Phase 1: Runtime observability ──────────────────────────────────
    fp = catalog_fingerprint(snapshot)
    n_tables = len(snapshot.tables)
    n_cols = sum(len(t.columns) for t in snapshot.tables)
    logger.info(
        "[catalog-boot] provider=%s fingerprint=%s tables=%d columns=%d "
        "source_type=%r source_path=%r doc_retrieval=%s "
        "doc_corpus=%r registry=%s",
        type(catalog_provider).__name__,
        fp,
        n_tables,
        n_cols,
        settings.metadata_source_type,
        settings.metadata_source_path or "(none)",
        doc_retrieval is not None,
        settings.document_corpus_path or "(none)",
        str(_REGISTRY_PATH),
    )

    # ── Phase 5: Registry boundary validation ───────────────────────────
    reg_errors = validate_registry_against_catalog(snapshot)
    if reg_errors:
        logger.warning(
            "[catalog-boot] semantic registry has %d integrity issue(s) "
            "against active catalog — check [registry-validation] log lines",
            len(reg_errors),
        )

    # Schema retrieval — always wired; strategy controls which back-end
    retrieval: SchemaRetrievalService = _build_schema_retriever(catalog_provider)

    catalog = CatalogService(catalog_provider, retrieval=retrieval)

    # Sprint 1 deterministic core
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    if executor is None:
        executor = _build_executor()
    orchestrator = Orchestrator(validator, compiler, executor)

    # Sprint 2 LLM services
    planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)
    narrator = NarratorService(llm)
    sessions = SessionService()

    return ChatOrchestrator(planner, orchestrator, narrator, sessions)


def _build_schema_retriever(catalog_provider: CatalogProvider) -> SchemaRetrievalService:
    """Construct the schema retriever based on ``settings.retrieval_strategy``.

    Strategy resolution
    -------------------
    * ``"keyword"``  — ``InMemoryRetriever`` (keyword/alias scoring).
    * ``"semantic"`` — ``EmbeddingRetriever`` backed by a
      ``CatalogEmbeddingIndexer``; falls back to keyword when embedding
      is not configured (``embedding_base_url`` / ``embedding_model`` empty).
    * ``"hybrid"``   — ``HybridRetriever`` (RRF fusion of keyword +
      semantic); falls back to keyword when embedding is not configured.

    The retriever is always returned (never None) so that
    ``CatalogService.get_relevant_context()`` always uses retrieval
    instead of the full-catalog dump.
    """
    strategy = settings.retrieval_strategy
    has_embedding = bool(settings.embedding_base_url and settings.embedding_model)

    if strategy in ("semantic", "hybrid") and not has_embedding:
        logger.warning(
            "[retriever-factory] strategy=%r requested but "
            "embedding_base_url/embedding_model not configured — "
            "falling back to keyword retrieval",
            strategy,
        )
        strategy = "keyword"

    if strategy == "semantic":
        emb_provider = _build_embedding_provider()
        indexer = _build_catalog_indexer(catalog_provider, emb_provider)
        from app.providers.retrieval.embedding_retriever import EmbeddingRetriever
        retriever = EmbeddingRetriever(catalog_provider, indexer, emb_provider)
        logger.info("[retriever-factory] strategy=semantic")
        return SchemaRetrievalService(retriever)

    if strategy == "hybrid":
        emb_provider = _build_embedding_provider()
        indexer = _build_catalog_indexer(catalog_provider, emb_provider)
        from app.providers.retrieval.embedding_retriever import EmbeddingRetriever
        from app.providers.retrieval.hybrid_retriever import HybridRetriever
        kw_retriever = InMemoryRetriever(catalog_provider)
        sem_retriever = EmbeddingRetriever(catalog_provider, indexer, emb_provider)
        retriever = HybridRetriever(
            kw_retriever, sem_retriever, alpha=settings.retrieval_alpha
        )
        logger.info(
            "[retriever-factory] strategy=hybrid alpha=%.2f",
            settings.retrieval_alpha,
        )
        return SchemaRetrievalService(retriever)

    # Default: keyword
    retriever = InMemoryRetriever(catalog_provider)
    logger.info("[retriever-factory] strategy=keyword")
    return SchemaRetrievalService(retriever)


def _build_embedding_provider():
    """Build the configured embedding provider."""
    from app.providers.embedding.openai_compatible import (
        OpenAICompatibleEmbeddingProvider,
    )
    return OpenAICompatibleEmbeddingProvider(
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        batch_size=settings.embedding_batch_size,
    )


def _build_catalog_indexer(catalog_provider, embedding_provider):
    """Build the catalog embedding indexer."""
    from app.services.catalog_embedding_indexer import CatalogEmbeddingIndexer
    cache_path = Path(settings.catalog_index_cache_path)
    if not cache_path.is_absolute():
        cache_path = Path(__file__).resolve().parents[2] / cache_path
    return CatalogEmbeddingIndexer(catalog_provider, embedding_provider, cache_path)
