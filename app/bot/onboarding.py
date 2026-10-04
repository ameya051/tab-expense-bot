"""Onboarding flow — ConversationHandler for /start that asks for preferred currency."""

import logging
import warnings

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from app.bot.handlers.common import esc
from app.config import settings
from app.database import AsyncSessionLocal
from app.services import user_service
from app.services.currency_service import (
    CurrencyConversionError,
    is_supported_currency,
)
from app.services.user_service import CurrencyChangeResult

logger = logging.getLogger(__name__)

# The ConversationHandler below mixes CallbackQueryHandler (currency buttons)
# with a MessageHandler (typed custom currency codes). PTB warns that with
# per_message=False callbacks "will not be tracked for every message" — that
# is intentional here: per_message=True would key the conversation on the
# button message, so the user's follow-up text message (a *new* message)
# would never reach CUSTOM_CURRENCY. The conversation is still correctly
# scoped per user+chat, so the warning is safe to silence.
warnings.filterwarnings(
    "ignore",
    message=".*per_message.*",
    category=PTBUserWarning,
)

# Conversation states
CHOOSE_CURRENCY, CUSTOM_CURRENCY = range(2)

# Abandoned conversations (e.g. "Other..." then silence) expire after this
# many seconds, so later free text is no longer read as a currency code.
CONVERSATION_TIMEOUT_SECONDS = 300

# Only new messages — edited messages carry update.message=None.
_NEW_MESSAGE = filters.UpdateType.MESSAGE

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

    if not is_supported_currency(choice):
        await query.edit_message_text("❌ Unknown currency. Send /settings to try again.")
        return ConversationHandler.END

    # Save the chosen currency
    return await _save_currency_and_finish(query, context, choice)


async def custom_currency_entered(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle free-text currency code input."""
    code = update.message.text.strip().upper()

    if not is_supported_currency(code):
        await update.message.reply_text(
            f"❌ I can't track <b>{esc(code[:10])}</b>. Please enter a supported "
            "3-letter currency code (e.g. <b>CAD</b>, <b>JPY</b>, <b>SGD</b>), "
            "or /skip to keep your current one:",
            parse_mode="HTML",
        )
        return CUSTOM_CURRENCY

    text = await _apply_currency(update.effective_user.id, code)
    await update.message.reply_text(text, parse_mode="HTML")
    return ConversationHandler.END


async def _save_currency_and_finish(query, context, code: str) -> int:
    """Save currency preference and send the finishing message."""
    text = await _apply_currency(query.from_user.id, code)
    await query.edit_message_text(text, parse_mode="HTML")
    return ConversationHandler.END


async def _apply_currency(user_id: int, code: str) -> str:
    """Switch the user's currency (converting their data); return the reply HTML."""
    async with AsyncSessionLocal() as db:
        try:
            result = await user_service.change_currency(db, user_id, code)
        except CurrencyConversionError:
            logger.warning("Currency change to %s failed for user %d: no FX rate", code, user_id)
            return (
                "❌ I couldn't get exchange rates to convert your existing expenses to "
                f"<b>{code}</b> right now, so nothing was changed. "
                "Please try /settings again later."
            )
        except Exception:
            logger.exception("Currency change to %s failed for user %d", code, user_id)
            return (
                "❌ Something went wrong converting your expenses, so nothing was "
                "changed. Please try /settings again later."
            )
        await user_service.mark_onboarding_complete(db, user_id)

    return _finish_message(code, result)


def _conversion_note(result: CurrencyChangeResult) -> str:
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


def _finish_message(code: str, result: CurrencyChangeResult | None = None) -> str:
    """Build the onboarding completion message."""
    flags = {
        "INR": "🇮🇳", "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧",
        "JPY": "🇯🇵", "CAD": "🇨🇦", "AUD": "🇦🇺", "CHF": "🇨🇭",
    }
    flag = flags.get(code, "🌍")
    note = _conversion_note(result) if result else ""

    return (
        f"✅ Your default currency is now <b>{code}</b> {flag}\n\n"
        f"{note}"
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
        user = await user_service.get_user(db, user_id)

    current = user.preferred_currency if user else settings.default_currency
    await update.message.reply_text(
        _finish_message(current),
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

    current = user.preferred_currency if user else settings.default_currency

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


async def expired_currency_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Answer currency buttons pressed after their conversation has ended."""
    await update.callback_query.answer(
        "This menu has expired — send /settings to change your currency.",
        show_alert=True,
    )


# ---------------------------------------------------------------------------
# Build the ConversationHandler
# ---------------------------------------------------------------------------

def build_onboarding_handler() -> ConversationHandler:
    """Create the ConversationHandler for /start onboarding."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start_entry, filters=_NEW_MESSAGE),
            CommandHandler("settings", settings_handler, filters=_NEW_MESSAGE),
        ],
        states={
            CHOOSE_CURRENCY: [
                CallbackQueryHandler(
                    currency_chosen, pattern=r"^currency:"
                ),
            ],
            CUSTOM_CURRENCY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & _NEW_MESSAGE,
                    custom_currency_entered,
                ),
            ],
        },
        fallbacks=[
            CommandHandler("skip", skip_onboarding, filters=_NEW_MESSAGE),
        ],
        # /start and /settings always restart the flow, even mid-conversation
        allow_reentry=True,
        conversation_timeout=CONVERSATION_TIMEOUT_SECONDS,
        # Deliberately False — see the module-level note about why
        # per_message=True would break the typed-currency step.
        per_message=False,
    )
