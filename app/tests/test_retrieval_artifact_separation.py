from __future__ import annotations

from pathlib import Path

import pytest

from app.core import data_paths
from app.providers.documents.models import DocumentCorpus, ExampleDocument, SchemaDocument
from app.services.example_embedding_indexer import (
    ExampleEmbeddingIndexer,
    build_example_index_documents,
)
from app.services.semantic_embedding_indexer import (
    SemanticEmbeddingIndexer,
    build_semantic_index_documents,
)
from app.semantic.repository import load_semantic_repository


class _FakeEmbeddingProvider:
    model_name = "fake-embedding-model"

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1), 1.0, 0.5] for index, _ in enumerate(texts)]


def test_catalog_source_path_falls_back_to_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(data_paths, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(data_paths, "DEFAULT_CATALOG_SOURCE_PATH", Path("data/catalog/sample_metadata.json"))
    monkeypatch.setattr(data_paths, "LEGACY_CATALOG_SOURCE_PATHS", (Path("data/sample_metadata.json"),))
    legacy = tmp_path / "data" / "sample_metadata.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}", encoding="utf-8")

    resolved, used_legacy = data_paths.resolve_catalog_source_path("data/catalog/sample_metadata.json")

    assert used_legacy is True
    assert resolved == legacy


def test_catalog_index_path_falls_back_to_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(data_paths, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(data_paths, "DEFAULT_CATALOG_INDEX_PATH", Path("data/indexes/catalog/catalog_index.npz"))
    monkeypatch.setattr(data_paths, "LEGACY_CATALOG_INDEX_PATHS", (Path("data/catalog_index.npz"),))
    legacy = tmp_path / "data" / "catalog_index.npz"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy-cache")

    resolved, used_legacy = data_paths.resolve_catalog_index_path(
        "data/indexes/catalog/catalog_index.npz",
        allow_legacy_fallback=True,
    )

    assert used_legacy is True
    assert resolved == legacy


def test_semantic_index_documents_are_built_from_canonical_repository() -> None:
    repository = load_semantic_repository()

    documents = build_semantic_index_documents(repository)
    record_ids = {document.record_id for document in documents}

    assert any(record_id.startswith("entity:") for record_id in record_ids)
    assert any(record_id.startswith("glossary:") for record_id in record_ids)
    assert any(record_id.startswith("metric:") for record_id in record_ids)
    assert any(record_id.startswith("relationship:") for record_id in record_ids)
    assert any(record_id.startswith("lookup:") for record_id in record_ids)
    assert any(record_id.startswith("flexfield:") for record_id in record_ids)


@pytest.mark.asyncio
async def test_semantic_indexer_uses_injected_repository_loader(tmp_path: Path) -> None:
    calls = {"count": 0}
    repository = load_semantic_repository()

    def _loader():
        calls["count"] += 1
        return repository

    indexer = SemanticEmbeddingIndexer(
        _FakeEmbeddingProvider(),
        tmp_path / "semantic_index.npz",
        repository_loader=_loader,
    )

    ok = await indexer.ensure_built()

    assert ok is True
    assert calls["count"] == 1
    assert indexer.record_ids
    meta_path = tmp_path / "semantic_index.meta.json"
    assert meta_path.exists()
    assert "canonical_semantic_repository" in meta_path.read_text(encoding="utf-8")


def test_example_index_documents_remain_separate_from_semantic_records() -> None:
    corpus = DocumentCorpus(
        schema_docs=[
            SchemaDocument(
                doc_id="doc_employee",
                doc_type="table",
                title="Employee table",
                content="Employee overview",
                table_name="XXBT_PDKS_PER_DETAILS_V",
                module="HR",
            )
        ],
        examples=[
            ExampleDocument(
                doc_id="ex_active",
                question="Aktif çalışanları listele",
                sql="SELECT 1 FROM dual",
                tables=["XXBT_PDKS_PER_DETAILS_V"],
                explanation="quit_date null olanlar aktiftir",
            )
        ],
    )

    documents = build_example_index_documents(corpus)
    record_ids = {document.record_id for document in documents}

    assert record_ids == {"schema_doc:doc_employee", "example:ex_active"}


@pytest.mark.asyncio
async def test_example_indexer_builds_from_document_corpus(tmp_path: Path) -> None:
    corpus_path = tmp_path / "sample_schema_documents.jsonl"
    corpus_path.write_text(
        "\n".join([
            '{"doc_type":"table","doc_id":"doc_employee","title":"Employee table","content":"Employee overview","table_name":"XXBT_PDKS_PER_DETAILS_V","module":"HR","tags":["employee"]}',
            '{"doc_type":"example","doc_id":"ex_active","question":"Aktif çalışanları listele","sql":"SELECT 1 FROM dual","tables":["XXBT_PDKS_PER_DETAILS_V"],"explanation":"quit_date null olanlar aktiftir","tags":["aktif"]}',
        ]),
        encoding="utf-8",
    )

    indexer = ExampleEmbeddingIndexer(_FakeEmbeddingProvider(), tmp_path / "example_index.npz")

    ok = await indexer.ensure_built(corpus_path)

    assert ok is True
    assert set(indexer.record_ids) == {"schema_doc:doc_employee", "example:ex_active"}
    meta_path = tmp_path / "example_index.meta.json"
    assert meta_path.exists()
    assert "example_document_corpus" in meta_path.read_text(encoding="utf-8")
