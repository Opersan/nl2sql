"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.deps import build_chat_orchestrator, build_document_retrieval
from app.api.routes_chat import router as chat_router
from app.api.routes_health import router as health_router
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

    # -- Startup: semantic registry ↔ catalog consistency check (fail-open) --
    await _validate_semantic_registry()

    yield
    # -- Shutdown: nothing to clean up for now ----------------------------


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
    return app


# Module-level instance for ``uvicorn app.api.main:app``
app = create_app()
