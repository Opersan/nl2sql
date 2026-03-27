"""Startup / dependency wiring tests for document retrieval.

Verifies fail-open behaviour: the application must start regardless of
document corpus availability.  The structured catalog layer is the
source-of-truth — document retrieval is auxiliary context.

All ``build_document_retrieval`` tests are **async** because the function
is an ``async def`` that awaits the JSONL loader — exactly as it runs
inside the FastAPI lifespan handler.

Settings are patched via ``monkeypatch.setattr`` on the real singleton
(not ``MagicMock``) to avoid flaky attribute-forwarding issues.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import build_chat_orchestrator, build_document_retrieval
from app.api.main import create_app
from app.core.config import settings
from app.services.document_retrieval_service import DocumentRetrievalService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Settings that every test must set explicitly so that no real env-var
# bleeds into the test run.  Keys are ``Settings`` field names; values
# are safe defaults for unit tests.
_SAFE_DEFAULTS: dict[str, object] = {
    "enable_document_retrieval": False,
    "document_corpus_path": "",
    "document_loader_strict": True,
    "llm_provider": "mock",
    "enable_metadata_retrieval": False,
    "enable_oracle_executor": False,
    "planner_prompt_max_chars": 12_000,
    "retrieval_top_k_examples": 2,
}


@pytest.fixture
def _safe_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply ``_SAFE_DEFAULTS`` to the real ``settings`` singleton.

    Individual tests override only the fields they care about via
    additional ``monkeypatch.setattr`` calls.
    """
    for key, value in _SAFE_DEFAULTS.items():
        monkeypatch.setattr(settings, key, value)


def _write_valid_corpus(path: Path) -> Path:
    """Write a minimal valid JSONL corpus file."""
    corpus_file = path / "corpus.jsonl"
    lines = [
        {
            "doc_type": "table",
            "doc_id": "t1",
            "title": "Employee tablosu",
            "content": "Personel bilgileri",
            "table_name": "XXBT_PDKS_PER_DETAILS_V",
        },
        {
            "doc_type": "example",
            "doc_id": "ex1",
            "question": "Aktif çalışanlar",
            "sql": "SELECT reg_no FROM employee WHERE quit_date IS NULL",
            "tables": ["XXBT_PDKS_PER_DETAILS_V"],
        },
    ]
    corpus_file.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )
    return corpus_file


def _write_invalid_corpus(path: Path) -> Path:
    """Write a corpus file with malformed JSON."""
    corpus_file = path / "bad_corpus.jsonl"
    corpus_file.write_text("NOT VALID JSON\n{broken", encoding="utf-8")
    return corpus_file


