"""Tests for Sprint 3 configuration settings."""

from __future__ import annotations

from app.core.config import APP_VERSION, Settings


class TestSprintThreeConfig:
    """Verify that Sprint 3 config fields have sane defaults.

    Phase 2 + Phase 4 change the defaults so that the bundled sample files
    are wired automatically:
      * metadata_source_type  → 'json'
      * metadata_source_path  → 'data/sample_metadata.json'
      * enable_document_retrieval → True
      * document_corpus_path  → 'data/sample_schema_documents.jsonl'
    """

    def test_metadata_defaults(self) -> None:
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.metadata_source_type == "json"
        assert s.metadata_source_path == "data/sample_metadata.json"
        assert s.enable_metadata_retrieval is True

    def test_document_retrieval_defaults(self) -> None:
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.enable_document_retrieval is True
        assert s.document_corpus_path == "data/sample_schema_documents.jsonl"

    def test_retrieval_defaults(self) -> None:
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.retrieval_top_k == 7
        assert s.retrieval_strategy == "keyword"
        assert s.enable_column_prune is True
        assert s.retrieval_alpha == 0.5
        assert s.catalog_index_cache_path == "data/catalog_index.npz"
        assert s.embedding_base_url == ""
        assert s.embedding_model == ""

    def test_oracle_defaults(self, monkeypatch) -> None:
        # Clear oracle env vars so we test the code defaults, not .env overrides.
        for var in ("ORACLE_DSN", "ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_TIMEOUT",
                    "ENABLE_ORACLE_EXECUTOR"):
            monkeypatch.delenv(var, raising=False)
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.enable_oracle_executor is False
        assert s.oracle_dsn == ""
        assert s.oracle_user == ""
        assert s.oracle_password == ""
        assert s.oracle_timeout == 30

    def test_version_is_0_3(self) -> None:
        assert APP_VERSION == "0.3.0"

    def test_existing_defaults_preserved(self) -> None:
        """Sprint 1-2 defaults must not change."""
        s = Settings()
        assert s.default_row_limit == 100
        assert s.max_row_limit == 1000
        assert s.llm_provider == "mock"
        assert s.enable_sql_in_api_response is True
        assert s.max_rows_preview == 20

