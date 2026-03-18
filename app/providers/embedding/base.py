"""Abstract base for embedding providers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Contract for text embedding back-ends."""

    @abstractmethod
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return a list of embedding vectors, one per input text.

        The order of vectors matches the order of *texts*.
        Implementations must handle empty input by returning [].
        """
        ...

    @property
    @abstractmethod
    def embedding_dim(self) -> int:
        """Return the dimensionality of this provider's embedding vectors."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return a stable identifier for this embedding model.

        Used as part of the cache key — changing the model name forces a
        cache rebuild even when the catalog fingerprint is unchanged.
        """
        ...
