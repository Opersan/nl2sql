# NL2SQL Assistant

Doğal dil sorgularını Oracle uyumlu SQL'e dönüştüren, FastAPI ile servis edilen ve LLM destekli planlama katmanı içeren bir NL2SQL asistanı.

Bu repo bugün deterministik validation/compiler/executor çekirdeğini, LLM planner ve narrator akışını, metadata ve document retrieval katmanlarını, semantic registry tabanlı plan normalizasyonunu ve kapsamlı evaluation script'lerini birlikte içerir.

## Öne Çıkan Özellikler

- Deterministik pipeline: validation -> SQL compilation -> execution
- LLM planner ve narrator desteği
- FastAPI endpoint'leri: `/health`, `/chat`, `/v1/chat/completions`
- Mock ve OpenAI-compatible LLM provider desteği
- Metadata ingestion ve schema retrieval desteği
- Document retrieval desteği (JSONL corpus + in-memory keyword retriever)
- Semantic registry tabanlı intent normalizasyonu ve join-path yönlendirme
- Query plan repair / expansion katmanı
- Execution risk ve intent guard kontrolleri
- Oracle executor adapter'ı + SQLGuard ile read-only enforcement
- Geniş test seti ve eval script'leri

## Mevcut Durum

| Alan | Durum | Not |
|---|---|---|
| Deterministik domain modelleri ve validation | ✅ | `QueryPlan`, catalog, execution modelleri aktif |
| Oracle uyumlu SQL compiler | ✅ | `ROWNUM` tabanlı pagination, named bind params |
| Mock executor | ✅ | Geliştirme ve test için aktif |
| FastAPI uygulaması | ✅ | `/health`, `/chat`, `/v1/chat/completions` |
| Session management | ✅ | In-memory |
| LLM planner / narrator | ✅ | Mock + OpenAI-compatible provider |
| Metadata ingestion | ✅ | JSON ve CSV yükleme |
| Schema retrieval | ✅ | In-memory keyword/alias scoring |
| Document retrieval | ✅ | JSONL loader + in-memory retrieval |
| Semantic normalization | ✅ | Registry tabanlı intent, root entity ve join path düzeltmeleri |
| Query plan repair | ✅ | Özellikle çoklu tablo planlarını toparlamak için |
| JOIN compilation | ✅ | Compiler ve testler çoklu tablo akışlarını kapsıyor |
| Oracle executor adapter | ✅ | Driver, credential ve pool init gerektirir |
| Streaming API | ❌ | Non-streaming only |

## Proje Yapısı

Ana klasörler ve önemli modüller:

```text
nl2sql/
├── app/
│   ├── api/
│   │   ├── main.py                 # FastAPI app factory + startup wiring
│   │   ├── deps.py                 # Provider/orchestrator dependency wiring
│   │   ├── routes_chat.py          # /chat ve /v1/chat/completions
│   │   ├── routes_health.py        # /health
│   │   └── schemas.py              # API request/response modelleri
│   ├── core/
│   │   ├── config.py               # Environment tabanlı ayarlar
│   │   ├── exceptions.py           # Domain ve execution exception'ları
│   │   ├── logging.py              # Logger setup
│   │   └── types.py                # Ortak tipler ve status alanları
│   ├── domain/
│   │   ├── catalog_models.py       # Table/column/catalog modelleri
│   │   ├── execution_models.py     # Validation/execution/compile sonuç modelleri
│   │   ├── models.py               # Session/chat modelleri
│   │   ├── query_plan.py           # Planner çıktısı ve query AST
│   │   └── semantic_models.py      # Semantic registry modelleri
│   ├── providers/
│   │   ├── catalog/                # Catalog provider'lar
│   │   ├── documents/              # JSONL document corpus loader/modelleri
│   │   ├── executor/               # Mock ve Oracle executor + SQLGuard
│   │   ├── llm/                    # Mock ve OpenAI-compatible LLM provider
│   │   ├── metadata/               # JSON/CSV metadata loader'lar
│   │   └── retrieval/              # Schema ve document retriever'lar
│   ├── services/
│   │   ├── catalog_service.py
│   │   ├── document_retrieval_service.py
│   │   ├── execution_risk.py
│   │   ├── intent_guard.py
│   │   ├── metadata_ingestion_service.py
│   │   ├── narrator_service.py
│   │   ├── orchestrator.py
│   │   ├── planner_service.py
│   │   ├── plan_normalizer.py
│   │   ├── query_plan_repair.py
│   │   ├── registry_validator.py
│   │   ├── schema_retrieval_service.py
│   │   ├── semantic_planning.py
│   │   ├── session_service.py
│   │   ├── sql_compiler.py
│   │   └── validation_service.py
│   ├── tests/                      # Pytest testleri
│   └── utils/                      # Türkçe normalize/casefold yardımcıları
├── data/                           # Eval datasetleri, semantic registry, örnek metadata
├── docs/                           # Runbook ve teknik dökümanlar
├── results/                        # Eval çıktıları
├── scripts/                        # Smoke/eval/doğrulama scriptleri
└── pyproject.toml
```

