"""User service — upsert logic and preference management for Telegram users."""

from datetime import datetime

from sqlalchemy import select, update
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


async def get_user(db: AsyncSession, telegram_id: int) -> User | None:
    """Fetch a user by their Telegram ID, or None if not found."""
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_preferred_currency(
    db: AsyncSession, telegram_id: int, currency: str
) -> None:
    """Update a user's preferred currency."""
    stmt = (
        update(User)
        .where(User.telegram_id == telegram_id)
        .values(preferred_currency=currency.upper())
    )
    await db.execute(stmt)
    await db.commit()


async def mark_onboarding_complete(db: AsyncSession, telegram_id: int) -> None:
    """Mark the user's onboarding as complete."""
    stmt = (
        update(User)
        .where(User.telegram_id == telegram_id)
        .values(onboarding_complete=True)
    )
    await db.execute(stmt)
    await db.commit()
