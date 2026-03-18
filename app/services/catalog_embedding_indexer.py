"""Catalog embedding indexer.

Builds and caches a table-level embedding index so the EmbeddingRetriever
can perform fast cosine-similarity lookup without calling the embedding
server on every request.

Cache strategy
--------------
* Index is stored as a compressed NumPy archive (``.npz``) alongside a
  JSON metadata sidecar (``<path>.meta.json``).
* The cache is keyed by the catalog fingerprint (SHA-256[:12] over sorted
  table:column names, from ``in_memory.catalog_fingerprint``).
* On startup the indexer tries to load the cache; if the fingerprint
  matches it serves from cache without any network call.
* When the catalog changes (new tables/columns) or the cache is missing,
  the indexer rebuilds asynchronously.

Text representation
-------------------
Each table is represented as:
    "<name> <description> <aliases> <first 20 column names>"
This produces short, focused vectors that favour table-name matching
while including semantic description text.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.logging import get_logger
from app.domain.catalog_models import CatalogSnapshot
from app.providers.catalog.base import CatalogProvider
from app.providers.catalog.in_memory import catalog_fingerprint
from app.providers.embedding.base import EmbeddingProvider

logger = get_logger(__name__)

_MAX_COLS_IN_TEXT = 20  # Column names included in table text


def _table_text(table) -> str:  # type: ignore[no-untyped-def]
    """Build a short textual representation of a table for embedding."""
    parts = [table.name]
    if table.description:
        parts.append(table.description)
    if table.aliases:
        parts.append(" ".join(table.aliases))
    col_names = [c.name for c in table.columns[:_MAX_COLS_IN_TEXT]]
    if col_names:
        parts.append(" ".join(col_names))
    return " ".join(parts)


class CatalogEmbeddingIndexer:
    """Manages a dense embedding index over catalog tables.

    Parameters
    ----------
    catalog_provider:
        Supplies the live catalog snapshot.
    embedding_provider:
        Produces embedding vectors for text.
    cache_path:
        Path to the ``.npz`` cache file.  A sidecar ``.meta.json`` is
        written next to it.  The directory is created if needed.
    """

    def __init__(
        self,
        catalog_provider: CatalogProvider,
        embedding_provider: EmbeddingProvider,
        cache_path: str | Path,
    ) -> None:
        self._catalog = catalog_provider
        self._emb = embedding_provider
        self._cache_path = Path(cache_path)
        self._meta_path = self._cache_path.with_suffix("").with_name(
            self._cache_path.stem + ".meta.json"
        )
        # Loaded index state
        self._table_names: list[str] = []
        self._matrix = None  # np.ndarray | None, shape (n_tables, dim)
        self._loaded_fp: str | None = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def ensure_built(self) -> bool:
        """Ensure the index is built and ready for queries.

        Returns True if the index is available (from cache or freshly built),
        False if building failed (embedding server unreachable, etc.).
        """
        try:
            import numpy as np  # noqa: F401  (presence check only here)
        except ImportError:
            logger.warning(
                "[embedding-indexer] numpy not installed — "
                "semantic retrieval disabled"
            )
            return False

        snapshot = await self._catalog.get_snapshot()
        fp = catalog_fingerprint(snapshot)

        if self._matrix is not None and self._loaded_fp == fp:
            return True  # already up-to-date in memory

        if self._try_load_cache(fp):
            return True

        return await self._build(snapshot, fp)

    @property
    def is_ready(self) -> bool:
        return self._matrix is not None

    @property
    def table_names(self) -> list[str]:
        return list(self._table_names)

    def get_matrix(self):  # type: ignore[return]
        """Return the embedding matrix (n_tables × dim) or None."""
        return self._matrix

    # ------------------------------------------------------------------ #
    # Cache                                                                #
    # ------------------------------------------------------------------ #

    def _try_load_cache(self, expected_fp: str) -> bool:
        """Attempt to load index from disk cache.  Returns True on hit.

        Cache is invalidated when either the catalog fingerprint **or** the
        embedding model name changes.  This prevents silent dim mismatches
        when switching between models (e.g. bge-m3 → Qwen3-Embedding-8B).
        """
        try:
            import numpy as np
        except ImportError:
            return False

        if not self._cache_path.exists() or not self._meta_path.exists():
            return False

        try:
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))

            if meta.get("fingerprint") != expected_fp:
                logger.info(
                    "[embedding-indexer] cache catalog-fingerprint mismatch "
                    "(cached=%s, current=%s) — will rebuild",
                    meta.get("fingerprint"),
                    expected_fp,
                )
                return False

            current_model = self._emb.model_name
            if meta.get("embedding_model") != current_model:
                logger.info(
                    "[embedding-indexer] cache embedding-model mismatch "
                    "(cached=%r, current=%r) — will rebuild",
                    meta.get("embedding_model"),
                    current_model,
                )
                return False

            archive = np.load(str(self._cache_path))
            self._matrix = archive["matrix"]
            self._table_names = list(meta["table_names"])
            self._loaded_fp = expected_fp
            logger.info(
                "[embedding-indexer] loaded index from cache: "
                "%d tables, dim=%d, model=%r, fp=%s",
                len(self._table_names),
                self._matrix.shape[1],
                current_model,
                expected_fp,
            )
            return True
        except Exception:
            logger.exception(
                "[embedding-indexer] failed to load cache from %s",
                self._cache_path,
            )
            return False

    def _save_cache(self, fp: str) -> None:
        """Persist the current index to disk."""
        try:
            import numpy as np
        except ImportError:
            return

        if self._matrix is None:
            return

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(str(self._cache_path), matrix=self._matrix)
        meta = {
            "fingerprint": fp,
            "embedding_model": self._emb.model_name,
            "table_names": self._table_names,
        }
        self._meta_path.write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(
            "[embedding-indexer] saved index to %s (%d tables, model=%r)",
            self._cache_path,
            len(self._table_names),
            self._emb.model_name,
        )

    # ------------------------------------------------------------------ #
    # Build                                                                #
    # ------------------------------------------------------------------ #

    async def _build(self, snapshot: CatalogSnapshot, fp: str) -> bool:
        """Embed all tables and store in memory + cache."""
        try:
            import numpy as np
        except ImportError:
            return False

        texts = [_table_text(t) for t in snapshot.tables]
        names = [t.name for t in snapshot.tables]

        if not texts:
            logger.warning("[embedding-indexer] catalog is empty — no index built")
            return False

        logger.info(
            "[embedding-indexer] building index for %d tables (fp=%s)",
            len(texts),
            fp,
        )

        try:
            vectors = await self._emb.embed_texts(texts)
        except Exception:
            logger.exception("[embedding-indexer] embedding call failed — index unavailable")
            return False

        matrix = np.array(vectors, dtype=np.float32)  # (n_tables, dim)
        # L2-normalise each row for fast cosine similarity via dot product
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        matrix = matrix / norms

        self._matrix = matrix
        self._table_names = names
        self._loaded_fp = fp
        self._save_cache(fp)
        return True
