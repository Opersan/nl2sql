"""Trace models for Pipeline Live View observability (opt-in layer).

These models support the additive tracing/streaming system that lets
users watch the NL2SQL pipeline execute step-by-step.

IMPORTANT: This module is purely additive.  It does NOT change any
pipeline business logic, retrieval semantics, or narration behavior.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _elapsed_ms_from(started_at_mono: float | None) -> int | None:
    if started_at_mono is None:
        return None
    elapsed_seconds = time.monotonic() - started_at_mono
    if elapsed_seconds <= 0:
        return 0
    return max(1, round(elapsed_seconds * 1000))


class StageStatus(str, Enum):
    """Lifecycle status for a pipeline stage."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StageEvent(BaseModel):
    """A single stage lifecycle event emitted by the trace layer.

    Every event carries:
    - ``trace_id``     – unique per-request identifier
    - ``stage_name``   – matches the stage keys listed in the roadmap
    - ``status``       – current lifecycle status
    - ``elapsed_ms``   – ms since stage start (only on completed/failed)
    - ``summary``      – human-readable one-liner
    - ``payload``      – safe serialized stage artifacts (truncated)
    - ``metadata``     – non-sensitive diagnostic metadata
    """

    trace_id: str
    stage_name: str
    status: StageStatus
    event_index: int | None = None
    trace_elapsed_ms: int | None = None
    started_at: float | None = None
    completed_at: float | None = None
    elapsed_ms: int | None = None
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceCollector:
    """Async-safe, per-request event collector for Pipeline Live View.

    Pipeline stages push events via the ``stage_started`` / ``stage_completed``
    / ``stage_failed`` / ``stage_skipped`` helpers, or directly via ``emit()``.

    The SSE endpoint consumes events via ``async for event in collector``.
    """

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self._queue: asyncio.Queue[StageEvent | None] = asyncio.Queue()
        self._closed = False
        self._started_at_mono: dict[str, float] = {}
        self._trace_started_mono = time.monotonic()
        self._event_index = 0

    # ------------------------------------------------------------------
    # Stage lifecycle helpers
    # ------------------------------------------------------------------

    def stage_started(
        self,
        stage_name: str,
        *,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> float:
        """Emit a RUNNING event and return a monotonic start timestamp."""
        now_mono = time.monotonic()
        self._started_at_mono[stage_name] = now_mono
        self.emit(
            StageEvent(
                trace_id=self.trace_id,
                stage_name=stage_name,
                status=StageStatus.RUNNING,
                started_at=time.time(),
                summary=summary,
                payload=payload or {},
                metadata=metadata or {},
            )
        )
        return now_mono

    def stage_completed(
        self,
        stage_name: str,
        *,
        started_at_mono: float | None = None,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit a PASSED event."""
        mono_start = started_at_mono or self._started_at_mono.get(stage_name)
        elapsed = _elapsed_ms_from(mono_start)
        self.emit(
            StageEvent(
                trace_id=self.trace_id,
                stage_name=stage_name,
                status=StageStatus.PASSED,
                completed_at=time.time(),
                elapsed_ms=elapsed,
                summary=summary,
                payload=payload or {},
                metadata=metadata or {},
            )
        )

    def stage_failed(
        self,
        stage_name: str,
        *,
        started_at_mono: float | None = None,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit a FAILED event."""
        mono_start = started_at_mono or self._started_at_mono.get(stage_name)
        elapsed = _elapsed_ms_from(mono_start)
        self.emit(
            StageEvent(
                trace_id=self.trace_id,
                stage_name=stage_name,
                status=StageStatus.FAILED,
                completed_at=time.time(),
                elapsed_ms=elapsed,
                summary=summary,
                payload=payload or {},
                metadata=metadata or {},
            )
        )

    def stage_skipped(
        self,
        stage_name: str,
        *,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit a SKIPPED event."""
        self.emit(
            StageEvent(
                trace_id=self.trace_id,
                stage_name=stage_name,
                status=StageStatus.SKIPPED,
                completed_at=time.time(),
                summary=summary,
                payload=payload or {},
                metadata=metadata or {},
            )
        )

    # ------------------------------------------------------------------
    # Low-level emit / consume
    # ------------------------------------------------------------------

    def emit(self, event: StageEvent) -> None:
        """Put an event on the queue (non-blocking, fire-and-forget)."""
        if not self._closed:
            self._event_index += 1
            event_index = event.event_index if event.event_index is not None else self._event_index
            trace_elapsed_ms = (
                event.trace_elapsed_ms
                if event.trace_elapsed_ms is not None
                else int((time.monotonic() - self._trace_started_mono) * 1000)
            )
            self._queue.put_nowait(
                event.model_copy(
                    update={
                        "event_index": event_index,
                        "trace_elapsed_ms": trace_elapsed_ms,
                    }
                )
            )

    async def get_event(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[bool, StageEvent | None]:
        """Return ``(timed_out, event)`` for the next queued event."""
        try:
            if timeout_seconds is None:
                event = await self._queue.get()
            else:
                event = await asyncio.wait_for(self._queue.get(), timeout_seconds)
            return False, event
        except asyncio.TimeoutError:
            return True, None

    def close(self) -> None:
        """Signal end-of-stream. Idempotent."""
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)  # sentinel

    async def __aiter__(self):
        """Async iterator over emitted events.  Stops when ``close()`` is called."""
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event
