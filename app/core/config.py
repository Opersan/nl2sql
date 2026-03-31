"""Application configuration via pydantic-settings."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings

from app.core.data_paths import (
    DEFAULT_CATALOG_INDEX_PATH,
    DEFAULT_CATALOG_SOURCE_PATH,
    DEFAULT_DOCUMENT_SOURCE_PATH,
    DEFAULT_EXAMPLE_INDEX_PATH,
    DEFAULT_SEMANTIC_INDEX_PATH,
)

# Centralised version – imported by schemas.py, main.py, etc.
APP_VERSION: str = "0.3.0"


class Settings(BaseSettings):
    """Central application settings loaded from environment / .env file."""

    app_name: str = "nl2sql-assistant"
    environment: str = "development"
    default_row_limit: int = 10000
    max_row_limit: int = 10000
    safe_mode_simple_listing_limit: int = 25

    # LLM provider
    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    openai_base_url: str = "http://10.50.110.11:8100/v1"
    openai_api_key: str = "EMPTY"
    openai_model: str = "Qwen/Qwen3.5-122B-A10B-FP8"

    # API response
    enable_sql_in_api_response: bool = True
    max_rows_preview: int = 20

    # --- Sprint 3: Metadata ingestion ---
    # Default: use the bundled sample JSON catalog so the API and eval share
    # a single file-backed source of truth out of the box.
    metadata_source_path: str = DEFAULT_CATALOG_SOURCE_PATH.as_posix()
    metadata_source_type: Literal["json", "csv", "none"] = "json"

    # --- Sprint 3: Schema retrieval ---
    enable_metadata_retrieval: bool = True
    retrieval_top_k: int = 7
    retrieval_strategy: Literal["keyword", "semantic", "hybrid"] = "keyword"
    retrieval_alpha: float = 0.5  # RRF alpha for hybrid retriever

    # --- Phase 2: Column pruning ---
    # When enabled, a lightweight LLM call strips irrelevant columns from
    # the retrieved table detail before injecting into the planner prompt.
    enable_column_prune: bool = True

    # --- Phase 3: Embedding settings ---
    # Set embedding_base_url + embedding_model to activate semantic / hybrid
    # retrieval.  Leave empty to fall back to keyword-only retrieval.
    embedding_base_url: str = ""
    embedding_api_key: str = "EMPTY"
    embedding_model: str = ""
    embedding_batch_size: int = 32
    catalog_index_cache_path: str = DEFAULT_CATALOG_INDEX_PATH.as_posix()
    semantic_index_cache_path: str = DEFAULT_SEMANTIC_INDEX_PATH.as_posix()
    example_index_cache_path: str = DEFAULT_EXAMPLE_INDEX_PATH.as_posix()

    # --- Sprint 3: Document / example retrieval ---
    # Default: wire the bundled JSONL corpus so the planner prompt is
    # enriched with schema docs and few-shot examples.
    enable_document_retrieval: bool = True
    document_corpus_path: str = DEFAULT_DOCUMENT_SOURCE_PATH.as_posix()
    document_loader_strict: bool = True
    retrieval_top_k_examples: int = 2

    # --- Sprint 3: Prompt budget ---
    # Maximum total characters for the assembled hybrid planner prompt.
    # Budget guard trims examples → docs → explanations → content before
    # hard-truncating as a last resort.
    planner_prompt_max_chars: int = 24_000

    # NOTE – Example corpus evolution roadmap:
    #   Today ExampleDocument.sql is retained for offline evaluation, gold
    #   reference tests and future migration tooling.  The planner prompt
    #   already omits raw SQL and shows only plan_hint / query_plan_shape
    #   labels.  A future sprint may replace ``sql`` with a structured
    #   ``plan_hint`` field.  All changes will be backward-compatible.

    # --- Sprint 3: Oracle executor ---
    # Credentials must be supplied via environment variables or .env file.
    # No defaults are provided here to prevent accidental credential exposure.
    enable_oracle_executor: bool = False
    oracle_dsn: str = ""
    oracle_user: str = ""
    oracle_password: str = ""
    oracle_timeout: int = 30

    # --- System context (date/time/fiscal) ---
    system_timezone: str = "Europe/Istanbul"
    fiscal_year_start: str = "2026-01-01"   # YYYY-MM-DD; year is advanced automatically
    default_time_window: str = "last_30_days"
    week_start: str = "monday"
    system_currency: str = "TRY"

    # --- LLM sampling parameters ---
    # Planner (generate_structured): precise/deterministic — low temperature for
    # stable JSON output, no presence penalty to avoid mangling JSON keys.
    planner_temperature: float = 0.6
    planner_top_p: float = 0.95
    planner_top_k: int = 20
    planner_min_p: float = 0.0
    planner_presence_penalty: float = 0.0
    planner_repetition_penalty: float = 1.0

    # Narrator / direct LLM (generate_text / generate_stream): fluent Turkish
    # prose with moderate diversity.
    narrator_temperature: float = 0.7
    narrator_top_p: float = 0.8
    narrator_top_k: int = 20
    narrator_min_p: float = 0.0
    narrator_presence_penalty: float = 1.5
    narrator_repetition_penalty: float = 1.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Module-level singleton – import and use directly.
settings = Settings()
