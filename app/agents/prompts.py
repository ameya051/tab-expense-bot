"""Chat prompt template that converts user text into structured intents."""

from datetime import date

from langchain_core.prompts import ChatPromptTemplate

from app.schemas import DeleteIntent, LogExpenseIntent, QueryIntent, UnknownIntent

_INTENT_SCHEMAS = (
    f"Log an expense — number amount (required), date YYYY-MM-DD (required), "
    f"short lowercase category (required), description, currency, "
    f"recurring=true for monthly/subscription expenses:\n"
    f"{LogExpenseIntent.model_json_schema()}\n\n"
    f"Query about spending — period (today|this_week|this_month|all_time), "
    f"group_by (category|day|none), optional category filter, optional limit:\n"
    f"{QueryIntent.model_json_schema()}\n\n"
    f"Delete the user's last expense:\n"
    f"{DeleteIntent.model_json_schema()}\n\n"
    f"Message is not expense-related:\n"
    f"{UnknownIntent.model_json_schema()}"
)

INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an expense tracking assistant for a Telegram bot. "
            "Classify the user's message into exactly one intent and return a "
            "JSON object matching that intent's schema.\n"
            "Today's date is {today}. "
            "The user's preferred currency is {default_currency}; default "
            'currency fields to "{default_currency}" unless the message names '
            "another currency (symbols: $ USD, € EUR, £ GBP, ¥ JPY, ₹ INR).\n\n"
            "{intent_schemas}\n\n"
            "Rules: return valid JSON only, nothing else; relative dates like "
            "'yesterday' must be resolved against the given today date; keep "
            "categories short and lowercase; assume this_month for queries "
            "without a period; fill currency with the message's currency or "
            "the preferred currency.",
        ),
        ("human", "{text}"),
    ]
).partial(intent_schemas=_INTENT_SCHEMAS)


def format_intent_prompt(text: str, default_currency: str) -> list:
    """Format INTENT_PROMPT with today's date, currency default and user text.

    Returns the rendered system + human messages ready to send to a chat
    model, so the graph node (and tests) can inspect exactly what the LLM
    will see.
    """
    rendered = INTENT_PROMPT.invoke(
        {"today": date.today().isoformat(), "default_currency": default_currency, "text": text}
    )
    return rendered.to_messages()
