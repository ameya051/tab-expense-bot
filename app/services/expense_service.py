"""Expense service — CRUD and query operations for expenses."""

from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Expense


# ---------------------------------------------------------------------------
# Date range helper
# ---------------------------------------------------------------------------

def resolve_date_range(period: str) -> tuple[date, date]:
    """Convert a period string into a concrete (start, end) date range."""
    today = date.today()
    match period:
        case "today":
            return (today, today)
        case "this_week":
            start = today - timedelta(days=today.weekday())  # Monday
            return (start, today)
        case "this_month":
            return (today.replace(day=1), today)
        case "all_time":
            return (date(2000, 1, 1), today)
        case _:
            return (today.replace(day=1), today)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

async def add_expense(
    db: AsyncSession,
    user_id: int,
    amount: float,
    category: str,
    expense_date: date,
    currency: str = "INR",
    description: str | None = None,
    original_amount: float | None = None,
    original_currency: str | None = None,
) -> Expense:
    """Insert a new expense and return the created row."""
    expense = Expense(
        user_id=user_id,
        amount=amount,
        currency=currency,
        original_amount=original_amount,
        original_currency=original_currency,
        category=category.lower(),
        date=expense_date,
        description=description,
    )
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return expense


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

async def delete_last_expense(db: AsyncSession, user_id: int) -> Expense | None:
    """Delete the user's most recent expense. Returns the deleted row or None."""
    # Find the most recent expense
    stmt = (
        select(Expense)
        .where(Expense.user_id == user_id)
        .order_by(Expense.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    expense = result.scalar_one_or_none()

    if expense is None:
        return None

    await db.delete(expense)
    await db.commit()
    return expense


async def delete_expense_by_id(
    db: AsyncSession, user_id: int, expense_id: int
) -> Expense | None:
    """Delete a specific expense by ID (scoped to user). Returns the deleted row or None."""
    stmt = select(Expense).where(
        Expense.id == expense_id, Expense.user_id == user_id
    )
    result = await db.execute(stmt)
    expense = result.scalar_one_or_none()

    if expense is None:
        return None

    await db.delete(expense)
    await db.commit()
    return expense


# ---------------------------------------------------------------------------
# Query — aggregations
# ---------------------------------------------------------------------------

async def get_total(
    db: AsyncSession, user_id: int, start: date, end: date
) -> float:
    """Total spending in a date range."""
    stmt = select(func.coalesce(func.sum(Expense.amount), 0)).where(
        Expense.user_id == user_id,
        Expense.date >= start,
        Expense.date <= end,
    )
    result = await db.execute(stmt)
    return float(result.scalar_one())


async def get_by_category(
    db: AsyncSession, user_id: int, start: date, end: date
) -> list[dict]:
    """Spending breakdown grouped by category, sorted descending."""
    stmt = (
        select(Expense.category, func.sum(Expense.amount).label("total"))
        .where(
            Expense.user_id == user_id,
            Expense.date >= start,
            Expense.date <= end,
        )
        .group_by(Expense.category)
        .order_by(func.sum(Expense.amount).desc())
    )
    result = await db.execute(stmt)
    return [{"category": row.category, "total": float(row.total)} for row in result]


async def get_daily_trend(
    db: AsyncSession, user_id: int, start: date, end: date
) -> list[dict]:
    """Daily spending trend, ordered chronologically."""
    stmt = (
        select(Expense.date, func.sum(Expense.amount).label("total"))
        .where(
            Expense.user_id == user_id,
            Expense.date >= start,
            Expense.date <= end,
        )
        .group_by(Expense.date)
        .order_by(Expense.date)
    )
    result = await db.execute(stmt)
    return [{"date": row.date, "total": float(row.total)} for row in result]


async def get_category_total(
    db: AsyncSession,
    user_id: int,
    category: str,
    start: date,
    end: date,
) -> float:
    """Total spending for a specific category in a date range."""
    stmt = select(func.coalesce(func.sum(Expense.amount), 0)).where(
        Expense.user_id == user_id,
        Expense.category == category.lower(),
        Expense.date >= start,
        Expense.date <= end,
    )
    result = await db.execute(stmt)
    return float(result.scalar_one())


async def get_recent(
    db: AsyncSession, user_id: int, limit: int = 5
) -> list[Expense]:
    """Return the N most recent expenses."""
    stmt = (
        select(Expense)
        .where(Expense.user_id == user_id)
        .order_by(Expense.date.desc(), Expense.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
