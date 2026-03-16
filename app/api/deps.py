"""Dependency injection helpers for the API layer."""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.catalog.in_memory import InMemoryCatalogProvider
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
) -> ChatOrchestrator:
    """Wire up the full dependency graph for the chat pipeline.

    Parameters
    ----------
    doc_retrieval:
        Pre-built document retrieval service.  When ``None`` the planner
        falls back to structured catalog context only.  Callers should
        build this via :func:`build_document_retrieval` **before** calling
        this function (typically in the async lifespan handler).
    """
    llm = build_llm_provider()

    # Catalog provider
    catalog_provider = InMemoryCatalogProvider()

    # Schema retrieval (Sprint 3)
    retrieval: SchemaRetrievalService | None = None
    if settings.enable_metadata_retrieval:
        retriever = InMemoryRetriever(catalog_provider)
        retrieval = SchemaRetrievalService(retriever)

    catalog = CatalogService(catalog_provider, retrieval=retrieval)

    # Sprint 1 deterministic core
    validator = ValidationService(catalog)
    compiler = SQLCompiler()
    executor = _build_executor()
    orchestrator = Orchestrator(validator, compiler, executor)

    # Sprint 2 LLM services
    planner = PlannerService(llm, catalog, doc_retrieval=doc_retrieval)
    narrator = NarratorService(llm)
    sessions = SessionService()

    return ChatOrchestrator(planner, orchestrator, narrator, sessions)
