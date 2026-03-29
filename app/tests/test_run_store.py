"""Targeted tests for the RunStore persistence layer and viewer API."""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from app.providers.run_store import RunStore


@pytest.fixture
async def store(tmp_path):
    """Create a RunStore backed by a temporary SQLite DB."""
    db_path = str(tmp_path / "test_run_store.db")
    s = RunStore(db_path=db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_conversation_lifecycle(store: RunStore):
    """Test conversation create/resolve/list."""
    conv_id = await store.resolve_conversation("session-1", title="Test Q")
    assert conv_id.startswith("conv-")
    assert conv_id != "session-1"

    # Re-resolve returns same canonical ID
    conv_id2 = await store.resolve_conversation("session-1")
    assert conv_id2 == conv_id

    convs = await store.list_conversations()
    assert len(convs) == 1
    assert convs[0]["conversation_id"] == conv_id
    assert convs[0]["session_id"] == "session-1"
    assert convs[0]["title"] == "Test Q"


@pytest.mark.asyncio
async def test_openwebui_chat_id_mapping(store: RunStore):
    """openwebui_chat_id is stored on create and updated on re-resolve."""
    conv_id = await store.resolve_conversation(
        "sess-owui", openwebui_chat_id="owui-abc-123", title="OWUI Chat",
    )
    conv = await store.get_conversation(conv_id)
    assert conv["openwebui_chat_id"] == "owui-abc-123"
    assert conv["session_id"] == "sess-owui"

    # Re-resolve with different openwebui_chat_id updates it
    await store.resolve_conversation("sess-owui", openwebui_chat_id="owui-xyz-999")
    conv = await store.get_conversation(conv_id)
    assert conv["openwebui_chat_id"] == "owui-xyz-999"

    # Re-resolve without openwebui_chat_id preserves existing
    await store.resolve_conversation("sess-owui")
    conv = await store.get_conversation(conv_id)
    assert conv["openwebui_chat_id"] == "owui-xyz-999"


@pytest.mark.asyncio
async def test_message_persistence(store: RunStore):
    conv_id = await store.resolve_conversation("s1")
    msg_id = await store.persist_message(conv_id, "user", "Hello!", source="openwebui")
    assert msg_id
    msgs = await store.list_messages(conv_id)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "Hello!"
    assert msgs[0]["source"] == "openwebui"


@pytest.mark.asyncio
async def test_run_lifecycle(store: RunStore):
    conv_id = await store.resolve_conversation("s1")
    run_id = await store.create_run(conv_id, run_type="initial", trace_id="trace-abc")
    assert run_id

    run = await store.get_run(run_id)
    assert run["status"] == "running"
    assert run["trace_id"] == "trace-abc"

    await store.finish_run(run_id, status="success")
    run = await store.get_run(run_id)
    assert run["status"] == "success"
    assert run["finished_at"] is not None


@pytest.mark.asyncio
async def test_stage_persistence(store: RunStore):
    conv_id = await store.resolve_conversation("s1")
    run_id = await store.create_run(conv_id)
    stage_id = await store.persist_stage(
        run_id,
        stage_name="validation",
        stage_order=0,
        status="passed",
        elapsed_ms=42,
        summary="Validation OK",
        payload={"ok": True, "resolved_table": "XXBT_EMPLOYEES"},
    )
    assert stage_id

    stages = await store.list_stages(run_id)
    assert len(stages) == 1
    assert stages[0]["stage_name"] == "validation"
    assert stages[0]["stage_group"] == "execution"
    assert stages[0]["payload"]["ok"] is True


@pytest.mark.asyncio
async def test_clarification_lifecycle(store: RunStore):
    conv_id = await store.resolve_conversation("s1")
    run_id = await store.create_run(conv_id)
    clar_id = await store.persist_clarification(
        run_id, conv_id,
        clarification_id="clar-1",
        reason_code="filter_value_ambiguous",
        question_text="Which BIRIM_ADI?",
        options=[{"value": "ELEKTRİK", "score": 0.95}],
    )
    assert clar_id == "clar-1"

    clar = await store.get_clarification("clar-1")
    assert clar["status"] == "pending"
    assert clar["options"][0]["value"] == "ELEKTRİK"

    await store.resolve_clarification(
        "clar-1",
        selected_option="ELEKTRİK",
        resolved_value="ELEKTRİK DİZAYN",
        status="answered",
    )
    clar = await store.get_clarification("clar-1")
    assert clar["status"] == "answered"
    assert clar["resolved_value"] == "ELEKTRİK DİZAYN"


@pytest.mark.asyncio
async def test_run_detail_aggregate(store: RunStore):
    conv_id = await store.resolve_conversation("s1")
    run_id = await store.create_run(conv_id, run_type="initial")
    await store.persist_stage(run_id, stage_name="question", stage_order=0, status="passed", summary="Q")
    await store.persist_stage(run_id, stage_name="validation", stage_order=1, status="passed", summary="V")
    await store.persist_event(run_id, event_type="pipeline_started")
    await store.finish_run(run_id, status="success")

    detail = await store.get_run_detail(run_id)
    assert detail is not None
    assert len(detail["stages"]) == 2
    assert len(detail["events"]) == 1
    assert detail["status"] == "success"


@pytest.mark.asyncio
async def test_conversation_detail_aggregate(store: RunStore):
    conv_id = await store.resolve_conversation("s1", title="Test")
    await store.persist_message(conv_id, "user", "Question")
    run_id = await store.create_run(conv_id)
    await store.finish_run(run_id, status="success")

    detail = await store.get_conversation_detail(conv_id)
    assert detail is not None
    assert len(detail["messages"]) == 1
    assert len(detail["runs"]) == 1
    assert detail["runs"][0]["stage_count"] == 0


@pytest.mark.asyncio
async def test_conversation_search(store: RunStore):
    conv_id = await store.resolve_conversation("s1", title="Elektrik sorgulari")
    await store.persist_message(conv_id, "user", "Aktif calisanlari listele")

    results = await store.search_conversations("Elektrik")
    assert len(results) >= 1

    results = await store.search_conversations("Aktif")
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_lineage(store: RunStore):
    """Parent → child run linkage."""
    conv_id = await store.resolve_conversation("s1")
    parent_id = await store.create_run(conv_id, run_type="initial")
    await store.finish_run(parent_id, status="clarification")

    child_id = await store.create_run(conv_id, run_type="clarification_resume", parent_run_id=parent_id)
    await store.finish_run(child_id, status="success")

    detail = await store.get_run_detail(parent_id)
    assert len(detail["child_runs"]) == 1
    assert detail["child_runs"][0]["run_id"] == child_id
    assert detail["child_runs"][0]["run_type"] == "clarification_resume"


@pytest.mark.asyncio
async def test_unified_pipeline_view(store: RunStore):
    """Unified view merges parent + child into a single pipeline."""
    conv_id = await store.resolve_conversation("s-uni")
    parent_id = await store.create_run(conv_id, run_type="initial")
    await store.persist_stage(parent_id, stage_name="question", stage_order=0, status="passed", summary="q")
    await store.persist_stage(parent_id, stage_name="planner_llm_request", stage_order=1, status="passed")
    await store.finish_run(parent_id, status="clarification")

    child_id = await store.create_run(conv_id, run_type="clarification_resume", parent_run_id=parent_id)
    await store.persist_stage(child_id, stage_name="compile", stage_order=0, status="passed")
    await store.persist_stage(child_id, stage_name="execute", stage_order=1, status="passed")
    await store.persist_event(child_id, event_type="success")
    await store.finish_run(child_id, status="success")

    # Unified from parent
    unified = await store.get_run_detail_unified(parent_id)
    assert unified is not None
    assert unified["is_unified"] is True
    assert len(unified["run_chain"]) == 2
    assert unified["run_chain"][0] == parent_id
    assert unified["run_chain"][1] == child_id
    assert unified["status"] == "success"
    assert len(unified["stages"]) == 4  # 2 from parent + 2 from child
    assert len(unified["events"]) == 1
    assert unified["child_runs"] == []  # Flattened

    # Unified from child should also resolve to same root
    unified2 = await store.get_run_detail_unified(child_id)
    assert unified2["run_chain"] == unified["run_chain"]
    assert len(unified2["stages"]) == 4


@pytest.mark.asyncio
async def test_delete_conversation(store: RunStore):
    """Cascade delete removes conversation + all related data."""
    conv_id = await store.resolve_conversation("s-del", title="To Be Deleted")
    await store.persist_message(conv_id, "user", "Hello")
    run_id = await store.create_run(conv_id)
    await store.persist_stage(run_id, stage_name="test_stage", stage_order=0, status="passed", summary="ok")
    await store.persist_event(run_id, event_type="info", payload={"msg": "hi"})
    await store.finish_run(run_id, status="success")

    # Verify it exists
    assert await store.get_conversation_detail(conv_id) is not None

    # Delete
    deleted = await store.delete_conversation(conv_id)
    assert deleted is True

    # Verify everything is gone
    assert await store.get_conversation_detail(conv_id) is None
    assert await store.get_run_detail(run_id) is None
    convs = await store.list_conversations()
    assert len(convs) == 0

    # Delete again returns False
    assert await store.delete_conversation(conv_id) is False
