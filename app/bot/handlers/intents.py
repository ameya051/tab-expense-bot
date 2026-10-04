"""Intent → action router for free-text and voice messages."""

import logging

from starlette.concurrency import run_in_threadpool
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.handlers.common import (
    ensure_user,
    esc,
    format_amount,
    get_preferred_currency,
    reply_chunked,
    send_photo_bytes,
)
from app.bot.ratelimit import user_rate_limiter
from app.database import AsyncSessionLocal
from app.nlp.parser import AIUnavailableError, NLPParser
from app.nlp.transcriber import VoiceTranscriber
from app.reports import charts, tables
from app.schemas import (
    DeleteIntent,
    LogExpenseIntent,
    QueryIntent,
    is_storable_amount,
)
from app.services import budget_service, expense_service, recurring_service
from app.services.currency_service import (
    CurrencyConversionError,
    currency_service,
    is_supported_currency,
)

logger = logging.getLogger(__name__)

RATE_LIMIT_MESSAGE = "⏳ You're sending messages too fast. Please slow down a bit!"
AI_UNAVAILABLE_MESSAGE = "🤖 My AI service is temporarily unavailable. Please try again in a minute."
UNKNOWN_INTENT_MESSAGE = (
    "🤔 I didn't understand that. Try something like:\n"
    "• \"spent 200 on lunch\"\n"
    "• \"how much this week?\"\n"
    "• \"show my top categories\""
)


def _get_parser(context: ContextTypes.DEFAULT_TYPE) -> NLPParser:
    """Return the NLP parser stored in bot_data."""
    return context.bot_data["nlp_parser"]


def _get_transcriber(context: ContextTypes.DEFAULT_TYPE) -> VoiceTranscriber:
    """Return the voice transcriber stored in bot_data."""
    return context.bot_data["voice_transcriber"]


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parse natural language and route to the appropriate action."""
    await ensure_user(update)
    user_id = update.effective_user.id
    text = update.message.text

    if not user_rate_limiter.check(user_id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    try:
        pref_currency = await get_preferred_currency(user_id)
        await _dispatch_intent(update, context, user_id, text, pref_currency)

    except AIUnavailableError:
        logger.warning("AI unavailable for message from user_id=%s (len=%d)", user_id, len(text or ""))
        await update.message.reply_text(AI_UNAVAILABLE_MESSAGE)

    except Exception:
        logger.exception(
            "Error handling message from user_id=%s (len=%d)",
            user_id,
            len(text or ""),
        )
        await update.message.reply_text("❌ Something went wrong. Please try again.")


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe a voice message and process it as text."""
    await ensure_user(update)
    user_id = update.effective_user.id

    if not user_rate_limiter.check(user_id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    try:
        # Download the voice file
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = bytes(await file.download_as_bytearray())

        # Transcribe
        text = await _get_transcriber(context).transcribe(audio_bytes)

        if not text:
            await update.message.reply_text(
                "🎙️ Sorry, I couldn't understand that voice message. "
                "Try again or type your expense instead."
            )
            return

        # Show what we heard, then process through the regular NLP pipeline
        heard_msg = f'🎙️ I heard: "<i>{esc(text)}</i>"\n\n'
        pref_currency = await get_preferred_currency(user_id)
        await _dispatch_intent(
            update, context, user_id, text, pref_currency, prefix=heard_msg
        )

    except AIUnavailableError:
        logger.warning("AI unavailable for voice message from user_id=%s", user_id)
        await update.message.reply_text(AI_UNAVAILABLE_MESSAGE)

    except Exception:
        logger.exception("Error handling voice message")
        await update.message.reply_text("❌ Something went wrong. Please try again.")


async def _dispatch_intent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    text: str,
    pref_currency: str,
    prefix: str = "",
) -> None:
    """Parse text into an intent and run it, replying in HTML mode.

    prefix is prepended (as already-escaped HTML) to the reply, e.g. the
    "I heard: ..." line for voice messages.
    """
    intent = await _get_parser(context).parse(text, default_currency=pref_currency)
    message = update.message

    async with AsyncSessionLocal() as db:
        if isinstance(intent, LogExpenseIntent):
            response = await _handle_log_expense(db, user_id, intent, pref_currency)
            await message.reply_text(prefix + response, parse_mode="HTML")

        elif isinstance(intent, QueryIntent):
            if prefix:
                await message.reply_text(prefix, parse_mode="HTML")
            await _handle_query(update, context, db, user_id, intent, pref_currency)

        elif isinstance(intent, DeleteIntent):
            response = await _handle_delete(db, user_id, intent)
            await message.reply_text(prefix + response, parse_mode="HTML")

        else:
            await message.reply_text(prefix + esc(UNKNOWN_INTENT_MESSAGE), parse_mode="HTML")


