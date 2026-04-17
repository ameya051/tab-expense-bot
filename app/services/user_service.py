"""User service — upsert logic for Telegram users."""

from datetime import datetime

from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def upsert_user(
    db: AsyncSession,
    telegram_id: int,
    first_name: str | None = None,
    username: str | None = None,
) -> None:
    """Create the user if they don't exist, or update their name/username if they do.

    Uses PostgreSQL's ON CONFLICT ... DO UPDATE for an atomic upsert.
    """
    stmt = pg_insert(User).values(
        telegram_id=telegram_id,
        first_name=first_name,
        username=username,
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
