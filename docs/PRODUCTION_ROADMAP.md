# NL2SQL Production Roadmap (Master + Immediate Sprints)

## 1. Purpose

Bu dokümanın amacı:

- sistemi benchmark geçiren bir demo değil  
- **production-grade, güvenli, açıklanabilir ve sürdürülebilir bir NL2SQL platformuna dönüştürmek**

Başarı tanımı:

> “25 soruda %100” değil  
> “yeni ve bilinmeyen sorularda sistematik olarak doğru ve güvenli davranış”

---

# 2. System North Star (Production Charter)

## 2.1 Non-Negotiable Principles

Bu kurallar hiçbir aşamada ihlal edilmez:

1. SQL doğrudan üretilmez → her zaman **QueryPlan-first**
2. Planner, validation, compiler, execution ayrımı korunur
3. Yanlış ama executable plan = başarısızlık
4. `empty_result` ≠ sistem hatası
5. `clarification` bir failure değil → kontrollü davranış
6. Semantic layer tek source-of-truth
7. Prompt büyütme ile hatalar gizlenmez
8. Narrator ile yanlış plan maskelenmez

---

## 2.2 Production Success Metrics

Tek bir metrik yok. Aşağıdakiler birlikte izlenir:

### Core Quality
- business_success_rate
- wrong_plan_rate
- handled_safely_rate
- clarification_precision / recall

### Pipeline Reliability
- planner_parse_fail_rate
- validation_failure_rate
- compile_failure_rate
- execution_error_rate
- timeout_rate

### Semantics & Retrieval
- root_entity_accuracy
- join_path_accuracy
- filter_ownership_accuracy
- semantic_override_harm_rate

### Safety
- policy_block_precision
- unsafe_sql_count
- restricted_field_escape_count

### Observability
- trace_completeness_rate
- unclassified_failure_rate

---

# 3. Current System State (Run2 Diagnosis)

## Current metrics (özet):
- success_rate ≈ 64%
- business_success_rate ≈ 68%
- clarification_rate ≈ 12%
- planner_parse_fail ≈ düşük
- validation/compile error ≈ 0

## Ana darboğazlar:

### 1. Execution Layer
- timeout (3)
- oracle_date_type_error (2)
- p95 latency çok yüksek

### 2. Narrator
- raw leak rate %40
- sanitizer dependency yüksek

### 3. Empty Results
- 6 vaka → data mı yok semantic mi belirsiz

### 4. Repair Layer
- repair_apply_rate = 0

---

# 4. Roadmap Structure

Bu roadmap iki katmandan oluşur:

## A. Strategic Programs (Production Target)
## B. Immediate Execution (Next 2 Sprints)

---

# 5. A. Strategic Programs (Long-Term)

### Program A — Observability & Failure Taxonomy
- tüm failure’lar reason code ile sınıflanır
- unclassified_failure_rate ≈ 0 hedeflenir

### Program B — Planner Contracts
- stage-based planner architecture (DONE ✔)

### Program C — Semantic & Retrieval Hardening
- root entity accuracy ↑
- semantic override harm ↓

### Program D — Validation & Repair Hardening
- safe auto-healing
- validation sonrası repair

### Program E — Execution Reliability
- Oracle edge cases
- timeout handling
- degrade strategies

### Program F — Clarification Policy
- gereksiz clarification ↓
- ambiguity yönetimi ↑

### Program G — Narration Quality
- sanitizer bağımlılığı ↓
- iş dili ↑

### Program H — Evaluation Redesign
- benchmark → domain-balanced + replay

### Program I — Release Governance
- canary
- rollback
- feature flags

---

# 6. B. Immediate Execution Plan (CRITICAL)

⚠️ Şu an odak:
**Accuracy Uplift (Execution + Narrator + Data + Repair)**

---

# 🚀 Sprint 1 (Highest ROI)

## 1. Execution Stabilization (P0)

### Hedef:
- execution_error ↓
- timeout ↓
- date-type error ↓

### İşler:
- Oracle DATE/TIMESTAMP bind fix
- date normalization helper
- timeout-prone query detection
- safer default execution
- simple listing optimize

### Başarı Kriteri:
- timeout: 3 → ≤1
- execution_error_rate ciddi düşmeli

---

## 2. Narrator Raw Leak Reduction (P0)

### Hedef:
- raw leak ↓
- sanitizer bağımlılığı ↓

### İşler:
- narrator prompt hardening
- no thinking / no meta / no echo
- empty response fallback iyileştirme
- leak taxonomy

### Başarı Kriteri:
- raw leak %40 → %15 altı
- sanitized_but_model_failed ↓

---

# 🚀 Sprint 2

## 3. Empty Result Diagnosis (P1)

### Hedef:
- false empty ↓
- data vs semantic ayrımı

### İşler:
- empty_result classification:
  - true_no_data
  - semantic_mismatch
- value encoding mapping (1/0, string vs code)
- synonym mismatch fix
- trace diagnosis flag

### Başarı Kriteri:
- yanlış empty sonuçlar azalmalı

---

## 4. Repair Activation (P1)

### Hedef:
- repair gerçekten çalışsın

### İşler:
- alias → canonical mapping
- invalid sort fix
- validation feedback loop
- safe repair rules

### Başarı Kriteri:
- repair_apply_rate > 10%
- recoverable errors ↓

---

# 7. Post-Sprint Target Metrics

Sprint 1 + 2 sonrası hedef:

- success_rate → **75–80%**
- business_success_rate → **80%+**
- execution_error_rate → **<10%**
- timeout → ~0–1
- raw_narrator_leak → <15%
- repair_apply_rate → >10%
- clarification_rate → stabil veya düşüş

---

# 8. After Sprint 2 (Next Phase)

## Devreye girer:
- Program A (failure taxonomy)
- Program H (evaluation redesign)
- Program I (release governance)

---

# 9. Release Gate (Future)

Production release için minimum:

- wrong_plan_rate ↓ trend
- execution_error_rate düşük
- policy violations = 0
- trace_completeness yüksek
- replay suite pass

---

# 10. What We Explicitly Avoid

❌ Benchmark’a özel hack  
❌ Prompt büyüterek sorunu gizleme  
❌ Narrator ile yanlış sonucu “anlatma”  
❌ Sensitive query’leri executable yapma  
❌ Empty result’ı failure sayma  

---

# 11. Final Operating Model

Pipeline sabit:

→ NL
→ query understanding
→ retrieval
→ semantic resolution
→ QueryPlan
→ validation
→ compile
→ execution guard
→ execution
→ narration
→ trace + eval


Bu pipeline:
- **bozulmadan optimize edilir**
- stage’ler karıştırılmaz
- her değişiklik trace + eval ile doğrulanır

---

# 12. Final Summary

## Bu planın özü:

- Agent planı → **doğru yön**
- Accuracy uplift → **doğru sıra**

## Şu an yapılacak:

👉 Sprint 1: Execution + Narrator  
👉 Sprint 2: Empty Result + Repair  

## Sonra:

👉 Observability + Evaluation + Governance  