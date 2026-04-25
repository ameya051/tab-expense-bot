"""Recurring expense scheduler — daily background task that auto-logs due expenses."""

import asyncio
import logging
from datetime import datetime, time as dt_time, timedelta, timezone

from app.database import AsyncSessionLocal
from app.services import expense_service, recurring_service
from app.reports.tables import _get_emoji

logger = logging.getLogger(__name__)

# IST timezone offset (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))


def _seconds_until_midnight_ist() -> float:
    """Calculate seconds until the next midnight IST."""
    now = datetime.now(IST)
    tomorrow = now.date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, dt_time.min, tzinfo=IST)
    delta = (midnight - now).total_seconds()
    return max(delta, 60)  # at least 60 seconds to avoid tight loops


async def recurring_expense_loop(bot_app) -> None:
    """Background task that runs daily, auto-logging due recurring expenses.

    This task:
    1. Sleeps until midnight IST
    2. Queries all active recurring expenses with next_run_date <= today
    3. Logs each one as a new expense
    4. Advances the next_run_date by one month
    5. Sends a Telegram notification to the user
    6. Handles missed days (catches up on all overdue entries)
    """
    logger.info("Recurring expense scheduler started")

    # On first startup, process any overdue entries immediately
    await _process_due_expenses(bot_app)

    while True:
        try:
            sleep_seconds = _seconds_until_midnight_ist()
            logger.info(
                "Scheduler sleeping for %.0f seconds (until midnight IST)",
                sleep_seconds,
            )
            await asyncio.sleep(sleep_seconds)
            await _process_due_expenses(bot_app)

        except asyncio.CancelledError:
            logger.info("Recurring expense scheduler shutting down")
            break
        except Exception:
            logger.exception("Error in recurring expense scheduler")
            # Sleep for 1 hour before retrying to avoid rapid error loops
            await asyncio.sleep(3600)


async def _process_due_expenses(bot_app) -> None:
    """Find and process all due recurring expenses."""
    try:
        async with AsyncSessionLocal() as db:
            due_entries = await recurring_service.get_due_expenses(db)

        if not due_entries:
            logger.info("No recurring expenses due today")
            return

        logger.info("Processing %d due recurring expense(s)", len(due_entries))

        for entry in due_entries:
            try:
                # Log the expense
                async with AsyncSessionLocal() as db:
                    expense = await expense_service.add_expense(
                        db,
                        user_id=entry.user_id,
                        amount=float(entry.amount),
                        category=entry.category,
                        expense_date=entry.next_run_date,
                        currency=entry.currency,
                        description=entry.description,
                    )

                # Advance to next month
                async with AsyncSessionLocal() as db:
                    await recurring_service.advance_next_run(db, entry.id)

                # Notify the user
                emoji = _get_emoji(entry.category)
                desc = f" — {entry.description}" if entry.description else ""
                message = (
                    f"🔄 Auto-logged: {entry.currency} {float(entry.amount):,.2f} "
                    f"for {emoji} {entry.category.title()}{desc}"
                )

                try:
                    await bot_app.bot.send_message(
                        chat_id=entry.user_id,
                        text=message,
                    )
                except Exception:
                    logger.warning(
                        "Could not notify user %d about recurring expense #%d",
                        entry.user_id,
                        entry.id,
                    )

                logger.info(
                    "Auto-logged recurring expense #%d for user %d: %s %s",
                    entry.id,
                    entry.user_id,
                    entry.amount,
                    entry.category,
                )

            except Exception:
                logger.exception(
                    "Failed to process recurring expense #%d", entry.id
                )

    except Exception:
        logger.exception("Failed to query due recurring expenses")
