"""Targeted tests for the read-only viewer API endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport

from app.providers.run_store import RunStore


@pytest.fixture
async def store_and_client(tmp_path):
    """Create test app with a RunStore backed by temp DB."""
    from fastapi import FastAPI
    from app.api.routes_viewer import router

    db_path = str(tmp_path / "test_viewer.db")
    store = RunStore(db_path=db_path)
    await store.initialize()

    app = FastAPI()
    app.state.run_store = store
    app.include_router(router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield store, client

    await store.close()


@pytest.mark.asyncio
async def test_list_conversations_empty(store_and_client):
    store, client = store_and_client
    resp = await client.get("/viewer/conversations")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_full_viewer_flow(store_and_client):
    store, client = store_and_client

    # Seed data
    conv_id = await store.resolve_conversation("s1", title="Test Conversation")
    await store.persist_message(conv_id, "user", "Aktif calisanlari listele")
    run_id = await store.create_run(conv_id, run_type="initial", trace_id="t1")
    await store.persist_stage(run_id, stage_name="question", stage_order=0, status="passed", summary="Q")
    await store.persist_stage(run_id, stage_name="validation", stage_order=1, status="passed", summary="V OK")
    await store.persist_stage(run_id, stage_name="compile", stage_order=2, status="passed",
                              summary="SQL compiled", payload={"sql": "SELECT 1 FROM DUAL"})
    await store.persist_event(run_id, event_type="pipeline_complete")
    await store.finish_run(run_id, status="success")
    await store.persist_message(conv_id, "assistant", "Sonuclar...")

    # List conversations
    resp = await client.get("/viewer/conversations")
    assert resp.status_code == 200
    convs = resp.json()
    assert len(convs) == 1
    assert convs[0]["conversation_id"] == conv_id
    assert convs[0]["session_id"] == "s1"

    # Get conversation detail
    resp = await client.get(f"/viewer/conversations/{conv_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert len(detail["messages"]) == 2
    assert len(detail["runs"]) == 1

    # List runs
    resp = await client.get(f"/viewer/conversations/{conv_id}/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1

    # Get run detail
    resp = await client.get(f"/viewer/runs/{run_id}")
    assert resp.status_code == 200
    rd = resp.json()
    assert rd["status"] == "success"
    assert len(rd["stages"]) == 3
    assert rd["stages"][2]["payload"]["sql"] == "SELECT 1 FROM DUAL"
    assert len(rd["events"]) == 1

    # List stages
    resp = await client.get(f"/viewer/runs/{run_id}/stages")
    assert resp.status_code == 200
    stages = resp.json()
    assert len(stages) == 3

    # Search conversations
    resp = await client.get("/viewer/conversations/search?q=Test")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_404_on_missing(store_and_client):
    _, client = store_and_client
    resp = await client.get("/viewer/conversations/nonexistent")
    assert resp.status_code == 404
    resp = await client.get("/viewer/runs/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_no_chat_endpoints_on_viewer(store_and_client):
    """Verify the viewer router does NOT expose any chat/clarify/trace endpoints."""
    _, client = store_and_client
    # These should 404/405 because viewer router only has GET read-only endpoints
    resp = await client.post("/viewer/chat", json={"message": "test"})
    assert resp.status_code in (404, 405, 422)
    resp = await client.post("/viewer/chat/clarify", json={})
    assert resp.status_code in (404, 405, 422)
    resp = await client.post("/viewer/chat/trace", json={})
    assert resp.status_code in (404, 405, 422)
