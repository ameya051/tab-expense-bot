"""Pydantic schemas: agent tool arguments and slash-command arguments."""

from __future__ import annotations

import datetime as dt
import math
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Largest value a Numeric(10, 2) column can hold.
MAX_AMOUNT = 99_999_999.99

# Most expenses a "last N expenses" query will list.
MAX_RECENT_LIMIT = 50


def is_storable_amount(amount: float) -> bool:
    """True if the amount fits Numeric(10, 2) and is at least 0.01 after rounding."""
    return math.isfinite(amount) and 0.01 <= round(amount, 2) <= MAX_AMOUNT


RecurringFrequency = Literal["daily", "weekly", "monthly"]


Period = Literal[
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "all_time",
]


class LogExpenseArgs(BaseModel):
    """Arguments for the log_expense tool."""

    amount: float = Field(gt=0, le=MAX_AMOUNT)
    currency: str | None = Field(
        default=None,
        description="ISO 4217 code, only if the user named a currency or symbol",
    )
    category: str = Field(description="Short, lowercase, e.g. food, transport, groceries")
    # dt.date: a field named "date" with a default would shadow the type.
    date: dt.date | None = Field(default=None, description="YYYY-MM-DD; omit for today")
    description: str | None = None
    # Never guess a schedule: only set when the user said the expense repeats.
    recurring_frequency: RecurringFrequency | None = None

    @field_validator("amount")
    @classmethod
    def _storable(cls, value: float) -> float:
        if not is_storable_amount(value):
            raise ValueError("amount must be between 0.01 and 99,999,999.99")
        return value

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        return value.strip().upper() or None if value else None

    @field_validator("category")
    @classmethod
    def _clean_category(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("category must not be empty")
        return value

    @field_validator("recurring_frequency", mode="before")
    @classmethod
    def _lowercase_frequency(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class DateRangeArgs(BaseModel):
    """A named period, or an explicit range that takes precedence over it."""

    period: Period = "this_month"
    # Explicit range for anything a named period can't express ("in September",
    # "last 10 days").
    start_date: date | None = Field(default=None, description="YYYY-MM-DD")
    end_date: date | None = Field(default=None, description="YYYY-MM-DD")

    @model_validator(mode="after")
    def _order_dates(self) -> DateRangeArgs:
        """Swap a reversed custom range instead of returning nothing."""
        if self.start_date and self.end_date and self.start_date > self.end_date:
            self.start_date, self.end_date = self.end_date, self.start_date
        return self


class GetSpendingArgs(DateRangeArgs):
    """Arguments for the get_spending tool."""

    category: str | None = Field(default=None, description="Only this category")
    group_by: Literal["none", "category", "day"] = "none"


class ShowChartArgs(DateRangeArgs):
    """Arguments for the show_chart tool."""

    kind: Literal["category", "trend"] = Field(
        default="category",
        description="category = breakdown bar chart + table, trend = daily line chart",
    )


class ExportArgs(DateRangeArgs):
    """Arguments for the export_csv tool."""


class ShowArgs(BaseModel):
    """Arguments for lookup tools that can either show results or only return them."""

    show: bool = Field(
        default=True,
        description="false = only look up ids for a follow-up action, show nothing",
    )


class ListExpensesArgs(ShowArgs):
    """Arguments for the list_expenses tool."""

    limit: int = 5
    category: str | None = None

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int) -> int:
        """Treat non-positive limits as the default and cap large ones at 50."""
        if value <= 0:
            return 5
        return min(value, MAX_RECENT_LIMIT)


class DeleteExpenseArgs(BaseModel):
    """Arguments for the delete_expense tool."""

    expense_id: int | None = Field(
        default=None, description="Omit to delete the most recently logged expense"
    )


class SetBudgetArgs(BaseModel):
    """Arguments for the set_budget tool."""

    amount: float = Field(gt=0, le=MAX_AMOUNT, description="Monthly limit")
    category: str | None = Field(default=None, description="Omit for the total budget")

    @field_validator("amount")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("amount must be a number")
        return value

    @field_validator("category")
    @classmethod
    def _lower_category(cls, value: str | None) -> str | None:
        return value.strip().lower() or None if value else None


class RemoveBudgetArgs(BaseModel):
    """Arguments for the remove_budget tool."""

    category: str | None = Field(default=None, description="Omit for the total budget")

    @field_validator("category")
    @classmethod
    def _lower_category(cls, value: str | None) -> str | None:
        return value.strip().lower() or None if value else None


class CancelRecurringArgs(BaseModel):
    """Arguments for the cancel_recurring tool."""

    recurring_id: int


class ChangeCurrencyArgs(BaseModel):
    """Arguments for the change_currency tool."""

    new_currency: str = Field(description="ISO 4217 code, e.g. USD")

    @field_validator("new_currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class NoArgs(BaseModel):
    """For tools that take no arguments."""


_NOT_POSITIVE = (
    "Budget amount must be greater than zero.\n"
    "Example: /budget food 5000"
)

_NOT_POSITIVE_TOTAL = (
    "Budget amount must be greater than zero.\n"
    "Example: /budget 20000 — set total monthly budget"
)


def _parse_amount(raw: Any, message: str) -> float:
    """Parse a budget amount, rejecting non-numbers and values <= 0."""
    try:
        amount = float(raw)
    except ValueError as exc:
        raise ValueError(
            "Amount must be a number.\nExample: /budget food 5000"
        ) from exc
    if not math.isfinite(amount):
        raise ValueError("Amount must be a number.\nExample: /budget food 5000")
    if amount <= 0:
        raise ValueError(message)
    if amount > MAX_AMOUNT:
        raise ValueError(f"Budget amount can be at most {MAX_AMOUNT:,.2f}.")
    return amount


class BudgetArgs(BaseModel):
    """Validated arguments for /budget: either a total amount or category + amount."""

    amount: float
    category: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _parse_args(cls, data: Any) -> dict:
        if not isinstance(data, list) or not data:
            raise ValueError("No budget arguments provided")

        if len(data) == 1:
            return {"amount": _parse_amount(data[0], _NOT_POSITIVE_TOTAL)}

        # len(data) >= 2: category (possibly multi-word) + amount (last arg)
        category = " ".join(data[:-1]).lower()
        try:
            float(category)
        except ValueError:
            pass  # Category is non-numeric, valid category name
        else:
            raise ValueError(
                "Usage: /budget <amount> — for user total budget\n"
                "       /budget <category> <amount> — for category budget\n"
                "Examples:\n"
                "• /budget 20000 — set total monthly budget to 20000\n"
                "• /budget food 5000 — set food budget to 5000"
            )

        return {
            "amount": _parse_amount(data[-1], _NOT_POSITIVE),
            "category": category,
        }
