from __future__ import annotations

import pytest

from scripts import build_catalog_index, build_example_index, build_semantic_index


@pytest.mark.asyncio
async def test_build_catalog_index_dry_run_succeeds() -> None:
    rc = await build_catalog_index._run(cache_path=None, dry_run=True)
    assert rc == 0


@pytest.mark.asyncio
async def test_build_semantic_index_dry_run_succeeds() -> None:
    rc = await build_semantic_index._run(cache_path=None, dry_run=True)
    assert rc == 0


@pytest.mark.asyncio
async def test_build_example_index_dry_run_succeeds() -> None:
    rc = await build_example_index._run(cache_path=None, corpus_path=None, dry_run=True)
    assert rc == 0
