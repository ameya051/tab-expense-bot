"""SQLAlchemy ORM models for users and expenses."""

from datetime import date as date_type
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class User(Base):
    """Telegram user — identified by their permanent telegram_id."""

    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_currency: Mapped[str] = mapped_column(String(10), default="INR")
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    expenses: Mapped[list["Expense"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User telegram_id={self.telegram_id} username={self.username}>"


class Expense(Base):
    """A single expense entry linked to a user."""

    __tablename__ = "expenses"
    __table_args__ = (
        Index("ix_expenses_user_date", "user_id", "date"),
        Index("ix_expenses_user_category", "user_id", "category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True
    )
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    original_amount: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    original_currency: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    category: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    user: Mapped["User"] = relationship(back_populates="expenses")

    def __repr__(self) -> str:
        return f"<Expense id={self.id} amount={self.amount} category={self.category}>"


class RecurringExpense(Base):
    """A recurring expense that the bot auto-logs monthly."""

    __tablename__ = "recurring_expenses"
    __table_args__ = (
        Index("ix_recurring_active_next_run", "active", "next_run_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True
    )
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    category: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    day_of_month: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<RecurringExpense id={self.id} category={self.category} "
            f"amount={self.amount} day={self.day_of_month}>"
        )


class Budget(Base):
    """A monthly budget limit - either user-level total (category=None) or category-specific."""

    __tablename__ = "budgets"
    __table_args__ = (
        Index("ix_budgets_user_category", "user_id", "category", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True
    )
    category: Mapped[str | None] = mapped_column(Text, nullable=True)  # NULL = user-level total budget
    monthly_limit: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        cat_label = self.category or "TOTAL"
        return (
            f"<Budget id={self.id} category={cat_label} "
            f"limit={self.monthly_limit}>"
        )
