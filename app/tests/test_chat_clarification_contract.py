"""Response-contract tests for structured clarification UX."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_chat import router as chat_router
from app.api.schemas import ChatResponse
from app.domain.models import ChatResult, ClarificationOption, ClarificationPayload


class _StubOrchestrator:
    def __init__(self, result: ChatResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    async def handle_message(self, session_id: str, message: str) -> ChatResult:
        self.calls.append((session_id, message))
        return self.result


def _client_with_stub(result: ChatResult) -> tuple[TestClient, _StubOrchestrator]:
    app = FastAPI()
    app.include_router(chat_router)
    stub = _StubOrchestrator(result)
    app.state.chat_orchestrator = stub
    return TestClient(app), stub


class TestClarificationPayloadContract:
    def test_chat_response_preserves_clarification_payload(self) -> None:
        payload = ClarificationPayload(
            clarification_id="clar-123",
            message="Hangi birimi kastediyorsunuz?",
            options=[
                ClarificationOption(index=1, label="Mekanik Dizayn", value="Mekanik Dizayn", score=0.91),
                ClarificationOption(index=2, label="Mekanik Bakim", value="Mekanik Bakim", score=0.82),
            ],
            target_column="BIRIM_ADI",
            target_table="XXBT_PDKS_PER_DETAILS_V",
            original_filter_value="mekanik",
        )
        result = ChatResult(
            session_id="sess-1",
            status="clarification",
            answer="Iki olasi eslesme buldum.",
            clarification_payload=payload,
        )

        response = ChatResponse.from_chat_result(result)

        assert response.status == "clarification"
        assert response.clarification_payload is not None
        assert response.clarification_payload.clarification_id == "clar-123"
        assert response.clarification_payload.options[0].label == "Mekanik Dizayn"
        assert response.clarification_payload.options[1].index == 2

    def test_chat_clarify_endpoint_routes_reply_to_orchestrator(self) -> None:
        result = ChatResult(
            session_id="sess-clarify",
            status="success",
            answer="Seciminize gore devam edildi.",
        )
        client, stub = _client_with_stub(result)

        with client:
            response = client.post(
                "/chat/clarify",
                json={
                    "session_id": "sess-clarify",
                    "clarification_id": "clar-321",
                    "message": "1",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["answer"] == "Seciminize gore devam edildi."
        assert stub.calls == [("sess-clarify", "1")]

    def test_success_response_keeps_clarification_payload_null(self) -> None:
        result = ChatResult(
            session_id="sess-chat",
            status="success",
            answer="Tamamlandi.",
        )
        client, _stub = _client_with_stub(result)

        with client:
            response = client.post(
                "/chat",
                json={
                    "session_id": "sess-chat",
                    "message": "Aktif calisanlari listele",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["clarification_payload"] is None