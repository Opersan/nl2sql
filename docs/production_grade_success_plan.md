# Production-Grade Success Plan

Bu planın hedefi belirli bir benchmark setini geçirmek değil, sistemin production ortamında yeni, beklenmeyen ve dağılımı değişken sorularda da güvenli, doğru, açıklanabilir ve sürdürülebilir biçimde çalışmasını sağlamaktır.

Başarı tanımı `25 soru içinde %100` değildir. Başarı tanımı şudur:

- yeni soru dağılımlarında plan-first pipeline'ın bozulmaması
- yanlış ama executable plan oranının sistematik olarak düşmesi
- güvenlik/izin/sensitive policy sınırlarının korunması
- empty result, clarification ve execution failure ayrımının tutarlı olması
- regression yakalanmadan production davranışının değişmemesi
- trace, eval ve rollback mekanizmalarıyla sistemin yönetilebilir olması

Bu nedenle plan benchmark tuning yerine şu omurgaya oturur:

`NL -> query understanding -> retrieval -> semantic resolution -> QueryPlan -> validation -> compilation -> execution guard -> execution -> interpretation -> trace/eval`

## 1. Target Operating Model

Production-grade başarı için sistem her sorguda aşağıdaki sözleşmeyi sağlamalıdır.

1. Doğrudan SQL üretmemeli; her zaman `QueryPlan` üretmeli.
2. Her aşama ayrı sorumluluk taşımalı; retrieval, planning, validation ve execution birbirine karışmamalı.
3. Aynı giriş ve aynı veri/metadata koşullarında davranış mümkün olduğunca deterministik olmalı.
4. Başarısızlıklar tek sepette toplanmamalı; `clarification`, `validation_error`, `execution_error`, `empty_result`, `policy_block`, `wrong_plan` ayrımı korunmalı.
5. Her sonuç için yeterli trace üretilmeli; plan, repair, compile, risk, execution ve narration çıktıları kaybolmamalı.
6. Benchmark'ta iyi görünen ama production'da kırılan heuristics yerine ölçülebilir guard ve contract'lar kullanılmalı.

## 2. Production Success Metrics

Production-grade başarıyı yalnızca tek bir `success_rate` ile yönetmek yetersizdir. Aşağıdaki metrikler birlikte izlenmelidir.

### Core quality

- `business_success_rate`: iş açısından doğru cevap üreten oran
- `handled_safely_rate`: yanlış SQL veya policy ihlali olmadan güvenli şekilde sonuçlanan oran
- `wrong_plan_rate`: executable olsa bile iş semantiği yanlış plan oranı
- `clarification_precision`: clarification gereken yerde clarification üretme doğruluğu
- `clarification_recall`: clarification gerektiren belirsiz soruları kaçırmama oranı

### Pipeline reliability

- `planner_parse_fail_rate`
- `validation_failure_rate`
- `post_validation_repair_success_rate`
- `compile_failure_rate`
- `execution_error_rate`
- `timeout_rate`
- `narration_fallback_rate`

### Retrieval and semantics

- `schema_selection_accuracy`
- `root_entity_accuracy`
- `filter_ownership_accuracy`
- `join_path_accuracy`
- `semantic_override_precision`
- `semantic_override_harm_rate`

### Production safety

- `policy_block_precision`
- `restricted_field_escape_count`
- `unsafe_sql_count`
- `trace_completeness_rate`
- `unclassified_failure_rate`

## 3. Non-Negotiable Principles

1. Benchmark başarısı için güvenlik veya mimari sınırlar gevşetilmeyecek.
2. `empty_result` her zaman başarısızlık değildir; veri yokluğu ile sistem hatası ayrıştırılacak.
3. `clarification` sistem zayıflığı olarak değil, kontrollü davranış olarak ele alınacak; ancak aşırı clarification ayrı problem olarak ölçülecek.
4. Semantic layer tek source-of-truth olacak; ham schema veya LLM tahmini join bilgisi ile sistem yürümeyecek.
5. Production değişiklikleri önce trace ve eval görünürlüğü kazanmadan rollout edilmeyecek.

## 4. Current Risk Map

Repo durumuna göre production-genel başarı önündeki ana risk alanları şunlardır.

