"""Build the semantic embedding index from the canonical semantic repository."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.deps import _build_embedding_provider
from app.core.config import settings
from app.core.data_paths import resolve_semantic_index_path
from app.core.logging import get_logger
from app.services.semantic_embedding_indexer import SemanticEmbeddingIndexer

logger = get_logger(__name__)


async def _run(cache_path: Path | None, dry_run: bool) -> int:
    from app.semantic.repository import get_semantic_repository

    repository = get_semantic_repository()
    logger.info(
        "Semantic repository loaded: %d entities, %d glossary entries, %d metrics",
        len(repository.entities),
        len(repository.glossary),
        len(repository.metrics),
    )

    if dry_run:
        logger.info("--dry-run: stopping before embedding call")
        for entity in repository.entities:
            print(f"  {entity.entity_id} ({entity.root_table})")
        return 0

    has_embedding = bool(settings.embedding_base_url and settings.embedding_model)
    if not has_embedding:
        logger.error(
            "embedding_base_url and embedding_model must be set "
            "(via env vars EMBEDDING_BASE_URL / EMBEDDING_MODEL)"
        )
        return 1

    effective_cache = cache_path or resolve_semantic_index_path(settings.semantic_index_cache_path)
    if not effective_cache.is_absolute():
        effective_cache = Path(__file__).resolve().parents[1] / effective_cache

    indexer = SemanticEmbeddingIndexer(_build_embedding_provider(), effective_cache)
    ok = await indexer.ensure_built()
    if ok:
        logger.info("Semantic index built successfully: %s", effective_cache)
        return 0
    logger.error("Semantic index build failed — check embedding server logs")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Build semantic embedding index")
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help="Override the cache .npz path (default: settings.semantic_index_cache_path)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List semantic entities only, skip embedding call",
    )
    rc = asyncio.run(_run(**vars(parser.parse_args())))
    sys.exit(rc)


if __name__ == "__main__":
    main()
