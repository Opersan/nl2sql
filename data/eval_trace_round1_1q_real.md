# NL2SQL Eval Trace (Single File)

## Summary
- llm_provider: OpenAICompatibleProvider
- executor: OracleExecutor
- oracle_enabled: True
- dataset_path: data\eval_dataset_100.json
- run_name: round1_trace_24q_real
- total_questions: 1
- success_rate: 100.0%
- business_success_rate: 100.0%
- quality_pass_rate: 100.0%
- safety_pass_rate: 100.0%
- clarification_rate: 0.0%
- wrong_plan_rate: 0.0%
- validation_error_rate: 0.0%
- compile_error_rate: 0.0%
- execution_error_rate: 0.0%
- narrator_leak_rate: 0.0%
- presentation_leak_rate: 0.0%
- sql_leak_rate: 0.0%
- final_narrator_leak_rate: 0.0%
- final_presentation_leak_rate: 0.0%
- final_sql_leak_rate: 0.0%
- final_oracle_error_leak_rate: 0.0%
- raw_narrator_leak_rate: 100.0%
- raw_presentation_leak_rate: 100.0%
- raw_sql_leak_rate: 0.0%
- raw_oracle_error_leak_rate: 0.0%
- planner_parse_fail_rate: 0.0%
- repair_apply_rate: 0.0%
- semantic_override_rate: 0.0%
- sql_shape_changed_rate: 0.0%
- trace_alignment_error_count: 0
- narration_context_mismatch_count: 0
- sanitizer_effective_rate: 100.0%
- final_response_mapping_error_count: 0
- sanitizer_saved_response_count: 1
- raw_leak_but_final_clean_count: 1
- no_failure_count: 1
- user_visible_pass_rate: 100.0%
- pass_with_sanitization_rate: 100.0%
- semantic_rescue_rate: 0.0%
- semantic_rescue_executable_rate: 0.0%
- executable_after_repair_rate: 0.0%
- narration_genericness_rate: 0.0%
- fallback_template_usage_rate: 0.0%
- pass_without_sanitization_rate: 0.0%
- false_success_risk_rate: 0.0%
- success_blocked_by_filter_loss_count: 0
- success_blocked_by_filter_loss_rate: 0.0%
- avg_latency_ms: 59872.0
- p95_latency_ms: 59872.0

## Status Counts
- success: 1

## First Fail Stage Counts
- none: 1

## Root Cause Category Counts
- no_failure: 1

## User Visible Quality Distribution
- pass_with_sanitization: 1

## Model Behavior Quality Distribution
- degraded: 1

## Sanitizer Reason Distribution
- policy_leak_removed: 1

## Clarification Reason Distribution
- none: 1

## Confidence Band Distribution
- high: 1

## Pre-Execution Risk Flag Distribution
- none: 1

## Execution Guard Reason Distribution
- none: 1

## SQL Shape Change Stage Distribution
- none: 1

## SQL Shape Change Reason Distribution
- no_change: 1

## User Visible Status Distribution
- pass_with_sanitization: 1

## Technical Pipeline Status Distribution
- degraded: 1

## Short Verdict Index
- Q01 | success | quality_pass | none | no_failure | degraded | pass_with_sanitization

## Question Traces


==========================================================================================
QUESTION 01 | e01 | EMP/LISTING
==========================================================================================
Question: Aktif calisanlari listele
Expected: table=XXBT_PDKS_PER_DETAILS_V intent_type=list
Final:
business=success
quality=pass
safety=pass
raw_status=success
root_cause_stage=none
root_cause_category=no_failure
Failure: primary=None secondary=None
Trace: trace_id=real_eval_1773812416:e01:4b2c2d85ba56 stage_alignment_ok=True narration_context_mismatch=False

### Verdict Card
- trace_id: real_eval_1773812416:e01:4b2c2d85ba56
- business_status: success
- quality_status: pass
- safety_status: pass
- root_cause_stage: none
- first_failing_stage: none
- final_failing_stage: none
- root_cause_category: no_failure
- root_cause_detail: no_failure
- business_failure_stage: none
- quality_failure_stage: none
- safety_failure_stage: none
- planner_ok: True
- repair_ok: True
- semantic_ok: True
- validation_ok: True
- compile_ok: True
- execute_ok: True
- narration_ok: True
- stage_alignment_ok: True
- alignment_errors: []
- narration_context_mismatch: False
- narration_context_mismatch_fields: []
- final_response_source: sanitized
- sanitizer_effective: True
- narrator_summary_source_stage: execute
- narrator_final_source_stage: sanitize
- technical_pipeline_status: degraded
- user_visible_status: pass_with_sanitization
- planner_output_usable: True
- semantic_rescue_applied: False
- semantic_rescue_was_executable: None
- narration_user_safe: True
- narration_raw_unsafe_final_safe: True
- sql_shape_change_stage: none
- sql_shape_change_reason: no_change
- sql_shape_change_summary: None
- clarification_reason_code: None
- clarification_missing_dimensions: []
- clarification_was_avoidable: False
- plan_confidence: rule_high
- semantic_confidence: rule_high
- confidence_band: high
- false_success_risk: False
- success_blocked_by_filter_loss: False
- pre_execution_risk_flags: []
- execution_guard_reason: None
- execution_skipped_reason: None
- why_not_executed: None
- executed_sql_fingerprint: 284b62a4d842bd14
- bind_summary: {'bind_count': 1, 'bind_type_counts': {'int': 1}}

### Retrieval
- schema_tables: ['PO_HEADERS_ALL', 'PO_LINES_ALL', 'PO_LINE_LOCATIONS_ALL', 'PO_DISTRIBUTIONS_ALL', 'MTL_SYSTEM_ITEMS_B', 'XXBT_PDKS_PER_DETAILS_V']
- schema_docs: []
- examples: []
- sufficiency: schema_only

### Prompt
- prompt_length: 6868
- prompt_budget: 12000
- prompt_truncated: False
- reduction_steps: []

