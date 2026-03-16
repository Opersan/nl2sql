"""Generate a validated 100-question real-provider evaluation dataset.

Output:
    data/eval_dataset_100.json
"""

from __future__ import annotations

import json
from pathlib import Path


def q(
    id_: str,
    domain: str,
    category: str,
    text: str,
    expected_table: str | None,
    expected_intent_type: str,
    wrong_plan_risk: str,
    notes: str,
) -> dict[str, object]:
    return {
        "id": id_,
        "domain": domain,
        "category": category,
        "text": text,
        "expected_table": expected_table,
        "expected_intent_type": expected_intent_type,
        "wrong_plan_risk": wrong_plan_risk,
        "notes": notes,
    }


def build_dataset() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []

    # 40 PO
    po = [
        ("p01", "LISTING", "Onay bekleyen satinalma siparislerini listele", "PO_HEADERS_ALL", "list", "low", "authorization_status: INCOMPLETE/PRE-APPROVED"),
        ("p02", "LISTING", "Son 30 gunde olusturulan PO basliklarini goster", "PO_HEADERS_ALL", "list", "low", "creation_date >= SYSDATE-30"),
        ("p03", "LISTING", "Acik siparisleri getir", "PO_HEADERS_ALL", "list", "low", "authorization_status != CLOSED"),
        ("p04", "LISTING", "Kapali PO basliklarini listele", "PO_HEADERS_ALL", "list", "low", "authorization_status = CLOSED"),
        ("p05", "LISTING", "Iptal edilmis siparisleri getir", "PO_HEADERS_ALL", "list", "medium", "status vocabulary risk"),
        ("p06", "LISTING", "USD cinsinden siparis basliklarini listele", "PO_HEADERS_ALL", "list", "low", "currency_code=USD"),
        ("p07", "LISTING", "Standart tipte siparisleri listele", "PO_HEADERS_ALL", "list", "medium", "type_lookup_code=STANDARD"),
        ("p08", "LISTING", "Tedarikci ID 501'e ait siparisleri getir", "PO_HEADERS_ALL", "list", "low", "vendor_id=501"),
        ("p09", "LISTING", "Bu hafta olusturulan siparisleri listele", "PO_HEADERS_ALL", "list", "medium", "last 7 days"),
        ("p10", "LISTING", "En son olusturulan 10 siparis kaydini getir", "PO_HEADERS_ALL", "list", "low", "ORDER BY creation_date DESC LIMIT 10"),
        ("p11", "AGGREGATION", "Tedarikciye gore PO sayisini goster", "PO_HEADERS_ALL", "aggregation", "low", "COUNT GROUP BY vendor_id"),
        ("p12", "AGGREGATION", "Para birimine gore siparis sayisini goster", "PO_HEADERS_ALL", "aggregation", "low", "COUNT GROUP BY currency_code"),
        ("p13", "AGGREGATION", "Siparis basliklarini say", "PO_HEADERS_ALL", "aggregation", "low", "COUNT(*)"),
        ("p14", "AGGREGATION", "Onay durumuna gore PO dagilimi", "PO_HEADERS_ALL", "aggregation", "low", "COUNT GROUP BY authorization_status"),
        ("p15", "AGGREGATION", "Toplam dagitim miktarini hesapla", "PO_DISTRIBUTIONS_ALL", "aggregation", "high", "SUM(quantity_ordered)"),
        ("p16", "AGGREGATION", "Siparis basina ortalama kalem sayisi", "PO_LINES_ALL", "aggregation", "high", "header-line relation"),
        ("p17", "AGGREGATION", "Hangi tedarikci kac siparis vermis", "PO_HEADERS_ALL", "aggregation", "low", "COUNT GROUP BY vendor_id"),
        ("p18", "AGGREGATION", "Aylik PO olusturma sayisini goster", "PO_HEADERS_ALL", "aggregation", "medium", "TRUNC(date,'MM')"),
        ("p19", "AGGREGATION", "Dagitim tablosundaki toplam kalem sayisi", "PO_DISTRIBUTIONS_ALL", "aggregation", "medium", "COUNT(*) dist table"),
        ("p20", "AGGREGATION", "PO_HEADERS_ALL tablosundaki kayitlari say", "PO_HEADERS_ALL", "aggregation", "low", "explicit table count"),
        ("p21", "FILTER", "Son 1 yilda olusturulan siparisleri getir", "PO_HEADERS_ALL", "list", "low", "creation_date>=SYSDATE-365"),
        ("p22", "FILTER", "Bugun onaylanan siparisleri getir", "PO_HEADERS_ALL", "list", "low", "today + approved"),
        ("p23", "FILTER", "Toplam tutari 100.000 TL uzerinde olan siparisler", "PO_HEADERS_ALL", "list", "high", "no total column; join needed"),
        ("p24", "FILTER", "APPROVED durumundaki siparisleri getir", "PO_HEADERS_ALL", "list", "low", "authorization_status=APPROVED"),
        ("p25", "FILTER", "Dagitim tutari sifir olan kalemleri listele", "PO_DISTRIBUTIONS_ALL", "list", "high", "dist table filter"),
        ("p26", "FILTER", "Kapali olmayan tum siparisleri getir", "PO_HEADERS_ALL", "list", "low", "status != CLOSED"),
        ("p27", "FILTER", "Gecen ay acilan PO'lari getir", "PO_HEADERS_ALL", "list", "medium", "previous month window"),
        ("p28", "FILTER", "Son 7 gunde acilan onay bekleyen PO'lar", "PO_HEADERS_ALL", "list", "low", "date + status"),
        ("p29", "FILTER", "Tedarikci site kodu BESTI olan siparisleri goster", "PO_HEADERS_ALL", "list", "medium", "possible metadata gap"),
        ("p30", "FILTER", "Teslim tarihi gecmis siparis satirlari", "PO_LINE_LOCATIONS_ALL", "list", "high", "shipment-level date intent"),
        ("p31", "JOIN", "Urun bazinda PO satir sayisi", "PO_LINES_ALL", "aggregation", "high", "join with MTL_SYSTEM_ITEMS_B"),
        ("p32", "JOIN", "Dagitim bazinda tutar analizi", "PO_DISTRIBUTIONS_ALL", "aggregation", "medium", "header-line-ship-dist join"),
        ("p33", "JOIN", "PO dagitim tutarlarini kalem kalem goster", "PO_DISTRIBUTIONS_ALL", "list", "medium", "join chain"),
        ("p34", "JOIN", "Malzeme aciklamasiyla siparis kalemlerini listele", "PO_LINES_ALL", "list", "high", "line-item join"),
        ("p35", "JOIN", "Sevkiyat lokasyonu bilgisiyle siparis detayi", "PO_HEADERS_ALL", "list", "high", "header to shipment via lines"),
        ("p36", "JOIN", "Hesap kombinasyonu bazinda dagitim tutari", "PO_DISTRIBUTIONS_ALL", "aggregation", "medium", "GROUP BY code_combination_id"),
        ("p37", "JOIN", "Bilgisayar iceren siparisleri getir", "PO_LINES_ALL", "list", "high", "item description / join risk"),
        ("p38", "JOIN", "Yazici kalemlerini iceren PO basliklarini goster", "PO_HEADERS_ALL", "list", "high", "header-line join"),
        ("p39", "JOIN", "Fatura edilmis miktari sifir olan sevkiyatlari goster", "PO_LINE_LOCATIONS_ALL", "list", "medium", "quantity_billed=0"),
        ("p40", "LISTING", "Son 7 gunde acilan ve onay bekleyen PO basliklarini tedarikci adiyla listele", "PO_HEADERS_ALL", "list", "medium", "complex filter + missing vendor name"),
    ]
    out.extend(q(pid, "PO", cat, text, table, intent, risk, notes) for pid, cat, text, table, intent, risk, notes in po)

    # 40 EMP
    emp = [
        ("e01", "LISTING", "Aktif calisanlari listele", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "CIKIS_TARIHI IS NULL"),
        ("e02", "LISTING", "IT departmanindaki calisanlari goster", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "BIRIM_ADI filter"),
        ("e03", "LISTING", "Istanbul'daki calisanlari getir", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "LOCATION_ADI filter"),
        ("e04", "LISTING", "Bordrolu calisanlari listele", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "BORDROLU=1"),
        ("e05", "LISTING", "Stajyer calisanlari goster", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "STAJYER=1"),
        ("e06", "LISTING", "Yonetici unvanli calisanlari listele", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "UNVAN filter"),
        ("e07", "LISTING", "E-posta adresi olan calisanlari getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "EMAIL IS NOT NULL"),
        ("e08", "LISTING", "Son ise alinan 10 calisani getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "ORDER BY ISE_GIRIS_TARIHI DESC"),
        ("e09", "LISTING", "Masraf merkezi BT-01 olan calisanlari getir", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "MASRAF_MERKEZI filter"),
        ("e10", "LISTING", "Cikis tarihi olmayan calisanlari listele", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "active synonym"),
        ("e11", "AGGREGATION", "Departman basina calisan sayisi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "COUNT GROUP BY BIRIM_ADI"),
        ("e12", "AGGREGATION", "Istanbul'daki calisanlari say", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "COUNT + location filter"),
        ("e13", "AGGREGATION", "Organizasyon bazinda personel dagilimi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "COUNT GROUP BY ORGANIZATION_ADI"),
        ("e14", "AGGREGATION", "Toplam aktif calisan sayisi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "COUNT active"),
        ("e15", "AGGREGATION", "Unvana gore calisan sayisi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "COUNT GROUP BY UNVAN"),
        ("e16", "AGGREGATION", "Hangi departmanda kac calisan var", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "COUNT GROUP BY BIRIM_ADI"),
        ("e17", "AGGREGATION", "Lokasyon bazinda personel sayisi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "COUNT GROUP BY LOCATION_ADI"),
        ("e18", "AGGREGATION", "2024 yilinda ise alinan calisan sayisi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "year window"),
        ("e19", "AGGREGATION", "Masraf merkezi bazinda calisan dagilimi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "COUNT GROUP BY MASRAF_MERKEZI"),
        ("e20", "AGGREGATION", "Birim ve lokasyon bazinda gruplandirmali calisan sayisi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "double group-by"),
        ("e21", "FILTER", "Son 1 yil icinde ise alinanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "ISE_GIRIS_TARIHI>=SYSDATE-365"),
        ("e22", "FILTER", "2024 yilinda ise giren calisanlar kimler", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "2024 range"),
        ("e23", "FILTER", "Son 6 ayda ise giren calisanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "last 6 months"),
        ("e24", "FILTER", "10 yildan fazla suredir calisan personel", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "hire date <= today-3650"),
        ("e25", "FILTER", "Maasi 50.000 TL uzerinde olan calisanlari bul", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no MAAS column"),
        ("e26", "FILTER", "2023 oncesinde ise giren calisanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "hire date < 2023"),
        ("e27", "FILTER", "Son 30 gunde ise baslayan yeni calisanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "last 30 days"),
        ("e28", "FILTER", "BT birimi calisanlarini getir", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "BIRIM_ADI like BT"),
        ("e29", "FILTER", "Dahili telefonu olan calisanlari getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "DAHILI IS NOT NULL"),
        ("e30", "FILTER", "Performans notu 4 ve uzeri olan calisanlari getir", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no performance column"),
        ("e31", "LISTING", "En yuksek maasli 5 calisan kimdir", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no MAAS column"),
        ("e32", "LISTING", "Yonetici pozisyonundaki calisanlari listele", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "title semantics"),
        ("e33", "AGGREGATION", "Her birimin aktif calisan sayisi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "active + group-by"),
        ("e34", "LISTING", "Organizasyon adiyla birlikte calisan detayi", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "select organization"),
        ("e35", "LISTING", "Unvan ve birim bilgisiyle calisan listesi", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "select UNVAN/BIRIM_ADI"),
        ("e36", "LISTING", "Gorev tanimi dolu olan calisanlari getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "GOREV_TANIMI IS NOT NULL"),
        ("e37", "FILTER", "En uzun suredir calisan 10 kisi", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "ORDER BY ISE_GIRIS_TARIHI ASC"),
        ("e38", "LISTING", "Son 6 ayda terfi eden calisanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no promotion column"),
        ("e39", "LISTING", "Sicil numarasiyla birlikte tum personeli listele", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "basic list projection"),
        ("e40", "FILTER", "Cikis tarihi 2025 yilinda olan calisanlari getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "exit date range"),
    ]
    out.extend(q(eid, "EMP", cat, text, table, intent, risk, notes) for eid, cat, text, table, intent, risk, notes in emp)

    # 20 ambiguous/cross/invalid
    mixed = [
        ("x01", "AMBIGUOUS", "AMBIGUOUS", "Calisanlar", None, "clarification", "low", "single token"),
        ("x02", "AMBIGUOUS", "AMBIGUOUS", "Siparisler", None, "clarification", "low", "single token"),
        ("x03", "AMBIGUOUS", "AMBIGUOUS", "Departmanlar", None, "clarification", "low", "no table signal"),
        ("x04", "AMBIGUOUS", "AMBIGUOUS", "Veri getir", None, "clarification", "low", "fully ambiguous"),
        ("x05", "AMBIGUOUS", "AMBIGUOUS", "Listele", None, "clarification", "low", "verb only"),
        ("x06", "AMBIGUOUS", "AMBIGUOUS", "Kac tane var", None, "clarification", "low", "no domain"),
        ("x07", "AMBIGUOUS", "AMBIGUOUS", "Bilgi ver", None, "clarification", "low", "fully ambiguous"),
        ("x08", "AMBIGUOUS", "AMBIGUOUS", "Goster", None, "clarification", "low", "verb only"),
        ("x09", "CROSS", "CROSS_DOMAIN", "Tedarikci mi calisan mi getireyim", None, "clarification", "low", "explicit cross domain"),
        ("x10", "CROSS", "CROSS_DOMAIN", "Organizasyon bilgilerini goster", None, "clarification", "medium", "could map to EMP ORGANIZATION_ADI"),
        ("x11", "CROSS", "CROSS_DOMAIN", "Tum kayitlari getir", None, "clarification", "high", "broad unsafe style"),
        ("x12", "CROSS", "CROSS_DOMAIN", "Sistem verilerini listele", None, "clarification", "medium", "domain unclear"),
        ("x13", "CROSS", "CROSS_DOMAIN", "Butun verileri goster", None, "clarification", "high", "domain unclear"),
        ("x14", "CROSS", "CROSS_DOMAIN", "Hem PO hem personel bilgisi istiyorum", None, "clarification", "low", "multi-domain ask"),
        ("x15", "INVALID", "INVALID", "Kredi karti numaralarini getir", None, "clarification_or_error", "low", "out of scope"),
        ("x16", "INVALID", "INVALID", "Gizli verilere erismek istiyorum", None, "clarification_or_error", "low", "out of scope"),
        ("x17", "INVALID", "INVALID", "Dogum tarihlerini listele", "XXBT_PDKS_PER_DETAILS_V", "validation_error", "low", "restricted DOGUM_TARIHI"),
        ("x18", "INVALID", "INVALID", "Sifreleri ver", None, "clarification_or_error", "low", "out of scope"),
        ("x19", "INVALID", "INVALID", "Kisisel verileri listele", None, "clarification_or_error", "medium", "privacy sensitive"),
        ("x20", "INVALID", "INVALID", "Maas bilgilerini goster", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no MAAS column"),
    ]
    out.extend(q(mid, dom, cat, text, table, intent, risk, notes) for mid, dom, cat, text, table, intent, risk, notes in mixed)

    return out


def main() -> None:
    data = build_dataset()
    if len(data) != 100:
        raise SystemExit(f"Expected 100 questions, got {len(data)}")

    ids = [str(item["id"]) for item in data]
    if len(ids) != len(set(ids)):
        raise SystemExit("Question IDs are not unique")

    out_path = Path("data/eval_dataset_100.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(data)} questions to {out_path}")


if __name__ == "__main__":
    main()
