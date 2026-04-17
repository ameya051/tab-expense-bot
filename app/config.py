"""Application configuration loaded from environment variables."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the expense tracker bot.

    All values are read from environment variables or a .env file.
    """

    telegram_bot_token: str
    groq_api_key: str
    database_url: str
    webhook_base_url: str = ""  # Only required in webhook mode
    webhook_secret: str = "default-secret"
    groq_model: str = "llama-3.3-70b-versatile"
    mode: Literal["polling", "webhook"] = "polling"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