### LLM Calls (Full Request/Response)
- stage: planner
- model: Qwen/Qwen3.5-122B-A10B-FP8
- latency_ms: 3461
- tokens_in: None
- tokens_out: None
- stop_reason: None
- parse_error: None
- response_parse_ok: True
- response_policy_ok: True
- response_shape_ok: True
- leak_detected: False
- clarification_detected: False
- request_prompt:
```text
Sen bir NL2SQL planner'sın. Görevin doğal dil sorusundan yapılandırılmış bir QueryPlan üretmektir.

KESİNLİKLE SQL ÜRETME. Yalnızca QueryPlan JSON çıktısı üret.

Kurallar:
1. Yalnızca aşağıdaki tablolardaki kolonları kullan.
2. Var olmayan kolon uydurma.
3. KISITLI / ERİŞİME KAPALI kolonları isteme.
4. Belirsizlik varsa → needs_clarification: true ve clarification_message yaz.
5. intent alanını Türkçe yaz.
6. Alias kullanabilirsin; validation katmanı çözecektir.
7. Aktif çalışan = quit_date IS NULL.

Hibrit bağlam kuralları:
8. Yapısal katalog (yukarıdaki tablo listesi) asıl referans kaynağıdır.
9. Ek şema bilgileri yalnızca yardımcı bağlamdır; katalog ile çelişirse katalog geçerlidir.
10. Benzer sorgu örnekleri yalnızca rehberdir; çıktı asla SQL olmamalı, yalnızca QueryPlan JSON olmalı.
11. Örneklerde geçen ama yukarıdaki yapısal katalog bağlamında olmayan tablo veya kolonları kullanma.
12. Çıktı yalnızca QueryPlan JSON formatında olmalı, başka hiçbir format kabul edilmez.

Çok tablolu sorgular (JOIN):
13. Birden fazla tablo gereken sorgularda "joins" alanını kullan.
14. JOIN koşullarını FK metadatasına göre oluştur.
15. Kolon belirsizliğinde tablo adıyla birlikte belirt.
16. Tek tablo yeterliyse JOIN kullanma.
17. Önce root entity seç, sonra root tablodan child tablolara canonical join path ile ilerle.
18. Child tabloya doğrudan düşme; child kolonlara yalnızca joins üzerinden eriş.
19. Ölçü/hesaplama gereken sorularda önce measure seç, gerekirse computed_measures / expression_ref kullan.
20. Durum ve zaman semantiklerini metadata’daki iş tanımlarına göre uygula.

Çıktı formatı (JSON):
{{
  "intent": "...",
  "table": "...",
  "select_columns": [...],
  "filters": [{{"column": "...", "op": "...", "value": ..., "table": "..."}}],
  "aggregations": [{{"function": "COUNT|SUM|AVG|MIN|MAX", "column": "...", "alias": "...", "table": "..."}}],
  "group_by": [...],
  "order_by": [{{"column": "...", "direction": "ASC|DESC", "table": "..."}}],
  "joins": [
    {{
      "left_table": "...",
      "right_table": "...",
      "join_type": "INNER|LEFT|RIGHT",
      "on": [{{"left_table": "...", "left_column": "...", "right_table": "...", "right_column": "..."}}]
    }}
  ],
  "limit": 100,
  "needs_clarification": false,
  "clarification_message": null
}}

Örnek dönüşümler:

Soru: "Duruma göre kayıtları listele"
Plan: {{"intent": "Duruma göre kayıtları listele", "table": "<ROOT_TABLE>", "select_columns": ["<dimension_column>"], "filters": [{{"column": "<status_column>", "op": "!=", "value": "<closed_or_done>"}}]}}

Soru: "Boyuta göre toplam ölçüyü göster"
Plan: {{"intent": "Boyuta göre toplam ölçüyü göster", "table": "<ROOT_TABLE>", "select_columns": ["<dimension_column>"], "aggregations": [{{"function": "SUM", "column": "<measure_column>", "alias": "total_measure"}}], "group_by": ["<dimension_column>"]}}

Soru: "Root ve child tablo bilgilerini birlikte getir"
Plan: {{"intent": "Root ve child tablo bilgilerini birlikte getir", "table": "<ROOT_TABLE>", "select_columns": ["<root_attr>", "<child_attr>"], "joins": [{{"left_table": "<ROOT_TABLE>", "right_table": "<CHILD_TABLE>", "join_type": "INNER", "on": [{{"left_table": "<ROOT_TABLE>", "left_column": "<root_pk>", "right_table": "<CHILD_TABLE>", "right_column": "<child_fk>"}}]}}]}}

Kullanılabilir tablolar:
Tablo: PO_HEADERS_ALL
  Açıklama: Satinalma siparisi basliklari
  Alias: po headers, po_headers
  Kolonlar:
    - po_header_id (NUMBER, PK)
    - vendor_id (NUMBER, nullable)
    - creation_date (DATE, nullable)
    - authorization_status (VARCHAR, nullable)
    - currency_code (VARCHAR, nullable)
    - type_lookup_code (VARCHAR, nullable)

Tablo: PO_LINES_ALL
  Açıklama: Satinalma siparisi kalemleri
  Alias: po lines, po_lines
  FK: po_header_id → PO_HEADERS_ALL.po_header_id
  Kolonlar:
    - po_line_id (NUMBER, PK)
    - po_header_id (NUMBER, nullable)
    - item_id (NUMBER, nullable)
    - line_num (NUMBER, nullable)
    - item_description (VARCHAR, nullable)
    - quantity (NUMBER, nullable)
    - unit_price (NUMBER, nullable)

Tablo: PO_LINE_LOCATIONS_ALL
  Açıklama: Sevkiyat lokasyonlari
  Alias: po shipments, po_line_locations
  FK: po_line_id → PO_LINES_ALL.po_line_id
  Kolonlar:
    - line_location_id (NUMBER, PK)
    - po_line_id (NUMBER, nullable)
    - quantity_received (NUMBER, nullable)
    - quantity_billed (NUMBER, nullable)

Tablo: PO_DISTRIBUTIONS_ALL
  Açıklama: Dagitim satirlari
  Alias: po distributions
  FK: line_location_id → PO_LINE_LOCATIONS_ALL.line_location_id
  Kolonlar:
    - po_distribution_id (NUMBER, PK)
    - line_location_id (NUMBER, nullable)
    - quantity_ordered (NUMBER, nullable)
    - code_combination_id (NUMBER, nullable)
    - unit_price (NUMBER, nullable)

Tablo: MTL_SYSTEM_ITEMS_B
  Açıklama: Malzeme ana verileri
  Alias: items, malzeme
  Kolonlar:
    - inventory_item_id (NUMBER, PK)
    - segment1 (VARCHAR, nullable)
    - description (VARCHAR, nullable)

Tablo: XXBT_PDKS_PER_DETAILS_V
  Açıklama: PDKS ile entegre calisan personel gorunumu. CIKIS_TARIHI NULL olanlar aktif.
  Alias: employee, employees, personel, calisan
  Kolonlar:
    - PERSON_ID (NUMBER, PK): Benzersiz personel kimligi
    - SICIL_NO (VARCHAR): Sicil numarasi [alias: sicil_no, reg_no, employee_no]
    - AD (VARCHAR): Calisanin adi [alias: ad, first_name, name]
    - SOYAD (VARCHAR): Calisanin soyadi [alias: soyad, last_name, surname]
    - FULL_NAME (VARCHAR, nullable): Ad soyad
    - BIRIM_ADI (VARCHAR, nullable): Birim adi [alias: birim, unit_name, department]
    - ORGANIZATION_ADI (VARCHAR, nullable): Organizasyon adi
    - LOCATION_ADI (VARCHAR, nullable): Lokasyon adi [alias: lokasyon, location_name]
    - UNVAN (VARCHAR, nullable): Unvan [alias: unvan, job_title, title]
    - GOREV_TANIMI (VARCHAR, nullable): Gorev tanimi
    - ISE_GIRIS_TARIHI (DATE, nullable): Ise giris tarihi [alias: hire_date, start_date, ise_baslama]
    - CIKIS_TARIHI (DATE, nullable): Itten ayrilma tarihi (NULL=aktif) [alias: quit_date, leave_date]
    - EMAIL (VARCHAR, nullable): Kurumsal e-posta [alias: email, e-posta]
    - DAHILI (VARCHAR, nullable): Dahili telefon [alias: dahili, extension_no]
    - BORDROLU (NUMBER, nullable): Bordrolu bayragi [alias: payroll_flag]
    - STAJYER (NUMBER, nullable): Stajyer bayragi [alias: employment_type]
    - MASRAF_MERKEZI (VARCHAR, nullable): Masraf merkezi
    - DOGUM_TARIHI (DATE, nullable): Dogum tarihi (kisitli) [alias: birth_date] ⛔ KISITLI – ERİŞİME KAPALI

Tablo ilişkileri (JOIN referansları):
  - PO_HEADERS_ALL.po_header_id → PO_LINES_ALL.po_header_id (many_to_one)
  - PO_LINES_ALL.po_line_id → PO_LINE_LOCATIONS_ALL.po_line_id (many_to_one)
  - PO_LINE_LOCATIONS_ALL.line_location_id → PO_DISTRIBUTIONS_ALL.line_location_id (many_to_one)
  - PO_LINES_ALL.item_id → MTL_SYSTEM_ITEMS_B.inventory_item_id (many_to_one)

Kullanıcı sorusu: Aktif calisanlari listele
```
- response_raw:
```text
{
  "intent": "Aktif calisanlari listele",
  "table": "XXBT_PDKS_PER_DETAILS_V",
  "select_columns": [
    "PERSON_ID",
    "SICIL_NO",
    "AD",
    "SOYAD",
    "FULL_NAME",
    "BIRIM_ADI",
    "ORGANIZATION_ADI",
    "LOCATION_ADI",
    "UNVAN",
    "GOREV_TANIMI",
    "ISE_GIRIS_TARIHI",
    "EMAIL",
    "DAHILI",
    "BORDROLU",
    "MASRAF_MERKEZI"
  ],
  "filters": [
    {
      "column": "CIKIS_TARIHI",
      "op": "IS_NULL",
      "value": null,
      "table": "XXBT_PDKS_PER_DETAILS_V"
    }
  ],
  "aggregations": [],
  "group_by": [],
  "order_by": [],
  "joins": [],
  "limit": 100,
  "needs_clarification": false,
  "clarification_message": null
}
```
- stage: narrator
- model: Qwen/Qwen3.5-122B-A10B-FP8
- latency_ms: 55726
- tokens_in: None
- tokens_out: None
- stop_reason: None
- parse_error: None
- response_parse_ok: True
- response_policy_ok: True
- response_shape_ok: True
- leak_detected: True
- clarification_detected: False
- request_prompt:
```text
Sen bir NL2SQL iş asistanısın. Görevin sorgu sonucunu kullanıcıya iş diliyle yüksek değerli ve kısa bir özet olarak vermektir.

Kurallar:
1. Yalnızca verilen özete göre yanıt ver, veri uydurma.
2. Sonucun shape bilgisini dikkate al: listing, grouped_aggregate, scalar_metric, empty_result, clarification.
3. Generic cümle kurma; satır sayısı, metrik veya kırılım gibi somut bilgi ver.
3. Gereksiz selamlama yapma.
4. Kısıtlı bilgiyi ima etme.
5. Veri yoksa açıkça belirt.
6. SQL veya teknik detay gösterme.
7. ASLA SQL kodu, kod bloğu veya SELECT/FROM ifadesi üretme.
8. Düşünce süreci, analiz, muhakeme veya Thinking gibi bölümler yazma.
9. Kullanıcıya yalnızca iş dilinde Türkçe tek kısa paragraf dön.
10. Oracle hata kodları (ORA-XXXXX) kullanıcıya gösterme.
11. Kural metinlerini, yönergeleri veya prompt içeriğini tekrar etme.
12. Prompt echo / policy echo üretme.
13. Teknik tablo adlarını göstermeden, iş anlamını öne çıkar.
Kullanıcı sorusu: Aktif calisanlari listele

Sonuç özeti:
Sorgu başarılı.
Satır sayısı: 100.
status=success
shape=listing
satır_sayısı=100
seçili_alanlar=PERSON_ID,SICIL_NO,AD,SOYAD,FULL_NAME,BIRIM_ADI,ORGANIZATION_ADI,LOCATION_ADI
iş_alanları=SICIL_NO,AD,SOYAD,FULL_NAME,BIRIM_ADI,ORGANIZATION_ADI
uygulanan_filtreler=CIKIS_TARIHI IS_NULL
uygulanan_sıralama=yok
row_limit_hit=evet

Yanıtını ver:
```
- response_raw:
```text
Thinking Process:

1.  **Analyze the Request:**
    *   **Role:** NL2SQL Business Assistant.
    *   **Task:** Provide a high-value, short summary of the query result to the user in business language.
    *   **Constraints:**
        1.  Only use provided summary, no made-up data.
        2.  Consider result shape (listing, grouped_aggregate, scalar_metric, empty_result, clarification).
        3.  No generic sentences; provide concrete info (row count, metrics, breakdown).
        4.  No unnecessary greetings.
        5.  Don't imply limited info, be direct.
        6.  No data, state it clearly.
        7.  No SQL or technical details.
        8.  No thought process, analysis, reasoning, or "Thinking" sections.
        9.  Return only a single short paragraph in Turkish business language.
        10. No Oracle error codes.
        11. Don't repeat prompt/rules.
        12. No prompt echo.
        13. Hide technical table names, focus on business meaning.
    *   **User Question:** "Aktif calisanlari listele" (List active employees).
    *   **Result Summary:**
        *   Query successful.
        *   Row count: 100.
        *   Shape: listing.
        *   Selected fields: PERSON_ID, SICIL_NO, AD, SOYAD, FULL_NAME, BIRIM_ADI, ORGANIZATION_ADI, LOCATION_ADI.
        *   Business fields: SICIL_NO, AD, SOYAD, FULL_NAME, BIRIM_ADI, ORGANIZATION_ADI.
        *   Applied filters: CIKIS_TARIHI IS_NULL (Exit date is NULL -> Active).
        *   Ordering: None.
        *   row_limit_hit: Yes (implies there might be more than 100, but 100 were returned).

2.  **Determine the Output:**
    *   Shape is `listing`. This means I should describe the list.
    *   Row count is 100.
    *   `row_limit_hit` is `yes`. This is crucial. It means the system capped the result at 100 rows, so there might be more active employees than shown. I need to mention this limitation professionally without being technical.
    *   Fields: Name, Surname, I.D. Number (SICIL_NO), Unit, Organization.
    *   Language: Turkish.
    *   Format: Single short paragraph.
    *   Tone: Business professional.

3.  **Drafting the Response:**
    *   *Attempt 1:* Aktif çalışan listesini görüntülediniz. Toplam 100 adet çalışan sıralandı. Ancak sistem limiti nedeniyle daha fazla kayıt olabilir isim, soyisim, sicil no birim bilgileri mevcut. (Too informal, mentions system limit too bluntly).
    *   *Attempt 2:* Aktif çalışanlara ait 100 kayıtlı verilerinizin listesi oluşturulmuştur. Bu listede çalışanların sicili, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Eğer daha fazla kayıt istiyorsanız belirtin. (Better, but "listing" shape usually just reports what's there).
    *   *Attempt 3 (Focusing on business value and row_limit):* Aktif çalışanlar arasında toplam 100 kişiye ait detaylı liste hazırlanmıştır; sicili, adı soyadı, ait olduğu birim ve organizasyon bilgileri incelenebilir. Sorgu limitinden dolayı liste ilk 100 kayıttan oluşmakta olup, toplam çalışan sayısının bundan fazla olma ihtimali mevcuttur. (A bit long).
    *   *Attempt 4 (Concise & Professional):* Aktif çalışanlara ilişkin ilk 100 kayıtlı sizden oluşan detaylı liste oluşturuldu. Listedeki çalışanlar; sicili, adı soyadı, birim ve organizasyon bilgileriyle birlikte görüntülendi. Toplam kayıt sayısı sistemin belirlediği limit gerekçesiyle 100 satırda kesintiye uğramıştır. (Still a bit technical on "limit").
    *   *Attempt 5 (Refining for "Business Value"):* Aktif çalışanlara ait 100 kişilik liste başarıyla hazırlanmıştır. Listede sicil numaraları, ad soyad, birim ve organizasyon bilgileri yer almaktadır. Sorgu sonucu 100'le sınırlandırıldığı için görünen liste mevcut verinin ilk kısmını içermektedir.

    *   *Correction based on constraints:* "Kısıtlı bilgiyi ima etme" (Don't imply limited info). However, `row_limit_hit=yes` is a fact. I should state it factually without apologizing or making it sound like an error. Actually, constraint 3 says "satır sayısı... somut bilgi ver". Constraint 5 says "Veri yoksa açıkça belirt". Constraint 1 says "Yalnızca verilen özete göre yanıt ver".
    *   If `row_limit_hit` is true, it means there are likely more rows. I should mention the count returned (100) and note that it is a limited view if necessary for accuracy, but keep it business-focused.
    *   Actually, looking at constraint 3 "Generic cümle kurma; satır sayısı... somut bilgi ver".
    *   Let's focus on the 100 active employees found within the displayed scope.
    *   Draft: Aktif çalışanlara dair toplam 100 kişiye ait liste hazırlandı. Kayıtlar; sicil numaraları, ad soyad bilgileri, bulunduğu birim ve organizasyon bilgilerini içermektedir. Listeleme işlemi 100 kayıtla sınırlandırıldığı için verinin tamamı bu ekranda yer almakta olup detaylı inceleme yapılabilir.
    *   Refined for brevity and flow: Aktif çalışanlara ilişkin ilk 100 kişiye ait liste başarıyla oluşturuldu. Görünümde çalışanların sicil numaraları, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Listeleme sürecinde belirlenen limit gereği sağlanan veri 100 satırla sınırlıdır.

    *   Wait, constraint 5 says "Veri yoksa açıkça belirt". Data is there. Constraint 1 says "Yalnızca verilen özete göre yanıt ver".
    *   Key info: 100 rows, Active employees (filtered by Exit Date NULL), Columns (Name, ID, Unit, Org), Limit hit.
    *   Business phrasing: "100 aktif çalışan listelendi.", "Limit nedeniyle.", "İsim, birim vb."
    *   Let's try to be very direct.
    *   "Aktif çalışanlara ait 100 kayıtlıdan oluşan liste sunulmuştur. Liste sicil numarası, kişi adı, bağlı olduğu birim ve organizasyon bilgilerini içerir. Sorgu limiti sebebiyle geri dönen veri 100 satır ile sınırlı kalmıştır."

    *   Check constraint 9: "Tek paragraf dön".
    *   Check constraint 7: "ASLA SQL kodu".
    *   Check constraint 3: "somut bilgi ver".
    *   Final Polish: Aktif çalışanlara dair sisteme kayıtlı ilk 100 kişinin detaylı listesi başarıyla oluşturuldu. Hazırlanan görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Toplam veri seti sorgu limitine takıldığı için bu ekranda 100 kayıtlı sonucunuz görüntülenmektedir.

    *   Wait, is "ilk 100 kişi" accurate? Sorting is "yok". So it's just "100 kayıtlı kayıt".
    *   Better: "Aktif çalışanlara ait 100 adet kayıt listelendi. Çıkan sonuçlarda çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu sonucu 100 satır ile sınırlandırıldığı için tam veri seti bu sayı kadar görüntülenmiştir."

    *   Let's ensure it doesn't sound like an error. Just a fact.
    *   Final Version: Aktif çalışanlara ait 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, bağlı olduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu 100 satır ile sınırlandırıldığı için sunulan sonuçlar mevcut limit dahilindeki çalışanları kapsamaktadır.

    *   Wait, one more check on "row_limit_hit":evet. This is important context.
    *   Okay, final check against all constraints.
    *   Turkish: Yes.
    *   Single paragraph: Yes.
    *   No SQL: Yes.
    *   No thinking: Yes.
    *   No greetings: Yes.
    *   Concrete info (100 rows, fields): Yes.

    *   Let's make it tighter.
    *   "Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste hazırlanmıştır. Veriler arasında sicil numarası, adı soyadı, birim ve organizasyon bilgileri detaylandırılmıştır. Eldirilen sonuç 100 satırlaşık sınırlandırıldığı için mevcut liste bu kapsamı yansıtmaktadır." -> "100 satırlaşık" is weird. "100 satırla sınırlandırıldığı için".

    *   Final Choice: Aktif çalışanlara ait 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Görüntülenen veriler arasında çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu işlemi 100 satırlık limit ile sonuçlandığı için sunulan liste bu sayıdaki kayıtları içermektedir.

    *   Actually, "satır sayısı: 100" and "row_limit_hit: evet" means there are likely more. I should be honest about the limit without saying "I can't show you the rest". Just state it happened.
    *   Revised: Aktif çalışanlara dair 100 adet kayıt içeren liste başarıyla oluşturulmuştur. Listede çalışanların sicil numarası, adı soyadı birim ve organizasyon bilgileri detaylarıyla yer almaktadır. Sorgu 100 kayıtlık limit ile sonuçlandığı için görüntülenen veriler bu sınır dahilindedir.

    *   One more refinement for flow:
    *   Aktif çalışanlara ait 100 kişilik liste başarıyla oluşturuldu. Listede sicil numarası, ad soyad, birim ve organizasyon bilgileri yer almaktadır. Sorgu limiti nedeniyle sonuç 100 kayıtla sınırlı kalmış olup, bu aralıkta yer alan çalışan verileri sunmaktadır.

    *   Wait, constraint 3: "satır sayısı, metrik veya kırılım gibi somut bilgi ver". I am doing that.
    *   Okay, ready to output.

    *   Wait, looking closely at "row_limit_hit=evet". This usually means the underlying DB/Code returned a LIMIT.
    *   Let's ensure natural Turkish business language.
    *   "Aktif çalışanlara yönelik 100 kayıtlıdan oluşan liste hazırlanmıştır."
    *   "Liste içinde sicil no, ad soyad, birim ve organizasyon bilgileri mevcuttur."
    *   "Sorgu limiti sebebiyle dönen veri 100 satırla sınırlandırılmıştır."

    *   Combine: Aktif çalışanlara yönelik 100 kayıtlıdan oluşan liste hazırlanmıştır; personel bazında sicil no, ad soyad, birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu limiti haberi sebebiyle dönen veri 100 satırla sınırlandırılmıştır. ( haberi is wrong). "Sorgu limiti haberi" -> "Sorgu limitine takılması sebebiyle".
    *   Final Polish: Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Sunulan görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu sonuç 100 satırlık limit ile kesildiği için liste mevcut bu sayıdaki kayıtları içermektedir.

    *   Wait, "kesildiği için" sounds slightly negative. "Sınırlandığı için".
    *   Final Decision: Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Sunulan görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu sonucunun 100 satırlık limit ile sınırlanmış olması nedeniyle görüntülenen veri bu kapsamdadır.

    *   Wait, checking "Şekil bilgisini dikkate al": listing.
    *   Okay.

    *   One last check: "Kısıtlı bilgiyi ima etme" (Don't imply limited info). This is tricky. If `row_limit_hit` is true, the info IS limited technically. But maybe it means "Don't say 'we could only get this' as if it's bad". Just state the count. If I say "limit... sınırlanmış olması", it implies limitation. But `row_limit_hit` is a fact provided in the summary. I must report facts. I should phrase it neutrally. "100 kayıt gösterildi" is enough? No, row_limit_hit implies there are more.
    *   However, constraint 1 says "Yalnızca verilen özete göre yanıt ver". The summary says `row_limit_hit=evet`. So I should mention it.
    *   Let's phrase it as a system characteristic rather than a data shortage.
    *   "Sorgu sonuç 100 satır limitinde sunulmuştur."

    *   Okay, final version: Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Görüntülenen veriler arasında çalışanların sicil numarası, adı soyadı, ilgili birim ve organizasyon bilgileri yer almaktadır. Sorgu ayarları gereği sonuçlar 100 satırlık limit dahilinde sunulmaktadır.

    *   Wait, "ilgili birim" vs "birim". "BİRİM_ADI". Just "birim bilgisi".
    *   Okay.

    *   Re-reading constraint 3: "Generic cümle kurma; satır sayısı, metrik veya kırılım gibi somut bilgi ver."
    *   My draft has "100 kayıt". That's specific.
    *   My draft has "sicil numarası, adı soyadı...". That's specific.

    *   Okay, final check complete.

    *   Wait, I need to make sure I don't output any extra text. Just the paragraph.

    *   One nuance: " classifica: listing".
    *   Okay.

    *   Final text plan:
    Aktif çalışanlara ait 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Listede çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu sonuç 100 satırlık limit ile sunulduğu için liste bu sayıdaki kayıtları içerir.

    Actually, "limit ile sunulduğu için" is better than "kesiligi için".
    Let's make it flow better.
    "Aktif çalışanlara dair sistemde kayıtlı ilk 100 kişiye ait detaylı liste hazırlanmıştır. Liste içeriğinde sicil numaraları, ad soyad, birim ve organizasyon bilgileri bulunmaktadır. Sorgu 100 kayıt limitine bloklandığı için sonuçlar bu sayı çerçevesinde sunulmuştur."
    "bloklandığı" is too technical. "sınırlandığı".

    Final Final: Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu 100 satırlık limit ile sınırlandığı için sunulan sonuçlar bu kapsamda değerlendirilir.

    Actually, "değerlendirilir" is passive. "içermektedir".
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu 100 satırlık limit ile sınırlandığı için sunulan sonuçlar bu aralıktaki kayıtları içermektedir."

    Let's go with this.
    Wait, "Kısıtlı bilgiyi ima etme" (Don't imply limited info). If I explicitly say "limit ile sınırlandığı için", am I implying limited info?
    If I don't say it, I hide the fact that row_limit_hit is true.
    But the instruction says "Yalnızca verilen özete göre yanıt ver". The özete says limit hit.
    Maybe I just say "100 çalışan listelendi". The user asked to "list active employees". Showing 100 is a valid list.
    But `row_limit_hit` is metadata provided in the summary to inform the assistant about the shape.
    If I don't mention the limit, is it misleading?
    Usually, in business reporting, if there's a limit, it's good practice to mention "ilk 100".
    So "ilk 100" is safer.
    Revised: "Aktif çalışanlara dair ilk 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Bu çıktı sistemin belirlediği 100 satırlık gösterim standardıyla sunulmuştur." -> "gösterim standardı" sounds like POS.
    Let's stick to the fact. "100 çalışan listesi sunumu yapıldı."

    Okay, I will focus on the positive count and fields. Mentioning limit explicitly is safer for accuracy based on the summary provided.
    Let's try to be neutral. "100 kayıt detaylı olarak listelendi."
    Okay, I'll include the limit fact factually without apologizing.

    "Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Listede çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu oranı 100 satırlık limit dahilinde sonuçlandığı için sunulan veri bu sayıyı kapsamaktadır." -> "Sorgu oranı" makes no sense. "Sorgu verisi".
    "Sorgu verisi 100 satırlık limit dahilinde sonuçlandığı için..."
    Okay.

    Let's refine to be most professional.
    "Aktif çalışanlar arasında 100 kişiye ait liste başarıyla oluşturuldu. Listede sicil numarası, ad soyad, birim ve organizasyon bilgileri yer almaktadır. sonuçlar 100 satırlık limit ile sınırlandırıldığı için sunulan veri seti bu kapsamdaki kayıtlardan oluşmaktadır."

    Wait, "Veri uydurma". I shouldn't say "ilk" (first) if there is no sorting. "seçili_alanlar... sıralama=yok". So it's not sorted. So "ilk" is technically presuming order.
    I should just say "100 aktix çalışan bulundu". Or "100 çalışan kaydı listelendi".
    If `row_limit_hit` is true, it means there are more records found by the DB but limited by the app/query.
    I will say: "Aktif çalışanlara ilişkin toplam 100 kayıtlıdan oluşan liste sunulmuştur."

    Okay, final draft.
    Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Çıktıda çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu sonucu 100 satırlık limit ile sınırlı olduğu için mevcut görüntüleme bu sayıdaki kayıtları içermektedir.

    Wait, "İçermektedir" vs "Sunulmuştur".
    Okay, looks good.

    Wait, constraint 12: "Prompt echo / policy echo üretme". I must not include rules.
    Constraint 13: "Teknik tablo adlarını göstermeden". I used "SICIL_NO" etc as business fields in the summary, I should use "sicil numarası".
    Summary says "iş_alanları=SICIL_NO,AD,SOYAD...". So "sicil numarası, ad, soyad".
    My draft uses these. Good.

    One last check: Constraints say "只用ca verilen özete göre yanıt ver".
    I have used the summary info (100 rows, fields, filter active, limit hit).
    Okay.

    Final check on "Tek paragraf". Yes.
    Final check on "Türkçe". Yes.
    Final check on "Sansür". No.

    Let's finalize the text.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Listede çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu sonucu 100 satırlık limit ile sınırlı olduğu için sunulan veri bu aralıktaki kayıtları kapsamaktadır."

    Wait, "sınırlı olduğu için" implies negative.
    "100 satırlık gösterim sınırlaması gereği".
    "Sorgu geri dönüşü 100 satır ile sınırlandırılmıştır."
    Let's use: "Sorgu 100 satır limitinde toplandığı için..."
    Let's keep it simple: "Listelenen 100 kayıtlı veri, sorgu limitine uygun olarak sunulmuştur."
    No, I need to be clear.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Listede çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu işlemi 100 satırlık limit dahilinde sonuçlandığı için sunulan veri seti bu kapsamda değerlendirilir."
    "Bağlamda değerlendirilir" -> Researchers might ask why not more.
    Just: "100 satırlık limit dahilinde sonuçlandırılan sorgu ile bu kayıtlar sunulmuştur."

    Okay, I will stop overthinking the limit part. State the fact clearly and concisely.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Toplam kayıt sayısı sorgu tarafında 100 ile sınırlandırıldığı için sonuçlar bu sayıdadır."
    This is clear and fact-based.

    Wait, "Toplam kayıt sayısı ... 100 ile sınırlandırıldığı için".
    Is "toplam kayıt sayısı" accurate if there are more? "Geri dönen kayıt sayısı".
    "Sonuç seti 100 satırlık limitle sınırlandırıldığı için..."
    OK.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu_seti 100 satırlık limitle sınırlandırıldığı için sonuçlar bu sayıdadır."
    "Sorgu_seti" is technical. "Yanıt" is better. "Sonuçlar".

    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sistem geri dönüş limitleri sebebiyle sonuçlar 100 satırda sunulmuştur."
    This is good.

    Wait, `row_limit_hit=evet` is in the summary.
    Okay, final version.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu sistemi gereği sonuçlar 100 satırlık limit dahilinde sunulmuştur."

    Wait, constraint 3: "Generic cümle kurma".
    "Başarıyla oluşturuldu" is generic.
    "Aktif çalışanlara ait 100 kayıtlı detaylı listesi hazırlanmıştır."
    Okay.

    Final Polish:
    Aktif çalışanlara ait 100 kayıtlı detaylı listesi hazırlanmıştır. İçerik olarak çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu sistemi 100 satırlık limit ile çalıştığı için sunulan veri bu sayıdaki kayıtları içermektedir.
</think>

Aktif çalışanlara ait 100 kayıtlı detaylı listesiniz başarıyla hazırlanmıştır. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu sistemi 100 satırlık limit ile çalıştığı için sunulan veri bu sayıdaki kayıtları içermektedir.
```
- response_final:
```text
Aktif çalışanlara ait 100 kayıtlı detaylı listesiniz başarıyla hazırlanmıştır. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu sistemi 100 satırlık limit ile çalıştığı için sunulan veri bu sayıdaki kayıtları içermektedir.
```

