"""Timezone-aware "now" / "today" helpers based on the configured TIMEZONE."""

from datetime import date, datetime
from functools import cache
from zoneinfo import ZoneInfo

from app.config import settings


@cache
def local_tz() -> ZoneInfo:
    """Return the bot's configured timezone."""
    return ZoneInfo(settings.timezone)


def now() -> datetime:
    """Current aware datetime in the configured timezone."""
    return datetime.now(local_tz())


def today() -> date:
    """Current date in the configured timezone."""
    return now().date()
