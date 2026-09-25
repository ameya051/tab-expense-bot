"""Unit tests for the swappable LLM provider factory."""

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.agents import providers
from app.agents.providers import build_chat_model


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply dummy keys so provider construction never hits the real env."""
    monkeypatch.setattr(providers.settings, "groq_api_key", "test-groq-key")
    monkeypatch.setattr(providers.settings, "openai_api_key", "test-openai-key")
    monkeypatch.setattr(providers.settings, "anthropic_api_key", "test-anthropic-key")


def test_build_chat_model_missing_api_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers.settings, "openai_api_key", None)

    with pytest.raises(ValueError, match="Missing API key for LLM provider 'openai'"):
        build_chat_model("openai")


@pytest.mark.parametrize(
    ("provider", "expected_class"),
    [
        ("groq", ChatGroq),
        ("openai", ChatOpenAI),
        ("anthropic", ChatAnthropic),
    ],
)
def test_build_chat_model_returns_provider_class(provider: str, expected_class: type) -> None:
    model = build_chat_model(provider)

    assert isinstance(model, expected_class)
    assert isinstance(model, BaseChatModel)


def test_build_chat_model_falls_back_to_settings_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers.settings, "llm_provider", "openai")

    model = build_chat_model()

    assert isinstance(model, ChatOpenAI)


def test_build_chat_model_provider_arg_overrides_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers.settings, "llm_provider", "anthropic")

    model = build_chat_model("groq")

    assert isinstance(model, ChatGroq)


def test_build_chat_model_rejects_unsupported_provider() -> None:
    with pytest.raises(ValueError, match="Supported providers: groq, openai, anthropic"):
        build_chat_model("mistral")
