"""FastAPI application — entrypoint, lifespan, and webhook/polling dual mode."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from telegram import Update

from app.bot.setup import create_bot_application
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Build the bot application (handlers are registered inside)
bot_app = create_bot_application(settings.telegram_bot_token)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage bot lifecycle — startup and shutdown."""
    await bot_app.initialize()

    if settings.mode == "webhook":
        webhook_url = f"{settings.webhook_base_url}/webhook"
        await bot_app.bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook_secret,
        )
        logger.info("Webhook set: %s", webhook_url)
        await bot_app.start()
        yield
        await bot_app.stop()
    else:
        # Polling mode — start the updater in a background task
        await bot_app.start()
        logger.info("Bot running in POLLING mode")

        # Start polling in background (non-blocking)
        polling_task = asyncio.create_task(
            bot_app.updater.start_polling(drop_pending_updates=True)
        )

        yield

        # Shutdown polling
        if bot_app.updater.running:
            await bot_app.updater.stop()
        await bot_app.stop()

    await bot_app.shutdown()


app = FastAPI(title="Expense Tracker Bot", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Webhook endpoint (only used in webhook mode)
# ---------------------------------------------------------------------------

@app.post("/webhook")
async def telegram_webhook(request: Request) -> Response:
    """Receive Telegram updates via webhook."""
    # Verify the secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Simple health check endpoint for deployment platforms."""
    return {"status": "ok", "mode": settings.mode}
