"""Recurring expense service — CRUD and scheduling logic."""

import calendar
import logging
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RecurringExpense

logger = logging.getLogger(__name__)


def _next_month_date(from_date: date, day_of_month: int) -> date:
    """Calculate the next monthly run date, handling month-end edge cases.

    For example, if day_of_month=31 and next month has 28 days, it will
    use the 28th.
    """
    year = from_date.year
    month = from_date.month + 1
    if month > 12:
        month = 1
        year += 1

    # Clamp day to the last day of the target month
    max_day = calendar.monthrange(year, month)[1]
    actual_day = min(day_of_month, max_day)
    return date(year, month, actual_day)


async def create_recurring(
    db: AsyncSession,
    user_id: int,
    amount: float,
    currency: str,
    category: str,
    description: str | None,
    day_of_month: int,
) -> RecurringExpense:
    """Create a new recurring expense entry.

    Sets next_run_date to the same day next month.
    """
    today = date.today()
    next_run = _next_month_date(today, day_of_month)

    recurring = RecurringExpense(
        user_id=user_id,
        amount=amount,
        currency=currency,
        category=category.lower(),
        description=description,
        day_of_month=day_of_month,
        next_run_date=next_run,
        active=True,
    )
    db.add(recurring)
    await db.commit()
    await db.refresh(recurring)
    logger.info(
        "Created recurring expense: %s %s for user %d on day %d",
        amount,
        category,
        user_id,
        day_of_month,
    )
    return recurring


async def get_user_recurring(
    db: AsyncSession, user_id: int
) -> list[RecurringExpense]:
    """Return all active recurring expenses for a user."""
    stmt = (
        select(RecurringExpense)
        .where(
            RecurringExpense.user_id == user_id,
            RecurringExpense.active == True,
        )
        .order_by(RecurringExpense.next_run_date)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def cancel_recurring(
    db: AsyncSession, user_id: int, recurring_id: int
) -> RecurringExpense | None:
    """Soft-delete a recurring expense by setting active=False."""
    stmt = select(RecurringExpense).where(
        RecurringExpense.id == recurring_id,
        RecurringExpense.user_id == user_id,
        RecurringExpense.active == True,
    )
    result = await db.execute(stmt)
    recurring = result.scalar_one_or_none()

    if recurring is None:
        return None

    recurring.active = False
    await db.commit()
    await db.refresh(recurring)
    logger.info("Cancelled recurring expense #%d for user %d", recurring_id, user_id)
    return recurring


async def get_due_expenses(db: AsyncSession) -> list[RecurringExpense]:
    """Return all active recurring expenses that are due (next_run_date <= today)."""
    today = date.today()
    stmt = (
        select(RecurringExpense)
        .where(
            RecurringExpense.active == True,
            RecurringExpense.next_run_date <= today,
        )
        .order_by(RecurringExpense.next_run_date)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def advance_next_run(db: AsyncSession, recurring_id: int) -> None:
    """Advance the next_run_date by one month."""
    stmt = select(RecurringExpense).where(RecurringExpense.id == recurring_id)
    result = await db.execute(stmt)
    recurring = result.scalar_one_or_none()

    if recurring is None:
        return

    recurring.next_run_date = _next_month_date(
        recurring.next_run_date, recurring.day_of_month
    )
    await db.commit()
    logger.info(
        "Advanced recurring #%d next_run_date to %s",
        recurring_id,
        recurring.next_run_date,
    )
