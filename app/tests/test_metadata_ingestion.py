"""Tests for metadata ingestion pipeline (JSON & CSV loaders + service)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.catalog_models import CatalogSnapshot, ColumnType
from app.providers.metadata.file_loader import CSVMetadataLoader, JSONMetadataLoader
from app.providers.metadata.models import MetadataBundle, RawColumnDef, RawTableDef
from app.services.metadata_ingestion_service import MetadataIngestionService, _map_column_type


# ---------------------------------------------------------------------------
# Data-type mapping
# ---------------------------------------------------------------------------


class TestColumnTypeMapping:
    def test_standard_types(self) -> None:
        assert _map_column_type("VARCHAR") == ColumnType.VARCHAR
        assert _map_column_type("NUMBER") == ColumnType.NUMBER
        assert _map_column_type("INTEGER") == ColumnType.INTEGER
        assert _map_column_type("DATE") == ColumnType.DATE
        assert _map_column_type("TIMESTAMP") == ColumnType.TIMESTAMP

    def test_oracle_types(self) -> None:
        assert _map_column_type("VARCHAR2") == ColumnType.VARCHAR
        assert _map_column_type("NVARCHAR2") == ColumnType.VARCHAR
        assert _map_column_type("NCLOB") == ColumnType.CLOB
        assert _map_column_type("LONG") == ColumnType.CLOB

    def test_parametrised_types(self) -> None:
        """NUMBER(10,2) should map to NUMBER after stripping parenthesised part."""
        assert _map_column_type("NUMBER(10,2)") == ColumnType.NUMBER
        assert _map_column_type("VARCHAR2(255)") == ColumnType.VARCHAR

    def test_unknown_falls_back_to_varchar(self) -> None:
        assert _map_column_type("XML_TYPE") == ColumnType.VARCHAR
        assert _map_column_type("CUSTOM") == ColumnType.VARCHAR

    def test_case_insensitive(self) -> None:
        assert _map_column_type("integer") == ColumnType.INTEGER
        assert _map_column_type("Date") == ColumnType.DATE


# ---------------------------------------------------------------------------
# MetadataBundle model
# ---------------------------------------------------------------------------


class TestMetadataModels:
    def test_raw_table_def_minimal(self) -> None:
        table = RawTableDef(name="test_table")
        assert table.name == "test_table"
        assert table.columns == []
        assert table.primary_key == []
        assert table.object_type is None
        assert table.module is None
        assert table.synonyms == []

    def test_raw_column_def_defaults(self) -> None:
        col = RawColumnDef(name="col1")
        assert col.data_type == "VARCHAR"
        assert col.nullable is True
        assert col.restricted is False
        assert col.aliases == []
        assert col.example_values == []
        assert col.semantic_tags == []
        assert col.business_name is None

    def test_raw_table_new_fields(self) -> None:
        table = RawTableDef(
            name="po_headers_all",
            object_type="TABLE",
            module="PO",
            synonyms=["po_headers", "purchase_orders"],
        )
        assert table.object_type == "TABLE"
        assert table.module == "PO"
        assert table.synonyms == ["po_headers", "purchase_orders"]

    def test_raw_column_new_fields(self) -> None:
        col = RawColumnDef(
            name="status",
            example_values=["ACTIVE", "CLOSED"],
            semantic_tags=["status", "lifecycle"],
            business_name="Sipariş Durumu",
        )
        assert col.example_values == ["ACTIVE", "CLOSED"]
        assert col.semantic_tags == ["status", "lifecycle"]
        assert col.business_name == "Sipariş Durumu"

    def test_raw_relationship_new_fields(self) -> None:
        from app.providers.metadata.models import RawRelationshipDef

        rel = RawRelationshipDef(
            from_table="po_lines",
            from_column="header_id",
            to_table="po_headers",
            to_column="header_id",
            constraint_name="PO_LINES_FK1",
            description="Lines → Header FK",
        )
        assert rel.constraint_name == "PO_LINES_FK1"
        assert rel.description == "Lines → Header FK"

    def test_metadata_bundle_roundtrip(self) -> None:
        bundle = MetadataBundle(
            tables=[
                RawTableDef(
                    name="XXBT_PDKS_PER_DETAILS_V",
                    columns=[RawColumnDef(name="id", data_type="INTEGER")],
                ),
            ],
            source="test",
            version="1.0",
        )
        data = bundle.model_dump()
        restored = MetadataBundle.model_validate(data)
        assert restored.tables[0].name == "XXBT_PDKS_PER_DETAILS_V"
        assert restored.tables[0].columns[0].data_type == "INTEGER"


# ---------------------------------------------------------------------------
# JSON loader
# ---------------------------------------------------------------------------


class TestJSONLoader:
    @pytest.mark.asyncio
    async def test_load_valid_json(self, tmp_path: Path) -> None:
        payload = {
            "tables": [
                {
                    "name": "XXBT_PDKS_PER_DETAILS_V",
                    "description": "HR table",
                    "aliases": ["emp"],
                    "primary_key": ["reg_no"],
                    "columns": [
                        {
                            "name": "reg_no",
                            "data_type": "INTEGER",
                            "nullable": False,
                            "restricted": False,
                            "description": "Sicil no",
                            "aliases": ["sicil_no"],
                        },
                        {
                            "name": "salary",
                            "data_type": "NUMBER",
                            "nullable": True,
                            "restricted": True,
                            "description": "Maaş",
                            "aliases": ["maas"],
                        },
                    ],
                },
            ],
            "source": "test_export",
            "version": "1.0",
        }
        json_file = tmp_path / "metadata.json"
        json_file.write_text(json.dumps(payload), encoding="utf-8")

        loader = JSONMetadataLoader()
        bundle = await loader.load(json_file)

        assert len(bundle.tables) == 1
        assert bundle.tables[0].name == "XXBT_PDKS_PER_DETAILS_V"
        assert len(bundle.tables[0].columns) == 2
        assert bundle.tables[0].columns[1].restricted is True
        assert bundle.source == "test_export"

    @pytest.mark.asyncio
    async def test_load_missing_file(self, tmp_path: Path) -> None:
        from app.core.exceptions import MetadataLoadError

        loader = JSONMetadataLoader()
        with pytest.raises(MetadataLoadError, match="not found"):
            await loader.load(tmp_path / "missing.json")

    @pytest.mark.asyncio
    async def test_load_invalid_json(self, tmp_path: Path) -> None:
        from app.core.exceptions import MetadataLoadError

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json!!", encoding="utf-8")

        loader = JSONMetadataLoader()
        with pytest.raises(MetadataLoadError, match="Failed to read"):
            await loader.load(bad_file)


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


class TestCSVLoader:
    @pytest.mark.asyncio
    async def test_load_valid_csv(self, tmp_path: Path) -> None:
        # Create _tables.csv
        tables_csv = tmp_path / "_tables.csv"
        tables_csv.write_text(
            "name,schema_name,description,aliases,primary_key\n"
            "employee,HR,HR tablosu,emp|personnel,reg_no\n",
            encoding="utf-8",
        )

        # Create employee.csv
        emp_csv = tmp_path / "employee.csv"
        emp_csv.write_text(
            "column_name,data_type,nullable,restricted,description,aliases\n"
            "reg_no,INTEGER,false,false,Sicil numarası,sicil_no\n"
            "salary,NUMBER,true,true,Maaş,maas|ucret\n",
            encoding="utf-8",
        )

        loader = CSVMetadataLoader()
        bundle = await loader.load(tmp_path)

        assert len(bundle.tables) == 1
        assert bundle.tables[0].name == "employee"
        assert bundle.tables[0].aliases == ["emp", "personnel"]
        assert len(bundle.tables[0].columns) == 2
        assert bundle.tables[0].columns[0].nullable is False
        assert bundle.tables[0].columns[1].restricted is True
        assert bundle.tables[0].columns[1].aliases == ["maas", "ucret"]

    @pytest.mark.asyncio
    async def test_load_missing_directory(self, tmp_path: Path) -> None:
        from app.core.exceptions import MetadataLoadError

        loader = CSVMetadataLoader()
        with pytest.raises(MetadataLoadError, match="not found"):
            await loader.load(tmp_path / "nonexistent")

    @pytest.mark.asyncio
    async def test_load_missing_tables_csv(self, tmp_path: Path) -> None:
        from app.core.exceptions import MetadataLoadError

        loader = CSVMetadataLoader()
        with pytest.raises(MetadataLoadError, match="tables.csv"):
            await loader.load(tmp_path)


# ---------------------------------------------------------------------------
# Full ingestion pipeline
# ---------------------------------------------------------------------------


class TestIngestionPipeline:
    @pytest.mark.asyncio
    async def test_json_to_catalog_snapshot(self, tmp_path: Path) -> None:
        """Full pipeline: JSON → MetadataBundle → CatalogSnapshot."""
        payload = {
            "tables": [
                {
                    "name": "department",
                    "columns": [
                        {"name": "dept_id", "data_type": "INTEGER", "nullable": False},
                        {"name": "dept_name", "data_type": "VARCHAR2(100)"},
                    ],
                },
            ],
        }
        json_file = tmp_path / "dept.json"
        json_file.write_text(json.dumps(payload), encoding="utf-8")

        service = MetadataIngestionService(JSONMetadataLoader())
        snapshot = await service.ingest(json_file)

        assert isinstance(snapshot, CatalogSnapshot)
        assert len(snapshot.tables) == 1
        assert snapshot.tables[0].name == "department"
        assert snapshot.tables[0].columns[0].data_type == ColumnType.INTEGER
        assert snapshot.tables[0].columns[1].data_type == ColumnType.VARCHAR

    @pytest.mark.asyncio
    async def test_csv_to_catalog_snapshot(self, tmp_path: Path) -> None:
        """Full pipeline: CSV → MetadataBundle → CatalogSnapshot."""
        (tmp_path / "_tables.csv").write_text(
            "name,schema_name,description,aliases,primary_key\n"
            "item,,Ürün tablosu,urun|product,item_id\n",
            encoding="utf-8",
        )
        (tmp_path / "item.csv").write_text(
            "column_name,data_type,nullable,restricted,description,aliases\n"
            "item_id,INTEGER,false,false,PK,\n"
            "item_name,VARCHAR2(200),false,false,Ürün adı,urun_adi\n"
            "price,NUMBER(10;2),true,true,Fiyat,fiyat\n",
            encoding="utf-8",
        )

        service = MetadataIngestionService(CSVMetadataLoader())
        snapshot = await service.ingest(tmp_path)

        assert len(snapshot.tables) == 1
        tbl = snapshot.tables[0]
        assert tbl.name == "item"
        assert tbl.aliases == ["urun", "product"]
        assert tbl.primary_key == ["item_id"]
        assert len(tbl.columns) == 3
        assert tbl.columns[2].restricted is True


# ---------------------------------------------------------------------------
# Synonym ingestion
# ---------------------------------------------------------------------------


class TestSynonymIngestion:
    @pytest.mark.asyncio
    async def test_synonyms_merged_into_aliases(self, tmp_path: Path) -> None:
        """Synonyms from RawTableDef should be merged into TableMetadata.aliases."""
        payload = {
            "tables": [
                {
                    "name": "po_headers_all",
                    "aliases": ["po_headers"],
                    "synonyms": ["purchase_orders", "po_headers"],
                    "columns": [
                        {"name": "header_id", "data_type": "INTEGER"},
                    ],
                },
            ],
        }
        json_file = tmp_path / "syn.json"
        json_file.write_text(json.dumps(payload), encoding="utf-8")

        service = MetadataIngestionService(JSONMetadataLoader())
        snapshot = await service.ingest(json_file)

        tbl = snapshot.tables[0]
        # "po_headers" appears in both aliases and synonyms → deduplicated
        assert "po_headers" in tbl.aliases
        assert "purchase_orders" in tbl.aliases
        assert tbl.aliases.count("po_headers") == 1  # no dupes

    @pytest.mark.asyncio
    async def test_alias_trim_and_dedup(self, tmp_path: Path) -> None:
        """Whitespace-padded and duplicate aliases should be cleaned."""
        payload = {
            "tables": [
                {
                    "name": "XXBT_PDKS_PER_DETAILS_V",
                    "aliases": [" emp ", "emp", "personnel"],
                    "columns": [],
                },
            ],
        }
        json_file = tmp_path / "dedup.json"
        json_file.write_text(json.dumps(payload), encoding="utf-8")

        service = MetadataIngestionService(JSONMetadataLoader())
        snapshot = await service.ingest(json_file)

        assert snapshot.tables[0].aliases == ["emp", "personnel"]


# ---------------------------------------------------------------------------
# Normalized CSV format
# ---------------------------------------------------------------------------


class TestNormalizedCSVLoader:
    @pytest.mark.asyncio
    async def test_load_normalized_csv(self, tmp_path: Path) -> None:
        """Normalized layout: tables.csv + columns.csv."""
        (tmp_path / "tables.csv").write_text(
            "name,schema_name,description,aliases,primary_key,object_type,module,synonyms\n"
            "employee,HR,Personel tablosu,emp|personnel,reg_no,TABLE,HR,calisan\n"
            "department,HR,Departman tablosu,dept,dept_id,TABLE,HR,bolum\n",
            encoding="utf-8",
        )
        (tmp_path / "columns.csv").write_text(
            "table_name,column_name,data_type,nullable,restricted,description,aliases\n"
            "employee,reg_no,INTEGER,false,false,Sicil no,sicil_no\n"
            "employee,salary,NUMBER,true,true,Maaş,maas\n"
            "department,dept_id,INTEGER,false,false,PK,\n"
            "department,dept_name,VARCHAR2(100),false,false,Adı,\n",
            encoding="utf-8",
        )

        loader = CSVMetadataLoader()
        bundle = await loader.load(tmp_path)

        assert len(bundle.tables) == 2
        emp = next(t for t in bundle.tables if t.name == "employee")
        assert emp.object_type == "TABLE"
        assert emp.module == "HR"
        assert emp.synonyms == ["calisan"]
        assert len(emp.columns) == 2

        dept = next(t for t in bundle.tables if t.name == "department")
        assert len(dept.columns) == 2

    @pytest.mark.asyncio
    async def test_load_with_relationships_csv(self, tmp_path: Path) -> None:
        """relationships.csv should be parsed into bundle.relationships."""
        (tmp_path / "tables.csv").write_text(
            "name,schema_name,description,aliases,primary_key\n"
            "employee,HR,Personel,emp,reg_no\n",
            encoding="utf-8",
        )
        (tmp_path / "columns.csv").write_text(
            "table_name,column_name,data_type\n"
            "employee,reg_no,INTEGER\n",
            encoding="utf-8",
        )
        (tmp_path / "relationships.csv").write_text(
            "from_table,from_column,to_table,to_column,relationship_type,constraint_name,description\n"
            "employee,dept_id,department,dept_id,many_to_one,EMP_DEPT_FK,Departman FK\n",
            encoding="utf-8",
        )

        loader = CSVMetadataLoader()
        bundle = await loader.load(tmp_path)

        assert len(bundle.relationships) == 1
        rel = bundle.relationships[0]
        assert rel.from_table == "employee"
        assert rel.constraint_name == "EMP_DEPT_FK"
        assert rel.description == "Departman FK"

    @pytest.mark.asyncio
    async def test_load_with_synonyms_csv(self, tmp_path: Path) -> None:
        """synonyms.csv should append to table synonyms."""
        (tmp_path / "tables.csv").write_text(
            "name,description\n"
            "employee,Personel\n",
            encoding="utf-8",
        )
        (tmp_path / "columns.csv").write_text(
            "table_name,column_name,data_type\n"
            "employee,reg_no,INTEGER\n",
            encoding="utf-8",
        )
        (tmp_path / "synonyms.csv").write_text(
            "table_name,synonym\n"
            "employee,calisan\n"
            "employee,personel\n",
            encoding="utf-8",
        )

        loader = CSVMetadataLoader()
        bundle = await loader.load(tmp_path)

        assert "calisan" in bundle.tables[0].synonyms
        assert "personel" in bundle.tables[0].synonyms

    @pytest.mark.asyncio
    async def test_legacy_csv_still_works(self, tmp_path: Path) -> None:
        """Legacy _tables.csv layout should still function."""
        (tmp_path / "_tables.csv").write_text(
            "name,schema_name,description,aliases,primary_key\n"
            "employee,HR,Personel,emp,reg_no\n",
            encoding="utf-8",
        )
        (tmp_path / "employee.csv").write_text(
            "column_name,data_type,nullable,restricted,description,aliases\n"
            "reg_no,INTEGER,false,false,Sicil,sicil_no\n",
            encoding="utf-8",
        )

        loader = CSVMetadataLoader()
        bundle = await loader.load(tmp_path)

        assert len(bundle.tables) == 1
        assert bundle.tables[0].name == "employee"
        assert len(bundle.tables[0].columns) == 1


# ---------------------------------------------------------------------------
# Unknown type warning
# ---------------------------------------------------------------------------


class TestUnknownTypeWarning:
    def test_unknown_type_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """_map_column_type should warn for unrecognised types."""
        import logging

        with caplog.at_level(logging.WARNING):
            result = _map_column_type("XML_TYPE")

        assert result == ColumnType.VARCHAR
        assert any("Unknown column data-type" in m for m in caplog.messages)

    def test_known_type_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        with caplog.at_level(logging.WARNING):
            _map_column_type("INTEGER")

        assert not any("Unknown" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# Relationship preservation
# ---------------------------------------------------------------------------


class TestRelationshipPreservation:
    @pytest.mark.asyncio
    async def test_relationships_preserved_in_service(self, tmp_path: Path) -> None:
        """Relationships should be preserved in service.last_relationships."""
        payload = {
            "tables": [
                {"name": "XXBT_PDKS_PER_DETAILS_V", "columns": [{"name": "id", "data_type": "INTEGER"}]},
            ],
            "relationships": [
                {
                    "from_table": "XXBT_PDKS_PER_DETAILS_V",
                    "from_column": "dept_id",
                    "to_table": "department",
                    "to_column": "dept_id",
                },
            ],
        }
        json_file = tmp_path / "rel.json"
        json_file.write_text(json.dumps(payload), encoding="utf-8")

        service = MetadataIngestionService(JSONMetadataLoader())
        await service.ingest(json_file)

        rels = service.get_relationships()
        assert len(rels) == 1
        assert rels[0].from_table == "XXBT_PDKS_PER_DETAILS_V"
