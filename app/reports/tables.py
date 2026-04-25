"""Monospace text table formatters for Telegram messages."""

from app.models import Expense
from app.services.currency_service import get_currency_symbol

# ---------------------------------------------------------------------------
# Emoji mapping — best-effort lookup with fallback
# ---------------------------------------------------------------------------

CATEGORY_EMOJI: dict[str, str] = {
    "food": "🍔",
    "groceries": "🛒",
    "transport": "🚗",
    "transportation": "🚗",
    "cab": "🚕",
    "auto": "🛺",
    "entertainment": "🎬",
    "shopping": "🛍️",
    "health": "💊",
    "medical": "💊",
    "bills": "📄",
    "utilities": "📄",
    "education": "📚",
    "travel": "✈️",
    "rent": "🏠",
    "subscriptions": "📺",
    "clothing": "👕",
    "personal": "💅",
    "gifts": "🎁",
    "drinks": "🍺",
    "coffee": "☕",
    "snacks": "🍿",
    "fuel": "⛽",
    "petrol": "⛽",
    "phone": "📱",
    "internet": "🌐",
    "gym": "🏋️",
    "fitness": "🏋️",
}

FALLBACK_EMOJI = "💰"


def _get_emoji(category: str) -> str:
    """Look up an emoji for a category, falling back to 💰."""
    return CATEGORY_EMOJI.get(category.lower(), FALLBACK_EMOJI)


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def format_summary_table(
    data: list[dict],
    total: float,
    period_label: str = "This Month",
    currency: str = "INR",
) -> str:
    """Format a category breakdown as a monospace text table.

    Args:
        data: List of {"category": str, "total": float} dicts.
        total: Grand total for the period.
        period_label: Human-readable period label.
        currency: Currency code for formatting.

    Returns:
        Formatted string ready to be sent as a Telegram message.
    """
    if not data:
        return f"📊 {period_label}\n\nNo expenses recorded yet."

    symbol = get_currency_symbol(currency)

    # Calculate column widths
    max_cat_len = max(len(d["category"]) for d in data)
    col_width = max(max_cat_len + 4, 16)  # emoji + space + name + padding

    lines: list[str] = []
    lines.append(f"📊 {period_label}")
    lines.append("")
    lines.append(f"{'Category':<{col_width}} {'Amount':>10}")
    lines.append("─" * (col_width + 12))

    for d in data:
        emoji = _get_emoji(d["category"])
        name = f"{emoji} {d['category'].title()}"
        amount = f"{symbol}{d['total']:,.2f}"
        lines.append(f"{name:<{col_width}} {amount:>10}")

    lines.append("─" * (col_width + 12))
    lines.append(f"{'Total':<{col_width}} {symbol + f'{total:,.2f}':>10}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Recent expenses list
# ---------------------------------------------------------------------------

def format_recent_expenses(
    expenses: list[Expense],
    currency: str = "INR",
) -> str:
    """Format a list of recent expenses as a numbered list.

    Args:
        expenses: List of Expense model instances.
        currency: User's preferred currency for display.

    Returns:
        Formatted string for Telegram.
    """
    if not expenses:
        return "No expenses found."

    symbol = get_currency_symbol(currency)
    lines: list[str] = ["📋 Recent Expenses", ""]
    for i, exp in enumerate(expenses, 1):
        emoji = _get_emoji(exp.category)
        date_str = exp.date.strftime("%b %d")
        desc = f" — {exp.description}" if exp.description else ""

        # Show original currency if it differs
        orig = ""
        if exp.original_currency and exp.original_currency != currency:
            orig_symbol = get_currency_symbol(exp.original_currency)
            orig = f" ({orig_symbol}{float(exp.original_amount):,.2f})"

        lines.append(
            f"{i}. {emoji} {exp.category.title()} · "
            f"{symbol}{float(exp.amount):,.2f}{orig} · {date_str}{desc}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Budget overview
# ---------------------------------------------------------------------------

def format_budget_overview(
    budgets_with_spending: list[dict],
    currency: str = "INR",
) -> str:
    """Format budgets with visual progress bars.

    Args:
        budgets_with_spending: List of dicts with keys: category, limit, spent, percent.
        currency: Currency code for formatting.

    Returns:
        Formatted string for Telegram.
    """
    if not budgets_with_spending:
        return "📋 No budgets set."

    symbol = get_currency_symbol(currency)
    lines: list[str] = ["📋 Monthly Budgets", ""]

    for b in budgets_with_spending:
        emoji = _get_emoji(b["category"])
        pct = b["percent"]
        spent = b["spent"]
        limit = b["limit"]

        # Build progress bar (10 segments)
        filled = min(round(pct / 10), 10)
        bar = "█" * filled + "░" * (10 - filled)

        # Alert indicator
        alert = ""
        if pct >= 100:
            alert = " 🚨"
        elif pct >= 80:
            alert = " ⚠️"

        lines.append(
            f"{emoji} {b['category'].title()}\n"
            f"  {bar} {symbol}{spent:,.2f}/{symbol}{limit:,.2f} ({pct}%){alert}"
        )

    return "\n".join(lines)
