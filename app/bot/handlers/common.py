"""Shared helpers used by bot handler modules."""

import logging

from telegram import Update

from app.bot.replies import reply_chunked as _reply_chunked
from app.config import settings
from app.database import AsyncSessionLocal
from app.formatting import (  # noqa: F401 — re-exported
    esc,
    format_amount,
    recurring_schedule_text,
)
from app.services import user_service

logger = logging.getLogger(__name__)


async def ensure_user(update: Update) -> None:
    """Upsert the user on every interaction."""
    tg_user = update.effective_user
    if tg_user is None:
        return
    async with AsyncSessionLocal() as db:
        await user_service.upsert_user(
            db,
            telegram_id=tg_user.id,
            first_name=tg_user.first_name,
            username=tg_user.username,
        )


async def get_preferred_currency(user_id: int) -> str:
    """Fetch the user's preferred currency, defaulting to configured default."""
    async with AsyncSessionLocal() as db:
        user = await user_service.get_user(db, user_id)
    return user.preferred_currency if user else settings.default_currency


async def reply_chunked(message, text: str, **kwargs) -> None:
    """Telegram-safe chunked reply wrapper."""
    await _reply_chunked(message, text, **kwargs)
