"""In-memory catalog provider — single source of truth for ALL tables.

This provider is the authoritative catalog for both the API and the eval
pipeline.  Any schema change must be made here and nowhere else.

Tables:
  PO domain  : PO_HEADERS_ALL, PO_LINES_ALL, PO_LINE_LOCATIONS_ALL,
               PO_DISTRIBUTIONS_ALL, MTL_SYSTEM_ITEMS_B
  HR domain  : XXBT_PDKS_PER_DETAILS_V
"""

from __future__ import annotations

import hashlib

from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    ForeignKeyMetadata,
    RelationshipMetadata,
    TableMetadata,
)
from app.providers.catalog.base import CatalogProvider


def catalog_fingerprint(snapshot: CatalogSnapshot) -> str:
    """Return a short deterministic fingerprint of all table/column names.

    Used to assert that API and eval use an identical catalog at runtime.
    Format: first 12 hex chars of SHA-256 over sorted table:column data.
    """
    parts: list[str] = []
    for t in sorted(snapshot.tables, key=lambda x: x.name):
        cols = ",".join(sorted(c.name for c in t.columns))
        parts.append(f"{t.name}:{cols}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def _build_po_tables() -> tuple[list[TableMetadata], list[RelationshipMetadata]]:
    """Build PO domain table definitions.  Single authoritative source."""

    def _col(name: str, dtype: ColumnType = ColumnType.NUMBER, **kw) -> ColumnMetadata:
        return ColumnMetadata(name=name, data_type=dtype, **kw)

    ph = TableMetadata(
        name="PO_HEADERS_ALL",
        description="Satın alma siparişi başlıkları",
        aliases=["po headers", "po_headers", "satinalma", "siparis basliklari"],
        primary_key=["po_header_id"],
        columns=[
            _col("po_header_id", nullable=False),
            _col("vendor_id"),
            _col("creation_date",        ColumnType.DATE),
            _col("authorization_status", ColumnType.VARCHAR),
            _col("currency_code",        ColumnType.VARCHAR),
            _col("type_lookup_code",     ColumnType.VARCHAR),
        ],
    )
    pl = TableMetadata(
        name="PO_LINES_ALL",
        description="Satın alma siparişi kalemleri",
        aliases=["po lines", "po_lines", "siparis kalemleri"],
        primary_key=["po_line_id"],
        foreign_keys=[ForeignKeyMetadata(
            column="po_header_id",
            referenced_table="PO_HEADERS_ALL",
            referenced_column="po_header_id",
        )],
        columns=[
            _col("po_line_id",       nullable=False),
            _col("po_header_id"),
            _col("item_id"),
            _col("line_num"),
            _col("item_description", ColumnType.VARCHAR),
            _col("quantity"),
            _col("unit_price"),
        ],
    )
    pll = TableMetadata(
        name="PO_LINE_LOCATIONS_ALL",
        description="Sevkiyat lokasyonları",
        aliases=["po shipments", "po_line_locations", "sevkiyat"],
        primary_key=["line_location_id"],
        foreign_keys=[ForeignKeyMetadata(
            column="po_line_id",
            referenced_table="PO_LINES_ALL",
            referenced_column="po_line_id",
        )],
        columns=[
            _col("line_location_id", nullable=False),
            _col("po_line_id"),
            _col("quantity_received"),
            _col("quantity_billed"),
        ],
    )
    pd_ = TableMetadata(
        name="PO_DISTRIBUTIONS_ALL",
        description="Dağıtım satırları",
        aliases=["po distributions", "dagitim"],
        primary_key=["po_distribution_id"],
        foreign_keys=[ForeignKeyMetadata(
            column="line_location_id",
            referenced_table="PO_LINE_LOCATIONS_ALL",
            referenced_column="line_location_id",
        )],
        columns=[
            _col("po_distribution_id",  nullable=False),
            _col("line_location_id"),
            _col("quantity_ordered"),
            _col("code_combination_id"),
            _col("unit_price"),
        ],
    )
    mtl = TableMetadata(
        name="MTL_SYSTEM_ITEMS_B",
        description="Malzeme ana verileri",
        aliases=["items", "malzeme", "stok"],
        primary_key=["inventory_item_id"],
        columns=[
            _col("inventory_item_id", nullable=False),
            _col("segment1",    ColumnType.VARCHAR),
            _col("description", ColumnType.VARCHAR),
        ],
    )
    rels = [
        RelationshipMetadata(from_table="PO_HEADERS_ALL",        from_column="po_header_id",    to_table="PO_LINES_ALL",           to_column="po_header_id"),
        RelationshipMetadata(from_table="PO_LINES_ALL",          from_column="po_line_id",       to_table="PO_LINE_LOCATIONS_ALL",  to_column="po_line_id"),
        RelationshipMetadata(from_table="PO_LINE_LOCATIONS_ALL", from_column="line_location_id", to_table="PO_DISTRIBUTIONS_ALL",   to_column="line_location_id"),
        RelationshipMetadata(from_table="PO_LINES_ALL",          from_column="item_id",          to_table="MTL_SYSTEM_ITEMS_B",     to_column="inventory_item_id"),
    ]
    return [ph, pl, pll, pd_, mtl], rels


def _build_employee_table() -> TableMetadata:
    """Construct XXBT_PDKS_PER_DETAILS_V table metadata."""
    return TableMetadata(
        name="XXBT_PDKS_PER_DETAILS_V",
        description=(
            "PDKS ile entegre çalışan personel görünümüdür. Her satır bir çalışanı temsil eder. "
            "ISE_GIRIS_TARIHI işe giriş tarihini, CIKIS_TARIHI ise işten ayrılma tarihini tutar. "
            "CIKIS_TARIHI NULL olan kayıtlar aktif çalışanlardır."
        ),
        aliases=["employee", "employees", "personel", "personnel", "calisan", "çalışan", "pdks personel", "ik personel"],
        primary_key=["PERSON_ID"],
        columns=[
            ColumnMetadata(name="PERSON_ID",        data_type=ColumnType.NUMBER,  nullable=False, description="Benzersiz personel kimliği"),
            ColumnMetadata(name="SICIL_NO",          data_type=ColumnType.VARCHAR, nullable=False, description="Sicil numarası",                aliases=["sicil_no", "reg_no", "employee_no"]),
            ColumnMetadata(name="AD",                data_type=ColumnType.VARCHAR, nullable=False, description="Çalışanın adı",                  aliases=["ad", "first_name", "name"]),
            ColumnMetadata(name="SOYAD",             data_type=ColumnType.VARCHAR, nullable=False, description="Çalışanın soyadı",               aliases=["soyad", "last_name", "surname"]),
            ColumnMetadata(name="FULL_NAME",         data_type=ColumnType.VARCHAR, nullable=True,  description="Ad soyad",                       aliases=["full_name", "adsoyad"]),
            ColumnMetadata(name="BIRIM_ID",          data_type=ColumnType.NUMBER,  nullable=True,  description="Birim teknik anahtarı"),
            ColumnMetadata(name="BIRIM_ADI",         data_type=ColumnType.VARCHAR, nullable=True,  description="Birim adı",                      aliases=["birim", "unit_name", "department"]),
            ColumnMetadata(name="ORGANIZATION_ADI",  data_type=ColumnType.VARCHAR, nullable=True,  description="Organizasyon adı"),
            ColumnMetadata(name="LOCATION_ID",       data_type=ColumnType.NUMBER,  nullable=True,  description="Lokasyon teknik anahtarı"),
            ColumnMetadata(name="LOCATION_ADI",      data_type=ColumnType.VARCHAR, nullable=True,  description="Lokasyon adı",                   aliases=["lokasyon", "location_name"]),
            ColumnMetadata(name="GOREV_ID",          data_type=ColumnType.NUMBER,  nullable=True,  description="Görev kimliği"),
            ColumnMetadata(name="GOREV_TANIMI",      data_type=ColumnType.VARCHAR, nullable=True,  description="Görev tanımı",                   aliases=["gorev", "job_title"]),
            ColumnMetadata(name="UNVAN",             data_type=ColumnType.VARCHAR, nullable=True,  description="Unvan",                          aliases=["unvan", "title"]),
            ColumnMetadata(name="UNVAN_ID",          data_type=ColumnType.NUMBER,  nullable=True,  description="Unvan teknik kimliği"),
            ColumnMetadata(name="GOREV_GURUBU",      data_type=ColumnType.VARCHAR, nullable=True,  description="Görev grubu"),
            ColumnMetadata(name="GOREV_FULL",        data_type=ColumnType.VARCHAR, nullable=True,  description="Görev tam adı"),
            ColumnMetadata(name="BOLUM",             data_type=ColumnType.VARCHAR, nullable=True,  description="Bölüm"),
            ColumnMetadata(name="CALISAN_TIPI",      data_type=ColumnType.VARCHAR, nullable=True,  description="Çalışan tipi"),
            ColumnMetadata(name="EMPLOYEE_CATEGORY", data_type=ColumnType.VARCHAR, nullable=True,  description="Çalışan kategorisi"),
            ColumnMetadata(name="ISE_GIRIS_TARIHI",  data_type=ColumnType.DATE,    nullable=True,  description="İşe giriş tarihi",               aliases=["hire_date", "start_date", "ise_baslama"]),
            ColumnMetadata(name="CIKIS_TARIHI",      data_type=ColumnType.DATE,    nullable=True,  description="İşten ayrılış tarihi (NULL=aktif)", aliases=["quit_date", "leave_date", "ayrilma_tarihi"]),
            ColumnMetadata(name="ISTEN_CIKTI",       data_type=ColumnType.VARCHAR, nullable=True,  description="İşten çıkış durumu"),
            ColumnMetadata(name="ASSG_START_DATE",   data_type=ColumnType.DATE,    nullable=True,  description="Assignment başlangıç tarihi"),
            ColumnMetadata(name="ASSG_END_DATE",     data_type=ColumnType.DATE,    nullable=True,  description="Assignment bitiş tarihi"),
            ColumnMetadata(name="PER_START_DATE",    data_type=ColumnType.DATE,    nullable=True,  description="Person başlangıç tarihi"),
            ColumnMetadata(name="IZIN_KIDEM_TARIHI", data_type=ColumnType.DATE,    nullable=True,  description="İzin kıdem tarihi"),
            ColumnMetadata(name="EMAIL",             data_type=ColumnType.VARCHAR, nullable=True,  description="Kurumsal e-posta",               aliases=["email", "e-posta"]),
            ColumnMetadata(name="USER_NAME",         data_type=ColumnType.VARCHAR, nullable=True,  description="Uygulama kullanıcı adı"),
            ColumnMetadata(name="AD_USER",           data_type=ColumnType.VARCHAR, nullable=True,  description="AD kullanıcı hesabı"),
            ColumnMetadata(name="DAHILI",            data_type=ColumnType.VARCHAR, nullable=True,  description="Dahili telefon",                 aliases=["dahili", "extension_no"]),
            ColumnMetadata(name="BORDROLU",          data_type=ColumnType.NUMBER,  nullable=True,  description="Bordrolu bayrağı",              aliases=["payroll_flag"]),
            ColumnMetadata(name="STAJYER",           data_type=ColumnType.NUMBER,  nullable=True,  description="Stajyer bayrağı",               aliases=["employment_type"]),
            ColumnMetadata(name="YON_SICIL_NO",      data_type=ColumnType.VARCHAR, nullable=True,  description="Yönetici sicil no"),
            ColumnMetadata(name="YON_FULL_NAME",     data_type=ColumnType.VARCHAR, nullable=True,  description="Yönetici ad soyad"),
            ColumnMetadata(name="YON_USER_NAME",     data_type=ColumnType.VARCHAR, nullable=True,  description="Yönetici kullanıcı adı"),
            ColumnMetadata(name="YON_PERSON_ID",     data_type=ColumnType.NUMBER,  nullable=True,  description="Yönetici personel kimliği"),
            ColumnMetadata(name="MASRAF_MERKEZI",    data_type=ColumnType.VARCHAR, nullable=True,  description="Masraf merkezi"),
            ColumnMetadata(name="ISYERI",            data_type=ColumnType.VARCHAR, nullable=True,  description="İşyeri"),
            ColumnMetadata(name="BIRIM_ETKINLIK_SONU", data_type=ColumnType.DATE,  nullable=True,  description="Birim etkinlik bitiş tarihi"),
            ColumnMetadata(name="CODE_COMBINATION",  data_type=ColumnType.VARCHAR, nullable=True,  description="Kod kombinasyonu"),
            ColumnMetadata(name="PDS_UST_DEPARTMAN", data_type=ColumnType.VARCHAR, nullable=True,  description="PDS üst departman"),
            ColumnMetadata(name="PDS_KATEGORI",      data_type=ColumnType.VARCHAR, nullable=True,  description="PDS kategori"),
            ColumnMetadata(name="PDS_ILK_YONETICI",  data_type=ColumnType.VARCHAR, nullable=True,  description="PDS ilk yönetici"),
            ColumnMetadata(name="PDS_IKINCI_YONETICI", data_type=ColumnType.VARCHAR, nullable=True, description="PDS ikinci yönetici"),
            ColumnMetadata(name="DG_GOSTER",         data_type=ColumnType.VARCHAR, nullable=True,  description="DG göster bayrağı"),
            ColumnMetadata(name="LAST_UPDATE_DATE",  data_type=ColumnType.DATE,    nullable=True,  description="Son güncelleme tarihi"),
            ColumnMetadata(name="LAST_UPDATED_BY",   data_type=ColumnType.VARCHAR, nullable=True,  description="Son güncelleyen"),
            ColumnMetadata(name="CREATION_DATE",     data_type=ColumnType.DATE,    nullable=True,  description="Oluşturma tarihi"),
            ColumnMetadata(name="CREATED_BY",        data_type=ColumnType.VARCHAR, nullable=True,  description="Oluşturan"),
            # Restricted columns
            ColumnMetadata(name="DOGUM_TARIHI",      data_type=ColumnType.DATE,    nullable=True,  restricted=True, description="Doğum tarihi (kısıtlı)", aliases=["birth_date", "dogum_tarihi"]),
            ColumnMetadata(name="TC_NO",             data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="T.C. kimlik no (kısıtlı)"),
            ColumnMetadata(name="KANGRUBU",          data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="Kan grubu (kısıtlı)"),
            ColumnMetadata(name="CINSIYET",          data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="Cinsiyet (kısıtlı)"),
            ColumnMetadata(name="MEDENI_HAL",        data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="Medeni hal (kısıtlı)"),
            ColumnMetadata(name="OGRENIM_DURUMU",    data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="Öğrenim durumu (kısıtlı)"),
            ColumnMetadata(name="MOBILE",            data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="Mobil telefon (kısıtlı)"),
            ColumnMetadata(name="IBAN_TR",           data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="TL IBAN (kısıtlı)"),
            ColumnMetadata(name="IBAN_USD",          data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="USD IBAN (kısıtlı)"),
            ColumnMetadata(name="IBAN_EUR",          data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="EUR IBAN (kısıtlı)"),
            ColumnMetadata(name="RESIM",             data_type=ColumnType.VARCHAR, nullable=True,  restricted=True, description="Resim referansı (kısıtlı)"),
        ],
    )


class InMemoryCatalogProvider(CatalogProvider):
    """Catalog provider backed by an in-memory table list.

    Single source of truth for ALL tables.  Used by both the API and the
    eval pipeline — never instantiate a different catalog provider in
    scripts.
    """

    def __init__(self) -> None:
        po_tables, po_rels = _build_po_tables()
        self._snapshot = CatalogSnapshot(
            tables=[*po_tables, _build_employee_table()],
            relationships=po_rels,
        )

    async def get_snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    async def get_table(self, table_name: str) -> TableMetadata | None:
        return self._snapshot.get_table(table_name)

    async def search_tables(self, query: str) -> list[TableMetadata]:
        return self._snapshot.search_tables(query)