1. Planner ve semantic resolution bazı durumlarda executable ama yanlış root entity veya yanlış filter ownership üretebiliyor.
2. Retrieval budget ve prompt daralması altında doğru tablolar/kolonlar kaybolabiliyor.
3. Validation sonrası güvenli repair yalnızca bazı hata sınıflarında var; production'da daha güçlü ama kontrollü bir retry katmanı gerekiyor.
4. Compiler ve executor tarafında Oracle-specific bind/date/type edge-case'leri halen kalite riskidir.
5. Timeout üreten geniş listing sorguları için degrade stratejisi tam sistematik değil.
6. Narration katmanı çoğu durumda sanitizer ile kurtarılıyor; bu production dayanıklılığı için yeterli değil.
7. Eval setleri benchmark odaklı; gerçek production soru dağılımını tam temsil etmiyor.
8. Rollout öncesi gating ve canary ölçütleri henüz ürün kalitesi seviyesinde tanımlı değil.

## 5. Roadmap Overview

Plan sekiz ana programdan oluşur. Bunlar sıra bağımlı ama kısmen paralel yürütülebilir.

1. Observability and failure taxonomy
2. Planner decomposition and stage contracts
3. Semantic and retrieval hardening
4. Validation and repair hardening
5. Compiler and execution reliability
6. Response quality and business interpretation
7. Evaluation system redesign
8. Release governance and production rollout

## 6. Program A: Observability And Failure Taxonomy

İlk hedef yeni sorularda sistemin neden başarısız olduğunu tartışmasız biçimde görebilmektir. Ölçemediğiniz şeyi production-grade hale getiremezsiniz.

### Objectives

- tüm başarısızlıkları tekil reason code'lara ayırmak
- her stage için trace completeness sağlamak
- benchmark ve production telemetry'yi aynı sınıflandırma diliyle konuşur hale getirmek

### Work items

1. Failure taxonomy'yi nihai hale getir:
   - `parse_recovery_failed`
   - `multiple_valid_entities`
   - `low_filter_coverage`
   - `unsupported_metric_shape`
   - `invalid_column`
   - `invalid_sort_column`
   - `oracle_date_type_error`
   - `timeout`
   - `policy_block_sensitive`
   - `empty_result_true_no_data`
   - `empty_result_semantic_mismatch`
   - `wrong_plan_root_entity`
   - `wrong_plan_join_path`
   - `wrong_plan_filter_semantics`
2. Planner, orchestrator, executor ve narrator trace alanlarını ortak sözleşmeye bağla.
3. `unclassified_failure_rate` için sıfıra yakın hedef koy; yeni hata tipi çıkarsa telemetry'de ayrı bucket oluşsun.
4. Eval script'lerinde summary yanında stage-distribution raporu üret.

### Relevant files

- `app/services/planner_service.py`
- `app/services/orchestrator.py`
- `app/providers/executor/oracle_executor.py`
- `app/services/narrator_service.py`
- `scripts/e2e_real_provider_eval.py`

### Exit criteria

- Her başarısız soru için tekil root-cause bucket atanabiliyor olmalı.
- Yeni run'larda `unclassified_failure_rate` ihmal edilebilir seviyeye inmeli.

## 7. Program B: Planner Decomposition And Stage Contracts

Production-genel kalite için planner tek parça davranıştan çıkmalı; aşamalar ayrı contract'larla yönetilmelidir.

### Objectives

- stage sınırlarını netleştirmek
- regression korkusu olmadan planner'ı geliştirilebilir hale getirmek
- prompt, normalization, repair ve clarification davranışını ayrı test edebilmek

### Work items

1. `PlannerService` içinde şu stage modelini kesinleştir:
   - request context build
   - query understanding
   - retrieval assembly
   - prompt assembly
   - plan generation
   - normalization
   - semantic resolution
   - clarification decision
   - repair/finalization
2. Mevcut private compatibility seam'leri koruyarak facade'ı incelt.
3. Typed stage payload'ları kullan; facade boundary'de legacy trace dict'e serialize et.
4. Her stage için input/output invariants yaz:
   - zorunlu intent davranışı
   - allowed missing fields
   - clarification cleanup garantisi
   - no raw SQL guarantee
5. Stage-level test harness oluştur; tam E2E olmadan parse/semantic/clarification regressions yakalansın.

### Relevant files

- `app/services/planner_service.py`
- `app/services/plan_generation_service.py`
- `app/services/plan_normalization_service.py`
- `app/services/plan_repair_service.py`
- `app/services/planning_context_service.py`
- `app/services/prompt_assembly_service.py`
- `app/services/planning_models.py`

