A. Eval dataset ozeti
- Toplam soru: 200
- PO: 80
- HR/EMP: 80
- Cross/Ambiguous/Invalid: 40

B. Pipeline sonuclari
- success: 101
- empty_result: 0
- clarification: 10
- validation_error: 10
- compile_error: 0
- execution_error: 35
- wrong_plan: 44

C. Wrong-plan analizi
- wrong_plan_rate (ambiguous olmayan sorular): 25.3%
- Manual review listesi boyutu: 89

D. Oracle runtime davranisi
- avg_latency: 107.4 ms
- p95_latency: 485.0 ms
- timeout_count: 0
- row_count_distribution: {'11_100': 131, '1_10': 14, 'unknown': 55}
- heavy_join_queries: 15
- top_slowest_queries: 10 kayit

E. Clarification analizi
- genuine_ambiguity: 2
- recoverable_ambiguity: 8
- metadata_gap: 0
- schema_linking_failure: 0

F. Guvenlik dogrulamasi
- SQLGuard SELECT-only: True
- multi-statement block: True
- bind param usage: True
- row limit enforced: True
- timeout enforced: True
- SQL leak count: 0
- restricted fields exposure count: 0

G. Execution error alt tipleri
- unknown_execution_error: 29
- invalid_date_value: 6
- structured_parse_errors: 0

H. Narrator leak analizi
- sql_leak_count: 0
- presentation_leak_count: 0

I. Top-20 failure buckets
- [ 29] execution_error/unknown_execution_error
- [ 17] wrong_plan/wrong_table
- [ 16] wrong_plan/wrong_filter_column
- [ 10] clarification
- [  7] wrong_plan/wrong_aggregation
- [  7] wrong_plan/semantically_incorrect_result
- [  6] execution_error/invalid_date_value
- [  3] validation_error/Tablo bulunamadı: 'AP_INVOICES_ALL'.
- [  3] validation_error/JOIN tablosu bulunamadı: 'LOCATION'.
- [  2] validation_error/Kolon bulunamadı: 'SICIL_NO' (tablo: PO_HEADERS_ALL).
- [  2] wrong_plan/wrong_join
- [  1] validation_error/JOIN tablosu bulunamadı: 'DEPARTMENT'.
- [  1] validation_error/Kolon bulunamadı: 'TC_NO' (tablo: XXBT_PDKS_PER_DETAILS_V).

J. Repair engine metrikleri
- questions_with_repair: 10/200
- questions_with_repair_rate: 5.0%
- repaired_fields_total: 10
- repair_action/F_clarification_rescue: 6
- repair_action/E_anchor_table: 4

K. Production readiness karari
- karar: not_ready

L. Sonuc metrikleri
| metric | value |
|---|---:|
| success_rate | 50.5% |
| clarification_rate | 5.0% |
| wrong_plan_rate | 25.3% |
| validation_error_rate | 5.0% |
| compile_error_rate | 0.0% |
| execution_error_rate | 17.5% |
| avg_latency | 107.4 ms |
| p95_latency | 485.0 ms |