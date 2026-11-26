from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProviderName = Literal["anthropic", "claude_cli"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    bot_token: str
    owner_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)  # безлимит + личный LLM-провайдер
    access_mode: Literal["public", "allowlist"] = "public"
    allowlist_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    daily_limit: int = 10  # запросов в сутки на обычного пользователя

    # Infra
    database_url: str = "postgresql+asyncpg://bot:bot@postgres:5432/bot"
    redis_url: str = "redis://redis:6379/0"

    # LLM: владелец и все остальные могут ходить через разных провайдеров
    llm_provider_owner: LLMProviderName = "claude_cli"
    llm_provider_public: LLMProviderName = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high"] = "medium"
    claude_cli_model: str = "opus"
    claude_cli_timeout: int = 600

    # YouTube
    transcript_languages: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["ru", "en"])
    webshare_proxy_username: str | None = None
    webshare_proxy_password: str | None = None
    generic_proxy_url: str | None = None  # http://user:pass@host:port

    @field_validator("owner_ids", "allowlist_ids", "transcript_languages", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
