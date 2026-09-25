"""Shared pytest fixtures for the expense-bot test suite."""

import json
from unittest.mock import MagicMock

import pytest

from app.nlp.parser import NLPParser


@pytest.fixture
def groq_client_mock() -> MagicMock:
    """A mocked Groq client whose completions call returns canned JSON."""
    client = MagicMock()
    completion = MagicMock()
    completion.choices = [
        MagicMock(
            message=MagicMock(
                content=json.dumps(
                    {
                        "intent": "log_expense",
                        "amount": 250.0,
                        "currency": "INR",
                        "category": "food",
                        "date": "2026-09-25",
                        "description": "lunch",
                        "recurring": False,
                    }
                )
            )
        )
    ]
    client.chat.completions.create.return_value = completion
    return client


@pytest.fixture
def parser(groq_client_mock: MagicMock) -> NLPParser:
    """An NLPParser with its Groq client replaced by a mock."""
    parser = NLPParser(api_key="test-key", model="test-model")
    parser.client = groq_client_mock
    return parser
