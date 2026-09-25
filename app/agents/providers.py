"""Swappable LLM provider factory for the LangGraph agent."""

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.config import settings

SUPPORTED_PROVIDERS = ("groq", "openai", "anthropic")


def _require_api_key(provider: str, api_key: str | None) -> str:
    """Fail fast when a provider is selected without its API key configured."""
    if not api_key:
        raise ValueError(
            f"Missing API key for LLM provider {provider!r}. "
            "Set the corresponding environment variable before using it."
        )
    return api_key


def build_chat_model(provider: str | None = None) -> BaseChatModel:
    """Return a LangChain chat model for the requested provider.

    Falls back to ``settings.llm_provider`` when no provider is given.
    """
    provider = (provider or settings.llm_provider).lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM provider {provider!r}. "
            f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}"
        )

    if provider == "groq":
        return ChatGroq(
            model=settings.groq_model,
            api_key=_require_api_key("groq", settings.groq_api_key),
        )
    if provider == "openai":
        return ChatOpenAI(
            model=settings.openai_model,
            api_key=_require_api_key("openai", settings.openai_api_key),
        )
    return ChatAnthropic(
        model=settings.anthropic_model,
        api_key=_require_api_key("anthropic", settings.anthropic_api_key),
    )
