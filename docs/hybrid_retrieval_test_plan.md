# Hybrid Retrieval — Evaluation & Test Plan

> Version: 0.4.0-eval · Last updated: 2026-03-12

## 1. Amaç

Hybrid retrieval katmanının (structured catalog + document corpus) gerçek
veriye yakın koşullarda doğruluğunu, dayanıklılığını ve prompt kalitesini
ölçmek.

---

## 2. Test Veri Katmanları

### 2.1 Structured Metadata (`sample_metadata.json`)

`CatalogSnapshot` formatında 5 tablo:

| Tablo              | Açıklama                            | Yaklaşık Kolon |
| ------------------ | ----------------------------------- | -------------- |
| `per_employees`    | Ana personel kaydı                  | 10+            |
| `per_departments`  | Departman/birim tanımları           | 5              |
| `per_positions`    | Kadro/pozisyon tanımları            | 6              |
| `per_assignments`  | Çalışan-departman-pozisyon ataması  | 8              |
| `per_salaries`     | Maaş/ücret geçmişi (kısıtlı)      | 7              |

Her tablo:
- `name`, `description`, `aliases`, `primary_key`
- Her kolon: `name`, `data_type`, `nullable`, `restricted`, `description`, `aliases`
- Oracle R12 HR modülü isimlendirme geleneğine uygun (`per_` prefix)

### 2.2 Document Corpus (`sample_schema_documents.jsonl`)

JSONL formatında, her satır `SchemaDocument` veya `ExampleDocument`:

| doc_type       | Hedef içerik                                         | Adet |
| -------------- | ---------------------------------------------------- | ---- |
| `table`        | Tablo açıklamaları, iş kuralları                     | 5    |
| `column`       | Kritik kolon semantiği (quit_date, effective_date)    | 3-5  |
| `relationship` | FK ilişkileri, join yolları                           | 3    |
| `glossary`     | İş terimleri (aktif çalışan, kadro, atama)            | 3-5  |
| `example`      | NL → SQL örnekleri (gold-reference)                   | 10+  |

### 2.3 Evaluation Question Set (`sample_eval_questions.csv`)

Her satır:

```
question_id, question_tr, expected_table, expected_columns, expected_filter_hint, difficulty, tags
```

Zorluk dağılımı:
- **easy** (3-4): Tek tablo, basit filtre veya listeleme
- **medium** (3-4): Join, aggregation, group by
- **hard** (2-3): Multi-join, sub-select mantığı, belirsiz ifade

---

## 3. Veri Hazırlama Adımları

### 3.1 Metadata

1. `data/sample_metadata.json` dosyasını `CatalogSnapshot` şemasına göre
   doldur.
2. `InMemoryCatalogProvider` yerine bu dosyayı yükleyen bir test fixture
   oluştur.
3. Doğrulama: her tablo için `TableMetadata.model_validate(...)` başarılı
   olmalı.

### 3.2 Document Corpus

1. `data/sample_schema_documents.jsonl` dosyasını her satır geçerli JSON
   olacak şekilde oluştur.
2. `JSONLDocumentLoader(strict=True).load(...)` ile yükle — sıfır hata.
3. Doğrulama: `DocumentCorpus.schema_docs` ve `.examples` uzunlukları
   beklenenle eşleşmeli.

### 3.3 Evaluation Questions

1. `data/sample_eval_questions.csv` dosyasını doldur.
2. Her soru için beklenen `QueryPlan` çıktısını (en azından `table`,
   `select_columns`, varsa `filters`) belirle.
3. Sorular Türkçe olmalı; alias ve günlük dil varyasyonları içermeli.

---

## 4. Evaluation Kriterleri

### 4.1 Doğruluk (Accuracy)

| Metrik                  | Tanım                                                    | Hedef  |
| ----------------------- | -------------------------------------------------------- | ------ |
| **Table match**         | Doğru tablo seçildi mi?                                  | ≥ 90%  |
| **Column precision**    | Seçilen kolonların kaçı doğru?                           | ≥ 85%  |
| **Column recall**       | Beklenen kolonların kaçı seçildi?                        | ≥ 80%  |
| **Filter accuracy**     | Filtre yapısı (kolon + op) doğru mu?                     | ≥ 80%  |
| **Clarification rate**  | Belirsiz sorularda `needs_clarification=true` oranı      | ≥ 70%  |

### 4.2 Güvenlik (Safety)

| Kontrol                       | Beklenen                                      |
| ----------------------------- | --------------------------------------------- |
| Kısıtlı kolon erişimi         | ValidationService tarafından reddedilmeli      |
| SQL injection girişimi         | QueryPlan üretilmemeli veya reject edilmeli    |
| Olmayan tablo/kolon referansı  | Validation hatası dönmeli                      |

### 4.3 Prompt Kalitesi

| Kontrol                       | Beklenen                                      |
| ----------------------------- | --------------------------------------------- |
| Budget aşımı                  | Hiçbir prompt `max_prompt_chars` aşmamalı      |
| Kullanıcı sorusu bütünlüğü    | Soru asla kesilmemeli (`ValueError` garantisi) |
| SQL sızıntısı                 | Prompt içinde raw SQL olmamalı                 |
| Retrieval ilgililik           | Top-k döküman/example ilgili tabloya ait olmalı |

### 4.4 Performans (Opsiyonel)

| Metrik                  | Hedef                                          |
| ----------------------- | ---------------------------------------------- |
| Prompt assembly süresi  | < 50ms (senkron, in-memory retriever ile)       |
| End-to-end latency      | LLM bağımlı — ölçüm için log/trace eklenecek   |

---

## 5. Test Çalıştırma Planı

```
Adım 1 — Birim doğrulama
  pytest app/tests/ -q                    # mevcut 488 test geçmeli

Adım 2 — Şablon yükleme
  python -c "
  from app.domain.catalog_models import CatalogSnapshot
  import json, pathlib
  data = json.loads(pathlib.Path('data/sample_metadata.json').read_text())
  snap = CatalogSnapshot.model_validate(data)
  print(f'{len(snap.tables)} tables loaded')
  "

Adım 3 — Corpus yükleme
  python -c "
  import asyncio
  from app.providers.documents.jsonl_loader import JSONLDocumentLoader
  async def _load():
      loader = JSONLDocumentLoader(strict=True)
      corpus = await loader.load('data/sample_schema_documents.jsonl')
      print(f'{len(corpus.schema_docs)} docs, {len(corpus.examples)} examples')
  asyncio.run(_load())
  "

Adım 4 — Prompt oluşturma (smoke test)
  # Her eval sorusu için build_hybrid_planner_prompt çağır,
  # budget aşımı ve SQL sızıntısı olup olmadığını kontrol et.

Adım 5 — LLM evaluation (entegrasyon)
  # Gerçek LLM ile çalıştır, QueryPlan çıktısını eval questions'daki
  # beklenen değerlerle karşılaştır.
```

---

## 6. Sonraki Adımlar

1. `sample_metadata.json` → gerçek Oracle R12 HR şemasından doldur.
2. `sample_schema_documents.jsonl` → domain uzmanı ile zenginleştir.
3. `sample_eval_questions.csv` → 30+ soruya genişlet.
4. Otomatik evaluation script yaz (`scripts/evaluate.py`).
5. CI pipeline'a evaluation step ekle.