### Exit criteria

- Planner stage testleri olmadan planner değişikliği merge edilememeli.
- Her stage'in ayrı trace ve error accounting'i olmalı.

## 8. Program C: Semantic And Retrieval Hardening

Production sorularında en kritik fark benchmark'tan sapmış ifade biçimleridir. Bu nedenle semantic layer ve retrieval gerçek başarının merkezidir.

### Objectives

- root entity seçiminde first-match bias'ı bitirmek
- synonym, glossary, TR/EN normalization ve filter ownership doğruluğunu yükseltmek
- retrieval'i prompt-budget daralmasında dahi güvenilir kılmak

### Work items

1. Semantic source-of-truth'u tamamen canonical repository üzerinden yönet.
2. Entity ranking için sinyal birleşimini zorunlu kıl:
   - lexical match
   - query understanding intent/domain
   - schema retrieval root
   - document/example agreement
   - filter ownership evidence
3. Override ancak güçlü kanıtta çalışsın; düşük güvenli semantic override'lar veto edilsin.
4. Turkish normalization, alias normalization ve compact naming mismatch hatalarını sistematik kapat.
5. Retrieval budget policy'yi iyileştir:
   - önce secondary table detail azalt
   - root table detail koru
   - docs/examples'i bütçe yüzünden körleştirmeden kademeli buda
6. Cross-domain noise detection ekle; retrieval context `noisy` ise planner'a düşük güven sinyali taşınsın.
7. Few-shot ve schema docs seçiminde domain-balanced örnekleme uygula; aynı pattern'e aşırı bağlanmayı azalt.

### Relevant files

- `app/services/semantic_planning.py`
- `app/services/semantic_resolution_service.py`
- `app/services/schema_retrieval_service.py`
- `app/services/document_retrieval_service.py`
- `app/services/query_understanding.py`
- `data/semantic/`

### Exit criteria

- Root entity accuracy ve join path accuracy production smoke setlerinde anlamlı biçimde artmalı.
- Semantic override harm rate sürekli düşmeli.

## 9. Program D: Validation And Repair Hardening

Validation sadece red mekanizması değil, güvenli toparlama katmanı da olmalıdır. Ancak bu katman kontrollü ve izlenebilir kalmalıdır.

### Objectives

- deterministik validasyon sınırlarını sertleştirmek
- güvenli repair kapsamını artırmak
- yanlış planı sessizce yürütmek yerine kontrollü düzeltmek veya net reddetmek

### Work items

1. Validation error sınıflarını normalize et.
2. Tek retry kuralını koruyarak safe repair repertuvarını genişlet:
   - alias-to-canonical
   - known synonym repair
   - invalid sort column map/drop
   - compact naming repair
   - table ownership correction
3. Repair only-on-high-confidence kuralını reason code ile trace et.
4. Repair sonrası revalidation zorunlu olsun; revalidate geçmeden compile yasak.
5. Filter-loss guard false positive'lerini düşür; fakat gerçek filter drop vakalarını kaçırma.
6. Restricted field policy ve role-based validation testlerini genişlet.

### Relevant files

- `app/services/validation_service.py`
- `app/services/validation_repair_service.py`
- `app/services/intent_guard.py`
- `app/services/registry_validator.py`

### Exit criteria

- `invalid_column` ve benzeri recoverable failure'ların büyük kısmı execution'a gitmeden toparlanmalı.
- Riskli repair denemeleri açık reason code ile loglanmalı.

## 10. Program E: Compiler And Execution Reliability

Production-grade başarı için plan doğru olsa bile Oracle-specific execution davranışı dayanıklı olmalıdır.

### Objectives

- type/date/bind edge-case'lerini sistematik kapatmak
- timeout üreten wide-listing sorgularında güvenli degrade stratejisi kurmak
- execution guard'ı statik değil, risk-aware hale getirmek

### Work items

1. Single-table ve multi-table compile yollarında bind coercion davranışını eşitle.
2. Date literal normalization ve Oracle DATE/TIMESTAMP bind dönüşümlerini ortak yardımcılarla merkezileştir.
3. `IS_NULL` ve `IS_NOT_NULL` gibi operatörlerde yanlış precheck bloklarını tamamen temizle.
4. Execution risk assessment'i şu boyutlarla genişlet:
   - wide listing
   - low selectivity filters
   - missing time window
   - large join fanout
   - sort-heavy query
