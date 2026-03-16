# NL2SQL Assistant

**Sprint 3.0 – Metadata Ingestion, Schema Retrieval & Oracle Adapter Skeleton**

Doğal dil sorgularını Oracle uyumlu SQL'e dönüştüren asistan sistemi.
Sprint 1 deterministik çekirdek + Sprint 2 LLM planner/narrator + Sprint 2.1 hardening + Sprint 3 metadata pipeline & Oracle adapter.

---

## Proje Amacı

Doğal dil sorgularını Oracle uyumlu SQL'e dönüştüren bir asistan sistemi inşa etmek.

- **Sprint 1**: Deterministik çekirdek (validation, SQL compilation, mock execution)
- **Sprint 2**: LLM planner, narrator, FastAPI endpoints, session management
- **Sprint 2.1**: Hardening / contract cleanup
- **Sprint 3**: Metadata ingestion pipeline, schema retrieval, Oracle executor skeleton (bu sürüm)

## Sprint Durumu

| Bileşen | Sprint | Durum |
|---|---|---|
| Domain modelleri (QueryPlan, Catalog, Execution) | 1 | ✅ |
| Catalog service + in-memory provider | 1 | ✅ |
| Validation service | 1 | ✅ |
| SQL compiler (Oracle ROWNUM, bind params) | 1 | ✅ |
| Mock executor (in-memory dataset) | 1 | ✅ |
| Orchestrator (validation → compile → execute) | 1 | ✅ |
| Türkçe case-insensitive yardımcılar | 1 | ✅ |
| LLM planner (mock + OpenAI uyumlu) | 2 | ✅ |
| Narrator servisi (sonuç → Türkçe yanıt) | 2 | ✅ |
| FastAPI endpoints (/chat, /health, /v1/chat/completions) | 2 | ✅ |
| Session management (in-memory) | 2 | ✅ |
| Post-plan normalization & safety checks | 2.1 | ✅ |
| Retrieval-ready catalog context interface | 2.1 | ✅ |
| Response contract hardening (ChatStatus Literal) | 2.1 | ✅ |
| Oracle ROWNUM regression tests | 2.1 | ✅ |
| Clarification plan contract cleanup | 2.1 | ✅ |
| Negative path & edge case tests | 2.1 | ✅ |
| Oracle metadata-RAG retrieval | 3 | ✅ (iskelet) |
| vLLM / gerçek LLM entegrasyonu | 3 | ✅ (interface hazır) |
| Oracle DB executor (gerçek bağlantı) | 3 | ✅ (iskelet + SQLGuard) |
| Metadata ingestion pipeline (JSON/CSV) | 3 | ✅ |
| Schema retrieval (keyword-based) | 3 | ✅ |
| Çoklu tablo & JOIN desteği | 3 | 🔲 |

## Klasör Yapısı

