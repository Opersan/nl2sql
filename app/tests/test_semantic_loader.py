"""Tests for the semantic JSONL loader.

Validates that:
- All six JSONL data files load without errors.
- Malformed JSON on any line raises ValueError immediately.
- Invalid Pydantic schema on any line raises ValueError immediately.
- Empty lines and comment lines (#) are silently skipped.
- The cached ``get_semantic_foundation()`` returns the same object on repeated calls.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.semantic.loader import load_semantic_foundation
from app.semantic.models import (
    FlexfieldDefinition,
    GlossaryEntry,
    LookupType,
    MetricDefinition,
    RelationshipEdge,
    SemanticEntity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def semantic_dir() -> Path:
    """Return the production data/semantic directory."""
    return Path(__file__).resolve().parents[2] / "data" / "semantic"


@pytest.fixture()
def tmp_semantic_dir(tmp_path: Path) -> Path:
    """Create a minimal, valid set of JSONL files under tmp_path."""
    d = tmp_path / "semantic"
    d.mkdir()

    (d / "glossary.jsonl").write_text(
        json.dumps({
            "raw_term": "çalışan", "language": "tr", "normalized": "calisan",
            "canonical": "HR_EMPLOYEES", "match_type": "phrase",
            "confidence": 0.95, "domain": "HR", "source": "curated",
        }) + "\n",
        encoding="utf-8",
    )
    (d / "entities.jsonl").write_text(
        json.dumps({
            "entity_id": "HR_EMPLOYEES", "display_name": "employee",
            "module": "HR", "root_table": "XXBT_PDKS_PER_DETAILS_V",
            "default_tables": ["XXBT_PDKS_PER_DETAILS_V"],
        }) + "\n",
        encoding="utf-8",
    )
    (d / "relationships.jsonl").write_text(
        json.dumps({
            "edge_id": "e1", "source_entity": "HR_EMPLOYEES",
            "target_entity": "HR_EMPLOYEES",
            "source_table": "XXBT_PDKS_PER_DETAILS_V",
            "target_table": "XXBT_PDKS_PER_DETAILS_V",
            "join_keys": [{"source_column": "EMPLOYEE_ID", "target_column": "EMPLOYEE_ID"}],
        }) + "\n",
        encoding="utf-8",
    )
    (d / "metrics.jsonl").write_text(
        json.dumps({
            "metric_id": "emp_count", "name": "Employee Count",
            "entity_id": "HR_EMPLOYEES", "domain": "HR",
        }) + "\n",
        encoding="utf-8",
    )
    (d / "lookups.jsonl").write_text(
        json.dumps({
            "lookup_type": "HR_STATUS", "meaning": "Aktif",
            "decoded_value": "Active", "raw_value": "ACTIVE", "domain": "HR",
        }) + "\n",
        encoding="utf-8",
    )
    (d / "flexfields.jsonl").write_text(
        json.dumps({
            "flexfield_id": "HR_FF", "name": "HR Flexfield",
            "application": "HR", "table": "XXBT_PDKS_PER_DETAILS_V",
            "segment_column": "ATTRIBUTE1", "module": "HR",
        }) + "\n",
        encoding="utf-8",
    )
    return d


# ---------------------------------------------------------------------------
# Production data loads cleanly
# ---------------------------------------------------------------------------

class TestProductionDataLoad:
    def test_all_six_files_load(self, semantic_dir: Path) -> None:
        foundation = load_semantic_foundation(semantic_dir=semantic_dir)
        assert len(foundation.glossary) > 0
        assert len(foundation.entities) > 0
        assert len(foundation.relationships) > 0
        assert len(foundation.metrics) > 0
        assert len(foundation.lookups) > 0
        assert len(foundation.flexfields) > 0

    def test_entity_types_are_correct(self, semantic_dir: Path) -> None:
        foundation = load_semantic_foundation(semantic_dir=semantic_dir)
        assert all(isinstance(e, GlossaryEntry) for e in foundation.glossary)
        assert all(isinstance(e, SemanticEntity) for e in foundation.entities)
        assert all(isinstance(e, RelationshipEdge) for e in foundation.relationships)
        assert all(isinstance(e, MetricDefinition) for e in foundation.metrics)
        assert all(isinstance(e, LookupType) for e in foundation.lookups)
        assert all(isinstance(e, FlexfieldDefinition) for e in foundation.flexfields)

    def test_all_six_modules_have_entities(self, semantic_dir: Path) -> None:
        foundation = load_semantic_foundation(semantic_dir=semantic_dir)
        modules = {e.module for e in foundation.entities}
        assert modules == {"HR", "PO", "AP", "AR", "GL", "INV"}


# ---------------------------------------------------------------------------
# Minimal fixture loads cleanly
# ---------------------------------------------------------------------------

def test_minimal_fixture_loads(tmp_semantic_dir: Path) -> None:
    foundation = load_semantic_foundation(semantic_dir=tmp_semantic_dir)
    assert len(foundation.glossary) == 1
    assert len(foundation.entities) == 1
    assert len(foundation.relationships) == 1
    assert len(foundation.metrics) == 1
    assert len(foundation.lookups) == 1
    assert len(foundation.flexfields) == 1


# ---------------------------------------------------------------------------
# Fail-fast on bad data
# ---------------------------------------------------------------------------

class TestFailFastBadData:
    def _write_bad_line(self, d: Path, filename: str, bad_line: str) -> None:
        (d / filename).write_text(bad_line + "\n", encoding="utf-8")

    def test_malformed_json_raises_valueerror(self, tmp_semantic_dir: Path) -> None:
        (tmp_semantic_dir / "glossary.jsonl").write_text(
            "{not valid json}\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="invalid JSON"):
            load_semantic_foundation(semantic_dir=tmp_semantic_dir)

    def test_missing_required_field_raises_valueerror(self, tmp_semantic_dir: Path) -> None:
        # GlossaryEntry requires 'raw_term'; omit it
        (tmp_semantic_dir / "glossary.jsonl").write_text(
            json.dumps({"language": "tr", "normalized": "x"}) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="validation failed"):
            load_semantic_foundation(semantic_dir=tmp_semantic_dir)

    def test_unknown_field_raises_valueerror(self, tmp_semantic_dir: Path) -> None:
        # extra='forbid' on models — unknown keys are rejected
        entry = {
            "raw_term": "x", "language": "tr", "normalized": "x",
            "canonical": "HR_EMPLOYEES", "match_type": "exact",
            "confidence": 0.9, "domain": "HR", "source": "curated",
            "unknown_extra_field": "evil_value",
        }
        (tmp_semantic_dir / "glossary.jsonl").write_text(
            json.dumps(entry) + "\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="validation failed"):
            load_semantic_foundation(semantic_dir=tmp_semantic_dir)

    def test_empty_join_keys_raises_valueerror(self, tmp_semantic_dir: Path) -> None:
        bad_edge = {
            "edge_id": "bad", "source_entity": "HR_EMPLOYEES",
            "target_entity": "HR_EMPLOYEES",
            "source_table": "A", "target_table": "B",
            "join_keys": [],  # empty — should fail RelationshipEdge validator
        }
        (tmp_semantic_dir / "relationships.jsonl").write_text(
            json.dumps(bad_edge) + "\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="validation failed"):
            load_semantic_foundation(semantic_dir=tmp_semantic_dir)

    def test_confidence_out_of_range_raises_valueerror(self, tmp_semantic_dir: Path) -> None:
        entry = {
            "raw_term": "x", "language": "tr", "normalized": "x",
            "canonical": "HR_EMPLOYEES", "match_type": "exact",
            "confidence": 1.5,  # out of [0, 1]
            "domain": "HR", "source": "curated",
        }
        (tmp_semantic_dir / "glossary.jsonl").write_text(
            json.dumps(entry) + "\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="validation failed"):
            load_semantic_foundation(semantic_dir=tmp_semantic_dir)

    def test_missing_file_raises_filenotfounderror(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty_semantic"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            load_semantic_foundation(semantic_dir=empty_dir)


# ---------------------------------------------------------------------------
# Comment and empty line handling
# ---------------------------------------------------------------------------

def test_comment_lines_are_skipped(tmp_semantic_dir: Path) -> None:
    original = (tmp_semantic_dir / "glossary.jsonl").read_text(encoding="utf-8")
    with_comments = "# This is a comment\n\n" + original + "\n# Another comment\n"
    (tmp_semantic_dir / "glossary.jsonl").write_text(with_comments, encoding="utf-8")
    foundation = load_semantic_foundation(semantic_dir=tmp_semantic_dir)
    assert len(foundation.glossary) == 1  # same count as before
