from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8-sig",
        env_prefix="CLOUD_DB_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="Cloud DB API", min_length=1)
    service_name: str = Field(default="cloud-db-api", min_length=1)
    environment: Literal["development", "test", "production"] = "development"
    sync_interval_seconds: int = Field(default=180, ge=180, le=180)
    api_base_url: str = "http://127.0.0.1:8000"

    mongo_database: str = Field(default="cloud_db", min_length=1)
    mongo_1_uri: SecretStr = Field(min_length=1)
    mongo_2_uri: SecretStr = Field(min_length=1)
    mongo_3_uri: SecretStr = Field(min_length=1)
    postgres_direct_uri: SecretStr = Field(min_length=1)
    api_token: SecretStr = SecretStr("")
    bot_tokens: dict[str, SecretStr] = Field(default_factory=dict)
    database_timeout_ms: int = Field(default=5000, ge=500, le=30000)

    @model_validator(mode="after")
    def validate_tokens(self):
        if set(self.bot_tokens) - {f"bot-{i}" for i in range(1, 10)}:
            raise ValueError("Unknown bot token identifier")
        tokens = [value.get_secret_value() for value in self.bot_tokens.values()]
        if self.api_token.get_secret_value():
            tokens.append(self.api_token.get_secret_value())
        if len(tokens) != len(set(tokens)) or any(not value for value in tokens):
            raise ValueError("API and bot tokens must be nonempty and distinct")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