5. Timeout-prone query'ler için degrade strateji tanımla:
   - projection shrink
   - safe default sort
   - default time window suggestion
   - aggregate-first fallback
   - top-N summary fallback
6. Explain-plan ve runtime fingerprint verisini eval trace'e taşı.
7. Oracle executor için timeout, cancellation ve partial trace korumasını standartlaştır.

### Relevant files

- `app/services/sql_compiler.py`
- `app/services/execution_risk.py`
- `app/providers/executor/oracle_executor.py`
- `app/services/orchestrator.py`

### Exit criteria

- Timeout ve date/type execution hataları istisna sınıfı haline inmeli.
- Execution guard kararı trace içinde açıklanabilir olmalı.

## 11. Program F: Clarification Quality And User Interaction Policy

Production'da az clarification üretmek tek başına hedef değildir. Doğru yerde clarification üretmek hedeftir.

### Objectives

- gereksiz clarification'ı azaltmak
- gerekli clarification'ı kaçırmamak
- benchmark uğruna policy veya ambiguity yönetimini bozmamak

### Work items

1. Clarification taxonomy'yi tek merkezden yönet.
2. Auto-recovery sadece güven eşiği yüksek, ambiguity düşük ve safe default projection mümkün olduğunda çalışsın.
3. Sensitive veya invalid istekleri `success` görünmesi için executable hale getirme; bunları policy-compliant handled bucket'ında ölç.
4. Clarification cevaplarını daha operasyonel yap:
   - hangi bilgi eksik
   - hangi seçim yapılmalı
   - mümkün default ne olurdu
5. Eval katmanında `clarification_quality_score` ekle.

### Relevant files

- `app/services/clarification_decision_service.py`
- `app/services/intent_guard.py`
- `app/services/planner_service.py`

### Exit criteria

- Clarification precision ve recall birlikte ölçülebilir hale gelmeli.
- Sensitive/invalid traffic, metric baskısı yüzünden yanlışlıkla executable path'e girmemeli.

## 12. Program G: Narration And Business Response Quality

Production-grade sistem sadece doğru SQL çalıştırmaz; doğru iş cevabı verir.

### Objectives

- sanitizer'a bağımlı anlatımı azaltmak
- empty result, aggregate, listing ve scalar çıktılar için tutarlı iş dili kullanmak
- final cevabın trace edilen filtrelerle uyumunu garanti etmek

### Work items

1. Narrator için structured output contract güçlendir.
2. Fallback-first response templates tasarla.
3. `empty_result` anlatımını sistem hatası gibi göstermeden filtre bağlamı ile açıkla.
4. Scalar, grouped aggregate, listing ve clarification için ayrı kalite ölçütleri tanımla.
5. Raw narrator leak rate ve sanitizer_saved_response_count'u production dashboard'a taşı.

### Relevant files

- `app/services/narrator_service.py`
- `app/services/orchestrator.py`

### Exit criteria

- Kullanıcıya giden cevabın trace ile çelişme oranı düşmeli.
- Sanitizer kurtarması istisna durum olmalı.

## 13. Program H: Evaluation System Redesign

Sadece 25 benchmark soru ile production kalitesi yönetilemez. Eval sistemi katmanlı hale gelmelidir.

### Evaluation layers

1. Unit and contract tests
   - parser/normalizer/repair/compiler/guard seviyesinde deterministic testler
2. Stage integration tests
   - retrieval + semantic + planner + validation sınır testleri
3. Golden scenario suites
   - HR, PO, AP, AR, inventory, timekeeping gibi domain bazlı senaryolar
4. Adversarial and ambiguity suites
   - synonym drift, mixed-language, incomplete filter, sensitive request, malformed phrasing
5. Replay suite
   - production trace'lerinden anonymized soru tekrar oynatma
6. Real-provider periodic eval
   - haftalık veya release öncesi tam akış ölçümü

### Work items

1. Eval dataset'lerini benchmark-only yapıdan çıkarıp domain-balanced hale getir.
2. `empty_result` için ayrı truth model tanımla:
   - true no-data
   - wrong filter semantics
   - wrong entity
3. Replay harness kur; production anonim soru/trace'leri golden sete dahil et.
4. Her release için gating threshold tanımla:
   - no safety regression
   - no material rise in wrong-plan rate
   - no material trace loss
5. Evaluation summary dosyalarında yalnızca outcome değil stage health de raporlansın.