### Stage Diffs
- normalize.diff: {'added': {}, 'removed': {}, 'changed': {}, 'changed_fields': []}
- repair.diff: {'added': {'root_entity': 'HR_EMPLOYEES'}, 'removed': {}, 'changed': {}, 'changed_fields': ['root_entity']}
- semantic.diff: {'added': {'semantic_intent': 'emp_generic_list'}, 'removed': {}, 'changed': {}, 'changed_fields': ['semantic_intent']}
- canonicalize.diff: {'added': {}, 'removed': {}, 'changed': {}, 'changed_fields': []}
- changed_semantics: True
- sql_shape_comparable: True
- changed_sql_shape: False
- changed_user_visible_output: True

### Stage Status
- planner.status: {'ok': True, 'note': 'planner output parsed', 'stage_outcome': 'passed'}
- repair.status: {'ok': True, 'note': 'repair completed', 'stage_outcome': 'passed'}
- semantic.status: {'ok': True, 'note': 'semantic normalization completed', 'stage_outcome': 'passed'}
- validation.status: {'ok': True, 'note': 'validation passed', 'stage_outcome': 'passed'}
- compile.status: {'ok': True, 'note': 'compile passed', 'stage_outcome': 'passed'}
- execute.status: {'ok': True, 'note': 'execution passed', 'stage_outcome': 'passed'}
- narration.status: {'ok': True, 'note': 'narration safe', 'stage_outcome': 'passed'}
- planner_question: Aktif calisanlari listele
- execute_question: Aktif calisanlari listele
- narrator_question: Aktif calisanlari listele