# ---------------------------------------------------------------------------
# build_document_retrieval — async unit tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_safe_settings")
class TestBuildDocumentRetrieval:
    """Async unit tests for :func:`build_document_retrieval`."""

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self) -> None:
        """When enable_document_retrieval=False, no service is created."""
        # _safe_settings already sets enable_document_retrieval=False.
        result = await build_document_retrieval()
        assert result is None

    @pytest.mark.asyncio
    async def test_enabled_no_path_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Enabled but empty path → fail-open → None."""
        monkeypatch.setattr(settings, "enable_document_retrieval", True)
        monkeypatch.setattr(settings, "document_corpus_path", "")
        result = await build_document_retrieval()
        assert result is None

    @pytest.mark.asyncio
    async def test_enabled_missing_file_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Enabled but corpus file absent → fail-open → None."""
        monkeypatch.setattr(settings, "enable_document_retrieval", True)
        monkeypatch.setattr(
            settings, "document_corpus_path", "/tmp/nonexistent_corpus.jsonl"
        )
        result = await build_document_retrieval()
        assert result is None

    @pytest.mark.asyncio
    async def test_enabled_valid_corpus(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Enabled + valid corpus → returns DocumentRetrievalService."""
        corpus_file = _write_valid_corpus(tmp_path)
        monkeypatch.setattr(settings, "enable_document_retrieval", True)
        monkeypatch.setattr(settings, "document_corpus_path", str(corpus_file))
        monkeypatch.setattr(settings, "document_loader_strict", True)
        result = await build_document_retrieval()
        assert isinstance(result, DocumentRetrievalService)

    @pytest.mark.asyncio
    async def test_enabled_invalid_corpus_strict_failopen(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Enabled + strict + malformed corpus → fail-open → None.

        ``document_loader_strict`` controls *line-level* validation, but
        the app startup itself is always fail-open.
        """
        corpus_file = _write_invalid_corpus(tmp_path)
        monkeypatch.setattr(settings, "enable_document_retrieval", True)
        monkeypatch.setattr(settings, "document_corpus_path", str(corpus_file))
        monkeypatch.setattr(settings, "document_loader_strict", True)
        result = await build_document_retrieval()
        assert result is None

    @pytest.mark.asyncio
    async def test_enabled_invalid_corpus_lenient_returns_empty_service(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Enabled + lenient + all-bad lines → service with empty corpus.

        The lenient JSONL loader skips malformed lines and returns a valid
        (but empty) ``DocumentCorpus``.  ``build_document_retrieval``
        wraps it in a ``DocumentRetrievalService`` — it does **not**
        return ``None``.
        """
        corpus_file = _write_invalid_corpus(tmp_path)
        monkeypatch.setattr(settings, "enable_document_retrieval", True)
        monkeypatch.setattr(settings, "document_corpus_path", str(corpus_file))
        monkeypatch.setattr(settings, "document_loader_strict", False)
        result = await build_document_retrieval()
        assert isinstance(result, DocumentRetrievalService)


# ---------------------------------------------------------------------------
# build_chat_orchestrator — sync, accepts pre-built doc_retrieval
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_safe_settings")
class TestChatOrchestratorWiring:
    """Integration tests verifying ``build_chat_orchestrator`` wiring."""

    def test_orchestrator_works_without_doc_retrieval(self) -> None:
        """Default (no doc retrieval) → orchestrator OK."""
        orch = build_chat_orchestrator()
        assert orch is not None
        assert orch._planner._doc_retrieval is None

    def test_orchestrator_accepts_doc_retrieval(self) -> None:
        """Pre-built doc_retrieval is forwarded to Planner."""
        from app.providers.documents.models import DocumentCorpus
        from app.providers.retrieval.in_memory_doc_retriever import (
            InMemoryDocumentRetriever,
        )

        corpus = DocumentCorpus(schema_docs=[], examples=[])
        retriever = InMemoryDocumentRetriever(corpus)
        doc_svc = DocumentRetrievalService(retriever)

        orch = build_chat_orchestrator(doc_retrieval=doc_svc)
        assert orch._planner._doc_retrieval is doc_svc


# ---------------------------------------------------------------------------
# Lifespan integration tests — startup wiring via the real FastAPI app
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_safe_settings")
class TestLifespanIntegration:
    """Verify the async lifespan wires everything correctly.

    Uses ``TestClient`` context manager which triggers the lifespan
    (startup → shutdown), just like ``test_api_smoke.py``.
    """

    def test_lifespan_disabled_doc_retrieval(self) -> None:
        """App starts with doc retrieval disabled."""
        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert app.state.chat_orchestrator._planner._doc_retrieval is None

    def test_lifespan_valid_corpus(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """App starts with a valid corpus → planner gets doc_retrieval."""
        corpus_file = _write_valid_corpus(tmp_path)
        monkeypatch.setattr(settings, "enable_document_retrieval", True)
        monkeypatch.setattr(settings, "document_corpus_path", str(corpus_file))
        monkeypatch.setattr(settings, "document_loader_strict", True)

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert (
                app.state.chat_orchestrator._planner._doc_retrieval is not None
            )

    def test_lifespan_missing_corpus_failopen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing corpus → app still starts (fail-open)."""
        monkeypatch.setattr(settings, "enable_document_retrieval", True)
        monkeypatch.setattr(
            settings, "document_corpus_path", "/nonexistent/corpus.jsonl"
        )

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert app.state.chat_orchestrator._planner._doc_retrieval is None

    def test_lifespan_invalid_corpus_failopen(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Invalid corpus → app starts with doc_retrieval=None."""
        corpus_file = _write_invalid_corpus(tmp_path)
        monkeypatch.setattr(settings, "enable_document_retrieval", True)
        monkeypatch.setattr(settings, "document_corpus_path", str(corpus_file))
        monkeypatch.setattr(settings, "document_loader_strict", True)

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert app.state.chat_orchestrator._planner._doc_retrieval is None

    def test_lifespan_empty_path_failopen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty corpus path → app starts with doc_retrieval=None."""
        monkeypatch.setattr(settings, "enable_document_retrieval", True)
        monkeypatch.setattr(settings, "document_corpus_path", "")

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert app.state.chat_orchestrator._planner._doc_retrieval is None

    def test_lifespan_shutdown_closes_executor_resources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Shutdown must close the executor so worker threads do not keep the app alive."""

        calls: list[str] = []

        class _FakeExecutor:
            async def close(self) -> None:
                calls.append("closed")

        fake_chat_orchestrator = SimpleNamespace(
            _planner=SimpleNamespace(_doc_retrieval=None),
            _orchestrator=SimpleNamespace(_executor=_FakeExecutor()),
        )

        monkeypatch.setattr("app.api.main.build_document_retrieval", _fake_build_document_retrieval)
        monkeypatch.setattr(
            "app.api.main.build_chat_orchestrator",
            lambda *, doc_retrieval=None: fake_chat_orchestrator,
        )

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

        assert calls == ["closed"]

    def test_lifespan_startup_initializes_executor_resources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Startup must initialise the executor pool when the executor supports it."""

        calls: list[str] = []

        class _FakeExecutor:
            async def init_pool(self) -> None:
                calls.append("init")

            async def close(self) -> None:
                calls.append("close")

        fake_chat_orchestrator = SimpleNamespace(
            _planner=SimpleNamespace(_doc_retrieval=None),
            _orchestrator=SimpleNamespace(_executor=_FakeExecutor()),
        )

        monkeypatch.setattr("app.api.main.build_document_retrieval", _fake_build_document_retrieval)
        monkeypatch.setattr(
            "app.api.main.build_chat_orchestrator",
            lambda *, doc_retrieval=None: fake_chat_orchestrator,
        )

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

        assert calls == ["init", "close"]


async def _fake_build_document_retrieval() -> None:
    return None
