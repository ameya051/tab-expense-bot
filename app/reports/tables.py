"""Monospace text table formatters for Telegram messages."""

from app.models import Expense

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
) -> str:
    """Format a category breakdown as a monospace text table.

    Args:
        data: List of {"category": str, "total": float} dicts.
        total: Grand total for the period.
        period_label: Human-readable period label.

    Returns:
        Formatted string ready to be sent as a Telegram message.
    """
    if not data:
        return f"📊 {period_label}\n\nNo expenses recorded yet."

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
        amount = f"₹{d['total']:,.2f}"
        lines.append(f"{name:<{col_width}} {amount:>10}")

    lines.append("─" * (col_width + 12))
    lines.append(f"{'Total':<{col_width}} {'₹' + f'{total:,.2f}':>10}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Recent expenses list
# ---------------------------------------------------------------------------

def format_recent_expenses(expenses: list[Expense]) -> str:
    """Format a list of recent expenses as a numbered list.

    Args:
        expenses: List of Expense model instances.

    Returns:
        Formatted string for Telegram.
    """
    if not expenses:
        return "No expenses found."

    lines: list[str] = ["📋 Recent Expenses", ""]
    for i, exp in enumerate(expenses, 1):
        emoji = _get_emoji(exp.category)
        date_str = exp.date.strftime("%b %d")
        desc = f" — {exp.description}" if exp.description else ""
        lines.append(
            f"{i}. {emoji} {exp.category.title()} · ₹{exp.amount:,.2f} · {date_str}{desc}"
        )

    return "\n".join(lines)
