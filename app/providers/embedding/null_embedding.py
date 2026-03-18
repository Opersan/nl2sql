"""Null embedding provider — returns zero vectors.

Used when no embedding server is configured (feature flag off, tests,
offline runs).  Every call returns zero vectors of a fixed dimensionality
so downstream code can proceed without an embedding server.
"""

from __future__ import annotations

from app.providers.embedding.base import EmbeddingProvider

_NULL_DIM = 1024  # Matches BAAI/bge-m3 default dim


class NullEmbeddingProvider(EmbeddingProvider):
    """Returns zero vectors of fixed dimensionality for every input."""

    def __init__(self, dim: int = _NULL_DIM, model: str = "null") -> None:
        self._dim = dim
        self._model = model

    @property
    def embedding_dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]
