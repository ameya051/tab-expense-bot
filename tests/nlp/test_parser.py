"""Smoke tests for the legacy NLP parser."""

import pytest

from app.nlp.parser import NLPParser
from app.schemas import LogExpenseIntent

pytestmark = pytest.mark.asyncio


async def test_parse_returns_log_expense_intent(parser: NLPParser) -> None:
    intent = await parser.parse("spent 250 on lunch", default_currency="INR")

    assert isinstance(intent, LogExpenseIntent)
    assert intent.intent == "log_expense"
    assert intent.amount == 250.0
    assert intent.category == "food"
    assert intent.currency == "INR"