## Mimari Akış

```text
User message
  -> PlannerService
  -> semantic normalization / repair
  -> ValidationService
  -> SQLCompiler
  -> ExecutorProvider
  -> NarratorService
  -> API response
```

Detaylar:

- Planner doğrudan SQL üretmez; `QueryPlan` üretir.
- Validation katmanı tablo, kolon, aggregate, restricted alan ve çoklu tablo tutarlılığını kontrol eder.
- SQL compiler Oracle uyumlu SQL üretir ve `FETCH FIRST` yerine `ROWNUM` kullanır.
- Execution katmanında `SQLGuard` ile read-only kontrol yapılır.
- Semantic registry, planner çıktısını intent ve canonical join path bilgisiyle normalize eder.
- Document ve schema retrieval planner prompt'una ek bağlam sağlar.

## Kurulum

Gereksinimler:

- Python 3.11+
- İsteğe bağlı Oracle entegrasyonu için `oracledb`

### Geliştirme kurulumu

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Oracle adapter'ını da kurmak isterseniz:

```bash
pip install -e ".[dev,oracle]"
```

Not: `pytest` her ortamda PATH üzerinde olmayabilir. Bu durumda `.venv\Scripts\python -m pytest` kullanın.

## Uygulamayı Çalıştırma

```bash
uvicorn app.api.main:app --reload
```

Varsayılan endpoint'ler:

- `GET /health`
- `POST /chat`
- `POST /v1/chat/completions`

### Örnek `/chat` isteği

```json
{
  "session_id": "demo-session",
  "message": "Son 30 gündeki satınalma siparişlerini listele"
}
```

### Örnek `/v1/chat/completions` isteği

```json
{
  "model": "nl2sql",
  "messages": [
    {"role": "user", "content": "Aktif çalışanları listele"}
  ]
}
```

## Konfigürasyon

Uygulama ayarları `app/core/config.py` içindeki `Settings` sınıfından ve `.env` / environment variable'larından yüklenir.

Sık kullanılan ayarlar:

