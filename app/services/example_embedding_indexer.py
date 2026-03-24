"""Example/document embedding index builder.

Builds a retrieval artefact from the approved schema-document and example
corpus. This layer is intentionally separate from both technical catalog
metadata and the canonical semantic repository.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from app.core.logging import get_logger
from app.providers.documents.jsonl_loader import JSONLDocumentLoader
from app.providers.documents.models import DocumentCorpus
from app.providers.embedding.base import EmbeddingProvider

logger = get_logger(__name__)


@dataclass(frozen=True)
class ExampleIndexDocument:
    record_id: str
    record_type: str
    text: str


def example_corpus_fingerprint(corpus: DocumentCorpus) -> str:
    payload = corpus.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def build_example_index_documents(corpus: DocumentCorpus) -> list[ExampleIndexDocument]:
    documents: list[ExampleIndexDocument] = []

    for doc in corpus.schema_docs:
        documents.append(
            ExampleIndexDocument(
                record_id=f"schema_doc:{doc.doc_id}",
                record_type=f"schema_{doc.doc_type.value}",
                text=" ".join(
                    part
                    for part in [
                        doc.title,
                        doc.content,
                        doc.table_name or "",
                        doc.column_name or "",
                        doc.module or "",
                        " ".join(doc.tags),
                    ]
                    if part
                ),
            )
        )

    for example in corpus.examples:
        documents.append(
            ExampleIndexDocument(
                record_id=f"example:{example.doc_id}",
                record_type="example",
                text=" ".join(
                    part
                    for part in [
                        example.question,
                        example.explanation or "",
                        " ".join(example.tables),
                        " ".join(example.tags),
                    ]
                    if part
                ),
            )
        )

    return documents


class ExampleEmbeddingIndexer:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        cache_path: str | Path,
        *,
        strict: bool = True,
    ) -> None:
        self._emb = embedding_provider
        self._cache_path = Path(cache_path)
        self._meta_path = self._cache_path.with_suffix("").with_name(
            self._cache_path.stem + ".meta.json"
        )
        self._strict = strict
        self._record_ids: list[str] = []
        self._matrix = None
        self._loaded_fp: str | None = None

    @property
    def record_ids(self) -> list[str]:
        return list(self._record_ids)

    def get_matrix(self):  # type: ignore[return]
        return self._matrix

    async def ensure_built(self, corpus_path: str | Path) -> bool:
        try:
            import numpy as np  # noqa: F401
        except ImportError:
            logger.warning("[example-indexer] numpy not installed — index unavailable")
            return False

        loader = JSONLDocumentLoader(strict=self._strict)
        corpus = await loader.load(corpus_path)
        fp = example_corpus_fingerprint(corpus)

        if self._matrix is not None and self._loaded_fp == fp:
            return True
        if self._try_load_cache(fp):
            return True
        return await self._build(corpus, fp)

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
                "[example-indexer] loaded index from cache: %d records, model=%r",
                len(self._record_ids),
                self._emb.model_name,
            )
            return True
        except Exception:
            logger.exception("[example-indexer] failed to load cache from %s", self._cache_path)
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
            "source": "example_document_corpus",
        }
        self._meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    async def _build(self, corpus: DocumentCorpus, fp: str) -> bool:
        try:
            import numpy as np
        except ImportError:
            return False

        documents = build_example_index_documents(corpus)
        texts = [document.text for document in documents]
        if not texts:
            logger.warning("[example-indexer] document corpus is empty — no index built")
            return False

        try:
            vectors = await self._emb.embed_texts(texts)
        except Exception:
            logger.exception("[example-indexer] embedding call failed — index unavailable")
            return False

        matrix = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        self._matrix = matrix / norms
        self._record_ids = [document.record_id for document in documents]
        self._loaded_fp = fp
        self._save_cache(fp)
        logger.info(
            "[example-indexer] built index for %d example/doc records (fp=%s)",
            len(self._record_ids),
            fp,
        )
        return True
