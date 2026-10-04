"""User service — upsert logic and preference management for Telegram users."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, null, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Budget, Expense, RecurringExpense, User
from app.services.currency_service import currency_service

logger = logging.getLogger(__name__)

# Smallest storable amount — conversions must never round a row down to 0
# (the amount > 0 CHECK constraints would reject it).
_MIN_AMOUNT = Decimal("0.01")


@dataclass(frozen=True)
class CurrencyChangeResult:
    """Outcome of switching a user's preferred currency."""

    old_currency: str
    new_currency: str
    expenses_converted: int = 0
    rates: dict[str, float] = field(default_factory=dict)  # source code -> rate to new


async def upsert_user(
    db: AsyncSession,
    telegram_id: int,
    first_name: str | None = None,
    username: str | None = None,
) -> None:
    """Create the user if they don't exist, or update their name/username if they do.

    Uses PostgreSQL's ON CONFLICT ... DO UPDATE for an atomic upsert. New users
    start with the configured DEFAULT_CURRENCY; existing users keep theirs.
    """
    stmt = pg_insert(User).values(
        telegram_id=telegram_id,
        first_name=first_name,
        username=username,
        preferred_currency=settings.default_currency,
        created_at=datetime.now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[User.telegram_id],
        set_={
            "first_name": stmt.excluded.first_name,
            "username": stmt.excluded.username,
        },
    )
    await db.execute(stmt)
    await db.commit()


async def get_user(db: AsyncSession, telegram_id: int) -> User | None:
    """Fetch a user by their Telegram ID, or None if not found."""
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def change_currency(
    db: AsyncSession, telegram_id: int, new_currency: str
) -> CurrencyChangeResult:
    """Switch the user's preferred currency and convert all their stored amounts.

    Expenses, recurring expenses and budgets are converted at today's rate in
    a single transaction. Expenses originally logged in the new currency get
    their exact original amount back. All rates are fetched before anything
    is written, so a failed FX lookup (CurrencyConversionError) leaves the
    user's data and currency untouched.
    """
    new = new_currency.upper()
    user = await get_user(db, telegram_id)
    old = user.preferred_currency if user else settings.default_currency
    if old == new:
        return CurrencyChangeResult(old_currency=old, new_currency=new)

    expense_currencies = set(
        (
            await db.execute(
                select(Expense.currency).where(Expense.user_id == telegram_id).distinct()
            )
        ).scalars()
    )
    recurring_currencies = set(
        (
            await db.execute(
                select(RecurringExpense.currency)
                .where(RecurringExpense.user_id == telegram_id)
                .distinct()
            )
        ).scalars()
    )
    budget_count = (
        await db.execute(
            select(func.count()).select_from(Budget).where(Budget.user_id == telegram_id)
        )
    ).scalar_one()
    # End the read transaction before making network calls.
    await db.rollback()

    # Budgets have no currency column — they are implicitly in the old currency.
    sources = expense_currencies | recurring_currencies
    if budget_count:
        sources.add(old)
    sources.discard(new)

    rates = {src: await currency_service.get_rate(src, new) for src in sorted(sources)}

    try:
        expenses_converted = 0
        for src, rate in rates.items():
            r = Decimal(str(rate))
            restore = Expense.original_currency == new
            converted = func.greatest(func.round(Expense.amount * r, 2), _MIN_AMOUNT)
            # Postgres evaluates every SET expression against the pre-update row.
            result = await db.execute(
                update(Expense)
                .where(Expense.user_id == telegram_id, Expense.currency == src)
                .values(
                    amount=case(
                        (restore, func.coalesce(Expense.original_amount, converted)),
                        else_=converted,
                    ),
                    original_amount=case(
                        (restore, null()),
                        else_=func.coalesce(Expense.original_amount, Expense.amount),
                    ),
                    original_currency=case(
                        (restore, null()),
                        else_=func.coalesce(Expense.original_currency, Expense.currency),
                    ),
                    currency=new,
                )
                .execution_options(synchronize_session=False)
            )
            expenses_converted += result.rowcount

            await db.execute(
                update(RecurringExpense)
                .where(RecurringExpense.user_id == telegram_id, RecurringExpense.currency == src)
                .values(
                    amount=func.greatest(
                        func.round(RecurringExpense.amount * r, 2), _MIN_AMOUNT
                    ),
                    currency=new,
                )
                .execution_options(synchronize_session=False)
            )

        if budget_count:
            r_old = Decimal(str(rates[old]))
            await db.execute(
                update(Budget)
                .where(Budget.user_id == telegram_id)
                .values(
                    monthly_limit=func.greatest(
                        func.round(Budget.monthly_limit * r_old, 2), _MIN_AMOUNT
                    )
                )
                .execution_options(synchronize_session=False)
            )

        await db.execute(
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(preferred_currency=new)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    logger.info(
        "User %d currency %s→%s, converted %d expenses",
        telegram_id,
        old,
        new,
        expenses_converted,
    )
    return CurrencyChangeResult(
        old_currency=old,
        new_currency=new,
        expenses_converted=expenses_converted,
        rates=rates,
    )


async def mark_onboarding_complete(db: AsyncSession, telegram_id: int) -> None:
    """Mark the user's onboarding as complete."""
    stmt = (
        update(User)
        .where(User.telegram_id == telegram_id)
        .values(onboarding_complete=True)
    )
    await db.execute(stmt)
    await db.commit()
