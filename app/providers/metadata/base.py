"""Abstract base for metadata loaders.

A metadata loader reads raw schema definitions from an external source
(file, database, API) and produces a ``MetadataBundle``.  The bundle is
then handed to ``MetadataIngestionService`` for transformation into the
domain ``CatalogSnapshot``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.providers.metadata.models import MetadataBundle


class MetadataLoader(ABC):
    """Contract for metadata source loaders."""

    @abstractmethod
    async def load(self, source: Path | str) -> MetadataBundle:
        """Load metadata from *source* and return a ``MetadataBundle``.

        Parameters
        ----------
        source:
            File path, directory path, or connection string depending on
            the concrete implementation.

        Raises
        ------
        ``MetadataLoadError`` when the source is unreadable or malformed.
        """
        ...
