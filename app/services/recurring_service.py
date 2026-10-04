"""Recurring expense service — CRUD and scheduling logic."""

import calendar
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Expense, RecurringExpense
from app.timeutils import today as local_today

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


def compute_next_run_date(from_date: date, day_of_month: int) -> date:
    """Public wrapper around the month-advancement logic for scheduler use."""
    return _next_month_date(from_date, day_of_month)


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
    next_run = _next_month_date(local_today(), day_of_month)

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
            RecurringExpense.active.is_(True),
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
        RecurringExpense.active.is_(True),
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


async def get_due_expenses(db: AsyncSession, today: date) -> list[RecurringExpense]:
    """Return all active recurring expenses that are due (next_run_date <= today)."""
    stmt = (
        select(RecurringExpense)
        .where(
            RecurringExpense.active.is_(True),
            RecurringExpense.next_run_date <= today,
        )
        .order_by(RecurringExpense.next_run_date)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def log_occurrence(
    db: AsyncSession, recurring_id: int, run_date: date
) -> RecurringExpense | None:
    """Log one period of a recurring expense and advance it, atomically.

    The expense insert and the next_run_date update share one transaction,
    so a crash can never log a period without advancing (or vice versa).
    Returns the updated entry, or None if the entry is inactive or run_date
    was already handled by another run.
    """
    stmt = (
        select(RecurringExpense)
        .where(RecurringExpense.id == recurring_id)
        .with_for_update()
    )
    recurring = (await db.execute(stmt)).scalar_one_or_none()

    if (
        recurring is None
        or not recurring.active
        or recurring.next_run_date != run_date
    ):
        await db.rollback()
        return None

    db.add(
        Expense(
            user_id=recurring.user_id,
            amount=recurring.amount,
            currency=recurring.currency,
            category=recurring.category,
            date=run_date,
            description=recurring.description,
        )
    )
    recurring.next_run_date = _next_month_date(run_date, recurring.day_of_month)
    await db.commit()
    logger.info(
        "Logged recurring #%d for %s, next_run_date now %s",
        recurring_id,
        run_date,
        recurring.next_run_date,
    )
    return recurring
