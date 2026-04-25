"""Groq-powered NLP parser — converts natural language into structured intents."""

import json
import logging
from datetime import date

from groq import Groq
from pydantic import TypeAdapter
from starlette.concurrency import run_in_threadpool

from app.schemas import ParsedIntent, UnknownIntent

logger = logging.getLogger(__name__)

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
  "period": "today|this_week|this_month|all_time",
  "group_by": "category|day|none",
  "category": "<optional category filter or null>",
  "limit": <optional number or null>
}}

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
    """Parses natural language expense messages via the Groq API."""

    def __init__(self, api_key: str, model: str) -> None:
        self.client = Groq(api_key=api_key)
        self.model = model

    async def parse(self, text: str, default_currency: str = "INR") -> ParsedIntent:
        """Parse user text into a structured intent (runs Groq call in threadpool)."""
        try:
            raw_json = await run_in_threadpool(
                self._call_groq, text, default_currency
            )
            parsed = json.loads(raw_json)
            return _intent_adapter.validate_python(parsed)
        except Exception:
            logger.exception("NLP parsing failed for text: %s", text)
            return UnknownIntent(intent="unknown")

    def _call_groq(self, text: str, default_currency: str) -> str:
        """Synchronous Groq API call — meant to be run via run_in_threadpool."""
        today = date.today().isoformat()
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            today=today, default_currency=default_currency
        )

        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        return completion.choices[0].message.content