| Değişken | Açıklama | Varsayılan |
|---|---|---|
| `LLM_PROVIDER` | `mock` veya `openai_compatible` | `mock` |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint | `http://10.50.110.11:8100/v1` |
| `OPENAI_API_KEY` | API anahtarı | `EMPTY` |
| `OPENAI_MODEL` | Model adı | config içindeki varsayılan |
| `DEFAULT_ROW_LIMIT` | Varsayılan satır limiti | `100` |
| `MAX_ROW_LIMIT` | Üst satır limiti | `1000` |
| `ENABLE_SQL_IN_API_RESPONSE` | API response içinde SQL dön | `true` |
| `MAX_ROWS_PREVIEW` | Response row preview limiti | `20` |
| `ENABLE_METADATA_RETRIEVAL` | Metadata retrieval aç/kapat | `false` |
| `RETRIEVAL_TOP_K` | Schema retrieval üst limiti | `5` |
| `ENABLE_DOCUMENT_RETRIEVAL` | Document retrieval aç/kapat | `false` |
| `DOCUMENT_CORPUS_PATH` | JSONL document corpus yolu | boş |
| `DOCUMENT_LOADER_STRICT` | JSONL loader strict modu | `true` |
| `RETRIEVAL_TOP_K_EXAMPLES` | Example retrieval limiti | `2` |
| `PLANNER_PROMPT_MAX_CHARS` | Planner prompt bütçesi | `12000` |
| `METADATA_SOURCE_PATH` | JSON/CSV metadata kaynağı | boş |
| `METADATA_SOURCE_TYPE` | `json`, `csv`, `none` | `none` |
| `ENABLE_ORACLE_EXECUTOR` | Oracle executor seçimi | `false` |
| `ORACLE_DSN` | Oracle bağlantı bilgisi | boş |
| `ORACLE_USER` | Oracle kullanıcı adı | boş |
| `ORACLE_PASSWORD` | Oracle şifresi | boş |
| `ORACLE_TIMEOUT` | Oracle sorgu timeout | `30` |

### Gerçek LLM provider örneği

Windows PowerShell:

```powershell
$env:LLM_PROVIDER='openai_compatible'
$env:OPENAI_BASE_URL='http://10.50.110.11:8100/v1'
$env:OPENAI_MODEL='Qwen/Qwen3.5-122B-A10B-FP8'
```

## Metadata ve Retrieval

Repo iki ayrı retrieval hattı içerir.

### Schema retrieval

- Metadata JSON veya CSV'den yüklenir
- `CatalogSnapshot` oluşturulur
- In-memory retriever ile tablo/kolon/alias bazlı scoring yapılır
- Planner'a daraltılmış schema context verilir

### Document retrieval

- JSONL corpus içinden schema dokümanları ve example'lar yüklenir
- In-memory keyword retriever ile sorguya göre dokümanlar seçilir
- Planner prompt'una yardımcı bağlam eklenir

Semantic katman ayrıca `data/semantic_registry.json` üzerinden:

- root entity belirleme
- intent sınıflandırma
- canonical join path seçimi
- aggregation, group_by ve select defaults uygulama

işlevlerini üstlenir.

## Oracle Çalıştırma Notları

Repoda gerçek Oracle bağlantısı için `OracleExecutor` adapter'ı vardır; ancak bunu üretim kullanımında devreye almak için driver, credential ve pool initialization gerekir.

Önemli noktalar:

- `oracledb` paketi opsiyonel dependency'dir
- `OracleExecutor.init_pool()` çağrılmadan sorgu çalıştırılamaz
- Script'ler bu akışı explicit olarak yönetir
- API startup akışı içinde otomatik pool initialization şu anda yok
- SQL execution öncesinde `SQLGuard` ile sadece `SELECT` sorgularına izin verilir

Oracle doğrulama için hazır script:

```powershell
.\.venv\Scripts\python scripts\oracle_uat_verify.py
```

## Testler

Tüm testler `app/tests/` altında yer alır. Repo; API smoke, planner, compiler, retrieval, semantic planning, repair, narrator leakage, eval runner ve Oracle adapter senaryolarını kapsayan geniş bir test setine sahiptir.

Sık kullanılan komutlar:

```bash
pytest -v
pytest app/tests/test_api_smoke.py -v
pytest app/tests/test_sql_compiler.py -v
pytest app/tests/test_semantic_planning.py -v
pytest app/tests/test_query_plan_repair.py -v
pytest app/tests/test_document_retrieval_service.py -v
```

Eğer `pytest` komutu bulunamazsa:

```powershell
.\.venv\Scripts\python -m pytest -v
```

## Script'ler

