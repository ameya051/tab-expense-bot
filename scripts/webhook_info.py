"""CLI helper to inspect the current Telegram webhook configuration.

Usage:
    python scripts/webhook_info.py

The script reads environment variables from .env (via app.config) and prints
Telegram's current webhook info for the configured bot.
"""

import asyncio
import logging

from telegram import Bot

from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Fetch and print webhook info from Telegram."""
    async with Bot(settings.telegram_bot_token) as bot:
        info = await bot.get_webhook_info()

    print("Telegram webhook info")
    print("-" * 40)
    print(f"URL:              {info.url or '(not set)'}")
    print(f"Has custom cert:  {info.has_custom_certificate}")
    print(f"Pending updates:  {info.pending_update_count}")
    print(f"Max connections:  {info.max_connections}")
    if info.ip_address:
        print(f"IP address:       {info.ip_address}")
    if info.last_error_date:
        print(f"Last error date:  {info.last_error_date}")
    if info.last_error_message:
        print(f"Last error:       {info.last_error_message}")

    expected_url = f"{settings.webhook_base_url.rstrip('/')}/webhook"
    if settings.mode == "webhook" and info.url != expected_url:
        print()
        logger.warning(
            "Configured mode is webhook, but Telegram reports URL '%s' (expected '%s')",
            info.url,
            expected_url,
        )


if __name__ == "__main__":
    asyncio.run(main())
