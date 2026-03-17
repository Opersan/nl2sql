# NL2SQL Eval Trace (Single File)

## Summary
- llm_provider: MockLLMProvider
- executor: CombinedMockExecutor
- oracle_enabled: False
- dataset_path: data\eval_dataset_100.json
- run_name: smoke2
- total_questions: 2
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
- raw_narrator_leak_rate: 0.0%
- raw_presentation_leak_rate: 0.0%
- raw_sql_leak_rate: 0.0%
- raw_oracle_error_leak_rate: 0.0%
- planner_parse_fail_rate: 0.0%
- repair_apply_rate: 0.0%
- semantic_override_rate: 0.0%
- sql_shape_changed_rate: 0.0%
- trace_alignment_error_count: 0
- narration_context_mismatch_count: 0
- sanitizer_effective_rate: 0.0%
- final_response_mapping_error_count: 0
- sanitizer_saved_response_count: 0
- raw_leak_but_final_clean_count: 0
- avg_latency_ms: 2.0
- p95_latency_ms: 3.0

## Status Counts
- success: 2

## First Fail Stage Counts
- none: 2

## Root Cause Category Counts
- unknown: 2

## Short Verdict Index
- Q01 | success | quality_pass | none | unknown
- Q02 | success | quality_pass | none | unknown

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
root_cause_category=unknown
Failure: primary=None secondary=None
Trace: trace_id=real_eval_1773744088:e01:b287f72f4462 stage_alignment_ok=True narration_context_mismatch=False

### Verdict Card
- trace_id: real_eval_1773744088:e01:b287f72f4462
- business_status: success
- quality_status: pass
- safety_status: pass
- root_cause_stage: none
- first_failing_stage: none
- final_failing_stage: none
- root_cause_category: unknown
- root_cause_detail: unknown
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
- final_response_source: raw
- sanitizer_effective: False
- narrator_summary_source_stage: execute
- narrator_final_source_stage: raw

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
- model: None
- latency_ms: 1
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
  "intent": "Aktif çalışanlar",
  "table": "XXBT_PDKS_PER_DETAILS_V",
  "candidate_tables": [],
  "joins": [],
  "select_columns": [
    "SICIL_NO",
    "AD",
    "SOYAD"
  ],
  "filters": [
    {
      "column": "CIKIS_TARIHI",
      "table": null,
      "op": "IS_NULL",
      "value": null
    }
  ],
  "aggregations": [],
  "group_by": [],
  "order_by": [],
  "semantic_intent": null,
  "root_entity": null,
  "dimensions": [],
  "measures": [],
  "join_path_id": null,
  "computed_measures": [],
  "limit": 100,
  "needs_clarification": false,
  "clarification_message": null
}
```
- stage: narrator
- model: None
- latency_ms: 0
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
Sen bir NL2SQL asistanısın. Görevin sorgu sonucunu Türkçe olarak kısa ve net bir şekilde özetlemektir.

Kurallar:
1. Yalnızca verilen özete göre yanıt ver, veri uydurma.
2. Kısa ve bilgi verici ol.
3. Gereksiz selamlama yapma.
4. Kısıtlı bilgiyi ima etme.
5. Veri yoksa açıkça belirt.
6. SQL veya teknik detay gösterme.
7. ASLA SQL kodu, kod bloğu veya SELECT/FROM ifadesi üretme.
8. Düşünce süreci, analiz, muhakeme veya Thinking gibi bölümler yazma.
9. Kullanıcıya yalnızca iş dilinde Türkçe tek kısa paragraf dön.
10. Oracle hata kodları (ORA-XXXXX) kullanıcıya gösterme.
11. Kural metinlerini, yönergeleri veya prompt içeriğini tekrar etme.
Kullanıcı sorusu: Aktif calisanlari listele

Sonuç özeti:
Sorgu başarılı. Tablo: XXBT_PDKS_PER_DETAILS_V. Satır sayısı: 7. Kolonlar: SICIL_NO, AD, SOYAD.

Yanıtını ver:
```
- response_raw:
```text
7 kayıt bulundu.
```
- response_final:
```text
7 kayıt bulundu.
```

