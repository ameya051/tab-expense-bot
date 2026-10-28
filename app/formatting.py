"""Reply formatting helpers shared by tools and handlers (no Telegram imports)."""

import html
from datetime import date

from app.services.currency_service import get_currency_symbol
from app.services.user_service import CurrencyChangeResult


def esc(text: str) -> str:
    """Escape user- or LLM-derived text for messages sent with parse_mode=HTML."""
    return html.escape(text, quote=False)


def format_amount(amount: float, currency: str) -> str:
    """Format an amount with its currency symbol."""
    symbol = get_currency_symbol(currency)
    return f"{symbol}{amount:,.2f}"


def recurring_schedule_text(frequency: str, day_of_month: int | None, on: date) -> str:
    """Describe a recurring schedule, e.g. "every Monday". `on` is any run date."""
    if frequency == "daily":
        return "every day"
    if frequency == "weekly":
        return f"every {on.strftime('%A')}"
    day = day_of_month or on.day
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"on the {day}{suffix} of every month"


def currency_change_note(result: CurrencyChangeResult) -> str:
    """Describe what a currency change converted, or '' if nothing was."""
    if not result.rates:
        return ""
    rate_text = ", ".join(
        f"1 {src} = {rate:,.4f} {result.new_currency}" for src, rate in result.rates.items()
    )
    count = result.expenses_converted
    return (
        f"🔁 Converted {count} expense{'s' if count != 1 else ''}, "
        f"your budgets and recurring expenses ({rate_text}).\n\n"
    )
