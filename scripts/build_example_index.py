"""Build the example/document embedding index from the approved corpus."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.deps import _build_embedding_provider
from app.core.config import settings
from app.core.data_paths import (
    resolve_document_source_path,
    resolve_example_index_path,
)
from app.core.logging import get_logger
from app.providers.documents.jsonl_loader import JSONLDocumentLoader
from app.services.example_embedding_indexer import ExampleEmbeddingIndexer

logger = get_logger(__name__)


async def _run(cache_path: Path | None, corpus_path: Path | None, dry_run: bool) -> int:
    resolved_corpus, used_legacy = resolve_document_source_path(
        corpus_path or settings.document_corpus_path
    )
    if not resolved_corpus.exists():
        logger.error("Document corpus file not found: %s", resolved_corpus)
        return 1

    loader = JSONLDocumentLoader(strict=settings.document_loader_strict)
    corpus = await loader.load(resolved_corpus)
    if used_legacy:
        logger.warning(
            "[example-index] new example/doc source path not found; using legacy path %s",
            resolved_corpus,
        )

    logger.info(
        "Document corpus loaded: %d schema docs, %d examples",
        len(corpus.schema_docs),
        len(corpus.examples),
    )

    if dry_run:
        logger.info("--dry-run: stopping before embedding call")
        for doc in corpus.schema_docs[:5]:
            print(f"  schema:{doc.doc_id} ({doc.doc_type.value})")
        for example in corpus.examples[:5]:
            print(f"  example:{example.doc_id}")
        return 0

    has_embedding = bool(settings.embedding_base_url and settings.embedding_model)
    if not has_embedding:
        logger.error(
            "embedding_base_url and embedding_model must be set "
            "(via env vars EMBEDDING_BASE_URL / EMBEDDING_MODEL)"
        )
        return 1

    effective_cache = cache_path or resolve_example_index_path(settings.example_index_cache_path)
    if not effective_cache.is_absolute():
        effective_cache = Path(__file__).resolve().parents[1] / effective_cache

    indexer = ExampleEmbeddingIndexer(
        _build_embedding_provider(),
        effective_cache,
        strict=settings.document_loader_strict,
    )
    ok = await indexer.ensure_built(resolved_corpus)
    if ok:
        logger.info("Example/doc index built successfully: %s", effective_cache)
        return 0
    logger.error("Example/doc index build failed — check embedding server logs")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Build example/document embedding index")
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help="Override the cache .npz path (default: settings.example_index_cache_path)",
    )
    parser.add_argument(
        "--corpus-path",
        type=Path,
        default=None,
        help="Override the document corpus path (default: settings.document_corpus_path)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List example/doc ids only, skip embedding call",
    )
    rc = asyncio.run(_run(**vars(parser.parse_args())))
    sys.exit(rc)


if __name__ == "__main__":
    main()
