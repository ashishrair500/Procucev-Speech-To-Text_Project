"""Application configuration, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Real-time Speech-to-Text (Hindi + English)"
    environment: str = "development"

    # --- Gemini ---------------------------------------------------------
    gemini_api_key: str = ""
    # Purpose-built Live transcription model (listens only, never chats back).
    gemini_live_model: str = "gemini-3.5-transcribe-live"
    # Comma-separated fallbacks, tried in order if the primary fails to connect.
    gemini_fallback_models: str = "gemini-3.1-flash-live-preview,gemini-3.8-live"

    # --- Database -------------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/transcriptions"
    )

    # --- HTTP / WebSocket limits ---------------------------------------
    allowed_origins: str = "*"
    # Hard cap on a single recording, protects the free Gemini quota.
    max_session_seconds: int = 900
    max_concurrent_sessions: int = 4
    max_transcript_chars: int = 50_000
    ws_ping_interval_seconds: int = 20

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.gemini_fallback_models.split(",") if m.strip()]

    @property
    def models_in_order(self) -> list[str]:
        """Primary model first, then fallbacks (de-duplicated)."""
        seen: list[str] = []
        for model in [self.gemini_live_model, *self.fallback_models]:
            if model and model not in seen:
                seen.append(model)
        return seen


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
