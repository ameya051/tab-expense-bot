"""AI-powered NLP parser (via OpenRouter) — converts natural language into structured intents."""

import asyncio
import json
import logging
import time

import openai
from openai import AsyncOpenAI
from pydantic import TypeAdapter

from app.logging_setup import summarize
from app.schemas import ParsedIntent, UnknownIntent
from app.timeutils import today

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

SYSTEM_PROMPT_TEMPLATE = """\
You are an expense tracking assistant. Parse the user's message and return \
a JSON object. Today's date is {today}.

For logging expenses, return:
{{
  "intent": "log_expense",
  "amount": <number>,
  "currency": "{default_currency}",
  "category": "<category — free-form, lowercase, e.g. food, transport, groceries>",
  "date": "<YYYY-MM-DD>",
  "description": "<brief description or null>",
  "recurring": false
}}

Currency detection:
- $ → USD, € → EUR, £ → GBP, ¥ → JPY, ₹ → INR
- If the user mentions a currency symbol or code, use the corresponding ISO 4217 code.
- If no currency is mentioned, default to "{default_currency}".

Recurring expenses:
- If the user indicates this is a recurring, monthly, or subscription expense, \
set "recurring": true.
- Look for keywords: "recurring", "monthly", "every month", "subscription", \
"auto-pay", "repeat".
- Default is false.

For queries about spending, return:
{{
  "intent": "query",
  "period": "today|yesterday|this_week|last_week|this_month|last_month|all_time",
  "group_by": "category|day|none",
  "category": "<optional category filter or null>",
  "limit": <optional number or null>,
  "start_date": "<YYYY-MM-DD or null>",
  "end_date": "<YYYY-MM-DD or null>"
}}

Query periods:
- For today, yesterday, this week, last week, this month, last month or all \
time, set "period" and leave "start_date" and "end_date" null. Weeks start \
on Monday; "last week" means the previous Monday-to-Sunday week.
- For any other range (a named month like "in September", "last 10 days", \
"since Sep 15", "between Aug 1 and Aug 20"), set "start_date" and/or \
"end_date" instead and leave "period" as "this_month".
- "last N days" means from N-1 days before today through today.
- "since <date>" means "start_date" = that date and "end_date" null.

For deletion requests, return:
{{
  "intent": "delete",
  "target": "last"
}}

If the message is not expense-related, return:
{{ "intent": "unknown" }}

Rules:
- Always return valid JSON, nothing else.
- For relative dates like "yesterday", "last Friday", compute the actual date.
- If the user doesn't mention a date, assume today.
- If the user doesn't specify a period for queries, assume "this_month".
- Keep category names short and lowercase.
"""

_intent_adapter = TypeAdapter(ParsedIntent)


class NLPParser:
    """Parses natural language expense messages via the OpenRouter API."""

    def __init__(self, api_key: str, model: str) -> None:
        # max_retries=0: retry_ai_call owns retries (the SDK's own would multiply them)
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=AI_TIMEOUT_SECONDS,
            max_retries=0,
        )
        self.model = model

    async def parse(self, text: str, default_currency: str = "INR") -> ParsedIntent:
        """Parse user text into a structured intent via the async AI client."""
        logger.info(
            "LLM request model=%s currency=%s text=%s",
            self.model, default_currency, summarize(text),
        )
        raw_json = None
        try:
            raw_json = await retry_ai_call(
                lambda: self._call_ai_async(text, default_currency), label="AI"
            )
            parsed = json.loads(raw_json)
            intent = _intent_adapter.validate_python(parsed)
        except AIUnavailableError:
            raise
        except Exception:
            logger.exception("NLP parsing failed; raw LLM response: %s", summarize(raw_json))
            return UnknownIntent(intent="unknown")
        logger.info("parsed: %s", summarize(intent))
        return intent

    async def _call_ai_async(self, text: str, default_currency: str) -> str:
        """Async chat-completions call (no temperature — gpt-5.6 models reject it)."""
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            today=today().isoformat(), default_currency=default_currency
        )

        started = time.perf_counter()
        completion = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content
        logger.info(
            "LLM response (%.2f s): %s", time.perf_counter() - started, summarize(content)
        )
        return content
