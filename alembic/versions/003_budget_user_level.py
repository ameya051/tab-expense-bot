"""Make budget category nullable to support user-level total budgets.

Revision ID: 003_budget_user_level
Revises: 002_phase2
Create Date: 2026-04-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003_budget_user_level"
down_revision: Union[str, None] = "002_phase2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make category nullable to support user-level total budgets (category=NULL)
    op.alter_column("budgets", "category",
        existing_type=sa.Text(),
        nullable=True,
        existing_server_default=None,
        existing_nullable=False
    )


def downgrade() -> None:
    # First, delete any user-level budgets (category=NULL) as they can't exist with non-nullable column
    op.execute("DELETE FROM budgets WHERE category IS NULL")

    # Make category non-nullable again
    op.alter_column("budgets", "category",
        existing_type=sa.Text(),
        nullable=False,
        existing_server_default=None,
        existing_nullable=True
    )
