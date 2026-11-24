"""Application settings, loaded from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Runtime -------------------------------------------------------------------------
    env: str = Field("development", description="development | production")
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Database (Supabase Postgres + pgvector) ----------------------------------------
    database_url: str = "postgresql://postgres:postgres@localhost:54329/support"
    db_pool_min: int = 1
    db_pool_max: int = 10
    # Supabase's transaction pooler (port 6543) does not support prepared statements.
    db_statement_cache_size: int = 0
    auto_migrate: bool = True
    auto_seed: bool = True
    auto_ingest: bool = True

    # --- Gemini ---------------------------------------------------------------------------
    gemini_api_key: SecretStr = SecretStr("")
    agent_model: str = "gemini-3.8-flash"
    fallback_model: str = "gemini-3.5-flash-lite"
    verifier_model: str = "gemini-3.5-flash-lite"
    live_model: str = "gemini-3.8-live"
    live_voice: str = "Kore"
    embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 768
    # Gemini 3.x "thinking_level" for the agent model (minimal | low | medium | high). Empty = model default.
    agent_thinking_level: str = "low"
    agent_temperature: float = 0.2

    llm_first_token_timeout_s: float = 20.0
    llm_total_timeout_s: float = 60.0
    llm_max_retries: int = 2
    verifier_timeout_s: float = 12.0

    # --- Agent ----------------------------------------------------------------------------
    agent_max_steps: int = 8
    history_max_messages: int = 30
    tool_timeout_s: float = 8.0
    tool_max_retries: int = 1

    # --- RAG & guardrails -----------------------------------------------------------------
    rag_top_k: int = 5
    rag_candidate_k: int = 20
    # Cosine-similarity bands for the best vector hit (calibrate with `python -m app.cli calibrate`).
    rag_high_confidence: float = 0.75
    rag_low_confidence: float = 0.66
    groundedness_enabled: bool = True
    groundedness_min_score: float = 0.7
    max_verification_attempts: int = 3

    # --- Abuse protection -----------------------------------------------------------------
    rate_limit_chat_per_min: int = 20
    rate_limit_voice_per_min: int = 6
    max_concurrent_voice_per_ip: int = 2
    max_message_chars: int = 2000
    max_turns_per_session: int = 200
    voice_max_session_s: int = 600

    # --- Demo / resilience testing --------------------------------------------------------
    enable_chaos: bool = True
    # Exposes demo customer credentials + a data reset endpoint for reviewers. Disable for real deployments.
    demo_mode: bool = True

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
