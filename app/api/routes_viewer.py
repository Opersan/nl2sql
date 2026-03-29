"""Read-only viewer API endpoints for the Pipeline Live View.

These endpoints serve ONLY persisted data. They do NOT execute pipeline,
send chat messages, create runs, or resume clarification.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.logging import get_logger

router = APIRouter(prefix="/viewer", tags=["viewer"])
logger = get_logger(__name__)


def _get_run_store(request: Request):
    """Extract RunStore from application state."""
    store = getattr(request.app.state, "run_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Run store not available")
    return store


# ------------------------------------------------------------------
# Conversations
# ------------------------------------------------------------------


@router.get("/conversations")
async def list_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List recent conversations."""
    store = _get_run_store(request)
    return await store.list_conversations(limit=limit, offset=offset)


@router.get("/conversations/search")
async def search_conversations(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Search conversations by title or content."""
    store = _get_run_store(request)
    return await store.search_conversations(q, limit=limit)


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    request: Request,
    conversation_id: str,
) -> dict[str, Any]:
    """Get full conversation detail including messages and run summaries."""
    store = _get_run_store(request)
    detail = await store.get_conversation_detail(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return detail


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    request: Request,
    conversation_id: str,
) -> dict[str, Any]:
    """Delete a conversation and all related data (cascade)."""
    store = _get_run_store(request)
    deleted = await store.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True, "deleted": conversation_id}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    request: Request,
    conversation_id: str,
    limit: int = Query(500, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List all messages in a conversation."""
    store = _get_run_store(request)
    return await store.list_messages(conversation_id, limit=limit, offset=offset)


# ------------------------------------------------------------------
# Runs
# ------------------------------------------------------------------


@router.get("/conversations/{conversation_id}/runs")
async def list_runs(
    request: Request,
    conversation_id: str,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List all runs for a conversation."""
    store = _get_run_store(request)
    return await store.list_runs(conversation_id, limit=limit, offset=offset)


@router.get("/runs/{run_id}")
async def get_run_detail(
    request: Request,
    run_id: str,
) -> dict[str, Any]:
    """Get full run detail including stages, clarifications, and events."""
    store = _get_run_store(request)
    detail = await store.get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return detail


@router.get("/runs/{run_id}/unified")
async def get_run_detail_unified(
    request: Request,
    run_id: str,
) -> dict[str, Any]:
    """Unified pipeline view: parent + child runs merged into one timeline."""
    store = _get_run_store(request)
    detail = await store.get_run_detail_unified(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return detail


@router.get("/runs/{run_id}/stages")
async def list_stages(
    request: Request,
    run_id: str,
) -> list[dict[str, Any]]:
    """List all stages for a run."""
    store = _get_run_store(request)
    return await store.list_stages(run_id)


@router.get("/runs/{run_id}/clarifications")
async def list_run_clarifications(
    request: Request,
    run_id: str,
) -> list[dict[str, Any]]:
    """List clarifications for a run."""
    store = _get_run_store(request)
    return await store.get_clarifications_for_run(run_id)


@router.get("/runs/{run_id}/events")
async def list_run_events(
    request: Request,
    run_id: str,
) -> list[dict[str, Any]]:
    """List events for a run."""
    store = _get_run_store(request)
    return await store.list_events(run_id)


# ------------------------------------------------------------------
# Clarifications
# ------------------------------------------------------------------


@router.get("/clarifications/{clarification_id}")
async def get_clarification(
    request: Request,
    clarification_id: str,
) -> dict[str, Any]:
    """Get a specific clarification record."""
    store = _get_run_store(request)
    detail = await store.get_clarification(clarification_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Clarification not found")
    return detail


@router.get("/conversations/{conversation_id}/clarifications")
async def list_conversation_clarifications(
    request: Request,
    conversation_id: str,
) -> list[dict[str, Any]]:
    """List all clarifications for a conversation."""
    store = _get_run_store(request)
    return await store.list_clarifications(conversation_id)
