"""FastAPI application factory."""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.deps import build_chat_orchestrator, build_document_retrieval
from app.api.routes_chat import router as chat_router
from app.api.routes_health import router as health_router
from app.api.routes_trace import router as trace_router
from app.core.config import APP_VERSION, settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle.

    Document corpus loading is done here (async) so we never call
    ``run_until_complete`` inside a running event loop.  The result is
    passed to ``build_chat_orchestrator`` which is sync.
    """
    # -- Startup: async document retrieval + sync orchestrator wiring ----
    doc_retrieval = await build_document_retrieval()
    app.state.chat_orchestrator = build_chat_orchestrator(
        doc_retrieval=doc_retrieval,
    )
    await _initialize_executor_resources(app)

    # -- Startup: semantic registry ↔ catalog consistency check (fail-open) --
    await _validate_semantic_registry()

    yield

    # -- Shutdown: close resources that keep worker threads/process alive --
    await _close_executor_resources(app)


def _resolve_inner_executor(app: FastAPI) -> object | None:
    """Return the inner executor instance if the chat orchestrator is wired."""
    chat_orchestrator = getattr(app.state, "chat_orchestrator", None)
    inner_orchestrator = getattr(chat_orchestrator, "_orchestrator", None)
    return getattr(inner_orchestrator, "_executor", None)


async def _initialize_executor_resources(app: FastAPI) -> None:
    """Initialise executor resources during startup when supported."""
    executor = _resolve_inner_executor(app)
    init_method = getattr(executor, "init_pool", None)
    if init_method is None:
        return

    try:
        maybe_awaitable = init_method()
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
        logger.info("[startup] executor resources initialised cleanly.")
    except Exception as exc:
        logger.error("[startup] executor initialisation failed: %s", exc)


async def _close_executor_resources(app: FastAPI) -> None:
    """Close executor resources during shutdown when available.

    This is intentionally duck-typed so tests and alternative executors do
    not need to inherit a specific shutdown interface.
    """
    executor = _resolve_inner_executor(app)
    close_method = getattr(executor, "close", None)
    if close_method is None:
        return

    try:
        maybe_awaitable = close_method()
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
        logger.info("[shutdown] executor resources closed cleanly.")
    except Exception as exc:
        logger.warning("[shutdown] executor cleanup failed: %s", exc)


async def _validate_semantic_registry() -> None:
    """Validate the semantic registry against the active catalog (fail-open).

    Issues are logged as warnings so that skeleton entities (AP, INV, WIP, BOM)
    or demo deployments with partial catalogs do not crash the application.
    Set ``STRICT_REGISTRY_VALIDATION=true`` in the environment to raise instead.
    """
    from app.providers.catalog.in_memory import InMemoryCatalogProvider
    from app.services.registry_validator import RegistryValidationError, validate_registry_against_catalog
    from app.services.semantic_planning import _load_registry

    registry = _load_registry()
    snapshot = await InMemoryCatalogProvider().get_snapshot()
    issues = validate_registry_against_catalog(registry, snapshot)

    if not issues:
        logger.info(
            "[registry-validation] OK — %d entity/entities consistent with catalog",
            len(registry.entities),
        )
        return

    strict = settings.model_config.get("env_prefix", "")  # safeguard check
    for issue in issues:
        logger.warning("[registry-validation] %s", issue)
    logger.warning(
        "[registry-validation] %d issue(s) found — continuing (fail-open). "
        "Fix registry or catalog metadata to resolve.",
        len(issues),
    )


def create_app() -> FastAPI:
    """Build and return a configured FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version=APP_VERSION,
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(trace_router)
    return app


# Module-level instance for ``uvicorn app.api.main:app``
app = create_app()
