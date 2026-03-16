"""Application configuration via pydantic-settings."""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings

# Centralised version – imported by schemas.py, main.py, etc.
APP_VERSION: str = "0.3.0"


class Settings(BaseSettings):
    """Central application settings loaded from environment / .env file."""

    app_name: str = "nl2sql-assistant"
    environment: str = "development"
    default_row_limit: int = 100
    max_row_limit: int = 1000

    # LLM provider
    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    openai_base_url: str = "http://10.50.110.11:8100/v1"
    openai_api_key: str = "EMPTY"
    openai_model: str = "Sehyo/Qwen3.5-122B-A10B-NVFP4"

    # API response
    enable_sql_in_api_response: bool = True
    max_rows_preview: int = 20

    # --- Sprint 3: Metadata ingestion ---
    metadata_source_path: str = ""
    metadata_source_type: Literal["json", "csv", "none"] = "none"

    # --- Sprint 3: Schema retrieval ---
    enable_metadata_retrieval: bool = False
    retrieval_top_k: int = 5

    # --- Sprint 3: Document / example retrieval ---
    enable_document_retrieval: bool = False
    document_corpus_path: str = ""
    document_loader_strict: bool = True
    retrieval_top_k_examples: int = 2

    # --- Sprint 3: Prompt budget ---
    # Maximum total characters for the assembled hybrid planner prompt.
    # Budget guard trims examples → docs → explanations → content before
    # hard-truncating as a last resort.
    planner_prompt_max_chars: int = 12_000

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Module-level singleton – import and use directly.
settings = Settings()
