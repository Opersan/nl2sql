"""Tests for JsonFileCatalogProvider and the load_catalog_from_json helper.

These tests cover:
  Phase 2 — JSON file catalog loads tables, columns, FK/relationship metadata
  Phase 2 — _build_catalog_provider() routing logic (json vs none)
  Phase 5 — validate_registry_against_catalog() reports unknown tables/columns
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_CATALOG = {
    "tables": [
        {
            "name": "EMPLOYEES",
            "description": "Employee view",
            "aliases": ["emp", "staff"],
            "primary_key": ["emp_id"],
            "foreign_keys": [],
            "columns": [
                {
                    "name": "emp_id",
                    "data_type": "NUMBER",
                    "nullable": False,
                    "restricted": False,
                    "description": "Primary key",
                    "aliases": [],
                },
                {
                    "name": "full_name",
                    "data_type": "VARCHAR",
                    "nullable": True,
                    "restricted": False,
                    "description": "Full name",
                    "aliases": ["name"],
                },
                {
                    "name": "secret",
                    "data_type": "VARCHAR",
                    "nullable": True,
                    "restricted": True,
                    "description": "Restricted field",
                    "aliases": [],
                },
            ],
        },
        {
            "name": "DEPARTMENTS",
            "description": "Department table",
            "aliases": [],
            "primary_key": ["dept_id"],
            "foreign_keys": [],
            "columns": [
                {
                    "name": "dept_id",
                    "data_type": "NUMBER",
                    "nullable": False,
                    "restricted": False,
                    "description": "PK",
                    "aliases": [],
                },
                {
                    "name": "dept_name",
                    "data_type": "VARCHAR2",
                    "nullable": True,
                    "restricted": False,
                    "description": "Department name",
                    "aliases": [],
                },
            ],
        },
    ]
}

FK_CATALOG = {
    "tables": [
        {
            "name": "ORDERS",
            "description": "Order header",
            "aliases": [],
            "primary_key": ["order_id"],
            "foreign_keys": [],
            "columns": [
                {"name": "order_id", "data_type": "NUMBER", "nullable": False, "restricted": False, "description": None, "aliases": []},
            ],
        },
        {
            "name": "ORDER_LINES",
            "description": "Order line items",
            "aliases": [],
            "primary_key": ["line_id"],
            "foreign_keys": [
                {
                    "column": "order_id",
                    "referenced_table": "ORDERS",
                    "referenced_column": "order_id",
                    "description": "FK to ORDERS",
                }
            ],
            "columns": [
                {"name": "line_id",  "data_type": "NUMBER", "nullable": False, "restricted": False, "description": None, "aliases": []},
                {"name": "order_id", "data_type": "NUMBER", "nullable": False, "restricted": False, "description": None, "aliases": []},
            ],
        },
    ]
}


@pytest.fixture()
def minimal_json(tmp_path: Path) -> Path:
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(MINIMAL_CATALOG), encoding="utf-8")
    return p


@pytest.fixture()
def fk_json(tmp_path: Path) -> Path:
    p = tmp_path / "fk_catalog.json"
    p.write_text(json.dumps(FK_CATALOG), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_catalog_from_json
# ---------------------------------------------------------------------------

class TestLoadCatalogFromJson:
    def test_loads_tables(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(minimal_json)
        assert len(snap.tables) == 2
        names = {t.name for t in snap.tables}
        assert names == {"EMPLOYEES", "DEPARTMENTS"}

    def test_column_count(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(minimal_json)
        emp = snap.get_table("EMPLOYEES")
        assert emp is not None
        assert len(emp.columns) == 3

    def test_column_types(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        from app.domain.catalog_models import ColumnType
        snap = load_catalog_from_json(minimal_json)
        emp = snap.get_table("EMPLOYEES")
        assert emp is not None
        assert emp.get_column("emp_id").data_type == ColumnType.NUMBER
        assert emp.get_column("full_name").data_type == ColumnType.VARCHAR
        dept = snap.get_table("DEPARTMENTS")
        assert dept.get_column("dept_name").data_type == ColumnType.VARCHAR2

    def test_restricted_flag(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(minimal_json)
        emp = snap.get_table("EMPLOYEES")
        assert emp.get_column("secret").restricted is True
        assert emp.get_column("full_name").restricted is False

    def test_aliases(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(minimal_json)
        emp = snap.get_table("EMPLOYEES")
        assert emp is not None
        assert "emp" in emp.aliases
        # Column alias
        assert emp.get_column("full_name").aliases == ["name"]

    def test_primary_key(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(minimal_json)
        emp = snap.get_table("EMPLOYEES")
        assert emp.primary_key == ["emp_id"]

    def test_relationships_derived_from_fks(self, fk_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(fk_json)
        assert len(snap.relationships) == 1
        rel = snap.relationships[0]
        assert rel.from_table == "ORDER_LINES"
        assert rel.from_column == "order_id"
        assert rel.to_table == "ORDERS"
        assert rel.to_column == "order_id"

    def test_missing_tables_key_raises(self, tmp_path: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"schemas": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="tables"):
            load_catalog_from_json(bad)

    def test_unknown_data_type_defaults_to_varchar(self, tmp_path: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        from app.domain.catalog_models import ColumnType
        catalog = {
            "tables": [
                {
                    "name": "T",
                    "description": None,
                    "aliases": [],
                    "primary_key": [],
                    "foreign_keys": [],
                    "columns": [
                        {
                            "name": "x",
                            "data_type": "XMLTYPE",   # unknown
                            "nullable": True,
                            "restricted": False,
                            "description": None,
                            "aliases": [],
                        }
                    ],
                }
            ]
        }
        p = tmp_path / "unk.json"
        p.write_text(json.dumps(catalog), encoding="utf-8")
        snap = load_catalog_from_json(p)
        assert snap.get_table("T").get_column("x").data_type == ColumnType.VARCHAR

    def test_malformed_table_skipped(self, tmp_path: Path) -> None:
        """A table entry missing the required 'name' key is skipped, good ones kept."""
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        catalog = {
            "tables": [
                {"no_name_here": True, "columns": []},  # bad
                {
                    "name": "GOOD",
                    "description": None,
                    "aliases": [],
                    "primary_key": [],
                    "foreign_keys": [],
                    "columns": [],
                },
            ]
        }
        p = tmp_path / "partial.json"
        p.write_text(json.dumps(catalog), encoding="utf-8")
        snap = load_catalog_from_json(p)
        assert len(snap.tables) == 1
        assert snap.tables[0].name == "GOOD"


# ---------------------------------------------------------------------------
# JsonFileCatalogProvider
# ---------------------------------------------------------------------------

class TestJsonFileCatalogProvider:
    def test_snapshot_accessible_synchronously(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import JsonFileCatalogProvider
        provider = JsonFileCatalogProvider(minimal_json)
        assert provider._snapshot is not None
        assert len(provider._snapshot.tables) == 2

    @pytest.mark.anyio
    async def test_get_snapshot(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import JsonFileCatalogProvider
        provider = JsonFileCatalogProvider(minimal_json)
        snap = await provider.get_snapshot()
        assert len(snap.tables) == 2

    @pytest.mark.anyio
    async def test_get_table(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import JsonFileCatalogProvider
        provider = JsonFileCatalogProvider(minimal_json)
        tbl = await provider.get_table("EMPLOYEES")
        assert tbl is not None
        assert tbl.name == "EMPLOYEES"

    @pytest.mark.anyio
    async def test_get_table_by_alias(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import JsonFileCatalogProvider
        provider = JsonFileCatalogProvider(minimal_json)
        tbl = await provider.get_table("emp")
        assert tbl is not None
        assert tbl.name == "EMPLOYEES"

    @pytest.mark.anyio
    async def test_search_tables(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import JsonFileCatalogProvider
        provider = JsonFileCatalogProvider(minimal_json)
        results = await provider.search_tables("emp")
        assert any(t.name == "EMPLOYEES" for t in results)


# ---------------------------------------------------------------------------
# _build_catalog_provider routing
# ---------------------------------------------------------------------------

class TestBuildCatalogProviderRouting:
    def test_json_route_uses_file_provider(self, minimal_json: Path, monkeypatch) -> None:
        from app.core.config import Settings
        fake_settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            metadata_source_type="json",
            metadata_source_path=str(minimal_json),
        )
        import app.api.deps as deps_module
        monkeypatch.setattr(deps_module, "settings", fake_settings)

        from app.providers.catalog.json_file_catalog import JsonFileCatalogProvider
        provider = deps_module._build_catalog_provider()
        assert isinstance(provider, JsonFileCatalogProvider)

    def test_none_route_uses_in_memory(self, monkeypatch) -> None:
        from app.core.config import Settings
        fake_settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            metadata_source_type="none",
            metadata_source_path="",
        )
        import app.api.deps as deps_module
        monkeypatch.setattr(deps_module, "settings", fake_settings)

        from app.providers.catalog.in_memory import InMemoryCatalogProvider
        provider = deps_module._build_catalog_provider()
        assert isinstance(provider, InMemoryCatalogProvider)

    def test_json_route_falls_back_when_file_missing(self, tmp_path: Path, monkeypatch) -> None:
        missing = str(tmp_path / "does_not_exist.json")
        from app.core.config import Settings
        fake_settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            metadata_source_type="json",
            metadata_source_path=missing,
        )
        import app.api.deps as deps_module
        monkeypatch.setattr(deps_module, "settings", fake_settings)

        from app.providers.catalog.in_memory import InMemoryCatalogProvider
        provider = deps_module._build_catalog_provider()
        assert isinstance(provider, InMemoryCatalogProvider)


# ---------------------------------------------------------------------------
# Phase 5: validate_registry_against_catalog
# ---------------------------------------------------------------------------

class TestValidateRegistryAgainstCatalog:
    def test_empty_registry_returns_no_errors(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        from app.services.semantic_planning import validate_registry_against_catalog
        from app.domain.semantic_models import SemanticRegistry
        snap = load_catalog_from_json(minimal_json)
        errors = validate_registry_against_catalog(snap, registry=SemanticRegistry())
        assert errors == []

    def test_unknown_root_table_reported(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        from app.services.semantic_planning import validate_registry_against_catalog
        from app.domain.semantic_models import SemanticRegistry, BusinessEntitySemantic
        snap = load_catalog_from_json(minimal_json)
        reg = SemanticRegistry(
            entities=[
                BusinessEntitySemantic(
                    entity_id="ghost",
                    root_table="NON_EXISTENT_TABLE",
                )
            ]
        )
        errors = validate_registry_against_catalog(snap, registry=reg)
        assert any("NON_EXISTENT_TABLE" in e for e in errors)

    def test_valid_registry_returns_no_errors(self, minimal_json: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        from app.services.semantic_planning import validate_registry_against_catalog
        from app.domain.semantic_models import SemanticRegistry, BusinessEntitySemantic
        snap = load_catalog_from_json(minimal_json)
        reg = SemanticRegistry(
            entities=[
                BusinessEntitySemantic(
                    entity_id="emp_entity",
                    root_table="EMPLOYEES",
                )
            ]
        )
        errors = validate_registry_against_catalog(snap, registry=reg)
        assert errors == []


# ---------------------------------------------------------------------------
# Phase 2: Sample catalog file integration
# ---------------------------------------------------------------------------

class TestSampleMetadataIntegration:
    """Smoke test against the actual data/sample_metadata.json from the repo."""

    @pytest.fixture()
    def sample_path(self) -> Path:
        p = Path(__file__).resolve().parents[2] / "data" / "sample_metadata.json"
        if not p.exists():
            pytest.skip("data/sample_metadata.json not found")
        return p

    def test_sample_loads_without_error(self, sample_path: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(sample_path)
        assert len(snap.tables) >= 6, f"Expected >=6 tables, got {len(snap.tables)}"

    def test_sample_has_employee_table(self, sample_path: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(sample_path)
        emp = snap.get_table("XXBT_PDKS_PER_DETAILS_V")
        assert emp is not None
        assert emp.get_column("CIKIS_TARIHI") is not None
        assert emp.get_column("SICIL_NO") is not None

    def test_sample_has_po_tables(self, sample_path: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(sample_path)
        assert snap.get_table("PO_HEADERS_ALL") is not None
        assert snap.get_table("PO_LINES_ALL") is not None
        assert snap.get_table("PO_LINE_LOCATIONS_ALL") is not None
        assert snap.get_table("PO_DISTRIBUTIONS_ALL") is not None
        assert snap.get_table("MTL_SYSTEM_ITEMS_B") is not None

    def test_sample_fingerprint_is_stable(self, sample_path: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        from app.providers.catalog.in_memory import catalog_fingerprint
        snap1 = load_catalog_from_json(sample_path)
        snap2 = load_catalog_from_json(sample_path)
        assert catalog_fingerprint(snap1) == catalog_fingerprint(snap2)

    def test_sample_relationships_derived(self, sample_path: Path) -> None:
        from app.providers.catalog.json_file_catalog import load_catalog_from_json
        snap = load_catalog_from_json(sample_path)
        rel_tables = {(r.from_table, r.to_table) for r in snap.relationships}
        # PO_LINES_ALL → PO_HEADERS_ALL FK should produce a relationship
        assert ("PO_LINES_ALL", "PO_HEADERS_ALL") in rel_tables
