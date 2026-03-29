"""Open WebUI-facing /v1/chat/completions integration contract tests."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_chat import router as chat_router
from app.domain.models import ChatResult, ClarificationOption, ClarificationPayload


class _StatefulStubOrchestrator:
    """Deterministic stub for Open WebUI integration tests.

    It mimics clarification lifecycle behavior while keeping NL2SQL engine
    logic out of the route test scope.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.pending: dict[str, ClarificationPayload] = {}

    async def handle_message(self, session_id: str, message: str) -> ChatResult:
        self.calls.append((session_id, message))
        msg = message.strip()
        lowered = msg.lower()
        pending = self.pending.get(session_id)

        if pending is not None:
            if msg == "1":
                self.pending.pop(session_id, None)
                return ChatResult(
                    session_id=session_id,
                    status="success",
                    answer="ELEKTRIK DIZAYN secildi ve sonuc uretildi.",
                )
            if lowered == "mekanik dizayn":
                self.pending.pop(session_id, None)
                return ChatResult(
                    session_id=session_id,
                    status="success",
                    answer="MEKANIK DIZAYN secildi ve sonuc uretildi.",
                )
            if lowered == "sen karar ver":
                self.pending.pop(session_id, None)
                return ChatResult(
                    session_id=session_id,
                    status="success",
                    answer="Sen karar ver secildi; ELEKTRIK DIZAYN ile devam edildi.",
                )
            return ChatResult(
                session_id=session_id,
                status="clarification",
                answer=pending.message,
                clarification_payload=pending,
            )

        if "dizayn" in lowered:
            payload = ClarificationPayload(
                clarification_id="clar-openwebui-1",
                message="Dizayn icin hangi birimi kastediyorsunuz?",
                options=[
                    ClarificationOption(
                        index=1,
                        label="ELEKTRIK DIZAYN",
                        value="ELEKTRIK DIZAYN",
                        score=0.91,
                    ),
                    ClarificationOption(
                        index=2,
                        label="MEKANIK DIZAYN",
                        value="MEKANIK DIZAYN",
                        score=0.87,
                    ),
                ],
                target_column="BIRIM_ADI",
                target_table="XXBT_PDKS_PER_DETAILS_V",
                original_filter_value="dizayn",
            )
            self.pending[session_id] = payload
            return ChatResult(
                session_id=session_id,
                status="clarification",
                answer=payload.message,
                clarification_payload=payload,
            )

        return ChatResult(
            session_id=session_id,
            status="success",
            answer="Normal cevap uretildi.",
        )


def _client_with_stub() -> tuple[TestClient, _StatefulStubOrchestrator]:
    app = FastAPI()
    app.include_router(chat_router)
    stub = _StatefulStubOrchestrator()
    app.state.chat_orchestrator = stub
    return TestClient(app), stub


def _post_oai(client: TestClient, *, messages: list[dict[str, str]], **extra: str) -> dict:
    payload: dict[str, object] = {
        "model": "nl2sql",
        "messages": messages,
    }
    payload.update(extra)
    r = client.post("/v1/chat/completions", json=payload)
    assert r.status_code == 200
    return r.json()