async def _handle_delete(db, user_id: int, intent: DeleteIntent) -> str:
    """Delete the targeted (or last) expense — returns the HTML response text."""
    try:
        expense_id = int(intent.target)
    except ValueError:
        expense_id = None

    if expense_id is not None:
        deleted = await expense_service.delete_expense_by_id(db, user_id, expense_id)
    else:
        deleted = await expense_service.delete_last_expense(db, user_id)

    if deleted is None:
        return "🤷 No matching expense found to delete."

    date_str = deleted.date.strftime("%b %d")
    return (
        f"🗑️ Deleted: {format_amount(float(deleted.amount), deleted.currency)} "
        f"for {esc(deleted.category)} on {date_str}"
    )


async def _handle_log_expense(
    db,
    user_id: int,
    intent: LogExpenseIntent,
    pref_currency: str,
) -> str:
    """Handle a log_expense intent — returns the HTML response text."""

    stated_currency = intent.currency.strip().upper()
    amount = intent.amount
    original_amount = None
    original_currency = None

    # Currency conversion if needed — never store an unconverted amount
    if stated_currency != pref_currency:
        if not is_supported_currency(stated_currency):
            return (
                f"❌ I can't convert {esc(stated_currency)} to {pref_currency}, "
                f"so nothing was saved. Please log it in {pref_currency}."
            )
        try:
            converted, _rate = await currency_service.convert(
                amount, stated_currency, pref_currency
            )
        except CurrencyConversionError:
            return (
                f"❌ Couldn't convert {stated_currency} → {pref_currency} right now, "
                f"so nothing was saved. Try again in a minute, or log it in {pref_currency}."
            )
        if not is_storable_amount(converted):
            return (
                f"❌ That converts to {format_amount(converted, pref_currency)}, which is "
                "outside the range I can store. Nothing was saved."
            )
        original_amount = amount
        original_currency = stated_currency
        amount = converted

    # Save the expense
    expense = await expense_service.add_expense(
        db,
        user_id=user_id,
        amount=amount,
        category=intent.category,
        expense_date=intent.date,
        currency=pref_currency,
        description=intent.description,
        original_amount=original_amount,
        original_currency=original_currency,
    )

    # Build confirmation message. The expense is committed from here on, so
    # later failures must not surface as "something went wrong" (the user
    # would retry and log it twice).
    date_str = expense.date.strftime("%b %d")
    desc = f" — {esc(expense.description)}" if expense.description else ""
    emoji = tables._get_emoji(expense.category)
    category_label = esc(expense.category.title())

    if original_currency:
        orig_formatted = format_amount(original_amount, original_currency)
        conv_formatted = format_amount(float(expense.amount), pref_currency)
        parts = [
            (
                f"✅ Logged {orig_formatted} (≈ {conv_formatted}) for {emoji} "
                f"{category_label} on {date_str}{desc}"
            )
        ]
    else:
        parts = [
            (
                f"✅ Logged {format_amount(float(expense.amount), pref_currency)} for {emoji} "
                f"{category_label} on {date_str}{desc}"
            )
        ]

    # Handle recurring
    if intent.recurring:
        try:
            await recurring_service.create_recurring(
                db,
                user_id=user_id,
                amount=float(expense.amount),
                currency=pref_currency,
                category=expense.category,
                description=expense.description,
                day_of_month=expense.date.day,
            )
            day = expense.date.day
            suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
            parts.append(
                f"🔄 Marked as recurring! I'll auto-log this on the {day}{suffix} of every month."
            )
        except Exception:
            logger.exception("Failed to create recurring expense for user_id=%s", user_id)
            await db.rollback()
            parts.append("⚠️ The expense was saved, but I couldn't set it up as recurring.")

    try:
        parts.extend(await _budget_alert_lines(db, user_id, expense.category, pref_currency))
    except Exception:
        logger.exception("Failed to check budgets for user_id=%s", user_id)
        await db.rollback()
        parts.append("⚠️ The expense was saved, but I couldn't check your budgets right now.")

    return "\n".join(parts)


