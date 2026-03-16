"""In-memory catalog provider with XXBT_PDKS_PER_DETAILS_V table.

This provider is the default for Sprint 1 and tests.  It serves a
hard-coded catalog that mirrors the real Oracle HR view.
"""

from __future__ import annotations

from app.domain.catalog_models import (
    CatalogSnapshot,
    ColumnMetadata,
    ColumnType,
    TableMetadata,
)
from app.providers.catalog.base import CatalogProvider


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
    """Catalog provider backed by an in-memory table list."""

    def __init__(self) -> None:
        self._snapshot = CatalogSnapshot(tables=[_build_employee_table()])

    async def get_snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    async def get_table(self, table_name: str) -> TableMetadata | None:
        return self._snapshot.get_table(table_name)

    async def search_tables(self, query: str) -> list[TableMetadata]:
        return self._snapshot.search_tables(query)
