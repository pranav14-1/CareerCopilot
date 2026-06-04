import os
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central application settings validated via Pydantic v2.
    Loads configurations from environment variables or .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # --- General ---
    APP_NAME: str = "CareerCopilot"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # --- Database & Cache ---
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/careercopilot",
        description="Asyncpg PostgreSQL connection URL"
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL"
    )

    # --- Integrations ---
    TELEGRAM_BOT_TOKEN: str = Field(
        default="placeholder_token",
        description="Telegram Bot API Token"
    )
    GEMINI_API_KEY: str = Field(
        default="placeholder_key",
        description="Google Gemini AI API Key"
    )

    # --- LangSmith Tracing ---
    LANGCHAIN_TRACING_V2: str = "false"
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"
    LANGCHAIN_API_KEY: Optional[str] = None
    LANGCHAIN_PROJECT: str = "career-copilot"

    # --- OpenTelemetry ---
    OTEL_SERVICE_NAME: str = "career-copilot"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure database URL is using asyncpg driver."""
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must start with 'postgresql+asyncpg://'")
        return v


# Instantiate settings singleton
settings = Settings()