### Relevant files

- `scripts/e2e_real_provider_eval.py`
- `data/eval_dataset_100.json`
- `results/`
- `data/question_trace_*.jsonl`

### Exit criteria

- Yeni release'ler production replay setini geçmeden ilerlememeli.
- Eval raporları benchmark skorundan öte gerçek kalite bileşenlerini göstermeli.

## 14. Program I: Release Governance And Rollout

Production-grade kalite yalnızca kod ile değil, güvenli rollout modeli ile sağlanır.

### Objectives

- riskli planner/semantic değişikliklerini kademeli yayımlamak
- regressions oluştuğunda hızlı rollback yapabilmek
- production öğrenimlerini kontrollü biçimde sisteme geri beslemek

### Work items

1. Planner/semantic/retrieval değişikliklerini feature flag veya config gate arkasına al.
2. Canary release modeli tanımla.
3. Rollback kriterlerini önceden belirle:
   - wrong_plan_rate artışı
   - policy_block precision düşüşü
   - timeout spike
   - trace loss
4. Release sonrası 24-48 saat yakın gözlem standardı oluştur.
5. Production incidents için root-cause template standardize et.

### Exit criteria

- Riskli değişiklikler tek seferde tam trafiğe açılmamalı.
- Rollback kararları metrik-temelli olmalı.

## 15. Suggested Delivery Order

Bu programların önerilen sırası aşağıdaki gibidir.

1. Program A: observability ve failure taxonomy
2. Program B: planner decomposition ve stage contracts
3. Program C: semantic/retrieval hardening
4. Program D: validation/repair hardening
5. Program E: compiler/execution reliability
6. Program F: clarification quality
7. Program G: narration quality
8. Program H: evaluation redesign
9. Program I: rollout governance

Bu sıra benchmark optimizasyonu için değil, production'da değişiklik yaparken sistemi körleştirmeden iyileştirmek için önerilmiştir.

## 16. Immediate Next Sprint

En yüksek kaldıraçlı ilk sprint aşağıdaki çıktı setini üretmelidir.

1. Planner stage flow haritası ve typed contract taslağı
2. Unified failure taxonomy ve trace schema
3. Production replay/eval tasarımı
4. Semantic override precision iyileştirme paketi
5. Execution risk degrade policy taslağı

## 17. Concrete Acceptance Criteria

Production-grade başarı için aşağıdaki kabul kriterleri önerilir.

1. Sistem yeni soru varyantlarında güvenli olmayan SQL veya policy ihlali üretmemeli.
2. `wrong_plan_rate` release bazında düşüş trendinde olmalı.
3. `handled_safely_rate` yüksek ve stabil olmalı.
4. Trace completeness kritik alanlarda neredeyse tam olmalı.
5. Production replay suite, benchmark suite'ten daha önemli release gate haline gelmeli.
6. Empty-result ve clarification davranışları iş açısından açıklanabilir olmalı.
7. Her büyük planner/semantic değişikliği domain-balanced eval ve replay ile doğrulanmalı.

## 18. What This Plan Explicitly Rejects

Bu plan aşağıdaki kısa yolları reddeder.

1. Sadece belirli benchmark sorularını geçirecek özel heuristics eklemek
2. Sensitive veya invalid soruları metrik için executable hale getirmek
3. Retrieval veya semantic hatalarını prompt büyüterek geçici biçimde gizlemek
4. Wrong-plan problemlerini narrator metni ile maskelemek
5. Empty-result ile sistem hatasını aynı şey gibi değerlendirmek

## 19. Repo Anchors

Bu plan şu mevcut modülleri ve sınırları esas alır.

- `app/services/planner_service.py`
- `app/services/planning_context_service.py`
- `app/services/plan_generation_service.py`
- `app/services/plan_normalization_service.py`
- `app/services/plan_repair_service.py`
- `app/services/semantic_resolution_service.py`
- `app/services/semantic_planning.py`
- `app/services/schema_retrieval_service.py`
- `app/services/document_retrieval_service.py`
- `app/services/validation_service.py`
- `app/services/validation_repair_service.py`
- `app/services/sql_compiler.py`
- `app/services/execution_risk.py`
- `app/services/orchestrator.py`
- `app/services/narrator_service.py`
- `scripts/e2e_real_provider_eval.py`

Bu planın doğal devamı, her program için ayrı implementation backlog ve test gate listesi çıkarmaktır.