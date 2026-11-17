"""Server-side configuration. Secrets are SecretStr and never logged."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Single source of truth for the default LLM; override with LLM_MODEL.
DEFAULT_LLM_MODEL = "claude-opus-5-5"


class ConfigError(RuntimeError):
    """Raised at startup for unsafe or incomplete configuration."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "production"] = "development"
    local_no_auth: bool = False

    database_url: str = "postgresql+asyncpg://wta:change-me-local-only@127.0.0.1:5432/wta"
    redis_url: str = "redis://127.0.0.1:6379/0"

    llm_provider: str = "anthropic"
    llm_model: str = DEFAULT_LLM_MODEL
    llm_effort: str = ""  # "" = model default; else low|medium|high|xhigh|max
    llm_refusal_fallback: bool = True  # Anthropic server-side refusal fallback
    llm_timeout_seconds: float = 60.0
    anthropic_api_key: SecretStr = SecretStr("")

    embedding_provider: str = "voyage"
    embedding_model: str = "voyage-4-lite"
    embedding_dimensions: int = 1024
    voyage_api_key: SecretStr = SecretStr("")

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    admin_cors_origins: str = ""
    public_max_body_bytes: int = 16384
    public_rate_limit_per_minute: int = 20
    public_max_concurrent_requests: int = 8
    conversation_retention_days: int = 30

    crawler_user_agent: str = "WebsiteToAgentsBot/0.1"
    crawler_rendering_enabled: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def admin_cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.admin_cors_origins.split(",") if o.strip()]

    @field_validator("llm_model", mode="before")
    @classmethod
    def _blank_model_means_default(cls, value: object) -> object:
        return DEFAULT_LLM_MODEL if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _fail_closed_in_production(self) -> Settings:
        if self.is_production and self.local_no_auth:
            raise ConfigError(
                "LOCAL_NO_AUTH=true is development-only and cannot be used with "
                "APP_ENV=production. Implement authentication before deploying."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
