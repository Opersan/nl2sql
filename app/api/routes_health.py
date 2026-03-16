"""Health-check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Simple liveness probe."""
    return HealthResponse()
