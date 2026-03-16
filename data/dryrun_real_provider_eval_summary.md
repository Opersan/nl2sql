A. Eval dataset ozeti
- Toplam soru: 100
- PO: 40
- HR/EMP: 40
- Cross/Ambiguous/Invalid: 20

B. Pipeline sonuclari
- success: 72
- empty_result: 0
- clarification: 1
- validation_error: 1
- compile_error: 0
- execution_error: 0
- wrong_plan: 26

C. Wrong-plan analizi
- wrong_plan_rate (ambiguous olmayan sorular): 30.2%
- Manual review listesi boyutu: 27

D. Oracle runtime davranisi
- avg_latency: 0.9 ms
- p95_latency: 1.0 ms
- timeout_count: 0
- row_count_distribution: {'1_10': 93, 'unknown': 2, '0': 5}
- heavy_join_queries: 8
- top_slowest_queries: 10 kayit

E. Clarification analizi
- genuine_ambiguity: 1
- recoverable_ambiguity: 0
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

G. Production readiness karari
- karar: not_ready

H. Sonuc metrikleri
| metric | value |
|---|---:|
| success_rate | 72.0% |
| clarification_rate | 1.0% |
| wrong_plan_rate | 30.2% |
| validation_error_rate | 1.0% |
| compile_error_rate | 0.0% |
| execution_error_rate | 0.0% |
| avg_latency | 0.9 ms |
| p95_latency | 1.0 ms |