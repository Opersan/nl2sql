"""Semantic embedding index builder.

Builds a retrieval artefact from the canonical semantic repository.
This index is derived from the authoritative semantic source-of-truth and
must not introduce a parallel semantic registry or planner path.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable

from app.core.logging import get_logger
from app.semantic.repository import SemanticRepository, load_semantic_repository
from app.providers.embedding.base import EmbeddingProvider

logger = get_logger(__name__)


@dataclass(frozen=True)
class SemanticIndexDocument:
    record_id: str
    record_type: str
    text: str


def semantic_repository_fingerprint(repository: SemanticRepository) -> str:
    payload = repository.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def build_semantic_index_documents(
    repository: SemanticRepository,
) -> list[SemanticIndexDocument]:
    documents: list[SemanticIndexDocument] = []

    for entity in repository.entities:
        text = " ".join(
            part
            for part in [
                entity.entity_id,
                entity.display_name,
                entity.module,
                entity.root_table,
                " ".join(entity.default_tables),
                " ".join(entity.child_tables),
                " ".join(entity.keywords),
                " ".join(entity.likely_filters),
                " ".join(entity.likely_identifiers),
            ]
            if part
        )
        documents.append(
            SemanticIndexDocument(
                record_id=f"entity:{entity.entity_id}",
                record_type="entity",
                text=text,
            )
        )

    for entry in repository.glossary:
        documents.append(
            SemanticIndexDocument(
                record_id=f"glossary:{entry.normalized}:{entry.canonical}",
                record_type="glossary",
                text=" ".join(
                    part
                    for part in [
                        entry.raw_term,
                        entry.normalized,
                        entry.canonical,
                        entry.domain,
                        entry.source,
                    ]
                    if part
                ),
            )
        )

    for metric in repository.metrics:
        documents.append(
            SemanticIndexDocument(
                record_id=f"metric:{metric.metric_id}",
                record_type="metric",
                text=" ".join(
                    part
                    for part in [
                        metric.metric_id,
                        metric.name,
                        metric.entity_id,
                        metric.domain,
                        " ".join(metric.aliases),
                        metric.function or "",
                        metric.expression or "",
                        metric.table or "",
                        metric.column or "",
                        metric.description or "",
                    ]
                    if part
                ),
            )
        )

    for relationship in repository.relationships:
        join_keys = " ".join(
            f"{pair.source_column}:{pair.target_column}" for pair in relationship.join_keys
        )
        documents.append(
            SemanticIndexDocument(
                record_id=f"relationship:{relationship.edge_id}",
                record_type="relationship",
                text=" ".join(
                    part
                    for part in [
                        relationship.edge_id,
                        relationship.source_entity,
                        relationship.target_entity,
                        relationship.source_table,
                        relationship.target_table,
                        relationship.join_direction,
                        join_keys,
                    ]
                    if part
                ),
            )
        )

    for lookup in repository.lookups:
        documents.append(
            SemanticIndexDocument(
                record_id=f"lookup:{lookup.lookup_type}:{lookup.raw_value}",
                record_type="lookup",
                text=" ".join(
                    part
                    for part in [
                        lookup.lookup_type,
                        lookup.raw_value,
                        lookup.decoded_value,
                        lookup.meaning,
                        lookup.domain,
                    ]
                    if part
                ),
            )
        )

    for flexfield in repository.flexfields:
        documents.append(
            SemanticIndexDocument(
                record_id=f"flexfield:{flexfield.flexfield_id}",
                record_type="flexfield",
                text=" ".join(
                    part
                    for part in [
                        flexfield.flexfield_id,
                        flexfield.name,
                        flexfield.application,
                        flexfield.table,
                        flexfield.segment_column,
                        flexfield.module or "",
                    ]
                    if part
                ),
            )
        )

    return documents


class SemanticEmbeddingIndexer:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        cache_path: str | Path,
        *,
        repository_loader: Callable[..., SemanticRepository] = load_semantic_repository,
    ) -> None:
        self._emb = embedding_provider
        self._cache_path = Path(cache_path)
        self._meta_path = self._cache_path.with_suffix("").with_name(
            self._cache_path.stem + ".meta.json"
        )
        self._repository_loader = repository_loader
        self._record_ids: list[str] = []
        self._matrix = None
        self._loaded_fp: str | None = None

    @property
    def record_ids(self) -> list[str]:
        return list(self._record_ids)

    def get_matrix(self):  # type: ignore[return]
        return self._matrix

    async def ensure_built(self) -> bool:
        try:
            import numpy as np  # noqa: F401
        except ImportError:
            logger.warning("[semantic-indexer] numpy not installed — index unavailable")
            return False

        repository = self._repository_loader()
        fp = semantic_repository_fingerprint(repository)

        if self._matrix is not None and self._loaded_fp == fp:
            return True

        if self._try_load_cache(fp):
            return True

        return await self._build(repository, fp)

    def _try_load_cache(self, expected_fp: str) -> bool:
        try:
            import numpy as np
        except ImportError:
            return False

        if not self._cache_path.exists() or not self._meta_path.exists():
            return False

        try:
            meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            if meta.get("fingerprint") != expected_fp:
                return False
            if meta.get("embedding_model") != self._emb.model_name:
                return False
            archive = np.load(str(self._cache_path))
            self._matrix = archive["matrix"]
            self._record_ids = list(meta["record_ids"])
            self._loaded_fp = expected_fp
            logger.info(
                "[semantic-indexer] loaded index from cache: %d records, model=%r",
                len(self._record_ids),
                self._emb.model_name,
            )
            return True
        except Exception:
            logger.exception("[semantic-indexer] failed to load cache from %s", self._cache_path)
            return False

    def _save_cache(self, fp: str) -> None:
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
            "record_ids": self._record_ids,
            "source": "canonical_semantic_repository",
        }
        self._meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    async def _build(self, repository: SemanticRepository, fp: str) -> bool:
        try:
            import numpy as np
        except ImportError:
            return False

        documents = build_semantic_index_documents(repository)
        texts = [document.text for document in documents]
        if not texts:
            logger.warning("[semantic-indexer] semantic repository is empty — no index built")
            return False

        try:
            vectors = await self._emb.embed_texts(texts)
        except Exception:
            logger.exception("[semantic-indexer] embedding call failed — index unavailable")
            return False

        matrix = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        self._matrix = matrix / norms
        self._record_ids = [document.record_id for document in documents]
        self._loaded_fp = fp
        self._save_cache(fp)
        logger.info(
            "[semantic-indexer] built index for %d semantic records (fp=%s)",
            len(self._record_ids),
            fp,
        )
        return True
