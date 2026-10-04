"""Recurring expense scheduler — daily background task that auto-logs due expenses."""

import asyncio
import logging
from datetime import datetime, time as dt_time, timedelta

from sqlalchemy import text

from app.database import AsyncSessionLocal, engine
from app.reports.tables import _get_emoji
from app.services import recurring_service
from app.timeutils import local_tz, now as local_now, today as local_today

logger = logging.getLogger(__name__)

# Fixed advisory-lock key so every uvicorn worker contends on the same lock.
SCHEDULER_LOCK_KEY = 715517


def _seconds_until_local_midnight() -> float:
    """Calculate seconds until the next midnight in the configured timezone."""
    now = local_now()
    tomorrow = now.date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, dt_time.min, tzinfo=local_tz())
    delta = (midnight - now).total_seconds()
    return max(delta, 60)  # at least 60 seconds to avoid tight loops


async def recurring_expense_loop(bot_app) -> None:
    """Background task that runs daily, auto-logging due recurring expenses.

    This task:
    1. Sleeps until local midnight (TIMEZONE setting)
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
            sleep_seconds = _seconds_until_local_midnight()
            logger.info(
                "Scheduler sleeping for %.0f seconds (until local midnight)",
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
    """Find and process all due recurring expenses under a Postgres advisory lock.

    The advisory lock is acquired and released on a single dedicated
    connection so multiple uvicorn workers never double-log the same
    recurring expense. Each due entry is caught up one expense per missed
    period until next_run_date is in the future.
    """
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": SCHEDULER_LOCK_KEY},
            )
            acquired = result.scalar_one()

            if not acquired:
                logger.info("another worker holds the scheduler lock, skipping")
                return

            try:
                await _process_due_expenses_locked(bot_app)
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": SCHEDULER_LOCK_KEY},
                )

    except Exception:
        logger.exception("Failed to run due-expense processing")


async def _process_due_expenses_locked(bot_app) -> None:
    """Process due recurring expenses; caller must hold the advisory lock.

    Each period is logged and advanced in its own transaction
    (recurring_service.log_occurrence), so a crash part-way through a
    catch-up never re-logs periods that were already recorded.
    """
    today = local_today()

    async with AsyncSessionLocal() as db:
        due_entries = await recurring_service.get_due_expenses(db, today)

    if not due_entries:
        logger.info("No recurring expenses due today")
        return

    logger.info("Processing %d due recurring expense(s)", len(due_entries))

    for entry in due_entries:
        try:
            run_date = entry.next_run_date
            while run_date <= today:
                # Log one expense per missed period (catch-up)
                async with AsyncSessionLocal() as db:
                    updated = await recurring_service.log_occurrence(
                        db, entry.id, run_date
                    )
                if updated is None:
                    break  # cancelled, or already handled by another run

                await _notify_user(bot_app, updated)
                run_date = updated.next_run_date

        except Exception:
            logger.exception(
                "Failed to process recurring expense #%d", entry.id
            )


async def _notify_user(bot_app, entry) -> None:
    """Tell the user a recurring expense was auto-logged (best effort)."""
    emoji = _get_emoji(entry.category)
    desc = f" — {entry.description}" if entry.description else ""
    message = (
        f"🔄 Auto-logged: {entry.currency} {float(entry.amount):,.2f} "
        f"for {emoji} {entry.category.title()}{desc}"
    )
    try:
        await bot_app.bot.send_message(chat_id=entry.user_id, text=message)
    except Exception:  # noqa: BLE001 — notification failure must not stop the run
        logger.warning(
            "Could not notify user %d about recurring expense #%d",
            entry.user_id,
            entry.id,
        )