```
nl2sql/
├── pyproject.toml
├── README.md
└── app/
    ├── core/
    │   ├── config.py          # Pydantic settings + APP_VERSION
    │   ├── exceptions.py      # Özel exception sınıfları (+ MetadataLoadError, RetrievalError)
    │   ├── logging.py         # Logger setup
    │   └── types.py           # ChatStatus, MessageRole, Row, ParamMap
    ├── domain/
    │   ├── catalog_models.py  # Table / Column metadata
    │   ├── execution_models.py # CompiledQuery, ExecutionResult, ValidationResult
    │   ├── query_plan.py      # QueryPlan, FilterSpec, AggregationSpec, …
    │   └── models.py          # Session, ChatResult + re-exports
    ├── services/
    │   ├── catalog_service.py # Catalog lookup + retrieval integration (Sprint 3)
    │   ├── metadata_ingestion_service.py # MetadataBundle → CatalogSnapshot transform
    │   ├── schema_retrieval_service.py   # High-level retrieval API
    │   ├── planner_service.py # NL → QueryPlan (+ post-plan normalization)
    │   ├── narrator_service.py# OrchestrationResult → Türkçe yanıt
    │   ├── validation_service.py # Deterministik plan validasyonu
    │   ├── sql_compiler.py    # Oracle SQL üretici (ROWNUM only)
    │   ├── session_service.py # In-memory session + clarification tracking
    │   └── orchestrator.py    # Pipeline zincirleri (Orchestrator + ChatOrchestrator)
    ├── providers/
    │   ├── catalog/
    │   │   ├── base.py        # Abstract CatalogProvider
    │   │   └── in_memory.py   # Demo employee tablosu
    │   ├── executor/
    │   │   ├── base.py        # Abstract ExecutorProvider
    │   │   ├── mock_executor.py # In-memory mock
    │   │   ├── oracle_executor.py # Oracle DB skeleton (SQLGuard entegre)
    │   │   └── sql_guard.py   # Read-only SQL enforcement
    │   ├── llm/
    │   │   ├── base.py        # Abstract LLMProvider
    │   │   ├── mock_llm.py    # Deterministic canned responses
    │   │   ├── openai_compatible.py # vLLM / OpenAI API client
    │   │   └── prompts.py     # Planner & narrator prompt templates
    │   ├── metadata/
    │   │   ├── base.py        # Abstract MetadataLoader
    │   │   ├── models.py      # RawTableDef, RawColumnDef, MetadataBundle
    │   │   └── file_loader.py # JSON & CSV file-based loaders
    │   └── retrieval/
    │       ├── base.py        # Abstract SchemaRetriever
    │       └── in_memory_retriever.py # Keyword/alias scoring retriever
    ├── api/
    │   ├── main.py            # FastAPI app factory
    │   ├── deps.py            # Dependency injection (retrieval + executor wiring)
    │   ├── routes_chat.py     # /chat, /v1/chat/completions
    │   ├── routes_health.py   # /health
    │   └── schemas.py         # Request/response models (Pydantic v2)
    ├── utils/
    │   ├── turkish.py         # Türkçe casefold, normalize
    │   └── text_normalization.py # Whitespace, identifier cleanup
    └── tests/
        ├── test_planner_models.py     # QueryPlan, FilterSpec, AggregationSpec
        ├── test_planner_service.py    # Planner + normalization + clarification
        ├── test_validation_service.py # Validation rules & contracts
        ├── test_sql_compiler.py       # SQL codegen + ROWNUM regression
        ├── test_mock_executor.py      # Mock executor contract fidelity
        ├── test_orchestrator_smoke.py # Sprint 1 pipeline E2E
        ├── test_orchestrator_llm_flow.py # Sprint 2 LLM flow E2E
        ├── test_narrator_service.py   # Narrator + no-fabrication contract
        ├── test_session_service.py    # Session + clarification tracking
        ├── test_api_smoke.py          # API + response contract + ROWNUM
        ├── test_metadata_ingestion.py # JSON/CSV loader + ingestion pipeline
        ├── test_schema_retrieval.py   # InMemoryRetriever + retrieval service
        ├── test_oracle_executor.py    # SQLGuard + Oracle skeleton
        └── test_config_sprint3.py     # Sprint 3 config defaults
```

## Kurulum

```bash
cd nl2sql
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Test Çalıştırma

```bash
# Tüm testler
pytest -v

# Tek modül
pytest app/tests/test_orchestrator_smoke.py -v

# Sadece compiler + ROWNUM regression
pytest app/tests/test_sql_compiler.py -v

# LLM flow testleri
pytest app/tests/test_orchestrator_llm_flow.py -v

# API smoke testleri
pytest app/tests/test_api_smoke.py -v
```

## Çalışma Akışı

```
Kullanıcı mesajı
    │
    ▼
PlannerService.plan(message)
    ├── CatalogService.get_relevant_context(message)  ← Sprint 3: RAG
    ├── build_planner_prompt(message, context)
    ├── LLMProvider.generate_structured(prompt, QueryPlan)
    └── _normalize_plan(plan)  ← limit clamp + clarification cleanup
    │
    ▼
QueryPlan
    │  clarification? → NarratorService → ChatResult(status="clarification")
    ▼
Orchestrator.run_plan(plan)
    │
    ├── ValidationService.validate(plan)
    │   ✓ tablo/kolon var mı, restricted kolon, aggregate consistency
    │
    ├── SQLCompiler.compile(plan, table)
    │   ✓ Oracle ROWNUM syntax, bind params (:p1, :p2, …)
    │
    └── ExecutorProvider.execute(compiled_query)
        ✓ Sprint 1-2: MockExecutor, Sprint 3: OracleExecutor
    │
    ▼