class TestOpenWebUIChatIntegration:
    def test_models_endpoint_lists_nl2sql_model_for_openwebui(self) -> None:
        client, _stub = _client_with_stub()
        with client:
            r = client.get("/v1/models")

        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "list"
        model_ids = [item["id"] for item in body["data"]]
        assert "nl2sql" in model_ids

    def test_normal_question_answer_flow_through_openwebui_surface(self) -> None:
        client, _stub = _client_with_stub()
        with client:
            body = _post_oai(
                client,
                messages=[{"role": "user", "content": "Aktif calisanlari listele"}],
            )

        assert body["status"] == "success"
        assert body["session_id"]
        assert body["choices"][0]["message"]["content"]
        assert body["choices"][0]["message"]["metadata"]["status"] == "success"
        assert "<!-- nl2sql:session=" not in body["choices"][0]["message"]["content"]

    def test_clarification_response_rendering_path(self) -> None:
        client, _stub = _client_with_stub()
        with client:
            body = _post_oai(
                client,
                messages=[{"role": "user", "content": "dizayn departmanindaki calisanlari goster"}],
            )

        content = body["choices"][0]["message"]["content"]
        actions = body["choices"][0]["message"]["metadata"]["actions"]
        assert body["status"] == "clarification"
        assert "Dizayn icin hangi birimi kastediyorsunuz?" in content
        assert "1. ELEKTRIK DIZAYN" in content
        assert "2. MEKANIK DIZAYN" in content
        assert "Sen karar ver" in content
        assert [a["value"] for a in actions] == ["1", "2", "sen karar ver"]

    def test_numeric_clarification_reply_path(self) -> None:
        client, stub = _client_with_stub()
        with client:
            first = _post_oai(
                client,
                messages=[{"role": "user", "content": "dizayn departmanindaki calisanlari goster"}],
            )
            clar_content = first["choices"][0]["message"]["content"]
            sid = first["session_id"]

            second = _post_oai(
                client,
                messages=[
                    {"role": "user", "content": "dizayn departmanindaki calisanlari goster"},
                    {"role": "assistant", "content": clar_content},
                    {"role": "user", "content": "1"},
                ],
            )

        assert second["status"] == "success"
        assert "ELEKTRIK DIZAYN" in second["choices"][0]["message"]["content"]
        assert stub.calls[0] == (sid, "dizayn departmanindaki calisanlari goster")
        assert stub.calls[1] == (sid, "1")

    def test_label_based_clarification_reply_path(self) -> None:
        client, stub = _client_with_stub()
        with client:
            first = _post_oai(
                client,
                messages=[{"role": "user", "content": "dizayn departmanindaki calisanlari goster"}],
            )
            clar_content = first["choices"][0]["message"]["content"]
            sid = first["session_id"]

            second = _post_oai(
                client,
                messages=[
                    {"role": "user", "content": "dizayn departmanindaki calisanlari goster"},
                    {"role": "assistant", "content": clar_content},
                    {"role": "user", "content": "MEKANIK DIZAYN"},
                ],
            )

        assert second["status"] == "success"
        assert "MEKANIK DIZAYN" in second["choices"][0]["message"]["content"]
        assert stub.calls[1] == (sid, "MEKANIK DIZAYN")

    def test_sen_karar_ver_path(self) -> None:
        client, stub = _client_with_stub()
        with client:
            first = _post_oai(
                client,
                messages=[{"role": "user", "content": "dizayn departmanindaki calisanlari goster"}],
            )
            clar_content = first["choices"][0]["message"]["content"]
            sid = first["session_id"]

            second = _post_oai(
                client,
                messages=[
                    {"role": "user", "content": "dizayn departmanindaki calisanlari goster"},
                    {"role": "assistant", "content": clar_content},
                    {"role": "user", "content": "sen karar ver"},
                ],
            )

        assert second["status"] == "success"
        assert "Sen karar ver secildi" in second["choices"][0]["message"]["content"]
        assert stub.calls[1] == (sid, "sen karar ver")

    def test_same_session_resume_path_uses_conversation_binding(self) -> None:
        client, stub = _client_with_stub()
        with client:
            first = _post_oai(
                client,
                messages=[{"role": "user", "content": "dizayn departmanindaki calisanlari goster"}],
                conversation_id="owui-chat-42",
            )
            clar_content = first["choices"][0]["message"]["content"]

            second = _post_oai(
                client,
                messages=[
                    {"role": "user", "content": "dizayn departmanindaki calisanlari goster"},
                    {"role": "assistant", "content": clar_content},
                    {"role": "user", "content": "1"},
                ],
                conversation_id="owui-chat-42",
            )

        assert first["session_id"] == "owui-chat-42"
        assert second["session_id"] == "owui-chat-42"
        assert stub.calls[0][0] == "owui-chat-42"
        assert stub.calls[1][0] == "owui-chat-42"

    def test_streaming_chat_completion_path(self) -> None:
        client, _stub = _client_with_stub()
        with client:
            r = client.post(
                "/v1/chat/completions",
                json={
                    "model": "nl2sql",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Aktif calisanlari listele"}],
                },
            )

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "chat.completion.chunk" in r.text
        assert "Normal cevap uretildi." in r.text
        assert "[DONE]" in r.text

    def test_openwebui_follow_up_helper_prompt_bypasses_nl2sql_orchestrator(self) -> None:
        client, stub = _client_with_stub()
        with client:
            body = _post_oai(
                client,
                messages=[
                    {"role": "user", "content": "yonetici unvanli calisanlari goster"},
                    {
                        "role": "user",
                        "content": (
                            "### Task:\n"
                            "Suggest 3-5 relevant follow-up questions or prompts that the user "
                            "might naturally ask next in this conversation as a user, based on "
                            "the chat history."
                        ),
                    },
                ],
            )

        assert body["status"] == "success"
        assert stub.calls == []
        assert "Sen karar ver" not in body["choices"][0]["message"]["content"]

    def test_openwebui_title_helper_prompt_bypasses_nl2sql_orchestrator(self) -> None:
        client, stub = _client_with_stub()
        with client:
            body = _post_oai(
                client,
                messages=[
                    {"role": "user", "content": "yonetici unvanli calisanlari goster"},
                    {
                        "role": "user",
                        "content": (
                            "### Task:\n"
                            "Generate a concise, 3-5 word title with an emoji summarizing the "
                            "chat history."
                        ),
                    },
                ],
            )

        assert body["status"] == "success"
        assert stub.calls == []
        assert "Yonetici" in body["choices"][0]["message"]["content"]

    def test_openwebui_tags_helper_prompt_bypasses_nl2sql_orchestrator(self) -> None:
        client, stub = _client_with_stub()
        with client:
            body = _post_oai(
                client,
                messages=[
                    {"role": "user", "content": "yonetici unvanli calisanlari goster"},
                    {
                        "role": "user",
                        "content": (
                            "### Task:\n"
                            "Generate 1-3 broad tags categorizing the main themes of the chat "
                            "history, along with 1-3 more specific subtopic tags."
                        ),
                    },
                ],
            )

        assert body["status"] == "success"
        assert stub.calls == []
        assert "yonetici" in body["choices"][0]["message"]["content"].lower()

    def test_no_raw_trace_leakage_into_openwebui_chat(self) -> None:
        client, _stub = _client_with_stub()
        with client:
            body = _post_oai(
                client,
                messages=[{"role": "user", "content": "Aktif calisanlari listele"}],
            )

        content = body["choices"][0]["message"]["content"].lower()
        assert "planner_llm_request" not in content
        assert "trace_id" not in content
        assert "root_cause_stage" not in content
        assert "rows_preview" not in content
        assert "<!-- nl2sql:session=" not in content
