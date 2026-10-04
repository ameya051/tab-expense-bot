"""FastAPI application — entrypoint, lifespan, and webhook/polling dual mode."""

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from telegram import Update
from telegram.error import TelegramError

from app.bot.setup import create_bot_application
from app.config import settings
from app.database import ping_db
from app.scheduler import recurring_expense_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook"

# Build the bot application (handlers are registered inside)
bot_app = create_bot_application(settings.telegram_bot_token)


def _webhook_url() -> str:
    """Return the fully-qualified webhook URL Telegram should call."""
    base = settings.webhook_base_url.rstrip("/")
    return f"{base}{WEBHOOK_PATH}"


async def _setup_webhook() -> None:
    """Register the webhook with Telegram."""
    url = _webhook_url()
    try:
        await bot_app.bot.set_webhook(
            url=url,
            secret_token=settings.webhook_secret,
        )
        logger.info("Webhook set: %s", url)
    except TelegramError:
        logger.exception("Failed to set webhook: %s", url)
        raise


async def _start_polling() -> asyncio.Task:
    """Start the updater in a background polling task."""
    logger.info("Bot running in POLLING mode")
    return asyncio.create_task(
        bot_app.updater.start_polling(drop_pending_updates=True)
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage bot lifecycle — startup and shutdown."""
    await bot_app.initialize()

    # Start the recurring expense scheduler as a background task
    scheduler_task = asyncio.create_task(recurring_expense_loop(bot_app))
    logger.info("Recurring expense scheduler task created")

    polling_task: asyncio.Task | None = None
    try:
        if settings.mode == "webhook":
            await _setup_webhook()
            await bot_app.start()
        else:
            await bot_app.start()
            polling_task = await _start_polling()

        yield

    finally:
        # Stop polling before shutting down the application
        if polling_task is not None and bot_app.updater.running:
            await bot_app.updater.stop()
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass

        # The webhook is deliberately NOT deleted here: during a rolling deploy
        # (or with several workers) the old process would remove the webhook
        # the new one just registered, and the bot would go silent.

        # stop() raises if start() never ran (e.g. set_webhook failed), which
        # would mask the original startup error.
        if bot_app.running:
            await bot_app.stop()

        # Cancel the scheduler
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass

        await bot_app.shutdown()


app = FastAPI(title="Expense Tracker Bot", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Webhook endpoint (only used in webhook mode)
# ---------------------------------------------------------------------------

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> Response:
    """Receive Telegram updates via webhook."""
    # Verify the secret token in constant time
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(
        secret.encode(), settings.webhook_secret.encode()
    ):
        logger.warning("Webhook request rejected: invalid secret token")
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
    except Exception:  # noqa: BLE001 — malformed payload is a client error
        logger.warning("Webhook request rejected: malformed update payload")
        raise HTTPException(status_code=400, detail="Bad Request")

    if update is None:
        raise HTTPException(status_code=400, detail="Bad Request")

    logger.debug(
        "Queueing update_id=%s from chat_id=%s",
        update.update_id,
        update.effective_chat.id if update.effective_chat else None,
    )

    # Hand the update to PTB's queue and acknowledge immediately: processing
    # inline (AI calls with retries) can outlast Telegram's webhook timeout,
    # making Telegram redeliver the update and log the expense twice.
    await bot_app.update_queue.put(update)
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check endpoint for deployment platforms.

    HTTP status stays 200 even when the DB is down so liveness probes
    do not restart the app in a loop; the payload reports db state.
    """
    payload = {"status": "ok", "mode": settings.mode, "db": "ok" if await ping_db() else "error"}
    if settings.mode == "webhook":
        payload["webhook_url"] = _webhook_url()
    return payload
