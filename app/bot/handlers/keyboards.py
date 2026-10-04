"""Inline-keyboard builders for bot handlers."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_cancel_recurring_keyboard(recurring_entries) -> InlineKeyboardMarkup:
    """Build a grid of cancel buttons for recurring expenses."""
    buttons = [
        InlineKeyboardButton(
            f"❌ Cancel #{i}",
            callback_data=f"cancel_recurring:{entry.id}",
        )
        for i, entry in enumerate(recurring_entries, 1)
    ]
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(keyboard)
