"""Phase 2 — user preferences, original currency, recurring expenses, budgets.

Revision ID: 002_phase2
Revises: 001_initial
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_phase2"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Users table: add preference columns ---
    op.add_column(
        "users",
        sa.Column(
            "preferred_currency",
            sa.String(length=10),
            server_default="INR",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "onboarding_complete",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # --- Expenses table: add original currency tracking ---
    op.add_column(
        "expenses",
        sa.Column("original_amount", sa.Numeric(precision=10, scale=2), nullable=True),
    )
    op.add_column(
        "expenses",
        sa.Column("original_currency", sa.String(length=10), nullable=True),
    )

    # --- Recurring expenses table ---
    op.create_table(
        "recurring_expenses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=10),
            server_default="INR",
            nullable=False,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("next_run_date", sa.Date(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recurring_user_id", "recurring_expenses", ["user_id"]
    )
    op.create_index(
        "ix_recurring_active_next_run",
        "recurring_expenses",
        ["active", "next_run_date"],
    )

    # --- Budgets table ---
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("monthly_limit", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_budgets_user_id", "budgets", ["user_id"])
    op.create_index(
        "ix_budgets_user_category",
        "budgets",
        ["user_id", "category"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_budgets_user_category", table_name="budgets")
    op.drop_index("ix_budgets_user_id", table_name="budgets")
    op.drop_table("budgets")

    op.drop_index("ix_recurring_active_next_run", table_name="recurring_expenses")
    op.drop_index("ix_recurring_user_id", table_name="recurring_expenses")
    op.drop_table("recurring_expenses")

    op.drop_column("expenses", "original_currency")
    op.drop_column("expenses", "original_amount")

    op.drop_column("users", "onboarding_complete")
    op.drop_column("users", "preferred_currency")
