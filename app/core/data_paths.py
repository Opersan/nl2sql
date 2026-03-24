"""Centralized data source and index artifact paths.

This module separates three retrieval artifact roles:

* catalog sources / indexes   -> technical table-column metadata
* semantic sources / indexes  -> canonical semantic repository projections
* example sources / indexes   -> schema prose and approved examples

Compatibility note
------------------
New defaults live under role-based directories. Existing legacy root-level
paths remain readable via explicit fallback helpers so local workflows are not
broken during migration.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CATALOG_SOURCE_PATH = Path("data/catalog/sample_metadata.json")
LEGACY_CATALOG_SOURCE_PATHS: tuple[Path, ...] = (Path("data/sample_metadata.json"),)

DEFAULT_DOCUMENT_SOURCE_PATH = Path("data/examples/sample_schema_documents.jsonl")
LEGACY_DOCUMENT_SOURCE_PATHS: tuple[Path, ...] = (Path("data/sample_schema_documents.jsonl"),)

DEFAULT_CATALOG_INDEX_PATH = Path("data/indexes/catalog/catalog_index.npz")
LEGACY_CATALOG_INDEX_PATHS: tuple[Path, ...] = (Path("data/catalog_index.npz"),)

DEFAULT_SEMANTIC_INDEX_PATH = Path("data/indexes/semantic/semantic_index.npz")
DEFAULT_EXAMPLE_INDEX_PATH = Path("data/indexes/examples/example_index.npz")

DEFAULT_SEMANTIC_SOURCE_DIR = Path("data/semantic")
DEFAULT_SEMANTIC_LEGACY_OVERLAY_PATH = Path("data/semantic_registry.json")


def resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _resolve_with_legacy_fallback(
    configured_path: str | Path,
    *,
    default_path: Path,
    legacy_candidates: tuple[Path, ...],
) -> tuple[Path, bool]:
    primary = resolve_repo_path(configured_path)
    if primary.exists():
        return primary, False

    configured_relative = Path(configured_path)
    if configured_relative != default_path:
        return primary, False

    for legacy in legacy_candidates:
        candidate = resolve_repo_path(legacy)
        if candidate.exists():
            return candidate, True

    return primary, False


def resolve_catalog_source_path(path_value: str | Path) -> tuple[Path, bool]:
    return _resolve_with_legacy_fallback(
        path_value,
        default_path=DEFAULT_CATALOG_SOURCE_PATH,
        legacy_candidates=LEGACY_CATALOG_SOURCE_PATHS,
    )


def resolve_document_source_path(path_value: str | Path) -> tuple[Path, bool]:
    return _resolve_with_legacy_fallback(
        path_value,
        default_path=DEFAULT_DOCUMENT_SOURCE_PATH,
        legacy_candidates=LEGACY_DOCUMENT_SOURCE_PATHS,
    )


def resolve_catalog_index_path(
    path_value: str | Path,
    *,
    allow_legacy_fallback: bool,
) -> tuple[Path, bool]:
    if not allow_legacy_fallback:
        return resolve_repo_path(path_value), False
    return _resolve_with_legacy_fallback(
        path_value,
        default_path=DEFAULT_CATALOG_INDEX_PATH,
        legacy_candidates=LEGACY_CATALOG_INDEX_PATHS,
    )


def resolve_semantic_index_path(path_value: str | Path) -> Path:
    return resolve_repo_path(path_value)


def resolve_example_index_path(path_value: str | Path) -> Path:
    return resolve_repo_path(path_value)