Repo içinde birden fazla smoke ve evaluation script'i bulunur:

| Script | Amaç |
|---|---|
| `scripts/e2e_llm_flow.py` | LLM flow E2E çalıştırma |
| `scripts/e2e_real_provider_eval.py` | Gerçek provider ile çok sorulu reliability eval |
| `scripts/evaluate_hybrid_retrieval.py` | Hybrid retrieval değerlendirmesi |
| `scripts/oracle_smoke_plan.py` | Oracle plan smoke kontrolleri |
| `scripts/oracle_uat_verify.py` | UAT erişim ve veri doğrulaması |
| `scripts/po_e2e_smoke.py` | PO domain smoke akışı |
| `scripts/po_eval_runner.py` | PO odaklı eval çalıştırıcısı |
| `scripts/build_eval_dataset.py` | Eval dataset üretimi |
| `scripts/build_eval_dataset_200.py` | Genişletilmiş eval dataset üretimi |

### Gerçek provider eval örneği

```powershell
$env:LLM_PROVIDER='openai_compatible'
$env:OPENAI_BASE_URL='http://10.50.110.11:8100/v1'
$env:OPENAI_MODEL='Qwen/Qwen3.5-122B-A10B-FP8'
.\.venv\Scripts\python scripts\e2e_real_provider_eval.py --dataset data/eval_dataset_100.json --max-questions 24 --run-name round1_trace_50q_real --single-output-md data/eval_trace_round1_1q_real.md --concurrency 24
```

Script'in öne çıkan parametreleri:

- `--dataset`
- `--run-name`
- `--max-questions`
- `--batch-index`
- `--single-output-md`
- `--emit-extra-files`
- `--concurrency`
- `--max-retries`
- `--question-timeout`
- `--benchmark-concurrency`
- `--no-oracle`

Tipik çıktı artefaktları:

- `eval_summary_<run>.json`
- `question_trace_<run>.jsonl`
- `question_trace_<run>.md`
- `manual_review_<run>.json`

## Metadata Formatı

### JSON

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
        }
      ]
    }
  ],
  "relationships": []
}
```

### CSV dizin formatı

```text
metadata_dir/
├── _tables.csv
├── employee.csv
└── department.csv
```

`_tables.csv` örneği:

```csv
name,schema_name,description,aliases,primary_key
employee,HR,Ana personel tablosu,employees|personnel|calisan,reg_no
department,HR,Departman tablosu,dept|bolum,dept_id
```

Tablo dosyası örneği:

```csv
column_name,data_type,nullable,restricted,description,aliases
reg_no,INTEGER,false,false,Sicil numarası,sicil_no|sicil
full_name,VARCHAR2(200),false,false,Ad soyad,isim|ad_soyad
salary,NUMBER(10;2),true,true,Maaş bilgisi,maas|ucret
dept_id,INTEGER,false,false,Departman FK,departman_id
```

Notlar:

- `aliases` ve `primary_key` alanları `|` ile ayrılır
- `nullable` ve `restricted` için `true/false`, `1/0`, `yes/no`, `evet/hayir` benzeri değerler kabul edilir

## Bilinen Sınırlamalar

- API sadece non-streaming çalışır; SSE/WebSocket streaming yoktur
- Session state in-memory tutulur; restart sonrası korunmaz
- Schema ve document retrieval şu anda in-memory ve ağırlıklı olarak keyword tabanlıdır
- Oracle executor adapter mevcut olsa da API içinde otomatik pool init wiring henüz yapılmamıştır
- Gerçek Oracle senaryolarında environment, Oracle client ve driver kurulumu gerekir
- Sonuç kalitesi metadata, semantic registry ve document corpus kalitesine bağlıdır
- OpenAI-compatible endpoint minimaldir; tam OpenAI feature parity hedeflenmemiştir

## İlgili Dökümanlar

- `docs/hybrid_retrieval_test_plan.md`
- `docs/real_provider_eval_runbook.md`
