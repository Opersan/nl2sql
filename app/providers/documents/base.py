"""Abstract base for document corpus loaders.

A document loader reads semi-structured content (JSONL files, databases,
APIs) and produces a ``DocumentCorpus`` containing schema documents
and/or few-shot examples.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.providers.documents.models import DocumentCorpus


class DocumentLoader(ABC):
    """Contract for document corpus loaders."""

    @abstractmethod
    async def load(self, source: Path | str) -> DocumentCorpus:
        """Load documents from *source* and return a ``DocumentCorpus``.

        Parameters
        ----------
        source:
            File path, directory, or connection string depending on the
            concrete implementation.

        Returns
        -------
        A ``DocumentCorpus`` with schema documents and/or examples.

        Raises
        ------
        DocumentLoadError
            When the source is unreadable or malformed.
        """
        ...