NarratorService.narrate_*(message, result)
    └── LLMProvider.generate_text(prompt)
    │
    ▼
ChatResult(status, answer, plan, sql, rows_preview)
```

## Mimari Sözleşmeler

### Oracle Legacy Uyumluluk
- SQL compiler **yalnızca** `ROWNUM` subquery wrapping kullanır
- `FETCH FIRST` / `OFFSET` syntax **kesinlikle** kullanılmaz
- Limit değeri her zaman bind parameter olarak iletilir (`:pN`)

### Planner Kuralları
- Planner **asla** SQL üretmez
- Planner restricted field'ları **engellemez** (validation'ın görevi)
- Clarification plan'lar query artifact'lardan temizlenir (normalization)
- Limit, `settings.max_row_limit` ile clamp edilir

### Narrator Kuralları
- Narrator veri **uydurmaz**
- Raw SQL, trace veya restricted değerler **gösterilmez**
- Boş sonuçlar açıkça belirtilir

### Validation Contract
- `resolved_table`: başarılı validation'da set edilir, başarısızda `None`
- `failed_phase`: hangi aşamada hata oluştu (`ErrorPhase` enum)
- Restricted column kontrolü tüm referans noktalarını kapsar (SELECT, WHERE, GROUP BY, ORDER BY, aggregate)

### API Response Statuses
- `success`: Sorgu başarılı, `rows_preview` ve `sql` mevcut
- `clarification`: Belirsiz sorgu, `plan.needs_clarification=True`
- `validation_error`: Plan doğrulaması başarısız, `error_code` mevcut
- `execution_error`: Compilation/execution hatası

## Sprint 3 Hazırlık Interface'leri

| Interface | Dosya | Sprint 3 Durumu |
|---|---|---|
| `CatalogService.get_relevant_context()` | catalog_service.py | ✅ Retrieval entegrasyonu (opsiyonel) |
| `MetadataLoader` (ABC) | metadata/base.py | ✅ JSON + CSV loaders |
| `SchemaRetriever` (ABC) | retrieval/base.py | ✅ InMemoryRetriever (keyword) |
| `SchemaRetrievalService` | schema_retrieval_service.py | ✅ High-level retrieval API |
| `MetadataIngestionService` | metadata_ingestion_service.py | ✅ RawTable → CatalogSnapshot |
| `OracleExecutor` | executor/oracle_executor.py | ✅ Skeleton + SQLGuard |
| `SQLGuard` | executor/sql_guard.py | ✅ Read-only enforcement |
| `CatalogProvider` (ABC) | catalog/base.py | InMemory → OracleCatalogProvider (Sprint 3+) |
| `ExecutorProvider` (ABC) | executor/base.py | Mock ↔ Oracle (config toggle) |
| `LLMProvider` (ABC) | llm/base.py | Mock → vLLM/OpenAI (config toggle) |

## Sprint 3 Akış Diyagramı

```
JSON / CSV dosya
    │
    ▼
MetadataLoader.load(path) → MetadataBundle
    │
    ▼
MetadataIngestionService.ingest()
    ├── transform(bundle)
    └── _map_column_type()  ← Oracle precision stripping
    │
    ▼
CatalogSnapshot  → InMemoryCatalogProvider (veya DB)
    │
    ▼
InMemoryRetriever.retrieve(query, top_k)
    ├── casefold_tr + token scoring
    └── tablo adı (+10), alias (+8), desc (+5), kolon (+3/+2/+1)
    │
    ▼
SchemaRetrievalService.retrieve_context()
    │
    ▼
CatalogService.get_relevant_context()  ← planner buradan alır
    │
    ▼
PlannerService.plan() → QueryPlan
    │
    ▼