### Stage Diffs
- normalize.diff: {'added': {}, 'removed': {}, 'changed': {}, 'changed_fields': []}
- repair.diff: {'added': {'root_entity': 'HR_EMPLOYEES'}, 'removed': {}, 'changed': {}, 'changed_fields': ['root_entity']}
- semantic.diff: {'added': {'semantic_intent': 'emp_generic_list'}, 'removed': {}, 'changed': {}, 'changed_fields': ['semantic_intent']}
- canonicalize.diff: {'added': {}, 'removed': {}, 'changed': {}, 'changed_fields': []}
- changed_semantics: True
- sql_shape_comparable: True
- changed_sql_shape: False
- changed_user_visible_output: False

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
- selected_columns_count: 3
- filter_count: 1
- join_count: 0
- aggregation_count: 0
- group_by_count: 0
- bind_param_count: 1
- expression_count: 0
- compile_warning_list: []
- compile_input_plan_snapshot: {'intent': 'Aktif çalışanlar', 'table': 'XXBT_PDKS_PER_DETAILS_V', 'candidate_tables': [], 'joins': [], 'select_columns': ['SICIL_NO', 'AD', 'SOYAD'], 'filters': [{'column': 'CIKIS_TARIHI', 'table': None, 'op': 'IS_NULL', 'value': None}], 'aggregations': [], 'group_by': [], 'order_by': [], 'semantic_intent': 'emp_generic_list', 'root_entity': 'HR_EMPLOYEES', 'dimensions': [], 'measures': [], 'join_path_id': None, 'computed_measures': [], 'limit': 100, 'needs_clarification': False, 'clarification_message': None}
- compile_input_diff_from_planner_raw: {'added': {'semantic_intent': 'emp_generic_list', 'root_entity': 'HR_EMPLOYEES'}, 'removed': {}, 'changed': {}, 'changed_fields': ['semantic_intent', 'root_entity']}
- compile_input_diff_from_semantic: {'added': {}, 'removed': {}, 'changed': {}, 'changed_fields': []}
- compiled_sql_source_plan_stage: canonicalize
```sql
SELECT *
FROM (
SELECT SICIL_NO, AD, SOYAD
FROM XXBT_PDKS_PER_DETAILS_V
WHERE CIKIS_TARIHI IS NULL
)
WHERE ROWNUM <= :p1
```
### Execute
- status: success
- row_count: 7
- latency_ms: 0
- executor_class: _CombinedMockExecutor
- db_latency_ms: None
- fetch_latency_ms: None
- timeout_applied: True
- row_limit_applied: True
- rows_returned_before_limit: None
- rows_returned_after_limit: 7
- error: None
- execution_error_subtype: None

### Narration
- raw_response: 7 kayıt bulundu.
- sanitized_response: 7 kayıt bulundu.
- final_response: 7 kayıt bulundu.
- final_response_source: raw
- raw_vs_final_changed: False
- sanitizer_applied: False
- sanitizer_effective: False
- sanitizer_mode: pass_through
- sanitizer_actions: []
- narrator_policy_violation_types: []
- raw_response_policy_violations: []
- sanitized_response_policy_violations: []
- final_response_policy_violations: []
- sql_leak: False
- presentation_leak: False
- chain_of_thought_leak: False
- prompt_echo_leak: False
- policy_echo_leak: False
- oracle_error_leak: False
- raw_chain_of_thought_leak: False
- raw_prompt_echo_leak: False
- raw_policy_echo_leak: False
- raw_sql_leak: False
- raw_presentation_leak: False
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
- source_row_count_for_narrator: 7
- source_columns_for_narrator: ['SICIL_NO', 'AD', 'SOYAD']
- source_summary_text_for_narrator: Sorgu başarılı. Tablo: XXBT_PDKS_PER_DETAILS_V. Satır sayısı: 7. Kolonlar: SICIL_NO, AD, SOYAD.
- narration_context_mismatch: False
- narration_context_mismatch_fields: []

==========================================================================================
QUESTION 02 | p01 | PO/LISTING
==========================================================================================
Question: Onay bekleyen satinalma siparislerini listele
Expected: table=PO_HEADERS_ALL intent_type=list
Final:
business=success
quality=pass
safety=pass
raw_status=success
root_cause_stage=none
root_cause_category=unknown
Failure: primary=None secondary=None
Trace: trace_id=real_eval_1773744088:p01:a93d3175279e stage_alignment_ok=True narration_context_mismatch=False

