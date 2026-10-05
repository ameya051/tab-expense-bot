"""Drop recurring_expenses.currency — amounts are in the user's preferred currency.

The column always mirrored users.preferred_currency (set on create, rewritten
on every currency change), so it was redundant.

Revision ID: 006_drop_recurring_currency
Revises: 005_budget_total_unique
Create Date: 2026-10-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "006_drop_recurring_currency"
down_revision: Union[str, None] = "005_budget_total_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("recurring_expenses", "currency")


def downgrade() -> None:
    op.add_column(
        "recurring_expenses",
        sa.Column("currency", sa.String(length=10), nullable=True),
    )
    op.execute(
        "UPDATE recurring_expenses r SET currency = u.preferred_currency "
        "FROM users u WHERE u.telegram_id = r.user_id"
    )
    op.alter_column(
        "recurring_expenses",
        "currency",
        nullable=False,
        server_default="INR",
    )