async def _budget_alert_lines(
    db, user_id: int, category: str, pref_currency: str
) -> list[str]:
    """Return budget alert lines (category + user total) after logging an expense."""
    lines: list[str] = []
    category_label = esc(category.title())

    # Check category budget alert
    budget_info = await budget_service.check_budget(db, user_id, category)

    if budget_info and budget_info["alert_level"]:
        spent_fmt = format_amount(budget_info["spent"], pref_currency)
        limit_fmt = format_amount(budget_info["budget"], pref_currency)
        pct = budget_info["percent"]

        if budget_info["alert_level"] == "danger":
            lines.append(
                f"🚨 {category_label} budget EXCEEDED! "
                f"{spent_fmt}/{limit_fmt} ({pct}%)"
            )
        elif budget_info["alert_level"] == "warning":
            lines.append(
                f"⚠️ You've used {pct}% of your {category_label} budget "
                f"({spent_fmt}/{limit_fmt})"
            )
        elif budget_info["alert_level"] == "info":
            lines.append(
                f"ℹ️ {category_label} budget: {pct}% used "
                f"({spent_fmt}/{limit_fmt})"
            )

    # Check user-level total budget alert
    user_budget_info = await budget_service.check_user_budget(db, user_id)

    if user_budget_info and user_budget_info["alert_level"]:
        # Only show user total alert if it would add new information
        # (i.e., category budget didn't already show a danger alert)
        show_user_alert = True
        if budget_info and budget_info["alert_level"] == "danger":
            show_user_alert = False

        if show_user_alert:
            spent_fmt = format_amount(user_budget_info["spent"], pref_currency)
            limit_fmt = format_amount(user_budget_info["budget"], pref_currency)
            pct = user_budget_info["percent"]

            if user_budget_info["alert_level"] == "danger":
                lines.append(
                    f"🚨💰 TOTAL monthly budget EXCEEDED! "
                    f"{spent_fmt}/{limit_fmt} ({pct}%)"
                )
            elif user_budget_info["alert_level"] == "warning":
                lines.append(
                    f"⚠️💰 You've used {pct}% of your TOTAL monthly budget "
                    f"({spent_fmt}/{limit_fmt})"
                )
            elif user_budget_info["alert_level"] == "info":
                lines.append(
                    f"ℹ️💰 TOTAL monthly budget: {pct}% used "
                    f"({spent_fmt}/{limit_fmt})"
                )

    return lines


async def _handle_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db,
    user_id: int,
    intent: QueryIntent,
    pref_currency: str,
) -> None:
    """Route a query intent to the right service call and format the response."""
    start, end = expense_service.resolve_date_range(intent.period)
    period_label = intent.period.replace("_", " ").title()

    # If asking about a specific category
    if intent.category:
        total = await expense_service.get_category_total(
            db, user_id, intent.category, start, end
        )
        emoji = tables._get_emoji(intent.category)
        await update.message.reply_text(
            f"{emoji} {intent.category.title()} — {period_label}: "
            f"{format_amount(total, pref_currency)}"
        )
        return

    # If asking for recent expenses list
    if intent.limit:
        expenses = await expense_service.get_recent(db, user_id, intent.limit)
        text = tables.format_recent_expenses(expenses, pref_currency)
        await reply_chunked(update.message, text)
        return

    # Group by category → bar chart + table
    if intent.group_by == "category":
        data = await expense_service.get_by_category(db, user_id, start, end)
        total = await expense_service.get_total(db, user_id, start, end)

        if not data:
            await update.message.reply_text(f"📊 {period_label}: No expenses found.")
            return

        chart_bytes = await run_in_threadpool(
            charts.generate_category_bar_chart, data, period_label, pref_currency
        )
        table_text = tables.format_summary_table(data, total, period_label, pref_currency)
        await send_photo_bytes(update, context, chart_bytes)
        await reply_chunked(update.message, f"<pre>{esc(table_text)}</pre>", parse_mode="HTML")
        return

    # Group by day → trend line chart
    if intent.group_by == "day":
        data = await expense_service.get_daily_trend(db, user_id, start, end)

        if not data:
            await update.message.reply_text(f"📈 {period_label}: No expenses found.")
            return

        chart_bytes = await run_in_threadpool(
            charts.generate_trend_line_chart, data, period_label, pref_currency
        )
        await send_photo_bytes(
            update, context, chart_bytes, caption=f"📈 Spending Trend — {period_label}"
        )
        return

    # Default: show total
    total = await expense_service.get_total(db, user_id, start, end)
    await update.message.reply_text(
        f"💰 {period_label}: {format_amount(total, pref_currency)}"
    )