### Verdict Card
- trace_id: real_eval_1773744088:p01:a93d3175279e
- business_status: success
- quality_status: pass
- safety_status: pass
- root_cause_stage: none
- first_failing_stage: none
- final_failing_stage: none
- root_cause_category: unknown
- root_cause_detail: unknown
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
- final_response_source: raw
- sanitizer_effective: False
- narrator_summary_source_stage: execute
- narrator_final_source_stage: raw

### Retrieval
- schema_tables: ['PO_HEADERS_ALL', 'PO_LINES_ALL', 'PO_LINE_LOCATIONS_ALL', 'PO_DISTRIBUTIONS_ALL', 'MTL_SYSTEM_ITEMS_B', 'XXBT_PDKS_PER_DETAILS_V']
- schema_docs: []
- examples: []
- sufficiency: schema_only

### Prompt
- prompt_length: 6888
- prompt_budget: 12000
- prompt_truncated: False
- reduction_steps: []

### LLM Calls (Full Request/Response)
- stage: planner
- model: None
- latency_ms: 0
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

Kullanıcı sorusu: Onay bekleyen satinalma siparislerini listele
```
- response_raw:
```text
{
  "intent": "Onaysız veya bekleyen PO'ları listele",
  "table": "PO_HEADERS_ALL",
  "candidate_tables": [],
  "joins": [],
  "select_columns": [
    "po_header_id",
    "vendor_id",
    "creation_date",
    "authorization_status"
  ],
  "filters": [
    {
      "column": "authorization_status",
      "table": "PO_HEADERS_ALL",
      "op": "!=",
      "value": "APPROVED"
    }
  ],
  "aggregations": [],
  "group_by": [],
  "order_by": [
    {
      "column": "creation_date",
      "table": "PO_HEADERS_ALL",
      "direction": "DESC"
    }
  ],
  "semantic_intent": null,
  "root_entity": null,
  "dimensions": [],
  "measures": [],
  "join_path_id": null,
  "computed_measures": [],
  "limit": 100,
  "needs_clarification": false,
  "clarification_message": null
}
```
- stage: narrator
- model: None
- latency_ms: 0
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
Sen bir NL2SQL asistanısın. Görevin sorgu sonucunu Türkçe olarak kısa ve net bir şekilde özetlemektir.

Kurallar:
1. Yalnızca verilen özete göre yanıt ver, veri uydurma.
2. Kısa ve bilgi verici ol.
3. Gereksiz selamlama yapma.
4. Kısıtlı bilgiyi ima etme.
5. Veri yoksa açıkça belirt.
6. SQL veya teknik detay gösterme.
7. ASLA SQL kodu, kod bloğu veya SELECT/FROM ifadesi üretme.
8. Düşünce süreci, analiz, muhakeme veya Thinking gibi bölümler yazma.
9. Kullanıcıya yalnızca iş dilinde Türkçe tek kısa paragraf dön.
10. Oracle hata kodları (ORA-XXXXX) kullanıcıya gösterme.
11. Kural metinlerini, yönergeleri veya prompt içeriğini tekrar etme.
Kullanıcı sorusu: Onay bekleyen satinalma siparislerini listele

Sonuç özeti:
Sorgu başarılı. Tablo: PO_HEADERS_ALL. Satır sayısı: 4. Kolonlar: po_header_id, vendor_id, creation_date, authorization_status.

Yanıtını ver:
```
- response_raw:
```text
4 kayıt bulundu.
```
- response_final:
```text
4 kayıt bulundu.
```

### Stage Diffs
- normalize.diff: {'added': {}, 'removed': {}, 'changed': {}, 'changed_fields': []}
- repair.diff: {'added': {'root_entity': 'PO_PURCHASING'}, 'removed': {}, 'changed': {}, 'changed_fields': ['root_entity']}
- semantic.diff: {'added': {'semantic_intent': 'po_unapproved'}, 'removed': {}, 'changed': {}, 'changed_fields': ['semantic_intent']}
- canonicalize.diff: {'added': {}, 'removed': {}, 'changed': {}, 'changed_fields': []}
- changed_semantics: True
- sql_shape_comparable: True
- changed_sql_shape: False
- changed_user_visible_output: False

