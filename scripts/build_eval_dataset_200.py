"""Generate a 200-question real-provider evaluation dataset.

Distribution:
- 80 PO
- 80 EMP
- 40 CROSS/AMBIGUOUS/INVALID

Output:
    data/eval_dataset_200.json
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

    po_80 = [
        ("p01", "LISTING", "Onay bekleyen satınalma siparişlerini listele", "PO_HEADERS_ALL", "list", "low", "authorization_status filter"),
        ("p02", "LISTING", "Son 30 günde açılan PO başlıklarını getir", "PO_HEADERS_ALL", "list", "low", "creation_date window"),
        ("p03", "LISTING", "Kapalı siparişleri göster", "PO_HEADERS_ALL", "list", "low", "status closed"),
        ("p04", "LISTING", "Açık siparişleri listele", "PO_HEADERS_ALL", "list", "low", "status != CLOSED"),
        ("p05", "LISTING", "USD para birimindeki siparişleri getir", "PO_HEADERS_ALL", "list", "low", "currency filter"),
        ("p06", "LISTING", "Tedarikçi 501 için sipariş başlıklarını listele", "PO_HEADERS_ALL", "list", "low", "vendor filter"),
        ("p07", "LISTING", "Bu ay oluşturulan siparişleri getir", "PO_HEADERS_ALL", "list", "medium", "current month window"),
        ("p08", "LISTING", "Son oluşturulan 10 PO kaydını göster", "PO_HEADERS_ALL", "list", "low", "order+limit"),
        ("p09", "LISTING", "Standart tip siparişleri listele", "PO_HEADERS_ALL", "list", "medium", "type_lookup_code risk"),
        ("p10", "LISTING", "Onaysız siparişleri getir", "PO_HEADERS_ALL", "list", "low", "authorization_status"),
        ("p11", "AGGREGATION", "Tedarikçiye göre PO sayısını ver", "PO_HEADERS_ALL", "aggregation", "low", "count group by vendor"),
        ("p12", "AGGREGATION", "Para birimine göre sipariş sayısını getir", "PO_HEADERS_ALL", "aggregation", "low", "count group by currency"),
        ("p13", "AGGREGATION", "Toplam PO başlığı sayısını ver", "PO_HEADERS_ALL", "aggregation", "low", "count all"),
        ("p14", "AGGREGATION", "Onay durumuna göre dağılımı göster", "PO_HEADERS_ALL", "aggregation", "low", "status histogram"),
        ("p15", "AGGREGATION", "Dağıtım tablosunda toplam quantity_ordered nedir", "PO_DISTRIBUTIONS_ALL", "aggregation", "low", "sum quantity_ordered"),
        ("p16", "AGGREGATION", "Kalem bazında toplam miktarları göster", "PO_LINES_ALL", "aggregation", "low", "sum quantity by line"),
        ("p17", "AGGREGATION", "Aylık PO oluşturma sayısını ver", "PO_HEADERS_ALL", "aggregation", "medium", "date trunc"),
        ("p18", "AGGREGATION", "Kaç farklı tedarikçiden sipariş var", "PO_HEADERS_ALL", "aggregation", "medium", "distinct risk"),
        ("p19", "AGGREGATION", "PO_LINES_ALL kaydını say", "PO_LINES_ALL", "aggregation", "low", "line count"),
        ("p20", "AGGREGATION", "PO_DISTRIBUTIONS_ALL kaydını say", "PO_DISTRIBUTIONS_ALL", "aggregation", "low", "dist count"),
        ("p21", "FILTER", "Son 7 günde açılan PO'ları getir", "PO_HEADERS_ALL", "list", "low", "7-day window"),
        ("p22", "FILTER", "Son 1 yıldaki siparişleri listele", "PO_HEADERS_ALL", "list", "low", "365-day window"),
        ("p23", "FILTER", "APPROVED durumundaki siparişleri getir", "PO_HEADERS_ALL", "list", "low", "status filter"),
        ("p24", "FILTER", "CLOSED olmayan siparişleri getir", "PO_HEADERS_ALL", "list", "low", "status neq"),
        ("p25", "FILTER", "Geçen ay açılan siparişleri ver", "PO_HEADERS_ALL", "list", "medium", "prev month"),
        ("p26", "FILTER", "Teslim alınan miktarı 0 olan sevkiyatları getir", "PO_LINE_LOCATIONS_ALL", "list", "medium", "shipment filter"),
        ("p27", "FILTER", "Fatura edilen miktarı 0 olan sevkiyatları getir", "PO_LINE_LOCATIONS_ALL", "list", "medium", "quantity_billed filter"),
        ("p28", "FILTER", "Tedarikçi site kodu BESTI olan siparişleri göster", "PO_HEADERS_ALL", "list", "high", "metadata gap risk"),
        ("p29", "FILTER", "Toplam tutarı 100000 üstü siparişler", "PO_HEADERS_ALL", "list", "high", "needs computed amount"),
        ("p30", "FILTER", "Teslim tarihi geçmiş sevkiyatları getir", "PO_LINE_LOCATIONS_ALL", "list", "high", "date column ambiguity"),
        ("p31", "JOIN", "Ürün koduna göre sipariş satır sayısı", "PO_LINES_ALL", "aggregation", "high", "join with items"),
        ("p32", "JOIN", "Malzeme açıklamasıyla kalemleri listele", "PO_LINES_ALL", "list", "high", "lines-items join"),
        ("p33", "JOIN", "Dağıtım bazında miktar analizi", "PO_DISTRIBUTIONS_ALL", "aggregation", "medium", "header-line-ship-dist"),
        ("p34", "JOIN", "PO dağıtım tutarlarını satır satır göster", "PO_DISTRIBUTIONS_ALL", "list", "medium", "join chain"),
        ("p35", "JOIN", "Kalem ve sevkiyat detaylarını birlikte getir", "PO_LINE_LOCATIONS_ALL", "list", "medium", "line-shipment join"),
        ("p36", "JOIN", "Hesap kombinasyonu bazında dağıtım adetleri", "PO_DISTRIBUTIONS_ALL", "aggregation", "medium", "group by code_combination_id"),
        ("p37", "JOIN", "Bilgisayar geçen sipariş kalemlerini getir", "PO_LINES_ALL", "list", "high", "description like"),
        ("p38", "JOIN", "Yazıcı geçen kalemlere ait PO başlıklarını getir", "PO_HEADERS_ALL", "list", "high", "header-line projection"),
        ("p39", "JOIN", "Kalem miktarı ile birim fiyatı birlikte göster", "PO_LINES_ALL", "list", "low", "projection"),
        ("p40", "JOIN", "Satır ve dağıtım tablolarını birleştirip getir", "PO_DISTRIBUTIONS_ALL", "list", "medium", "join chain"),
        ("p41", "LISTING", "Bugün oluşturulan siparişleri listele", "PO_HEADERS_ALL", "list", "low", "today"),
        ("p42", "LISTING", "Son 90 günün PO başlıklarını getir", "PO_HEADERS_ALL", "list", "low", "90-day window"),
        ("p43", "LISTING", "PRE-APPROVED siparişleri göster", "PO_HEADERS_ALL", "list", "medium", "status value variant"),
        ("p44", "LISTING", "EUR para birimli siparişleri getir", "PO_HEADERS_ALL", "list", "low", "currency filter"),
        ("p45", "LISTING", "Son 20 siparişi listele", "PO_HEADERS_ALL", "list", "low", "order+limit"),
        ("p46", "LISTING", "PO numarası ve durumuyla siparişleri getir", "PO_HEADERS_ALL", "list", "low", "projection"),
        ("p47", "LISTING", "Tedarikçi bazlı sipariş özetini getir", "PO_HEADERS_ALL", "list", "medium", "may become aggregation"),
        ("p48", "LISTING", "Açık ve onay bekleyen siparişleri getir", "PO_HEADERS_ALL", "list", "medium", "multi-status"),
        ("p49", "LISTING", "Bu hafta açılan PO'ları göster", "PO_HEADERS_ALL", "list", "medium", "week window"),
        ("p50", "LISTING", "Type STANDARD olan siparişleri getir", "PO_HEADERS_ALL", "list", "medium", "type filter"),
        ("p51", "AGGREGATION", "Her para biriminde kaç sipariş var", "PO_HEADERS_ALL", "aggregation", "low", "group currency"),
        ("p52", "AGGREGATION", "Gün bazında sipariş adedi", "PO_HEADERS_ALL", "aggregation", "medium", "date group"),
        ("p53", "AGGREGATION", "Tedarikçi başına ortalama kalem sayısı", "PO_LINES_ALL", "aggregation", "high", "needs multi-step agg"),
        ("p54", "AGGREGATION", "Sevkiyat tablosunda toplam quantity_received", "PO_LINE_LOCATIONS_ALL", "aggregation", "low", "sum"),
        ("p55", "AGGREGATION", "Dağıtım başına toplam unit_price", "PO_DISTRIBUTIONS_ALL", "aggregation", "medium", "unit_price sum"),
        ("p56", "AGGREGATION", "En çok sipariş verilen tedarikçileri say", "PO_HEADERS_ALL", "aggregation", "medium", "group+order"),
        ("p57", "AGGREGATION", "PO kalem adedini satıcıya göre kır", "PO_LINES_ALL", "aggregation", "high", "join to header for vendor"),
        ("p58", "AGGREGATION", "Onay durumuna göre toplam kayıt sayısı", "PO_HEADERS_ALL", "aggregation", "low", "group status"),
        ("p59", "AGGREGATION", "PO_LINE_LOCATIONS_ALL satırlarını say", "PO_LINE_LOCATIONS_ALL", "aggregation", "low", "count"),
        ("p60", "AGGREGATION", "PO_HEADERS_ALL içinde toplam kayıt sayısı", "PO_HEADERS_ALL", "aggregation", "low", "count"),
        ("p61", "FILTER", "Son 15 günde açılan siparişler", "PO_HEADERS_ALL", "list", "low", "current_date-15"),
        ("p62", "FILTER", "Bu yıl açılan siparişler", "PO_HEADERS_ALL", "list", "medium", "year-start"),
        ("p63", "FILTER", "Onay durumu INCOMPLETE olan siparişler", "PO_HEADERS_ALL", "list", "low", "status filter"),
        ("p64", "FILTER", "Vendor id 700 ve üstü siparişler", "PO_HEADERS_ALL", "list", "medium", "numeric range"),
        ("p65", "FILTER", "Birim fiyatı 0'dan büyük kalemleri getir", "PO_LINES_ALL", "list", "low", "unit_price filter"),
        ("p66", "FILTER", "Quantity 100 üstü satırlar", "PO_LINES_ALL", "list", "low", "quantity filter"),
        ("p67", "FILTER", "Quantity_received quantity_billed'dan büyük sevkiyatlar", "PO_LINE_LOCATIONS_ALL", "list", "high", "column-ref filter"),
        ("p68", "FILTER", "Code_combination_id dolu dağıtımları getir", "PO_DISTRIBUTIONS_ALL", "list", "low", "not null"),
        ("p69", "FILTER", "Item açıklaması boş olmayan kalemler", "PO_LINES_ALL", "list", "low", "not null"),
        ("p70", "FILTER", "Son 2 yılda açılan açık siparişler", "PO_HEADERS_ALL", "list", "medium", "date+status"),
        ("p71", "JOIN", "PO başlığına bağlı kalemleri satıcıyla beraber getir", "PO_HEADERS_ALL", "list", "high", "header-lines"),
        ("p72", "JOIN", "Kalem, sevkiyat ve dağıtım zincirini listele", "PO_DISTRIBUTIONS_ALL", "list", "medium", "3-step join"),
        ("p73", "JOIN", "Ürün segmenti ve kalem miktarını getir", "PO_LINES_ALL", "list", "high", "item join"),
        ("p74", "JOIN", "Dağıtım bazında toplam amount hesapla", "PO_DISTRIBUTIONS_ALL", "aggregation", "high", "computed metric"),
        ("p75", "JOIN", "PO başlığı ve sevkiyat miktarını birlikte getir", "PO_HEADERS_ALL", "list", "high", "header-lines-ship"),
        ("p76", "JOIN", "Item description ile vendor id eşleştir", "PO_HEADERS_ALL", "list", "high", "multi-table projection"),
        ("p77", "JOIN", "Dağıtımları item bazında grupla", "PO_DISTRIBUTIONS_ALL", "aggregation", "high", "dist-lines-items"),
        ("p78", "JOIN", "Sevkiyat ve dağıtım ilişkisini doğrula", "PO_DISTRIBUTIONS_ALL", "list", "medium", "line_location join"),
        ("p79", "JOIN", "Kalem numarası ve hesap kombinasyonunu birlikte ver", "PO_DISTRIBUTIONS_ALL", "list", "high", "lines+dist join"),
        ("p80", "JOIN", "Ürün koduna göre toplam sipariş miktarı", "PO_LINES_ALL", "aggregation", "high", "item join aggregation"),
    ]
    out.extend(q(pid, "PO", cat, text, table, intent, risk, notes) for pid, cat, text, table, intent, risk, notes in po_80)

    emp_80 = [
        ("e01", "LISTING", "Aktif çalışanları listele", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "cikis_tarihi null"),
        ("e02", "LISTING", "IT birimindeki çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "birim filter"),
        ("e03", "LISTING", "İstanbul lokasyonundaki çalışanları göster", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "location filter"),
        ("e04", "LISTING", "Bordrolu personeli listele", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "bordrolu flag"),
        ("e05", "LISTING", "Stajyer çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "stajyer flag"),
        ("e06", "LISTING", "Yönetici unvanlı personeli listele", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "title semantics"),
        ("e07", "LISTING", "E-postası olan çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "email not null"),
        ("e08", "LISTING", "Son işe başlayan 10 çalışanı getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "order+limit"),
        ("e09", "LISTING", "Masraf merkezi BT-01 olan çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "cost center"),
        ("e10", "LISTING", "Çıkış tarihi olmayan çalışanları listele", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "active synonym"),
        ("e11", "AGGREGATION", "Departman başına çalışan sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "count by birim"),
        ("e12", "AGGREGATION", "İstanbul'daki çalışan sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "count+location"),
        ("e13", "AGGREGATION", "Organizasyon bazında personel dağılımı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "count by organization"),
        ("e14", "AGGREGATION", "Toplam aktif personel sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "active count"),
        ("e15", "AGGREGATION", "Unvana göre çalışan sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "count by title"),
        ("e16", "AGGREGATION", "Lokasyon bazında çalışan dağılımı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "count by location"),
        ("e17", "AGGREGATION", "Masraf merkezi bazında kişi sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "count by cost center"),
        ("e18", "AGGREGATION", "2024 yılında işe giren kişi sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "year filter"),
        ("e19", "AGGREGATION", "Birim ve lokasyona göre çalışan sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "double group"),
        ("e20", "AGGREGATION", "Bordrolu çalışan sayısını getir", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "flag count"),
        ("e21", "FILTER", "Son 1 yılda işe giren çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "365-day"),
        ("e22", "FILTER", "2024 yılında işe başlayanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "year range"),
        ("e23", "FILTER", "Son 6 ayda işe girenler", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "6 month range"),
        ("e24", "FILTER", "10 yıldan uzun süredir çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "long tenure"),
        ("e25", "FILTER", "Maaşı 50000 üzeri çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no salary column"),
        ("e26", "FILTER", "2023 öncesi işe giren personel", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "date range"),
        ("e27", "FILTER", "Son 30 günde işe başlayanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "30-day"),
        ("e28", "FILTER", "BT birimindeki personel", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "birim like"),
        ("e29", "FILTER", "Dahili telefonu dolu çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "dahili not null"),
        ("e30", "FILTER", "Performans notu 4 üstü çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no performance column"),
        ("e31", "LISTING", "En yüksek maaşlı 5 çalışan", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no salary column"),
        ("e32", "LISTING", "Yönetici pozisyonundaki çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "title semantics"),
        ("e33", "AGGREGATION", "Her birimde aktif çalışan sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "active+group"),
        ("e34", "LISTING", "Organizasyon adıyla çalışan detayları", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "projection"),
        ("e35", "LISTING", "Unvan ve birim bilgisiyle çalışan listesi", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "projection"),
        ("e36", "LISTING", "Görev tanımı dolu çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "not null"),
        ("e37", "FILTER", "En uzun süredir çalışan 10 kişi", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "order asc"),
        ("e38", "LISTING", "Son 6 ayda terfi edenler", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no promotion column"),
        ("e39", "LISTING", "Sicil numarasıyla personel listesi", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "projection"),
        ("e40", "FILTER", "2025'te ayrılan çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "exit range"),
        ("e41", "LISTING", "Ankara lokasyonundaki çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "location"),
        ("e42", "LISTING", "Ünvanı uzman olan çalışanları listele", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "title filter"),
        ("e43", "LISTING", "E-mail adresi olmayan çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "null check"),
        ("e44", "LISTING", "Masraf merkezi dolu personeli getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "not null"),
        ("e45", "LISTING", "Son 20 işe giriş kaydını listele", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "order+limit"),
        ("e46", "LISTING", "Ad ve soyad ile çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "projection"),
        ("e47", "LISTING", "Departman ve lokasyon bilgisiyle çalışan listesi", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "projection"),
        ("e48", "LISTING", "Aktif bordrolu çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "compound filter"),
        ("e49", "LISTING", "Stajyer olmayan çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "neq filter"),
        ("e50", "LISTING", "Kurumsal e-postası olan yöneticileri getir", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "title+not null"),
        ("e51", "AGGREGATION", "Departman bazında aktif kişi sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "active+group"),
        ("e52", "AGGREGATION", "Lokasyona göre bordrolu personel sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "flag+group"),
        ("e53", "AGGREGATION", "Aylık işe giriş adedi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "date group"),
        ("e54", "AGGREGATION", "Organizasyon başına stajyer sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "flag+group"),
        ("e55", "AGGREGATION", "Birim bazında e-postasız çalışan adedi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "null+group"),
        ("e56", "AGGREGATION", "Ünvan bazında ayrılan çalışan sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "exit not null"),
        ("e57", "AGGREGATION", "Masraf merkezi bazında aktif kişi sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "group"),
        ("e58", "AGGREGATION", "Yıl bazında işe giriş trendi", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "medium", "year extraction"),
        ("e59", "AGGREGATION", "Toplam ayrılan çalışan sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "exit not null count"),
        ("e60", "AGGREGATION", "Toplam stajyer çalışan sayısı", "XXBT_PDKS_PER_DETAILS_V", "aggregation", "low", "stajyer count"),
        ("e61", "FILTER", "Bu yıl işe başlayan çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "year start"),
        ("e62", "FILTER", "Son 90 günde işe girenler", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "90-day"),
        ("e63", "FILTER", "Çıkış tarihi dolu çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "not null"),
        ("e64", "FILTER", "EMAIL alanı null olan personel", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "null filter"),
        ("e65", "FILTER", "UNVAN içinde müdür geçen çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "like filter"),
        ("e66", "FILTER", "BIRIM_ADI içinde satış geçen çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "like filter"),
        ("e67", "FILTER", "LOCATION_ADI içinde depo geçen çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "like filter"),
        ("e68", "FILTER", "SICIL_NO'su 1000 üstü çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "numeric-like field"),
        ("e69", "FILTER", "AD alanı A ile başlayan çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "starts with"),
        ("e70", "FILTER", "SOYAD alanı Y ile başlayan çalışanlar", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "starts with"),
        ("e71", "LISTING", "Son işe giriş tarihine göre personeli sırala", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "order by hire"),
        ("e72", "LISTING", "Ayrılma tarihine göre son ayrılanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "order by exit"),
        ("e73", "LISTING", "Birim bazında personel detaylarını getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "projection"),
        ("e74", "LISTING", "Lokasyon ve organizasyon ile personel listesi", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "projection"),
        ("e75", "LISTING", "Yönetici olmayan çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "medium", "title negation"),
        ("e76", "LISTING", "Dahili numarası olmayan personeli getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "null check"),
        ("e77", "LISTING", "Masraf merkezi ve unvanı birlikte göster", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "projection"),
        ("e78", "LISTING", "Tam adı dolu çalışanları getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "full_name not null"),
        ("e79", "LISTING", "Organizasyon adı dolu personeli getir", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "not null"),
        ("e80", "LISTING", "Son 14 günde işe başlayan personel", "XXBT_PDKS_PER_DETAILS_V", "list", "low", "14-day window"),
    ]
    out.extend(q(eid, "EMP", cat, text, table, intent, risk, notes) for eid, cat, text, table, intent, risk, notes in emp_80)

    mixed_40 = [
        ("x01", "AMBIGUOUS", "AMBIGUOUS", "Çalışanlar", None, "clarification", "low", "single token"),
        ("x02", "AMBIGUOUS", "AMBIGUOUS", "Siparişler", None, "clarification", "low", "single token"),
        ("x03", "AMBIGUOUS", "AMBIGUOUS", "Departmanlar", None, "clarification", "low", "no domain"),
        ("x04", "AMBIGUOUS", "AMBIGUOUS", "Veri getir", None, "clarification", "low", "generic"),
        ("x05", "AMBIGUOUS", "AMBIGUOUS", "Listele", None, "clarification", "low", "verb only"),
        ("x06", "AMBIGUOUS", "AMBIGUOUS", "Kaç tane var", None, "clarification", "low", "no entity"),
        ("x07", "AMBIGUOUS", "AMBIGUOUS", "Bilgi ver", None, "clarification", "low", "generic"),
        ("x08", "AMBIGUOUS", "AMBIGUOUS", "Göster", None, "clarification", "low", "verb only"),
        ("x09", "CROSS", "CROSS_DOMAIN", "Tedarikçi mi çalışan mı getireyim", None, "clarification", "low", "explicit cross"),
        ("x10", "CROSS", "CROSS_DOMAIN", "Organizasyon bilgilerini getir", None, "clarification", "medium", "cross risk"),
        ("x11", "CROSS", "CROSS_DOMAIN", "Tüm kayıtları getir", None, "clarification", "high", "unsafe broad"),
        ("x12", "CROSS", "CROSS_DOMAIN", "Sistem verilerini listele", None, "clarification", "medium", "unclear domain"),
        ("x13", "CROSS", "CROSS_DOMAIN", "Bütün verileri göster", None, "clarification", "high", "unclear domain"),
        ("x14", "CROSS", "CROSS_DOMAIN", "Hem PO hem personel bilgisi istiyorum", None, "clarification", "low", "multi-domain"),
        ("x15", "INVALID", "INVALID", "Kredi kartı numaralarını getir", None, "clarification_or_error", "low", "out of scope"),
        ("x16", "INVALID", "INVALID", "Gizli verilere erişmek istiyorum", None, "clarification_or_error", "low", "out of scope"),
        ("x17", "INVALID", "INVALID", "Doğum tarihlerini listele", "XXBT_PDKS_PER_DETAILS_V", "validation_error", "low", "restricted field"),
        ("x18", "INVALID", "INVALID", "Şifreleri ver", None, "clarification_or_error", "low", "out of scope"),
        ("x19", "INVALID", "INVALID", "Kişisel verileri listele", None, "clarification_or_error", "medium", "privacy"),
        ("x20", "INVALID", "INVALID", "Maaş bilgilerini göster", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "no salary column"),
        ("x21", "AMBIGUOUS", "AMBIGUOUS", "Rapor ver", None, "clarification", "low", "generic"),
        ("x22", "AMBIGUOUS", "AMBIGUOUS", "Durumu göster", None, "clarification", "low", "generic"),
        ("x23", "AMBIGUOUS", "AMBIGUOUS", "Liste çıkar", None, "clarification", "low", "generic"),
        ("x24", "AMBIGUOUS", "AMBIGUOUS", "Özet ver", None, "clarification", "low", "generic"),
        ("x25", "CROSS", "CROSS_DOMAIN", "Satınalma ve İK özetini aynı anda ver", None, "clarification", "low", "multi-domain"),
        ("x26", "CROSS", "CROSS_DOMAIN", "Vendor ve çalışan sayısını birlikte getir", None, "clarification", "medium", "cross entity"),
        ("x27", "CROSS", "CROSS_DOMAIN", "PO ile personeli joinle", None, "clarification", "high", "unsupported cross join"),
        ("x28", "CROSS", "CROSS_DOMAIN", "Organizasyon ve tedarikçi performansı ver", None, "clarification", "medium", "mixed concepts"),
        ("x29", "INVALID", "INVALID", "Banka hesaplarını getir", None, "clarification_or_error", "low", "out of scope"),
        ("x30", "INVALID", "INVALID", "TC kimlik numaralarını ver", None, "clarification_or_error", "low", "sensitive"),
        ("x31", "INVALID", "INVALID", "Gizli maaş bordrolarını indir", None, "clarification_or_error", "low", "sensitive"),
        ("x32", "INVALID", "INVALID", "Tüm kullanıcı şifrelerini listele", None, "clarification_or_error", "low", "sensitive"),
        ("x33", "AMBIGUOUS", "AMBIGUOUS", "Dünkü durum nedir", None, "clarification", "low", "entity missing"),
        ("x34", "AMBIGUOUS", "AMBIGUOUS", "Bu haftayı göster", None, "clarification", "low", "entity missing"),
        ("x35", "CROSS", "CROSS_DOMAIN", "İnsan kaynakları ve satınalma birleşik raporu", None, "clarification", "medium", "cross-domain"),
        ("x36", "CROSS", "CROSS_DOMAIN", "Personel ve PO trendini karşılaştır", None, "clarification", "medium", "cross-domain"),
        ("x37", "INVALID", "INVALID", "Yetkisiz alanları dök", None, "clarification_or_error", "low", "security"),
        ("x38", "INVALID", "INVALID", "Sistem admin parolasını getir", None, "clarification_or_error", "low", "security"),
        ("x39", "INVALID", "INVALID", "DOGUM_TARIHI kolonunu herkese aç", "XXBT_PDKS_PER_DETAILS_V", "validation_error", "low", "restricted"),
        ("x40", "INVALID", "INVALID", "Maaş ve prim detaylarını getir", "XXBT_PDKS_PER_DETAILS_V", "list", "high", "non-existing columns"),
    ]
    out.extend(q(mid, dom, cat, text, table, intent, risk, notes) for mid, dom, cat, text, table, intent, risk, notes in mixed_40)

    return out


def main() -> None:
    data = build_dataset()
    if len(data) != 200:
        raise SystemExit(f"Expected 200 questions, got {len(data)}")

    ids = [str(item["id"]) for item in data]
    if len(ids) != len(set(ids)):
        raise SystemExit("Question IDs are not unique")

    po_count = sum(1 for item in data if item["domain"] == "PO")
    emp_count = sum(1 for item in data if item["domain"] == "EMP")
    mixed_count = len(data) - po_count - emp_count
    if (po_count, emp_count, mixed_count) != (80, 80, 40):
        raise SystemExit(
            f"Domain distribution mismatch: PO={po_count}, EMP={emp_count}, MIXED={mixed_count}"
        )

    out_path = Path("data/eval_dataset_200.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(data)} questions to {out_path}")


if __name__ == "__main__":
    main()
