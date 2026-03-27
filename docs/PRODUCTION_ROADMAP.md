0. Executive Reality Check (Mevcut Sistem Durumu)

Mevcut run’lardan çıkan net gerçekler:

1. Sistem “çalışıyor ama doğru değil”
success_rate ≈ %52
user_visible_pass ≈ %96 (sanitizer kurtarıyor)

👉 Bu dangerous false confidence

2. Ana problem SQL değil → grounding + plan quality

Top root cause’lar:

missing_filter → %24 civarı
empty_result → canonical value mismatch
planner_output hataları
LIKE fallback → yanlış sonuç

👉 Sistem SQL üretiyor ama doğru business filtreyi bulamıyor

3. Kritik örnek (gerçek kırılma)
"IT departmanındaki çalışanlar"
→ BIRIM_ADI = 'IT'
→ RESULT = EMPTY

👉 Root cause:

canonical value yok
grounding yok
disambiguation yok
4. Clarification sistemi broken
clarification_rate: %24
ama çoğu anlamsız veya eksik

5. Execution layer noisy
timeout var
unknown_execution_error yüksek
root cause visibility düşük
🎯 STRATEGIC GOAL

Sistemi şu seviyeye taşımak:

FROM:
"SQL üreten sistem"

TO:
"Semantic + grounded + controllable query system"
🧠 TARGET ARCHITECTURE (FINAL)
User Query
   ↓
[Query Understanding]

   ↓
[Retrieval Layer]
(schema + glossary + examples)

   ↓
[Planner]
(QueryPlan)

   ↓
[Normalization + Repair + Semantic]

   ↓
[Filter Column Resolution]      ← Sprint 1 (DONE)

   ↓
[Filter Value Resolution]       ← Sprint 2 (NEW CORE)

   ↓
[Clarification Layer]           ← Sprint 2

   ↓
[Validation Layer]

   ↓
[SQL Compiler]

   ↓
[Execution Layer]

   ↓
[Narration Layer]
🧩 ROADMAP PHASES
🟢 PHASE 1 — FILTER COLUMN RESOLUTION (DONE)

Ama korunmalı.

Amaç:
doğru kolon seçimi
Örnek:
"IT departmanı"
→ BIRIM_ADI
Risk:
semantic override yanlışsa sistem sapar
🔴 PHASE 2 — VALUE GROUNDING SYSTEM (CRITICAL CORE)

Bu sistemin kalbi.

2.1 Problem Tanımı

Bugünkü sistem:

BIRIM_ADI = 'IT'

Ama DB’de:

"Bilgi Teknolojileri Operasyon"
"Yazılım Geliştirme"
2.2 Çözüm: Multi-stage grounding pipeline
A. Candidate Generation (MANDATORY)

Kaynaklar:

DISTINCT profile cache
semantic mapping
alias dictionary (config tabanlı)
offline precomputed value index

❌ Yasak:

runtime SELECT DISTINCT
hardcoded dict
B. Deterministic Ranking

Score =

w1 * exact_match
w2 * alias_match
w3 * token_overlap
w4 * fuzzy_score
w5 * source_confidence
C. LLM Tie-Break (LIMITED)

Sadece:

top_k ≤ 3
D. Confidence Model
if score_gap > threshold:
    auto_resolve

elif borderline:
    LLM_tiebreak

else:
    clarification
E. Clarification System
IT ile hangi birimi kastediyorsunuz?

1. Yazılım Geliştirme
2. BT Operasyon
3. BT Destek
4. Sen karar ver
F. “Sen karar ver” (IMPORTANT)
if top_score ≥ safe_threshold:
    select(top_candidate)
else:
    ask_again
G. FINAL RULE
WHERE BIRIM_ADI = 'Yazılım Geliştirme'

❌ LIKE yasak (fallback hariç)

🚨 Impact

Bu layer çözülmeden:

success_rate artmaz
empty_result çözülmez
business correctness gelmez
🟡 PHASE 3 — CLARIFICATION STATE ENGINE

Bugün eksik olan kritik katman.

Problem
system context tutmuyor
user reply kayboluyor
Solution
Persistent Clarification Object
{
  "clarification_id": "...",
  "question": "...",
  "column": "BIRIM_ADI",
  "candidates": [...],
  "scores": [...],
  "top_candidate": "...",
  "state": "waiting_user"
}
Resume Logic

User:

"2"

→ pipeline resume
→ SQL generate

Impact
conversational intelligence
user control
correctness ↑
🔵 PHASE 4 — EXECUTION INTELLIGENCE (Sprint C)
4.1 Error Decomposition

Map:

ORA-00904 → invalid_identifier
ORA-01722 → invalid_number
ORA-018*  → date_error
timeout   → timeout
4.2 Trace Fields
execution_error_subtype
execution_error_message_normalized
4.3 Impact
debug hızlanır
eval anlamlı olur
🟣 PHASE 5 — VALIDATION HARDENING
Problem
"Istanbul" query → ORDER BY LAST_NAME error

Solution
column existence check
alias resolution
schema guardrail
🟠 PHASE 6 — SQL SAFETY & COST CONTROL
Rules
LIMIT zorunlu
full scan detect
join depth limit
explain cost threshold
Oracle özel
ROWNUM limit enforce
index-aware hints (optional)
⚫ PHASE 7 — PIPELINE LIVE VIEW (OBSERVABILITY)

Yeni stage’ler:

filter_column_resolution
filter_value_resolution
candidate_generation
ranking
llm_tiebreak
clarification_required
clarification_answered
final_value_selected
Gösterilecekler
candidate list
score breakdown
confidence
chosen value
user decision
⚪ PHASE 8 — EVALUATION SYSTEM (CRITICAL)
Mevcut problem
success_rate misleading
sanitizer masking errors
Yeni metricler
business_success_rate
grounding_success_rate
filter_correctness_rate
clarification_resolution_rate
New failure classes
wrong_value_mapping
missing_filter
over_broad_filter
ambiguous_unresolved
🧪 PHASE 9 — TEST STRATEGY
Unit Tests
candidate generation
ranking correctness
threshold behavior
Integration Tests
IT departmanı
Istanbul lokasyonu
ambiguous queries
Regression Tests

Korunacak:

BORDROLU
STAJYER
NULL filters
🧠 PHASE 10 — FUTURE (POST-PROD)
10.1 Semantic Layer (VERY IMPORTANT)

Oracle EBS için:

semantic_dimension:
  department
  location
  cost_center
10.2 Agentic Query Planning
decomposition
multi-step reasoning
self-correction
10.3 Learned Ranking
feedback loop
user corrections → training data
⚠️ NON-NEGOTIABLE RULES
❌ Yapılmayacaklar
hardcoded mapping
direct SQL generation (no grounding)
uncontrolled LIKE
runtime DISTINCT scans
planner redesign
✅ Yapılacaklar
metadata-first
deterministic ranking
controlled LLM usage
validation-first execution
trace-first debugging
🧭 FINAL PRIORITY ORDER
Tier 1 (BLOCKER)
Value grounding
Clarification state
Deterministic ranking
Tier 2
Execution error decomposition
Validation hardening
Tier 3
Observability
Evaluation redesign
Tier 4
Semantic layer
Agentic planning
🔚 SONUÇ

Bu roadmap seni şuraya götürür:

Naive NL2SQL
   ↓
RAG NL2SQL
   ↓
Grounded NL2SQL
   ↓
Controlled Query System
   ↓
Enterprise Decision Assistant