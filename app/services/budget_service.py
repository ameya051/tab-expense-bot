"""Budget service — set, query, and check user-level and category budgets."""

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Budget, Expense

logger = logging.getLogger(__name__)


async def set_budget(
    db: AsyncSession, user_id: int, monthly_limit: float, category: str | None = None
) -> Budget:
    """Create or update a monthly budget - either user total (category=None) or category-specific.

    Uses PostgreSQL ON CONFLICT for an atomic upsert.
    """
    cat_lower = category.lower() if category else None
    stmt = pg_insert(Budget).values(
        user_id=user_id,
        category=cat_lower,
        monthly_limit=monthly_limit,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "category"],
        set_={"monthly_limit": stmt.excluded.monthly_limit},
    )
    await db.execute(stmt)
    await db.commit()

    # Fetch the upserted budget to return
    result = await db.execute(
        select(Budget).where(
            Budget.user_id == user_id,
            Budget.category == cat_lower,
        )
    )
    budget = result.scalar_one()
    cat_label = category or "TOTAL"
    logger.info(
        "Budget set: user %d, %s = %.2f", user_id, cat_label, monthly_limit
    )
    return budget


async def get_user_budget(db: AsyncSession, user_id: int) -> Budget | None:
    """Get the user-level total budget (category is NULL)."""
    stmt = select(Budget).where(
        Budget.user_id == user_id,
        Budget.category.is_(None),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_budgets(db: AsyncSession, user_id: int) -> list[Budget]:
    """Return all category-level budgets for a user, sorted by category."""
    stmt = (
        select(Budget)
        .where(Budget.user_id == user_id, Budget.category.isnot(None))
        .order_by(Budget.category)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_budget(
    db: AsyncSession, user_id: int, category: str | None = None
) -> Budget | None:
    """Delete a budget - either user-level total (category=None) or category-specific."""
    cat_lower = category.lower() if category else None
    stmt = select(Budget).where(
        Budget.user_id == user_id,
        Budget.category == cat_lower,
    )
    result = await db.execute(stmt)
    budget = result.scalar_one_or_none()

    if budget is None:
        return None

    await db.delete(budget)
    await db.commit()
    return budget


async def check_budget(
    db: AsyncSession, user_id: int, category: str
) -> dict | None:
    """Check current spending vs. budget for a category this month.

    Returns:
        Dict with keys: budget, spent, percent, alert_level.
        None if no budget is set for this category.
    """
    # Get the budget
    budget_stmt = select(Budget).where(
        Budget.user_id == user_id,
        Budget.category == category.lower(),
    )
    result = await db.execute(budget_stmt)
    budget = result.scalar_one_or_none()

    if budget is None:
        return None

    # Get current month spending for this category
    today = date.today()
    month_start = today.replace(day=1)

    spent_stmt = select(
        func.coalesce(func.sum(Expense.amount), 0)
    ).where(
        Expense.user_id == user_id,
        Expense.category == category.lower(),
        Expense.date >= month_start,
        Expense.date <= today,
    )
    spent_result = await db.execute(spent_stmt)
    spent = float(spent_result.scalar_one())

    limit = float(budget.monthly_limit)
    percent = round((spent / limit) * 100) if limit > 0 else 0

    # Determine alert level
    if percent >= 100:
        alert_level = "danger"
    elif percent >= 80:
        alert_level = "warning"
    elif percent >= 50:
        alert_level = "info"
    else:
        alert_level = None

    return {
        "budget": limit,
        "spent": spent,
        "percent": percent,
        "alert_level": alert_level,
    }


async def check_user_budget(db: AsyncSession, user_id: int) -> dict | None:
    """Check current total spending vs. user-level budget for this month.

    Returns:
        Dict with keys: budget, spent, percent, alert_level.
        None if no user-level budget is set.
    """
    # Get the user-level budget (category is NULL)
    budget = await get_user_budget(db, user_id)

    if budget is None:
        return None

    # Get current month total spending (all categories)
    today = date.today()
    month_start = today.replace(day=1)

    spent_stmt = select(
        func.coalesce(func.sum(Expense.amount), 0)
    ).where(
        Expense.user_id == user_id,
        Expense.date >= month_start,
        Expense.date <= today,
    )
    spent_result = await db.execute(spent_stmt)
    spent = float(spent_result.scalar_one())

    limit = float(budget.monthly_limit)
    percent = round((spent / limit) * 100) if limit > 0 else 0

    # Determine alert level
    if percent >= 100:
        alert_level = "danger"
    elif percent >= 80:
        alert_level = "warning"
    elif percent >= 50:
        alert_level = "info"
    else:
        alert_level = None

    return {
        "budget": limit,
        "spent": spent,
        "percent": percent,
        "alert_level": alert_level,
    }
