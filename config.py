from __future__ import annotations

import os
from typing import List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_token: str = Field(..., description="Telegram bot token")
    openrouter_api_key: str = Field(..., description="OpenRouter API key")
    openrouter_model: str = Field("deepseek/deepseek-v4-pro", description="Model name on OpenRouter")
    openrouter_base_url: str = Field("https://openrouter.ai/api/v1", description="OpenRouter API base URL")

    open_congress_api_base: str = Field("https://open-congress-api.bettergov.ph")
    database_url: str = Field("sqlite+aiosqlite:///./spending.db")

    use_mock_data: bool = Field(False)
    data_refresh_interval_hours: int = Field(24)
    anomaly_contamination: float = Field(0.05)
    zscore_threshold: float = Field(3.0)

    admin_telegram_user_ids: List[int] = Field(default_factory=list, json_schema_extra={"env": "ADMIN_TELEGRAM_USER_IDS"})

    @field_validator("admin_telegram_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, v):
        if v is None or v == "" or v == "[]":
            return []
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                import json as _json
                try:
                    parsed = _json.loads(stripped)
                    return [int(x) for x in parsed]
                except Exception:
                    return []
            return [int(x.strip()) for x in stripped.split(",") if x.strip()]
        if isinstance(v, list):
            return v
        return []


settings = Settings()
