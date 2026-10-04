"""Data integrity checks — positive amounts, valid day-of-month, currency format.

Revision ID: 004_integrity_checks
Revises: 003_budget_user_level
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "004_integrity_checks"
down_revision: Union[str, None] = "003_budget_user_level"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECKS: list[tuple[str, str, str]] = [
    ("budgets", "ck_budgets_monthly_limit_positive", "monthly_limit > 0"),
    ("expenses", "ck_expenses_amount_positive", "amount > 0"),
    (
        "recurring_expenses",
        "ck_recurring_expenses_amount_positive",
        "amount > 0",
    ),
    (
        "recurring_expenses",
        "ck_recurring_expenses_day_of_month",
        "day_of_month BETWEEN 1 AND 31",
    ),
    (
        "users",
        "ck_users_preferred_currency_format",
        "preferred_currency ~ '^[A-Z]{3}$'",
    ),
]


def upgrade() -> None:
    # NOTE: run after cleaning up any offending rows — a CHECK constraint
    # fails to create if existing data violates it.
    for table, name, condition in CHECKS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for table, name, _ in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
