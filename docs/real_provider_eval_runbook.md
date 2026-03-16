# Real Provider Eval Runbook

## 1. Hazirlanan dosyalar

- Dataset generator: [scripts/build_eval_dataset.py](scripts/build_eval_dataset.py)
- 100-soru dataset: [data/eval_dataset_100.json](data/eval_dataset_100.json)
- Real eval runner: [scripts/e2e_real_provider_eval.py](scripts/e2e_real_provider_eval.py)

## 2. Dry-run (mock executor) komutu

```powershell
.\.venv\Scripts\python scripts\e2e_real_provider_eval.py --no-oracle --dataset data\eval_dataset_100.json --report-json data\dryrun_real_provider_eval_report.json --report-md data\dryrun_real_provider_eval_summary.md --manual-review-json data\dryrun_real_provider_manual_review.json
```

## 3. Real run (vLLM + Oracle) oncesi

- LLM provider ayari: llm_provider=openai_compatible
- openai_base_url: vLLM endpoint
- openai_model: served model name
- Oracle ayarlari: enable_oracle_executor=true, oracle_dsn, oracle_user, oracle_password, oracle_timeout

## 4. Real run komutu

```powershell
.\.venv\Scripts\python scripts\e2e_real_provider_eval.py --dataset data\eval_dataset_100.json --report-json data\real_provider_eval_report.json --report-md data\real_provider_eval_summary.md --manual-review-json data\real_provider_manual_review.json
```

## 5. Uretilen raporlar

- Ana rapor JSON: [data/real_provider_eval_report.json](data/real_provider_eval_report.json)
- Ozet A-G markdown: [data/real_provider_eval_summary.md](data/real_provider_eval_summary.md)
- Manual review listesi: [data/real_provider_manual_review.json](data/real_provider_manual_review.json)

## 6. Scriptin kapsadigi zorunlu alanlar

- Per-question fields:
  - question
  - semantic_intent
  - predicted_tables
  - join_path
  - compiled_sql
  - execution_status
  - row_count
  - latency_ms
  - narrator_response
- Status labels:
  - success
  - empty_result
  - clarification
  - validation_error
  - compile_error
  - execution_error
  - wrong_plan
- Clarification classes:
  - genuine_ambiguity
  - recoverable_ambiguity
  - metadata_gap
  - schema_linking_failure
- Safety checks:
  - SQLGuard SELECT-only
  - multi-statement block
  - bind param usage
  - row limit enforced
  - timeout enforced
  - SQL leak
  - restricted fields exposure
- Final metric table:
  - success_rate
  - clarification_rate
  - wrong_plan_rate
  - validation_error_rate
  - compile_error_rate
  - execution_error_rate
  - avg_latency
  - p95_latency
