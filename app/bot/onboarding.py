"""Onboarding flow — ConversationHandler for /start that asks for preferred currency."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.database import AsyncSessionLocal
from app.services import user_service

logger = logging.getLogger(__name__)

# Conversation states
CHOOSE_CURRENCY, CUSTOM_CURRENCY = range(2)

# Currency options for the inline keyboard
CURRENCY_OPTIONS = [
    ("₹ INR", "INR"),
    ("$ USD", "USD"),
    ("€ EUR", "EUR"),
    ("£ GBP", "GBP"),
]


async def _ensure_user(update: Update) -> None:
    """Upsert the user on every interaction."""
    tg_user = update.effective_user
    if tg_user is None:
        return
    async with AsyncSessionLocal() as db:
        await user_service.upsert_user(
            db,
            telegram_id=tg_user.id,
            first_name=tg_user.first_name,
            username=tg_user.username,
        )


# ---------------------------------------------------------------------------
# /start — entry point
# ---------------------------------------------------------------------------

async def start_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Welcome message and currency picker."""
    await _ensure_user(update)

    # Check if returning user
    async with AsyncSessionLocal() as db:
        user = await user_service.get_user(db, update.effective_user.id)

    keyboard = [
        [
            InlineKeyboardButton(label, callback_data=f"currency:{code}")
            for label, code in CURRENCY_OPTIONS
        ],
        [InlineKeyboardButton("🌍 Other...", callback_data="currency:other")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if user and user.onboarding_complete:
        current = user.preferred_currency
        await update.message.reply_text(
            f"👋 <b>Welcome back!</b>\n\n"
            f"Your current currency is <b>{current}</b>.\n"
            f"Want to change it?",
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    else:
        await update.message.reply_text(
            "👋 <b>Hey there!</b> I'm your personal expense tracker.\n\n"
            "First, what's your preferred currency?",
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    return CHOOSE_CURRENCY


# ---------------------------------------------------------------------------
# Currency selection callback
# ---------------------------------------------------------------------------

async def currency_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle currency button press."""
    query = update.callback_query
    await query.answer()

    choice = query.data.replace("currency:", "")

    if choice == "other":
        await query.edit_message_text(
            "🌍 Type your preferred currency code (e.g. <b>CAD</b>, <b>AUD</b>, <b>SGD</b>):",
            parse_mode="HTML",
        )
        return CUSTOM_CURRENCY

    # Save the chosen currency
    return await _save_currency_and_finish(query, context, choice)


async def custom_currency_entered(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle free-text currency code input."""
    code = update.message.text.strip().upper()

    if len(code) != 3 or not code.isalpha():
        await update.message.reply_text(
            "❌ Please enter a valid 3-letter currency code (e.g. <b>CAD</b>, <b>JPY</b>):",
            parse_mode="HTML",
        )
        return CUSTOM_CURRENCY

    user_id = update.effective_user.id
    async with AsyncSessionLocal() as db:
        await user_service.update_preferred_currency(db, user_id, code)
        await user_service.mark_onboarding_complete(db, user_id)

    await update.message.reply_text(
        _finish_message(code),
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def _save_currency_and_finish(query, context, code: str) -> int:
    """Save currency preference and send the finishing message."""
    user_id = query.from_user.id
    async with AsyncSessionLocal() as db:
        await user_service.update_preferred_currency(db, user_id, code)
        await user_service.mark_onboarding_complete(db, user_id)

    await query.edit_message_text(
        _finish_message(code),
        parse_mode="HTML",
    )
    return ConversationHandler.END


def _finish_message(code: str) -> str:
    """Build the onboarding completion message."""
    flags = {
        "INR": "🇮🇳", "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧",
        "JPY": "🇯🇵", "CAD": "🇨🇦", "AUD": "🇦🇺", "CHF": "🇨🇭",
    }
    flag = flags.get(code, "🌍")

    return (
        f"✅ Your default currency is now <b>{code}</b> {flag}\n\n"
        "You're all set! Try logging your first expense:\n"
        "• <i>spent 500 on groceries</i>\n"
        "• <i>200 cab yesterday</i>\n"
        "• <i>lunch 150</i>\n\n"
        "📊 <b>Commands:</b>\n"
        "/summary — Monthly category breakdown\n"
        "/report — Spending trend over time\n"
        "/budget — Set category budgets\n"
        "/recurring — Manage recurring expenses\n"
        "/export — Download expenses as CSV\n"
        "/delete — Remove your last expense\n"
        "/settings — Change your currency"
    )


# ---------------------------------------------------------------------------
# /skip — use defaults
# ---------------------------------------------------------------------------

async def skip_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip onboarding and use default settings."""
    await _ensure_user(update)
    user_id = update.effective_user.id

    async with AsyncSessionLocal() as db:
        await user_service.mark_onboarding_complete(db, user_id)

    await update.message.reply_text(
        _finish_message("INR"),
        parse_mode="HTML",
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /settings — change currency (reuses the currency picker)
# ---------------------------------------------------------------------------

async def settings_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Let the user change their preferred currency."""
    await _ensure_user(update)
    async with AsyncSessionLocal() as db:
        user = await user_service.get_user(db, update.effective_user.id)

    current = user.preferred_currency if user else "INR"

    keyboard = [
        [
            InlineKeyboardButton(label, callback_data=f"currency:{code}")
            for label, code in CURRENCY_OPTIONS
        ],
        [InlineKeyboardButton("🌍 Other...", callback_data="currency:other")],
    ]

    await update.message.reply_text(
        f"⚙️ Current currency: <b>{current}</b>\n\n"
        "Pick a new one:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSE_CURRENCY


# ---------------------------------------------------------------------------
# Build the ConversationHandler
# ---------------------------------------------------------------------------

def build_onboarding_handler() -> ConversationHandler:
    """Create the ConversationHandler for /start onboarding."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start_entry),
            CommandHandler("settings", settings_handler),
        ],
        states={
            CHOOSE_CURRENCY: [
                CallbackQueryHandler(
                    currency_chosen, pattern=r"^currency:"
                ),
            ],
            CUSTOM_CURRENCY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, custom_currency_entered
                ),
            ],
        },
        fallbacks=[
            CommandHandler("skip", skip_onboarding),
            CommandHandler("start", start_entry),
        ],
    )
