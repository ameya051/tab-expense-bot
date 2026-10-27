"""Shared helpers for calls to the AI API (OpenRouter): retries and errors."""

import asyncio
import logging

import openai

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
AI_TIMEOUT_SECONDS = 30.0

# Errors that will not succeed on retry (bad key, bad request, unknown model…).
NON_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.BadRequestError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)


class AIUnavailableError(Exception):
    """Raised when the AI API remains unavailable after all retry attempts."""


async def retry_ai_call(call_factory, label: str = "AI"):
    """Run an async AI API call with exponential backoff (3 attempts total).

    call_factory is a zero-argument callable returning an awaitable, so each
    attempt gets a fresh call. Permanent API errors (auth, bad request, …)
    are not retried. Raises AIUnavailableError on final failure.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await call_factory()
        except NON_RETRYABLE_ERRORS as exc:
            logger.error("%s call failed permanently: %s", label, type(exc).__name__)
            raise AIUnavailableError(f"{label} rejected the request") from exc
        except Exception as exc:  # noqa: BLE001 — retry any transport/API failure
            last_exc = exc
            if attempt < MAX_ATTEMPTS:
                logger.warning(
                    "%s call attempt %d failed, retrying: %s",
                    label,
                    attempt,
                    type(exc).__name__,
                )
                await asyncio.sleep(2 ** (attempt - 1))
    logger.error(
        "%s call failed after %d attempts: %s",
        label,
        MAX_ATTEMPTS,
        type(last_exc).__name__ if last_exc else "unknown",
    )
    raise AIUnavailableError(f"{label} unavailable after {MAX_ATTEMPTS} attempts") from last_exc
