"""Pydantic schemas for NLP intent parsing and API data transfer."""

from __future__ import annotations

import math
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Largest value a Numeric(10, 2) column can hold.
MAX_AMOUNT = 99_999_999.99

# Most expenses a "last N expenses" query will list.
MAX_RECENT_LIMIT = 50


def is_storable_amount(amount: float) -> bool:
    """True if the amount fits Numeric(10, 2) and is at least 0.01 after rounding."""
    return math.isfinite(amount) and 0.01 <= round(amount, 2) <= MAX_AMOUNT


class LogExpenseIntent(BaseModel):
    """Structured result when the user wants to log an expense."""

    intent: Literal["log_expense"]
    amount: float = Field(gt=0, le=MAX_AMOUNT)
    currency: str = "INR"
    category: str
    date: date
    description: str | None = None
    recurring: bool = False

    @field_validator("amount")
    @classmethod
    def _storable(cls, value: float) -> float:
        if not is_storable_amount(value):
            raise ValueError("amount must be between 0.01 and 99,999,999.99")
        return value


class QueryIntent(BaseModel):
    """Structured result when the user wants to query their spending."""

    intent: Literal["query"]
    period: Literal["today", "this_week", "this_month", "all_time"] = "this_month"
    group_by: Literal["category", "day", "none"] = "none"
    category: str | None = None
    limit: int | None = None

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int | None) -> int | None:
        """Treat non-positive limits as "no limit" and cap large ones at 50."""
        if value is None or value <= 0:
            return None
        return min(value, MAX_RECENT_LIMIT)


class DeleteIntent(BaseModel):
    """Structured result when the user wants to delete an expense."""

    intent: Literal["delete"]
    target: str = "last"  # "last" or a stringified expense ID


class UnknownIntent(BaseModel):
    """Returned when the message is not expense-related."""

    intent: Literal["unknown"]


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


# Discriminated union — Pydantic picks the right subtype based on the "intent" field
ParsedIntent = Annotated[
    LogExpenseIntent | QueryIntent | DeleteIntent | UnknownIntent,
    Field(discriminator="intent"),
]
