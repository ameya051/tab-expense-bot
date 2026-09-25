"""Pydantic schemas for NLP intent parsing and API data transfer."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class LogExpenseIntent(BaseModel):
    """Structured result when the user wants to log an expense."""

    intent: Literal["log_expense"]
    amount: float
    currency: str = "INR"
    category: str
    date: date
    description: str | None = None
    recurring: bool = False


class QueryIntent(BaseModel):
    """Structured result when the user wants to query their spending."""

    intent: Literal["query"]
    period: Literal["today", "this_week", "this_month", "all_time"] = "this_month"
    group_by: Literal["category", "day", "none"] = "none"
    category: str | None = None
    limit: int | None = None


class DeleteIntent(BaseModel):
    """Structured result when the user wants to delete an expense."""

    intent: Literal["delete"]
    target: str = "last"  # "last" or a stringified expense ID


class UnknownIntent(BaseModel):
    """Returned when the message is not expense-related."""

    intent: Literal["unknown"]


# Discriminated union — Pydantic picks the right subtype based on the "intent" field
ParsedIntent = Annotated[
    LogExpenseIntent | QueryIntent | DeleteIntent | UnknownIntent,
    Field(discriminator="intent"),
]


# Some LLM tool-calling APIs cannot accept a union directly, so the graph's
# structured output targets this single-object envelope instead; the union
# inside is still validated by the discriminator.
class IntentEnvelope(BaseModel):
    """Single-object wrapper bundling a ParsedIntent for structured output."""

    intent_union: ParsedIntent
