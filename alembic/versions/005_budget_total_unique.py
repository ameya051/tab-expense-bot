"""Make the user-level total budget (category IS NULL) unique per user.

The original unique index on (user_id, category) treats NULL categories as
distinct, so ON CONFLICT never fired for total budgets and every
`/budget <amount>` inserted a duplicate row. Requires PostgreSQL 15+.

Revision ID: 005_budget_total_unique
Revises: 004_integrity_checks
Create Date: 2026-10-04

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "005_budget_total_unique"
down_revision: Union[str, None] = "004_integrity_checks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep only the newest total budget per user.
    op.execute(
        "DELETE FROM budgets b USING budgets b2 "
        "WHERE b.category IS NULL AND b2.category IS NULL "
        "AND b.user_id = b2.user_id AND b.id < b2.id"
    )
    op.drop_index("ix_budgets_user_category", table_name="budgets")
    op.create_index(
        "ix_budgets_user_category",
        "budgets",
        ["user_id", "category"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("ix_budgets_user_category", table_name="budgets")
    op.create_index(
        "ix_budgets_user_category",
        "budgets",
        ["user_id", "category"],
        unique=True,
    )