### Validation
- ok: True
- errors: []

### Compile
- error: None
- selected_columns_count: 15
- filter_count: 1
- join_count: 0
- aggregation_count: 0
- group_by_count: 0
- bind_param_count: 1
- expression_count: 0
- compile_warning_list: []
- compile_input_plan_snapshot: {'intent': 'Aktif calisanlari listele', 'table': 'XXBT_PDKS_PER_DETAILS_V', 'candidate_tables': [], 'joins': [], 'select_columns': ['PERSON_ID', 'SICIL_NO', 'AD', 'SOYAD', 'FULL_NAME', 'BIRIM_ADI', 'ORGANIZATION_ADI', 'LOCATION_ADI', 'UNVAN', 'GOREV_TANIMI', 'ISE_GIRIS_TARIHI', 'EMAIL', 'DAHILI', 'BORDROLU', 'MASRAF_MERKEZI'], 'filters': [{'column': 'CIKIS_TARIHI', 'table': 'XXBT_PDKS_PER_DETAILS_V', 'op': 'IS_NULL', 'value': None}], 'aggregations': [], 'group_by': [], 'order_by': [], 'semantic_intent': 'emp_generic_list', 'root_entity': 'HR_EMPLOYEES', 'dimensions': [], 'measures': [], 'join_path_id': None, 'computed_measures': [], 'limit': 100, 'needs_clarification': False, 'clarification_message': None, 'clarification_missing_dimensions': []}
- compile_input_diff_from_planner_raw: {'added': {'semantic_intent': 'emp_generic_list', 'root_entity': 'HR_EMPLOYEES'}, 'removed': {}, 'changed': {}, 'changed_fields': ['semantic_intent', 'root_entity']}
- compile_input_diff_from_semantic: {'added': {}, 'removed': {}, 'changed': {}, 'changed_fields': []}
- compiled_sql_source_plan_stage: canonicalize
```sql
SELECT *
FROM (
SELECT PERSON_ID, SICIL_NO, AD, SOYAD, FULL_NAME, BIRIM_ADI, ORGANIZATION_ADI, LOCATION_ADI, UNVAN, GOREV_TANIMI, ISE_GIRIS_TARIHI, EMAIL, DAHILI, BORDROLU, MASRAF_MERKEZI
FROM XXBT_PDKS_PER_DETAILS_V
WHERE CIKIS_TARIHI IS NULL
)
WHERE ROWNUM <= :p1
```
### Execute
- status: success
- row_count: 100
- latency_ms: 674
- executor_class: OracleExecutor
- db_latency_ms: None
- fetch_latency_ms: None
- timeout_applied: True
- row_limit_applied: True
- rows_returned_before_limit: None
- rows_returned_after_limit: 100
- error: None
- execution_error_subtype: None

