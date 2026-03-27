"""Pipeline Live View – trace/streaming endpoints.

All endpoints here are ADDITIVE and OPT-IN.  They do NOT change the
existing /chat or /v1/chat/completions behavior.

Endpoints
---------
GET  /trace          – Serve the Pipeline Live View HTML page
POST /chat/trace     – SSE streaming: run pipeline and stream stage events
GET  /chat/trace/schema  – Returns the StageEvent JSON schema (dev helper)
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from app.api.schemas import ChatRequest
from app.domain.trace_models import StageEvent, StageStatus, TraceCollector
from app.services.orchestrator import ChatOrchestrator

router = APIRouter(tags=["trace"])

# Path to the Pipeline Live View single-page HTML
_STATIC_DIR = Path(__file__).parent.parent / "static"
_PIPELINE_VIEW_HTML = _STATIC_DIR / "pipeline_view.html"
_PIPELINE_VIEW_LEGACY_HTML = _STATIC_DIR / "pipeline_view_legacy.html"


# ---------------------------------------------------------------------------
# Dependency helper (reuses same pattern as routes_chat.py)
# ---------------------------------------------------------------------------


def _get_orchestrator(request: Request) -> ChatOrchestrator:
    return request.app.state.chat_orchestrator  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# GET /trace  – serve the Pipeline Live View page
# ---------------------------------------------------------------------------


@router.get("/trace", response_class=HTMLResponse, include_in_schema=False)
async def pipeline_view() -> HTMLResponse:
    """Serve the Pipeline Live View single-page application."""
    if _PIPELINE_VIEW_HTML.exists():
        return HTMLResponse(content=_PIPELINE_VIEW_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>Pipeline Live View</h1><p>pipeline_view.html not found.</p>",
        status_code=404,
    )


@router.get("/trace/legacy", response_class=HTMLResponse, include_in_schema=False)
async def pipeline_view_legacy() -> HTMLResponse:
    """Serve the legacy Pipeline Live View single-page application."""
    if _PIPELINE_VIEW_LEGACY_HTML.exists():
        return HTMLResponse(content=_PIPELINE_VIEW_LEGACY_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>Pipeline Live View</h1><p>pipeline_view_legacy.html not found.</p>",
        status_code=404,
    )


# ---------------------------------------------------------------------------
# POST /chat/trace  – SSE streaming trace endpoint
# ---------------------------------------------------------------------------


class TraceRequest(ChatRequest):
    """Extends ChatRequest with optional trace options."""

    include_payloads: bool = True
    """When False, stage payloads are omitted from SSE events (leaner stream)."""


def _sse_line(data: str) -> str:
    """Format a single SSE data line."""
    return f"data: {data}\n\n"


def _sse_comment(text: str) -> str:
    """Format an SSE comment (keepalive / status annotation)."""
    return f": {text}\n\n"


async def _run_pipeline_with_collector(
    orchestrator: ChatOrchestrator,
    session_id: str,
    message: str,
    collector: TraceCollector,
) -> None:
    """Run the pipeline in the background, close collector when done."""
    try:
        await orchestrator.handle_message(
            session_id,
            message,
            trace_collector=collector,
        )
    except Exception as exc:
        # Emit a synthetic final_verdict failure so the UI always terminates cleanly
        collector.stage_failed(
            "final_verdict",
            summary=f"Unhandled pipeline error: {exc}",
            payload={"error": str(exc)[:300], "error_type": type(exc).__name__},
        )
    finally:
        collector.close()


async def _event_stream(
    orchestrator: ChatOrchestrator,
    session_id: str,
    message: str,
    trace_id: str,
    include_payloads: bool,
) -> AsyncIterator[str]:
    """Async generator that yields SSE-formatted strings."""
    collector = TraceCollector(trace_id=trace_id)
    heartbeat_interval_seconds = 8.0

    # Launch the pipeline as a background task
    pipeline_task = asyncio.create_task(
        _run_pipeline_with_collector(orchestrator, session_id, message, collector)
    )

    # Yield a header comment so the client knows the trace has started
    yield _sse_comment(f"trace_id={trace_id}")
    yield _sse_comment("stream=started")

    try:
        while True:
            timed_out, event = await collector.get_event(
                timeout_seconds=heartbeat_interval_seconds,
            )
            if timed_out:
                yield _sse_comment("heartbeat")
                await asyncio.sleep(0)
                continue
            if event is None:
                break
            # Optionally strip payloads for leaner streams
            if not include_payloads:
                event = event.model_copy(update={"payload": {}})
            yield _sse_line(event.model_dump_json())
            # Yield control to the event loop so data flushes immediately
            await asyncio.sleep(0)
    finally:
        # Always await the pipeline task to surface exceptions and avoid orphans
        try:
            await pipeline_task
        except Exception:
            pass
        yield _sse_comment("stream=ended")


@router.post("/chat/trace")
async def chat_trace(
    body: TraceRequest,
    orchestrator: ChatOrchestrator = Depends(_get_orchestrator),
) -> StreamingResponse:
    """Run the NL2SQL pipeline in trace mode and stream stage events via SSE.

    Each stage emits one or two Server-Sent Events (SSE):
    - A ``RUNNING`` event when the stage starts
    - A ``PASSED`` / ``FAILED`` / ``SKIPPED`` event when it completes

    Normal ``/chat`` behavior is completely unchanged.  This endpoint is
    purely additive and opt-in.

    Returns
    -------
    text/event-stream containing one JSON-encoded ``StageEvent`` per line.
    """
    trace_id = uuid.uuid4().hex

    return StreamingResponse(
        _event_stream(
            orchestrator=orchestrator,
            session_id=body.session_id,
            message=body.message,
            trace_id=trace_id,
            include_payloads=body.include_payloads,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "X-Trace-Id": trace_id,
        },
    )


# ---------------------------------------------------------------------------
# GET /chat/trace/schema – developer helper
# ---------------------------------------------------------------------------


@router.get("/chat/trace/schema")
async def trace_schema() -> dict[str, Any]:
    """Return the JSON schema of a ``StageEvent`` (developer reference)."""
    return StageEvent.model_json_schema()
