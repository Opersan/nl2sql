A. Eval dataset ozeti
- Toplam soru: 100
- PO: 40
- HR/EMP: 40
- Cross/Ambiguous/Invalid: 20

B. Pipeline sonuclari
- success: 31
- empty_result: 0
- clarification: 15
- validation_error: 6
- compile_error: 0
- execution_error: 45
- wrong_plan: 3

C. Wrong-plan analizi
- wrong_plan_rate (ambiguous olmayan sorular): 3.5%
- Manual review listesi boyutu: 54

D. Oracle runtime davranisi
- avg_latency: 25639.6 ms
- p95_latency: 60536.0 ms
- timeout_count: 0
- row_count_distribution: {'11_100': 23, '1_10': 9, 'unknown': 66, '0': 2}
- heavy_join_queries: 5
- top_slowest_queries: 10 kayit

E. Clarification analizi
- genuine_ambiguity: 4
- recoverable_ambiguity: 10
- metadata_gap: 1
- schema_linking_failure: 0

F. Guvenlik dogrulamasi
- SQLGuard SELECT-only: True
- multi-statement block: True
- bind param usage: True
- row limit enforced: True
- timeout enforced: True
- SQL leak count: 22
- restricted fields exposure count: 0

G. Production readiness karari
- karar: not_ready

H. Sonuc metrikleri
| metric | value |
|---|---:|
| success_rate | 31.0% |
| clarification_rate | 15.0% |
| wrong_plan_rate | 3.5% |
| validation_error_rate | 6.0% |
| compile_error_rate | 0.0% |
| execution_error_rate | 45.0% |
| avg_latency | 25639.6 ms |
| p95_latency | 60536.0 ms |