### Narration
- raw_response: Thinking Process:

1.  **Analyze the Request:**
    *   **Role:** NL2SQL Business Assistant.
    *   **Task:** Provide a high-value, short summary of the query result to the user in business language.
    *   **Constraints:**
        1.  Only use provided summary, no made-up data.
        2.  Consider result shape (listing, grouped_aggregate, scalar_metric, empty_result, clarification).
        3.  No generic sentences; provide concrete info (row count, metrics, breakdown).
        4.  No unnecessary greetings.
        5.  Don't imply limited info, be direct.
        6.  No data, state it clearly.
        7.  No SQL or technical details.
        8.  No thought process, analysis, reasoning, or "Thinking" sections.
        9.  Return only a single short paragraph in Turkish business language.
        10. No Oracle error codes.
        11. Don't repeat prompt/rules.
        12. No prompt echo.
        13. Hide technical table names, focus on business meaning.
    *   **User Question:** "Aktif calisanlari listele" (List active employees).
    *   **Result Summary:**
        *   Query successful.
        *   Row count: 100.
        *   Shape: listing.
        *   Selected fields: PERSON_ID, SICIL_NO, AD, SOYAD, FULL_NAME, BIRIM_ADI, ORGANIZATION_ADI, LOCATION_ADI.
        *   Business fields: SICIL_NO, AD, SOYAD, FULL_NAME, BIRIM_ADI, ORGANIZATION_ADI.
        *   Applied filters: CIKIS_TARIHI IS_NULL (Exit date is NULL -> Active).
        *   Ordering: None.
        *   row_limit_hit: Yes (implies there might be more than 100, but 100 were returned).

