"""Offline catalog embedding index builder.

Run this script whenever the catalog changes (new tables, updated
descriptions) to pre-build the embedding index cache so the first
API request doesn't trigger an embedding call.

Usage
-----
    # keyword-only (no embedding server needed)
    python scripts/build_catalog_index.py --dry-run

    # with embedding server
    EMBEDDING_BASE_URL=http://server:8100/v1 \\
    EMBEDDING_MODEL=BAAI/bge-m3 \\
    python scripts/build_catalog_index.py

    # override cache path
    python scripts/build_catalog_index.py --cache-path data/my_index.npz

The script uses the same settings as the application (env vars / .env).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running from repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.deps import _build_catalog_provider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def _run(cache_path: Path | None, dry_run: bool) -> int:
    catalog_provider = _build_catalog_provider()
    snapshot = await catalog_provider.get_snapshot()

    logger.info(
        "Catalog loaded: %d tables, source_type=%r",
        len(snapshot.tables),
        settings.metadata_source_type,
    )

    if dry_run:
        logger.info("--dry-run: stopping before embedding call")
        for table in snapshot.tables:
            print(f"  {table.name} ({len(table.columns)} columns)")
        return 0

    has_embedding = bool(settings.embedding_base_url and settings.embedding_model)
    if not has_embedding:
        logger.error(
            "embedding_base_url and embedding_model must be set "
            "(via env vars EMBEDDING_BASE_URL / EMBEDDING_MODEL)"
        )
        return 1

    from app.api.deps import _build_embedding_provider
    from app.services.catalog_embedding_indexer import CatalogEmbeddingIndexer

    emb_provider = _build_embedding_provider()
    effective_cache = cache_path or (
        Path(settings.catalog_index_cache_path)
        if not Path(settings.catalog_index_cache_path).is_absolute()
        else Path(settings.catalog_index_cache_path)
    )
    if not effective_cache.is_absolute():
        effective_cache = Path(__file__).resolve().parents[1] / effective_cache

    indexer = CatalogEmbeddingIndexer(catalog_provider, emb_provider, effective_cache)
    ok = await indexer.ensure_built()
    if ok:
        logger.info("Index built successfully: %s", effective_cache)
        return 0
    else:
        logger.error("Index build failed — check embedding server logs")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Build catalog embedding index")
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help="Override the cache .npz path (default: settings.catalog_index_cache_path)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List tables only, skip embedding call",
    )
    args = parser.parse_args()
    rc = asyncio.run(_run(args.cache_path, args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()
