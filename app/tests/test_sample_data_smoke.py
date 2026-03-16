"""Smoke tests for sample data files under data/."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app.domain.catalog_models import CatalogSnapshot
from app.providers.documents.jsonl_loader import JSONLDocumentLoader

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


# ---------------------------------------------------------------------------
# 1. Metadata — CatalogSnapshot validation
# ---------------------------------------------------------------------------

def test_metadata_loads_and_validates() -> None:
    path = DATA_DIR / "sample_metadata.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    snap = CatalogSnapshot.model_validate(data)
    assert len(snap.tables) >= 1
    for tbl in snap.tables:
        assert tbl.name
        assert len(tbl.columns) >= 1


# ---------------------------------------------------------------------------
# 2 & 3. Document corpus — JSONL load + content assertions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_corpus_loads_strict() -> None:
    loader = JSONLDocumentLoader(strict=True)
    corpus = await loader.load(str(DATA_DIR / "sample_schema_documents.jsonl"))
    assert len(corpus.schema_docs) >= 1, "corpus should contain at least 1 schema doc"
    assert len(corpus.examples) >= 1, "corpus should contain at least 1 example"


# ---------------------------------------------------------------------------
# 4. Eval questions CSV — row count
# ---------------------------------------------------------------------------

def test_eval_questions_has_minimum_rows() -> None:
    path = DATA_DIR / "sample_eval_questions.csv"
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 10, f"expected ≥10 eval questions, got {len(rows)}"
    for row in rows:
        assert row.get("question_id"), "question_id must not be empty"
        assert row.get("question_tr"), "question_tr must not be empty"


# ---------------------------------------------------------------------------
# 5. Eval questions expected_table ↔ metadata table consistency
# ---------------------------------------------------------------------------

def test_eval_expected_tables_exist_in_metadata() -> None:
    # Load metadata table names
    meta_path = DATA_DIR / "sample_metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    snap = CatalogSnapshot.model_validate(meta)
    table_names = {t.name.upper() for t in snap.tables}

    # Load eval questions
    csv_path = DATA_DIR / "sample_eval_questions.csv"
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    mismatches: list[str] = []
    for row in rows:
        expected = row.get("expected_table", "").strip().upper()
        if expected and expected not in table_names:
            mismatches.append(f"{row['question_id']}: '{expected}' not in metadata")

    assert not mismatches, (
        f"Eval questions reference unknown tables:\n" + "\n".join(mismatches)
    )
