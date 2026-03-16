"""API smoke tests using FastAPI TestClient.

All tests use the default mock LLM + mock executor (no network calls).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import APP_VERSION


@pytest.fixture
def client() -> TestClient:
    """Provide a fresh TestClient (triggers lifespan for each test)."""
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok(self, client: TestClient) -> None:
        r = client.get("/health")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body

    def test_health_version_matches(self, client: TestClient) -> None:
        """Health endpoint must report the centralised APP_VERSION."""
        r = client.get("/health")
        body = r.json()
        assert body["version"] == APP_VERSION


# ---------------------------------------------------------------------------
# /chat – success
# ---------------------------------------------------------------------------


class TestChatSuccess:
    def test_active_employees(self, client: TestClient) -> None:
        """'Aktif çalışanları listele' should succeed end-to-end."""
        r = client.post(
            "/chat",
            json={
                "session_id": "test-1",
                "message": "Aktif çalışanları listele",
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["session_id"] == "test-1"
        assert body["answer"]  # non-empty
        assert body["plan"] is not None
        assert body["sql"] is not None
        assert "ROWNUM" in body["sql"]

    def test_aggregate_query(self, client: TestClient) -> None:
        r = client.post(
            "/chat",
            json={
                "session_id": "test-2",
                "message": "Birim bazında çalışan sayısı",
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"


# ---------------------------------------------------------------------------
# /chat – clarification
# ---------------------------------------------------------------------------


class TestChatClarification:
    def test_unknown_query(self, client: TestClient) -> None:
        r = client.post(
            "/chat",
            json={
                "session_id": "test-3",
                "message": "xyz bilinmeyen sorgu 12345",
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "clarification"
        assert body["answer"]


# ---------------------------------------------------------------------------
# /chat – validation error
# ---------------------------------------------------------------------------


class TestChatValidationError:
    def test_restricted_column(self, client: TestClient) -> None:
        """TC kimlik request should be rejected by validation."""
        r = client.post(
            "/chat",
            json={
                "session_id": "test-4",
                "message": "Kimlik numaralarını göster",
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "validation_error"
        assert body["error_code"] == "restricted_column"


# ---------------------------------------------------------------------------
# Invalid request shape
# ---------------------------------------------------------------------------


class TestInvalidRequest:
    def test_missing_message(self, client: TestClient) -> None:
        r = client.post("/chat", json={"session_id": "test-5"})

        assert r.status_code == 422  # Validation error

    def test_empty_message(self, client: TestClient) -> None:
        r = client.post(
            "/chat",
            json={"session_id": "test-6", "message": ""},
        )

        assert r.status_code == 422

    def test_missing_session_id(self, client: TestClient) -> None:
        r = client.post(
            "/chat",
            json={"message": "Aktif çalışanları listele"},
        )

        assert r.status_code == 422


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoint
# ---------------------------------------------------------------------------


class TestOpenAICompat:
    def test_chat_completions(self, client: TestClient) -> None:
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "nl2sql",
                "messages": [
                    {"role": "user", "content": "Aktif çalışanları listele"},
                ],
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "chat.completion"
        assert len(body["choices"]) == 1
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["choices"][0]["message"]["content"]  # non-empty


# ---------------------------------------------------------------------------
# Response contract
# ---------------------------------------------------------------------------


class TestResponseContract:
    """Verify that all four status types have consistent response shape."""

    def test_success_response_shape(self, client: TestClient) -> None:
        r = client.post(
            "/chat",
            json={"session_id": "shape-1", "message": "Aktif çalışanları listele"},
        )
        body = r.json()

        assert r.status_code == 200
        assert body["status"] in ("success", "clarification", "validation_error", "execution_error")
        assert isinstance(body["session_id"], str)
        assert isinstance(body["answer"], str)
        assert body["answer"] != ""

    def test_clarification_has_no_sql_or_rows(self, client: TestClient) -> None:
        """Clarification responses must not carry SQL or rows."""
        r = client.post(
            "/chat",
            json={"session_id": "shape-2", "message": "xyz bilinmeyen"},
        )
        body = r.json()

        assert body["status"] == "clarification"
        assert body["sql"] is None
        assert body["rows_preview"] is None
        assert body["answer"] != ""

    def test_validation_error_has_error_code(self, client: TestClient) -> None:
        """Validation-error responses must carry error_code."""
        r = client.post(
            "/chat",
            json={"session_id": "shape-3", "message": "Kimlik numaralarını göster"},
        )
        body = r.json()

        assert body["status"] == "validation_error"
        assert body["error_code"] is not None
        assert body["error_message"] is not None

    def test_success_has_sql_when_enabled(self, client: TestClient) -> None:
        """Success responses must include SQL when enable_sql_in_api_response is True."""
        from app.core.config import settings

        r = client.post(
            "/chat",
            json={"session_id": "shape-4", "message": "Aktif çalışanları listele"},
        )
        body = r.json()

        assert body["status"] == "success"
        if settings.enable_sql_in_api_response:
            assert body["sql"] is not None
            assert "ROWNUM" in body["sql"]


# ---------------------------------------------------------------------------
# Additional invalid request tests
# ---------------------------------------------------------------------------


class TestInvalidRequestEdgeCases:
    def test_empty_session_id(self, client: TestClient) -> None:
        r = client.post(
            "/chat",
            json={"session_id": "", "message": "test"},
        )
        assert r.status_code == 422

    def test_whitespace_only_message(self, client: TestClient) -> None:
        """Messages with only whitespace should be rejected (422)."""
        r = client.post(
            "/chat",
            json={"session_id": "ws-1", "message": "   "},
        )
        assert r.status_code == 422

    def test_extra_fields_ignored(self, client: TestClient) -> None:
        """Extra fields in request body should be ignored (not cause errors)."""
        r = client.post(
            "/chat",
            json={
                "session_id": "extra-1",
                "message": "Aktif çalışanları listele",
                "extra_field": "should be ignored",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"

    def test_tab_only_message(self, client: TestClient) -> None:
        """Tab-only messages should also be rejected."""
        r = client.post(
            "/chat",
            json={"session_id": "ws-2", "message": "\t\t"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Session continuation
# ---------------------------------------------------------------------------


class TestSessionContinuation:
    """Verify that the same session_id maintains conversational state."""

    def test_two_turns_same_session(self, client: TestClient) -> None:
        """Two calls with identical session_id should share session state."""
        sid = "cont-1"

        r1 = client.post(
            "/chat",
            json={"session_id": sid, "message": "Aktif çalışanları listele"},
        )
        assert r1.status_code == 200
        assert r1.json()["status"] == "success"

        r2 = client.post(
            "/chat",
            json={"session_id": sid, "message": "Birim bazında çalışan sayısı"},
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "success"
        assert r2.json()["session_id"] == sid

    def test_different_sessions_isolated(self, client: TestClient) -> None:
        """Distinct session_ids should produce independent results."""
        r1 = client.post(
            "/chat",
            json={"session_id": "iso-a", "message": "Aktif çalışanları listele"},
        )
        r2 = client.post(
            "/chat",
            json={"session_id": "iso-b", "message": "Aktif çalışanları listele"},
        )

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["session_id"] == "iso-a"
        assert r2.json()["session_id"] == "iso-b"


# ---------------------------------------------------------------------------
# SQL visibility toggle
# ---------------------------------------------------------------------------


class TestSqlVisibilityToggle:
    """Test enable_sql_in_api_response=False hides SQL from responses."""

    def test_sql_hidden_when_disabled(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """When enable_sql_in_api_response is False, sql field must be None."""
        from app.core import config as config_module

        monkeypatch.setattr(config_module.settings, "enable_sql_in_api_response", False)

        r = client.post(
            "/chat",
            json={"session_id": "nosql-1", "message": "Aktif çalışanları listele"},
        )
        body = r.json()

        assert r.status_code == 200
        assert body["status"] == "success"
        assert body["sql"] is None

    def test_sql_visible_when_enabled(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """When enable_sql_in_api_response is True (default), sql field must be present."""
        from app.core import config as config_module

        monkeypatch.setattr(config_module.settings, "enable_sql_in_api_response", True)

        r = client.post(
            "/chat",
            json={"session_id": "withsql-1", "message": "Aktif çalışanları listele"},
        )
        body = r.json()

        assert r.status_code == 200
        assert body["status"] == "success"
        assert body["sql"] is not None
        assert "ROWNUM" in body["sql"]
