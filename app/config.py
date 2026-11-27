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

    # Свой сервер Telegram Bot API: файлы до 2 ГБ вместо 50 МБ
    telegram_api_url: str | None = None  # http://telegram-bot-api:8081

    # Скачивание видео
    downloads_dir: str = "/data/downloads"
    bgutil_url: str | None = "http://bgutil:4416"
    ytdlp_proxy: str | None = None  # прокси для скачивания (используется, если напрямую не вышло)
    default_quality: str = "720"
    default_audio: str = "orig"

    @property
    def max_upload_bytes(self) -> int:
        return (2000 if self.telegram_api_url else 50) * 1024 * 1024

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
