"""CSV export service — generates expense data as downloadable CSV."""

import csv
import io
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Expense

logger = logging.getLogger(__name__)


async def generate_csv(
    db: AsyncSession,
    user_id: int,
    start: date,
    end: date,
) -> tuple[bytes, int]:
    """Generate a CSV of expenses in a date range.

    Returns:
        Tuple of (csv_bytes, row_count).
    """
    stmt = (
        select(Expense)
        .where(
            Expense.user_id == user_id,
            Expense.date >= start,
            Expense.date <= end,
        )
        .order_by(Expense.date.desc(), Expense.created_at.desc())
    )
    result = await db.execute(stmt)
    expenses = list(result.scalars().all())

    string_buf = io.StringIO()
    writer = csv.writer(string_buf)

    # Header row
    writer.writerow([
        "Date",
        "Category",
        "Amount",
        "Currency",
        "Original Amount",
        "Original Currency",
        "Description",
    ])

    # Data rows
    for exp in expenses:
        writer.writerow([
            exp.date.isoformat(),
            exp.category.title(),
            f"{exp.amount:.2f}",
            exp.currency,
            f"{exp.original_amount:.2f}" if exp.original_amount else "",
            exp.original_currency or "",
            exp.description or "",
        ])

    string_buf.seek(0)
    csv_bytes = string_buf.getvalue().encode("utf-8")
    return csv_bytes, len(expenses)