2.  **Determine the Output:**
    *   Shape is `listing`. This means I should describe the list.
    *   Row count is 100.
    *   `row_limit_hit` is `yes`. This is crucial. It means the system capped the result at 100 rows, so there might be more active employees than shown. I need to mention this limitation professionally without being technical.
    *   Fields: Name, Surname, I.D. Number (SICIL_NO), Unit, Organization.
    *   Language: Turkish.
    *   Format: Single short paragraph.
    *   Tone: Business professional.

3.  **Drafting the Response:**
    *   *Attempt 1:* Aktif çalışan listesini görüntülediniz. Toplam 100 adet çalışan sıralandı. Ancak sistem limiti nedeniyle daha fazla kayıt olabilir isim, soyisim, sicil no birim bilgileri mevcut. (Too informal, mentions system limit too bluntly).
    *   *Attempt 2:* Aktif çalışanlara ait 100 kayıtlı verilerinizin listesi oluşturulmuştur. Bu listede çalışanların sicili, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Eğer daha fazla kayıt istiyorsanız belirtin. (Better, but "listing" shape usually just reports what's there).
    *   *Attempt 3 (Focusing on business value and row_limit):* Aktif çalışanlar arasında toplam 100 kişiye ait detaylı liste hazırlanmıştır; sicili, adı soyadı, ait olduğu birim ve organizasyon bilgileri incelenebilir. Sorgu limitinden dolayı liste ilk 100 kayıttan oluşmakta olup, toplam çalışan sayısının bundan fazla olma ihtimali mevcuttur. (A bit long).
    *   *Attempt 4 (Concise & Professional):* Aktif çalışanlara ilişkin ilk 100 kayıtlı sizden oluşan detaylı liste oluşturuldu. Listedeki çalışanlar; sicili, adı soyadı, birim ve organizasyon bilgileriyle birlikte görüntülendi. Toplam kayıt sayısı sistemin belirlediği limit gerekçesiyle 100 satırda kesintiye uğramıştır. (Still a bit technical on "limit").
    *   *Attempt 5 (Refining for "Business Value"):* Aktif çalışanlara ait 100 kişilik liste başarıyla hazırlanmıştır. Listede sicil numaraları, ad soyad, birim ve organizasyon bilgileri yer almaktadır. Sorgu sonucu 100'le sınırlandırıldığı için görünen liste mevcut verinin ilk kısmını içermektedir.

    *   *Correction based on constraints:* "Kısıtlı bilgiyi ima etme" (Don't imply limited info). However, `row_limit_hit=yes` is a fact. I should state it factually without apologizing or making it sound like an error. Actually, constraint 3 says "satır sayısı... somut bilgi ver". Constraint 5 says "Veri yoksa açıkça belirt". Constraint 1 says "Yalnızca verilen özete göre yanıt ver".
    *   If `row_limit_hit` is true, it means there are likely more rows. I should mention the count returned (100) and note that it is a limited view if necessary for accuracy, but keep it business-focused.
    *   Actually, looking at constraint 3 "Generic cümle kurma; satır sayısı... somut bilgi ver".
    *   Let's focus on the 100 active employees found within the displayed scope.
    *   Draft: Aktif çalışanlara dair toplam 100 kişiye ait liste hazırlandı. Kayıtlar; sicil numaraları, ad soyad bilgileri, bulunduğu birim ve organizasyon bilgilerini içermektedir. Listeleme işlemi 100 kayıtla sınırlandırıldığı için verinin tamamı bu ekranda yer almakta olup detaylı inceleme yapılabilir.
    *   Refined for brevity and flow: Aktif çalışanlara ilişkin ilk 100 kişiye ait liste başarıyla oluşturuldu. Görünümde çalışanların sicil numaraları, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Listeleme sürecinde belirlenen limit gereği sağlanan veri 100 satırla sınırlıdır.

    *   Wait, constraint 5 says "Veri yoksa açıkça belirt". Data is there. Constraint 1 says "Yalnızca verilen özete göre yanıt ver".
    *   Key info: 100 rows, Active employees (filtered by Exit Date NULL), Columns (Name, ID, Unit, Org), Limit hit.
    *   Business phrasing: "100 aktif çalışan listelendi.", "Limit nedeniyle.", "İsim, birim vb."
    *   Let's try to be very direct.
    *   "Aktif çalışanlara ait 100 kayıtlıdan oluşan liste sunulmuştur. Liste sicil numarası, kişi adı, bağlı olduğu birim ve organizasyon bilgilerini içerir. Sorgu limiti sebebiyle geri dönen veri 100 satır ile sınırlı kalmıştır."

    *   Check constraint 9: "Tek paragraf dön".
    *   Check constraint 7: "ASLA SQL kodu".
    *   Check constraint 3: "somut bilgi ver".
    *   Final Polish: Aktif çalışanlara dair sisteme kayıtlı ilk 100 kişinin detaylı listesi başarıyla oluşturuldu. Hazırlanan görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Toplam veri seti sorgu limitine takıldığı için bu ekranda 100 kayıtlı sonucunuz görüntülenmektedir.

    *   Wait, is "ilk 100 kişi" accurate? Sorting is "yok". So it's just "100 kayıtlı kayıt".
    *   Better: "Aktif çalışanlara ait 100 adet kayıt listelendi. Çıkan sonuçlarda çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu sonucu 100 satır ile sınırlandırıldığı için tam veri seti bu sayı kadar görüntülenmiştir."

    *   Let's ensure it doesn't sound like an error. Just a fact.
    *   Final Version: Aktif çalışanlara ait 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, bağlı olduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu 100 satır ile sınırlandırıldığı için sunulan sonuçlar mevcut limit dahilindeki çalışanları kapsamaktadır.

    *   Wait, one more check on "row_limit_hit":evet. This is important context.
    *   Okay, final check against all constraints.
    *   Turkish: Yes.
    *   Single paragraph: Yes.
    *   No SQL: Yes.
    *   No thinking: Yes.
    *   No greetings: Yes.
    *   Concrete info (100 rows, fields): Yes.

    *   Let's make it tighter.
    *   "Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste hazırlanmıştır. Veriler arasında sicil numarası, adı soyadı, birim ve organizasyon bilgileri detaylandırılmıştır. Eldirilen sonuç 100 satırlaşık sınırlandırıldığı için mevcut liste bu kapsamı yansıtmaktadır." -> "100 satırlaşık" is weird. "100 satırla sınırlandırıldığı için".

    *   Final Choice: Aktif çalışanlara ait 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Görüntülenen veriler arasında çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu işlemi 100 satırlık limit ile sonuçlandığı için sunulan liste bu sayıdaki kayıtları içermektedir.

    *   Actually, "satır sayısı: 100" and "row_limit_hit: evet" means there are likely more. I should be honest about the limit without saying "I can't show you the rest". Just state it happened.
    *   Revised: Aktif çalışanlara dair 100 adet kayıt içeren liste başarıyla oluşturulmuştur. Listede çalışanların sicil numarası, adı soyadı birim ve organizasyon bilgileri detaylarıyla yer almaktadır. Sorgu 100 kayıtlık limit ile sonuçlandığı için görüntülenen veriler bu sınır dahilindedir.

    *   One more refinement for flow:
    *   Aktif çalışanlara ait 100 kişilik liste başarıyla oluşturuldu. Listede sicil numarası, ad soyad, birim ve organizasyon bilgileri yer almaktadır. Sorgu limiti nedeniyle sonuç 100 kayıtla sınırlı kalmış olup, bu aralıkta yer alan çalışan verileri sunmaktadır.

    *   Wait, constraint 3: "satır sayısı, metrik veya kırılım gibi somut bilgi ver". I am doing that.
    *   Okay, ready to output.

    *   Wait, looking closely at "row_limit_hit=evet". This usually means the underlying DB/Code returned a LIMIT.
    *   Let's ensure natural Turkish business language.
    *   "Aktif çalışanlara yönelik 100 kayıtlıdan oluşan liste hazırlanmıştır."
    *   "Liste içinde sicil no, ad soyad, birim ve organizasyon bilgileri mevcuttur."
    *   "Sorgu limiti sebebiyle dönen veri 100 satırla sınırlandırılmıştır."

    *   Combine: Aktif çalışanlara yönelik 100 kayıtlıdan oluşan liste hazırlanmıştır; personel bazında sicil no, ad soyad, birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu limiti haberi sebebiyle dönen veri 100 satırla sınırlandırılmıştır. ( haberi is wrong). "Sorgu limiti haberi" -> "Sorgu limitine takılması sebebiyle".
    *   Final Polish: Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Sunulan görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu sonuç 100 satırlık limit ile kesildiği için liste mevcut bu sayıdaki kayıtları içermektedir.

    *   Wait, "kesildiği için" sounds slightly negative. "Sınırlandığı için".
    *   Final Decision: Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Sunulan görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu sonucunun 100 satırlık limit ile sınırlanmış olması nedeniyle görüntülenen veri bu kapsamdadır.

    *   Wait, checking "Şekil bilgisini dikkate al": listing.
    *   Okay.

    *   One last check: "Kısıtlı bilgiyi ima etme" (Don't imply limited info). This is tricky. If `row_limit_hit` is true, the info IS limited technically. But maybe it means "Don't say 'we could only get this' as if it's bad". Just state the count. If I say "limit... sınırlanmış olması", it implies limitation. But `row_limit_hit` is a fact provided in the summary. I must report facts. I should phrase it neutrally. "100 kayıt gösterildi" is enough? No, row_limit_hit implies there are more.
    *   However, constraint 1 says "Yalnızca verilen özete göre yanıt ver". The summary says `row_limit_hit=evet`. So I should mention it.
    *   Let's phrase it as a system characteristic rather than a data shortage.
    *   "Sorgu sonuç 100 satır limitinde sunulmuştur."

    *   Okay, final version: Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Görüntülenen veriler arasında çalışanların sicil numarası, adı soyadı, ilgili birim ve organizasyon bilgileri yer almaktadır. Sorgu ayarları gereği sonuçlar 100 satırlık limit dahilinde sunulmaktadır.

    *   Wait, "ilgili birim" vs "birim". "BİRİM_ADI". Just "birim bilgisi".
    *   Okay.

    *   Re-reading constraint 3: "Generic cümle kurma; satır sayısı, metrik veya kırılım gibi somut bilgi ver."
    *   My draft has "100 kayıt". That's specific.
    *   My draft has "sicil numarası, adı soyadı...". That's specific.

    *   Okay, final check complete.

    *   Wait, I need to make sure I don't output any extra text. Just the paragraph.

    *   One nuance: " classifica: listing".
    *   Okay.

    *   Final text plan:
    Aktif çalışanlara ait 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Listede çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu sonuç 100 satırlık limit ile sunulduğu için liste bu sayıdaki kayıtları içerir.

    Actually, "limit ile sunulduğu için" is better than "kesiligi için".
    Let's make it flow better.
    "Aktif çalışanlara dair sistemde kayıtlı ilk 100 kişiye ait detaylı liste hazırlanmıştır. Liste içeriğinde sicil numaraları, ad soyad, birim ve organizasyon bilgileri bulunmaktadır. Sorgu 100 kayıt limitine bloklandığı için sonuçlar bu sayı çerçevesinde sunulmuştur."
    "bloklandığı" is too technical. "sınırlandığı".

    Final Final: Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu 100 satırlık limit ile sınırlandığı için sunulan sonuçlar bu kapsamda değerlendirilir.

    Actually, "değerlendirilir" is passive. "içermektedir".
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu 100 satırlık limit ile sınırlandığı için sunulan sonuçlar bu aralıktaki kayıtları içermektedir."

    Let's go with this.
    Wait, "Kısıtlı bilgiyi ima etme" (Don't imply limited info). If I explicitly say "limit ile sınırlandığı için", am I implying limited info?
    If I don't say it, I hide the fact that row_limit_hit is true.
    But the instruction says "Yalnızca verilen özete göre yanıt ver". The özete says limit hit.
    Maybe I just say "100 çalışan listelendi". The user asked to "list active employees". Showing 100 is a valid list.
    But `row_limit_hit` is metadata provided in the summary to inform the assistant about the shape.
    If I don't mention the limit, is it misleading?
    Usually, in business reporting, if there's a limit, it's good practice to mention "ilk 100".
    So "ilk 100" is safer.
    Revised: "Aktif çalışanlara dair ilk 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Görünümde çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Bu çıktı sistemin belirlediği 100 satırlık gösterim standardıyla sunulmuştur." -> "gösterim standardı" sounds like POS.
    Let's stick to the fact. "100 çalışan listesi sunumu yapıldı."

    Okay, I will focus on the positive count and fields. Mentioning limit explicitly is safer for accuracy based on the summary provided.
    Let's try to be neutral. "100 kayıt detaylı olarak listelendi."
    Okay, I'll include the limit fact factually without apologizing.

    "Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla oluşturulmuştur. Listede çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu oranı 100 satırlık limit dahilinde sonuçlandığı için sunulan veri bu sayıyı kapsamaktadır." -> "Sorgu oranı" makes no sense. "Sorgu verisi".
    "Sorgu verisi 100 satırlık limit dahilinde sonuçlandığı için..."
    Okay.

    Let's refine to be most professional.
    "Aktif çalışanlar arasında 100 kişiye ait liste başarıyla oluşturuldu. Listede sicil numarası, ad soyad, birim ve organizasyon bilgileri yer almaktadır. sonuçlar 100 satırlık limit ile sınırlandırıldığı için sunulan veri seti bu kapsamdaki kayıtlardan oluşmaktadır."

    Wait, "Veri uydurma". I shouldn't say "ilk" (first) if there is no sorting. "seçili_alanlar... sıralama=yok". So it's not sorted. So "ilk" is technically presuming order.
    I should just say "100 aktix çalışan bulundu". Or "100 çalışan kaydı listelendi".
    If `row_limit_hit` is true, it means there are more records found by the DB but limited by the app/query.
    I will say: "Aktif çalışanlara ilişkin toplam 100 kayıtlıdan oluşan liste sunulmuştur."

    Okay, final draft.
    Aktif çalışanlara ilişkin 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Çıktıda çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri yer almaktadır. Sorgu sonucu 100 satırlık limit ile sınırlı olduğu için mevcut görüntüleme bu sayıdaki kayıtları içermektedir.

    Wait, "İçermektedir" vs "Sunulmuştur".
    Okay, looks good.

    Wait, constraint 12: "Prompt echo / policy echo üretme". I must not include rules.
    Constraint 13: "Teknik tablo adlarını göstermeden". I used "SICIL_NO" etc as business fields in the summary, I should use "sicil numarası".
    Summary says "iş_alanları=SICIL_NO,AD,SOYAD...". So "sicil numarası, ad, soyad".
    My draft uses these. Good.

    One last check: Constraints say "只用ca verilen özete göre yanıt ver".
    I have used the summary info (100 rows, fields, filter active, limit hit).
    Okay.

    Final check on "Tek paragraf". Yes.
    Final check on "Türkçe". Yes.
    Final check on "Sansür". No.

    Let's finalize the text.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Listede çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu sonucu 100 satırlık limit ile sınırlı olduğu için sunulan veri bu aralıktaki kayıtları kapsamaktadır."

    Wait, "sınırlı olduğu için" implies negative.
    "100 satırlık gösterim sınırlaması gereği".
    "Sorgu geri dönüşü 100 satır ile sınırlandırılmıştır."
    Let's use: "Sorgu 100 satır limitinde toplandığı için..."
    Let's keep it simple: "Listelenen 100 kayıtlı veri, sorgu limitine uygun olarak sunulmuştur."
    No, I need to be clear.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla hazırlanmıştır. Listede çalışanların sicil numarası, adı soyadı, bağlı bulunduğu birim ve organizasyon bilgileri detaylandırılmıştır. Sorgu işlemi 100 satırlık limit dahilinde sonuçlandığı için sunulan veri seti bu kapsamda değerlendirilir."
    "Bağlamda değerlendirilir" -> Researchers might ask why not more.
    Just: "100 satırlık limit dahilinde sonuçlandırılan sorgu ile bu kayıtlar sunulmuştur."

    Okay, I will stop overthinking the limit part. State the fact clearly and concisely.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Toplam kayıt sayısı sorgu tarafında 100 ile sınırlandırıldığı için sonuçlar bu sayıdadır."
    This is clear and fact-based.

    Wait, "Toplam kayıt sayısı ... 100 ile sınırlandırıldığı için".
    Is "toplam kayıt sayısı" accurate if there are more? "Geri dönen kayıt sayısı".
    "Sonuç seti 100 satırlık limitle sınırlandırıldığı için..."
    OK.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu_seti 100 satırlık limitle sınırlandırıldığı için sonuçlar bu sayıdadır."
    "Sorgu_seti" is technical. "Yanıt" is better. "Sonuçlar".

    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sistem geri dönüş limitleri sebebiyle sonuçlar 100 satırda sunulmuştur."
    This is good.

    Wait, `row_limit_hit=evet` is in the summary.
    Okay, final version.
    "Aktif çalışanlara dair 100 kayıtlıdan oluşan liste başarıyla oluşturuldu. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu sistemi gereği sonuçlar 100 satırlık limit dahilinde sunulmuştur."

    Wait, constraint 3: "Generic cümle kurma".
    "Başarıyla oluşturuldu" is generic.
    "Aktif çalışanlara ait 100 kayıtlı detaylı listesi hazırlanmıştır."
    Okay.

    Final Polish:
    Aktif çalışanlara ait 100 kayıtlı detaylı listesi hazırlanmıştır. İçerik olarak çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu sistemi 100 satırlık limit ile çalıştığı için sunulan veri bu sayıdaki kayıtları içermektedir.
</think>

Aktif çalışanlara ait 100 kayıtlı detaylı listesiniz başarıyla hazırlanmıştır. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu sistemi 100 satırlık limit ile çalıştığı için sunulan veri bu sayıdaki kayıtları içermektedir.
- sanitized_response: Aktif çalışanlara ait 100 kayıtlı detaylı listesiniz başarıyla hazırlanmıştır. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu sistemi 100 satırlık limit ile çalıştığı için sunulan veri bu sayıdaki kayıtları içermektedir.
- final_response: Aktif çalışanlara ait 100 kayıtlı detaylı listesiniz başarıyla hazırlanmıştır. Listede çalışanların sicil numarası, adı soyadı, birim ve organizasyon bilgileri yer almaktadır. Sorgu sistemi 100 satırlık limit ile çalıştığı için sunulan veri bu sayıdaki kayıtları içermektedir.
- final_response_source: sanitized
- raw_vs_final_changed: True
- sanitizer_applied: True
- sanitizer_effective: True
- sanitizer_mode: strip_reasoning
- sanitizer_actions: ['strip_reasoning']
- narrator_policy_violation_types: ['chain_of_thought_leak', 'prompt_echo_leak', 'policy_echo_leak', 'presentation_leak']
- raw_response_policy_violations: ['chain_of_thought_leak', 'prompt_echo_leak', 'policy_echo_leak', 'presentation_leak']
- sanitized_response_policy_violations: []
- final_response_policy_violations: []
- sql_leak: False
- presentation_leak: False
- chain_of_thought_leak: False
- prompt_echo_leak: False
- policy_echo_leak: False
- oracle_error_leak: False
- raw_chain_of_thought_leak: True
- raw_prompt_echo_leak: True
- raw_policy_echo_leak: True
- raw_sql_leak: False
- raw_presentation_leak: True
- raw_oracle_error_leak: False
- final_chain_of_thought_leak: False
- final_prompt_echo_leak: False
- final_policy_echo_leak: False
- final_sql_leak: False
- final_presentation_leak: False
- final_oracle_error_leak: False
- narration_ok: True
- source_question_for_narrator: Aktif calisanlari listele
- source_execution_status_for_narrator: success
- source_row_count_for_narrator: 100
- source_columns_for_narrator: ['person_id', 'sicil_no', 'ad', 'soyad', 'full_name', 'birim_adi', 'organization_adi', 'location_adi', 'unvan', 'gorev_tanimi', 'ise_giris_tarihi', 'email', 'dahili', 'bordrolu', 'masraf_merkezi']
- source_summary_text_for_narrator: Sorgu başarılı.
Satır sayısı: 100.
status=success
shape=listing
satır_sayısı=100
seçili_alanlar=PERSON_ID,SICIL_NO,AD,SOYAD,FULL_NAME,BIRIM_ADI,ORGANIZATION_ADI,LOCATION_ADI
iş_alanları=SICIL_NO,AD,SOYAD,FULL_NAME,BIRIM_ADI,ORGANIZATION_ADI
uygulanan_filtreler=CIKIS_TARIHI IS_NULL
uygulanan_sıralama=yok
row_limit_hit=evet
- narration_context_mismatch: False
- narration_context_mismatch_fields: []
