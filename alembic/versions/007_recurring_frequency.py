"""Add recurring_expenses.frequency — daily, weekly or monthly.

day_of_month becomes monthly-only: it stays the anchor for month-end
clamping (31st → Feb 28 → Mar 31), while daily and weekly entries step
next_run_date by a fixed number of days and leave it NULL.

Revision ID: 007_recurring_frequency
Revises: 006_drop_recurring_currency
Create Date: 2026-10-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "007_recurring_frequency"
down_revision: Union[str, None] = "006_drop_recurring_currency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_DAY_CHECK = "day_of_month BETWEEN 1 AND 31"
NEW_DAY_CHECK = (
    "(frequency = 'monthly') = (day_of_month IS NOT NULL) "
    "AND (day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31)"
)


def upgrade() -> None:
    # server_default backfills every existing row as monthly.
    op.add_column(
        "recurring_expenses",
        sa.Column("frequency", sa.Text(), nullable=False, server_default="monthly"),
    )
    op.create_check_constraint(
        "ck_recurring_expenses_frequency",
        "recurring_expenses",
        "frequency IN ('daily', 'weekly', 'monthly')",
    )
    op.alter_column("recurring_expenses", "day_of_month", nullable=True)
    op.drop_constraint(
        "ck_recurring_expenses_day_of_month", "recurring_expenses", type_="check"
    )
    op.create_check_constraint(
        "ck_recurring_expenses_day_of_month", "recurring_expenses", NEW_DAY_CHECK
    )


def downgrade() -> None:
    # The old schema can't represent daily/weekly entries. Expenses they
    # already logged are separate rows and are kept.
    op.execute("DELETE FROM recurring_expenses WHERE frequency <> 'monthly'")
    op.drop_constraint(
        "ck_recurring_expenses_day_of_month", "recurring_expenses", type_="check"
    )
    op.create_check_constraint(
        "ck_recurring_expenses_day_of_month", "recurring_expenses", OLD_DAY_CHECK
    )
    op.alter_column("recurring_expenses", "day_of_month", nullable=False)
    op.drop_constraint(
        "ck_recurring_expenses_frequency", "recurring_expenses", type_="check"
    )
    op.drop_column("recurring_expenses", "frequency")
