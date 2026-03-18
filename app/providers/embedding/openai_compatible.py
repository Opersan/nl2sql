"""OpenAI-compatible embedding provider.

Calls any /v1/embeddings endpoint (OpenAI, local vLLM, etc.) to obtain
dense vector representations of text.  The API key and base URL are
taken from application settings by default but can be overridden at
construction time.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger
from app.providers.embedding.base import EmbeddingProvider

logger = get_logger(__name__)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Calls a /v1/embeddings endpoint to produce text embeddings."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "EMPTY",
        batch_size: int = 32,
        timeout: float = 60.0,
        dim: int | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._batch_size = batch_size
        self._timeout = timeout
        self._dim: int | None = dim

    @property
    def embedding_dim(self) -> int:
        if self._dim is None:
            raise RuntimeError(
                "embedding_dim not known until first embed_texts() call"
            )
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        all_vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            vectors = await self._call_batch(batch)
            all_vectors.extend(vectors)
        return all_vectors

    async def _call_batch(self, texts: list[str]) -> list[list[float]]:
        url = f"{self._base_url}/embeddings"
        payload = {"model": self._model, "input": texts}
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            logger.error(
                "[embedding] API error %d: %s",
                response.status_code,
                response.text[:200],
            )
            raise RuntimeError(
                f"Embedding API returned HTTP {response.status_code}"
            )

        data = response.json()
        vectors: list[list[float]] = [item["embedding"] for item in data["data"]]

        if vectors and self._dim is None:
            self._dim = len(vectors[0])

        return vectors
