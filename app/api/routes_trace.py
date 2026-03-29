"""Pipeline view and trace streaming endpoints."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from app.api.schemas import ChatRequest
from app.domain.trace_models import StageEvent, TraceCollector
from app.services.orchestrator import ChatOrchestrator

router = APIRouter(tags=["trace"])

_STATIC_DIR = Path(__file__).parent.parent / "static"
_PIPELINE_VIEW_HTML = _STATIC_DIR / "pipeline_view.html"
_PIPELINE_LIVE_VIEW_HTML = _STATIC_DIR / "pipeline_live_view.html"


def _get_orchestrator(request: Request) -> ChatOrchestrator:
    return request.app.state.chat_orchestrator  # type: ignore[no-any-return]


@router.get("/pipeline", response_class=HTMLResponse, include_in_schema=False)
async def pipeline_view() -> HTMLResponse:
    if _PIPELINE_VIEW_HTML.exists():
        return HTMLResponse(content=_PIPELINE_VIEW_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>Pipeline Live View</h1><p>pipeline_view.html not found.</p>",
        status_code=404,
    )


@router.get("/pipeline/viewer", response_class=HTMLResponse, include_in_schema=False)
async def pipeline_live_view() -> HTMLResponse:
    """Serve the read-only Pipeline Live View (observability surface)."""
    if _PIPELINE_LIVE_VIEW_HTML.exists():
        return HTMLResponse(content=_PIPELINE_LIVE_VIEW_HTML.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>Pipeline Live View</h1><p>pipeline_live_view.html not found.</p>",
        status_code=404,
    )


class TraceRequest(ChatRequest):
    include_payloads: bool = True


def _sse_line(data: str) -> str:
    return f"data: {data}\n\n"


def _sse_comment(text: str) -> str:
    return f": {text}\n\n"


async def _run_pipeline_with_collector(
    orchestrator: ChatOrchestrator,
    session_id: str,
    message: str,
    collector: TraceCollector,
) -> None:
    try:
        await orchestrator.handle_message(session_id, message, trace_collector=collector)
    except Exception as exc:
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
    collector = TraceCollector(trace_id=trace_id)
    heartbeat_interval_seconds = 8.0

    pipeline_task = asyncio.create_task(
        _run_pipeline_with_collector(orchestrator, session_id, message, collector)
    )

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
            if not include_payloads:
                event = event.model_copy(update={"payload": {}})
            yield _sse_line(event.model_dump_json())
            await asyncio.sleep(0)
    finally:
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
            "X-Accel-Buffering": "no",
            "X-Trace-Id": trace_id,
        },
    )


@router.get("/chat/trace/schema")
async def trace_schema() -> dict[str, Any]:
    return StageEvent.model_json_schema()
