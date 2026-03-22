"""JSONL loader for the semantic foundation data layer.

Loads each of the six ``data/semantic/*.jsonl`` files, validates every
line strictly against its Pydantic model, and assembles a
``SemanticFoundation`` singleton.

Contract
--------
* Empty lines and lines beginning with ``#`` are silently skipped.
* A malformed JSON line raises ``ValueError`` immediately — fail-fast at
  startup rather than silently ignoring bad data.
* A line that fails Pydantic validation also raises ``ValueError`` with
  the file name and 1-based line number for easy debugging.
* The singleton is produced by ``load_semantic_foundation()`` which is
  cached via ``functools.lru_cache`` — safe across threads once warm.

Override
--------
Pass ``semantic_dir`` to ``load_semantic_foundation()`` to load from a
non-default directory (e.g. in tests)::

    foundation = load_semantic_foundation(semantic_dir=Path("tests/fixtures/semantic"))
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TypeVar, Type

from pydantic import BaseModel, ValidationError

from app.semantic.models import (
    FlexfieldDefinition,
    GlossaryEntry,
    LookupType,
    MetricDefinition,
    RelationshipEdge,
    SemanticEntity,
    SemanticFoundation,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_SEMANTIC_DIR = Path(__file__).resolve().parents[2] / "data" / "semantic"

T = TypeVar("T", bound=BaseModel)


def _load_jsonl(path: Path, model_cls: Type[T]) -> list[T]:
    """Load *path* as a JSONL file, validating each line against *model_cls*.

    Raises
    ------
    FileNotFoundError
        When the file does not exist.
    ValueError
        When any non-empty, non-comment line contains invalid JSON or
        fails Pydantic model validation.
    """
    if not path.exists():
        raise FileNotFoundError(f"Semantic data file not found: {path}")

    records: list[T] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path.name}:{line_no}: invalid JSON — {exc}"
                ) from exc
            try:
                records.append(model_cls.model_validate(data))
            except ValidationError as exc:
                raise ValueError(
                    f"{path.name}:{line_no}: validation failed — {exc}"
                ) from exc

    logger.debug("[semantic-loader] %s — loaded %d records", path.name, len(records))
    return records


def load_semantic_foundation(
    *,
    semantic_dir: Path | None = None,
) -> SemanticFoundation:
    """Load all six semantic JSONL files and return a ``SemanticFoundation``.

    This function is **not** cached itself — use ``_cached_load`` (the
    lru_cache wrapper) for the application singleton.  Pass
    ``semantic_dir`` to override the default path in tests.
    """
    base = semantic_dir or _DEFAULT_SEMANTIC_DIR
    return SemanticFoundation(
        glossary=_load_jsonl(base / "glossary.jsonl", GlossaryEntry),
        entities=_load_jsonl(base / "entities.jsonl", SemanticEntity),
        relationships=_load_jsonl(base / "relationships.jsonl", RelationshipEdge),
        metrics=_load_jsonl(base / "metrics.jsonl", MetricDefinition),
        lookups=_load_jsonl(base / "lookups.jsonl", LookupType),
        flexfields=_load_jsonl(base / "flexfields.jsonl", FlexfieldDefinition),
    )


@lru_cache(maxsize=1)
def _cached_load() -> SemanticFoundation:
    """Application-level cached singleton using the default data directory."""
    return load_semantic_foundation()


def get_semantic_foundation() -> SemanticFoundation:
    """Return the cached ``SemanticFoundation`` singleton.

    Call this from application code.  The first call loads and validates
    all JSONL files; subsequent calls return the cached instance in O(1).
    """
    return _cached_load()