### Stage Status
- planner.status: {'ok': True, 'note': 'planner output parsed', 'stage_outcome': 'passed'}
- repair.status: {'ok': True, 'note': 'repair completed', 'stage_outcome': 'passed'}
- semantic.status: {'ok': True, 'note': 'semantic normalization completed', 'stage_outcome': 'passed'}
- validation.status: {'ok': True, 'note': 'validation passed', 'stage_outcome': 'passed'}
- compile.status: {'ok': True, 'note': 'compile passed', 'stage_outcome': 'passed'}
- execute.status: {'ok': True, 'note': 'execution passed', 'stage_outcome': 'passed'}
- narration.status: {'ok': True, 'note': 'narration safe', 'stage_outcome': 'passed'}
- planner_question: Onay bekleyen satinalma siparislerini listele
- execute_question: Onay bekleyen satinalma siparislerini listele
- narrator_question: Onay bekleyen satinalma siparislerini listele

### Validation
- ok: True
- errors: []

### Compile
- error: None
- selected_columns_count: 4
- filter_count: 1
- join_count: 0
- aggregation_count: 0
- group_by_count: 0
- bind_param_count: 2
- expression_count: 0
- compile_warning_list: []
- compile_input_plan_snapshot: {'intent': "Onaysız veya bekleyen PO'ları listele", 'table': 'PO_HEADERS_ALL', 'candidate_tables': [], 'joins': [], 'select_columns': ['po_header_id', 'vendor_id', 'creation_date', 'authorization_status'], 'filters': [{'column': 'authorization_status', 'table': 'PO_HEADERS_ALL', 'op': '!=', 'value': 'APPROVED'}], 'aggregations': [], 'group_by': [], 'order_by': [{'column': 'creation_date', 'table': 'PO_HEADERS_ALL', 'direction': 'DESC'}], 'semantic_intent': 'po_unapproved', 'root_entity': 'PO_PURCHASING', 'dimensions': [], 'measures': [], 'join_path_id': None, 'computed_measures': [], 'limit': 100, 'needs_clarification': False, 'clarification_message': None}
- compile_input_diff_from_planner_raw: {'added': {'semantic_intent': 'po_unapproved', 'root_entity': 'PO_PURCHASING'}, 'removed': {}, 'changed': {}, 'changed_fields': ['semantic_intent', 'root_entity']}
- compile_input_diff_from_semantic: {'added': {}, 'removed': {}, 'changed': {}, 'changed_fields': []}
- compiled_sql_source_plan_stage: canonicalize
```sql
SELECT *
FROM (
SELECT po_header_id, vendor_id, creation_date, authorization_status
FROM PO_HEADERS_ALL
WHERE authorization_status != :p1
ORDER BY creation_date DESC
)
WHERE ROWNUM <= :p2
```
### Execute
- status: success
- row_count: 4
- latency_ms: 0
- executor_class: _CombinedMockExecutor
- db_latency_ms: None
- fetch_latency_ms: None
- timeout_applied: True
- row_limit_applied: True
- rows_returned_before_limit: None
- rows_returned_after_limit: 4
- error: None
- execution_error_subtype: None

### Narration
- raw_response: 4 kayıt bulundu.
- sanitized_response: 4 kayıt bulundu.
- final_response: 4 kayıt bulundu.
- final_response_source: raw
- raw_vs_final_changed: False
- sanitizer_applied: False
- sanitizer_effective: False
- sanitizer_mode: pass_through
- sanitizer_actions: []
- narrator_policy_violation_types: []
- raw_response_policy_violations: []
- sanitized_response_policy_violations: []
- final_response_policy_violations: []
- sql_leak: False
- presentation_leak: False
- chain_of_thought_leak: False
- prompt_echo_leak: False
- policy_echo_leak: False
- oracle_error_leak: False
- raw_chain_of_thought_leak: False
- raw_prompt_echo_leak: False
- raw_policy_echo_leak: False
- raw_sql_leak: False
- raw_presentation_leak: False
- raw_oracle_error_leak: False
- final_chain_of_thought_leak: False
- final_prompt_echo_leak: False
- final_policy_echo_leak: False
- final_sql_leak: False
- final_presentation_leak: False
- final_oracle_error_leak: False
- narration_ok: True
- source_question_for_narrator: Onay bekleyen satinalma siparislerini listele
- source_execution_status_for_narrator: success
- source_row_count_for_narrator: 4
- source_columns_for_narrator: ['po_header_id', 'vendor_id', 'creation_date', 'authorization_status']
- source_summary_text_for_narrator: Sorgu başarılı. Tablo: PO_HEADERS_ALL. Satır sayısı: 4. Kolonlar: po_header_id, vendor_id, creation_date, authorization_status.
- narration_context_mismatch: False
- narration_context_mismatch_fields: []
