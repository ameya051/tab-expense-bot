"""Shared helpers used by bot handler modules."""

import html
import io
import logging
import time

from starlette.concurrency import run_in_threadpool
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.replies import reply_chunked as _reply_chunked
from app.config import settings
from app.database import AsyncSessionLocal
from app.services import user_service
from app.services.currency_service import get_currency_symbol

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


async def render_chart(render, *args) -> bytes:
    """Draw a chart in a worker thread (CPU-bound) and log its size and time."""
    started = time.perf_counter()
    chart = await run_in_threadpool(render, *args)
    logger.info(
        "%s -> <%d bytes> (%.0f ms)",
        render.__name__, len(chart), (time.perf_counter() - started) * 1000,
    )
    return chart


async def send_photo_bytes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    photo_bytes: bytes,
    caption: str | None = None,
) -> None:
    """Send a PNG image from bytes to the chat."""
    buf = io.BytesIO(photo_bytes)
    buf.name = "chart.png"
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=buf,
        caption=caption,
    )


def esc(text: str) -> str:
    """Escape user- or LLM-derived text for messages sent with parse_mode=HTML."""
    return html.escape(text, quote=False)


def format_amount(amount: float, currency: str) -> str:
    """Format an amount with its currency symbol."""
    symbol = get_currency_symbol(currency)
    return f"{symbol}{amount:,.2f}"


async def reply_chunked(message, text: str, **kwargs) -> None:
    """Telegram-safe chunked reply wrapper."""
    await _reply_chunked(message, text, **kwargs)
