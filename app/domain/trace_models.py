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
        elapsed = int((time.monotonic() - mono_start) * 1000) if mono_start is not None else None
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
        elapsed = int((time.monotonic() - mono_start) * 1000) if mono_start is not None else None
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
            self._queue.put_nowait(event)

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
