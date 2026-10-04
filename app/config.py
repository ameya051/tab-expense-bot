"""Application configuration loaded from environment variables."""

import logging
import re
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Telegram only accepts these characters in a webhook secret_token.
_WEBHOOK_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Central configuration for the expense tracker bot.

    All values are read from environment variables or a .env file.
    """

    telegram_bot_token: str
    openrouter_api_key: str
    database_url: str
    webhook_base_url: str = ""  # Required in webhook mode
    webhook_secret: str = ""  # Required in webhook mode
    openrouter_model: str = "openai/gpt-5.6-luna"
    whisper_model: str = "openai/whisper-large-v3"
    mode: Literal["polling", "webhook"] = "webhook"
    default_currency: str = "INR"
    timezone: str = "Asia/Kolkata"
    rate_limit_per_minute: int = 20
    frankfurter_api_url: str = "https://api.frankfurter.dev/v2"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def check_settings(self) -> Self:
        """Validate webhook settings and the configured timezone."""
        if self.mode == "webhook":
            if not self.webhook_base_url:
                raise ValueError("WEBHOOK_BASE_URL is required when MODE=webhook")
            if not self.webhook_secret:
                raise ValueError("WEBHOOK_SECRET is required when MODE=webhook")
            if not _WEBHOOK_SECRET_RE.fullmatch(self.webhook_secret):
                raise ValueError(
                    "WEBHOOK_SECRET may only contain A-Z, a-z, 0-9, _ and - "
                    "(1-256 characters)"
                )
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"TIMEZONE {self.timezone!r} is not a valid IANA timezone") from exc
        self.default_currency = self.default_currency.upper()
        # Frankfurter v1 is deprecated; upgrade stale overrides copied from older .env files.
        self.frankfurter_api_url = self.frankfurter_api_url.rstrip("/")
        if self.frankfurter_api_url.endswith("/v1"):
            self.frankfurter_api_url = self.frankfurter_api_url[:-3] + "/v2"
            logger.warning(
                "FRANKFURTER_API_URL points at the deprecated v1 API; using %s instead",
                self.frankfurter_api_url,
            )
        return self


settings = Settings()