SQLCompiler → SQLGuard → OracleExecutor (veya MockExecutor)
```

## Metadata Input Format Örnekleri

### JSON Format

```json
{
  "tables": [
    {
      "name": "XXBT_PDKS_PER_DETAILS_V",
      "schema_name": "HR",
      "description": "Ana personel tablosu",
      "aliases": ["employees", "personnel", "calisan"],
      "primary_key": ["reg_no"],
      "columns": [
        {
          "name": "reg_no",
          "data_type": "INTEGER",
          "nullable": false,
          "restricted": false,
          "description": "Sicil numarası",
          "aliases": ["sicil_no", "sicil"]
        },
        {
          "name": "salary",
          "data_type": "NUMBER(10,2)",
          "nullable": true,
          "restricted": true,
          "description": "Maaş bilgisi",
          "aliases": ["maas", "ucret"]
        }
      ]
    }
  ],
  "relationships": [
    {
      "from_table": "XXBT_PDKS_PER_DETAILS_V",
      "from_column": "dept_id",
      "to_table": "department",
      "to_column": "dept_id",
      "relationship_type": "many_to_one"
    }
  ],
  "source": "hr_export_v1",
  "version": "1.0"
}
```

### CSV Format (dizin yapısı)

```
metadata_dir/
├── _tables.csv
├── employee.csv
└── department.csv
```

**_tables.csv:**
```csv
name,schema_name,description,aliases,primary_key
employee,HR,Ana personel tablosu,employees|personnel|calisan,reg_no
department,HR,Departman tablosu,dept|bolum,dept_id
```

**employee.csv:**
```csv
column_name,data_type,nullable,restricted,description,aliases
reg_no,INTEGER,false,false,Sicil numarası,sicil_no|sicil
full_name,VARCHAR2(200),false,false,Ad soyad,isim|ad_soyad
salary,NUMBER(10;2),true,true,Maaş bilgisi,maas|ucret
dept_id,INTEGER,false,false,Departman FK,departman_id
```

> **Not:** `aliases` ve `primary_key` alanları pipe (`|`) ile ayrılır.
> `nullable` ve `restricted` değerleri: `true/false`, `1/0`, `yes/no`, `evet` kabul edilir.

## Sprint 3 Config Ayarları

```bash
# Metadata ingestion
METADATA_SOURCE_PATH=/path/to/metadata.json   # veya dizin (CSV için)
METADATA_SOURCE_TYPE=json                       # json | csv | none

# Schema retrieval
ENABLE_METADATA_RETRIEVAL=true                 # false: Sprint 1-2 full-dump fallback
RETRIEVAL_TOP_K=5                              # Retrieval'dan dönen max tablo sayısı

# Oracle executor
ENABLE_ORACLE_EXECUTOR=true                    # false: MockExecutor kullanılır
ORACLE_DSN=host:port/service_name
ORACLE_USER=readonly_user
ORACLE_PASSWORD=secret
ORACLE_TIMEOUT=30                              # Sorgu timeout (saniye)
```

## Bilinen Limitasyonlar

- Gerçek DB bağlantısı yok (OracleExecutor iskelet — `oracledb` bağlantısı henüz yok)
- Tek tablo desteği (employee); çoklu tablo metadata yüklenebilir ama JOIN üretilmez
- JOIN / subquery desteği yok (Sprint 3+ planlı)
- Streaming response desteği yok
- Session state in-memory (restart'ta kaybolur)
- Mock LIKE: sadeleştirilmiş substring match (anchored pattern yok)
- Schema retrieval Phase 1: keyword matching (BM25/vector retrieval Sprint 3+)
- Metadata ilişkileri (relationships) yüklenebilir ama henüz JOIN planlama'da kullanılmıyor
- SQLGuard "DELETED" kolon adına izin verir ama bazı edge-case kolon isimlerinde false positive olabilir

## Gerçek Entegrasyon İçin Gereken Girdiler

| Girdi | Açıklama |
|---|---|
| Oracle DSN / connection string | `host:port/service_name` formatında |
| Read-only Oracle kullanıcı | Sadece SELECT yetkisi olan kullanıcı |
| Oracle şifre | `.env` dosyasında `ORACLE_PASSWORD` |
| Metadata JSON/CSV dosyaları | Yukarıdaki formatta tablo/kolon tanımları |
| vLLM endpoint URL | OpenAI-uyumlu API (ör. `http://vllm-host:8000/v1`) |
| vLLM model name | `openai_model` config'inde |
| `oracledb` Python paketi | `pip install oracledb` — pyproject.toml'a eklenecek |
