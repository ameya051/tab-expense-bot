"""Telegram bot handlers — commands and free-text message processing."""

import io
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import AsyncSessionLocal
from app.nlp.parser import NLPParser
from app.nlp.transcriber import VoiceTranscriber
from app.schemas import DeleteIntent, LogExpenseIntent, QueryIntent, UnknownIntent
from app.services import expense_service, user_service, recurring_service, budget_service, export_service
from app.services.currency_service import currency_service, get_currency_symbol
from app.reports import charts, tables

logger = logging.getLogger(__name__)

# Shared NLP parser and voice transcriber instances
nlp_parser = NLPParser(api_key=settings.groq_api_key, model=settings.groq_model)
voice_transcriber = VoiceTranscriber(
    api_key=settings.groq_api_key, model=settings.whisper_model
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


async def _get_preferred_currency(user_id: int) -> str:
    """Fetch the user's preferred currency, defaulting to INR."""
    async with AsyncSessionLocal() as db:
        user = await user_service.get_user(db, user_id)
    return user.preferred_currency if user else settings.default_currency


def _get_month_label() -> str:
    """Return a human-readable label like 'April 2026'."""
    from datetime import date
    return date.today().strftime("%B %Y")


async def _send_photo_bytes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    photo_bytes: bytes,
    caption: str | None = None,
) -> None:
    """Send a PNG image from bytes to the chat."""
    buf = io.BytesIO(photo_bytes)
    buf.name = "chart.png"
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=buf,
        caption=caption,
    )


def _format_amount(amount: float, currency: str) -> str:
    """Format an amount with its currency symbol."""
    symbol = get_currency_symbol(currency)
    return f"{symbol}{amount:,.2f}"


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

async def summary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send monthly category breakdown as chart + text table."""
    await _ensure_user(update)
    user_id = update.effective_user.id
    pref_currency = await _get_preferred_currency(user_id)

    try:
        async with AsyncSessionLocal() as db:
            start, end = expense_service.resolve_date_range("this_month")
            data = await expense_service.get_by_category(db, user_id, start, end)
            total = await expense_service.get_total(db, user_id, start, end)

        period_label = _get_month_label()

        if not data:
            await update.message.reply_text(
                f"📊 {period_label}\n\nNo expenses recorded yet. "
                "Send me something like \"spent 200 on food\" to get started!"
            )
            return

        # Generate chart in threadpool (matplotlib is not async-safe)
        chart_bytes = await run_in_threadpool(
            charts.generate_category_bar_chart, data, period_label, pref_currency
        )

        # Generate text table
        table_text = tables.format_summary_table(data, total, period_label, pref_currency)

        # Send both
        await _send_photo_bytes(update, context, chart_bytes)
        await update.message.reply_text(f"<pre>{table_text}</pre>", parse_mode="HTML")

    except Exception:
        logger.exception("Error in /summary handler")
        await update.message.reply_text("❌ Something went wrong generating your summary. Please try again.")


# ---------------------------------------------------------------------------
# /report
# ---------------------------------------------------------------------------

async def report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send monthly spending trend as a line chart."""
    await _ensure_user(update)
    user_id = update.effective_user.id
    pref_currency = await _get_preferred_currency(user_id)

    try:
        async with AsyncSessionLocal() as db:
            start, end = expense_service.resolve_date_range("this_month")
            data = await expense_service.get_daily_trend(db, user_id, start, end)

        period_label = _get_month_label()

        if not data:
            await update.message.reply_text(
                f"📈 {period_label}\n\nNo expenses recorded yet. "
                "Start logging to see your spending trend!"
            )
            return

        chart_bytes = await run_in_threadpool(
            charts.generate_trend_line_chart, data, period_label, pref_currency
        )
        await _send_photo_bytes(update, context, chart_bytes, caption=f"📈 Spending Trend — {period_label}")

    except Exception:
        logger.exception("Error in /report handler")
        await update.message.reply_text("❌ Something went wrong generating your report. Please try again.")


# ---------------------------------------------------------------------------
# /delete
# ---------------------------------------------------------------------------

async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete the user's most recent expense."""
    await _ensure_user(update)
    user_id = update.effective_user.id
    pref_currency = await _get_preferred_currency(user_id)

    try:
        async with AsyncSessionLocal() as db:
            deleted = await expense_service.delete_last_expense(db, user_id)

        if deleted is None:
            await update.message.reply_text("🤷 No expenses to delete.")
        else:
            date_str = deleted.date.strftime("%b %d")
            await update.message.reply_text(
                f"🗑️ Deleted: {_format_amount(float(deleted.amount), pref_currency)} "
                f"for {deleted.category} on {date_str}"
            )

    except Exception:
        logger.exception("Error in /delete handler")
        await update.message.reply_text("❌ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# /budget
# ---------------------------------------------------------------------------

async def budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set or view budgets - user-level total or category-specific."""
    await _ensure_user(update)
    user_id = update.effective_user.id
    pref_currency = await _get_preferred_currency(user_id)
    args = context.args

    try:
        if args and len(args) >= 2:
            # Set category budget: /budget food 5000
            category = args[0].lower()
            # Validate category is not a number (to distinguish from user budget)
            try:
                float(category)
                # If it parses as a number, treat as user budget with invalid syntax
                await update.message.reply_text(
                    "❌ Usage: /budget <amount> — for user total budget\n"
                    "       /budget <category> <amount> — for category budget\n"
                    "Examples:\n"
                    "• /budget 20000 — set total monthly budget to 20000\n"
                    "• /budget food 5000 — set food budget to 5000"
                )
                return
            except ValueError:
                pass  # It's a valid category name

            try:
                limit = float(args[1])
            except ValueError:
                await update.message.reply_text(
                    "❌ Amount must be a number.\n"
                    "Example: /budget food 5000"
                )
                return

            async with AsyncSessionLocal() as db:
                await budget_service.set_budget(db, user_id, limit, category=category)

            emoji = tables._get_emoji(category)
            await update.message.reply_text(
                f"✅ Budget set: {emoji} {category.title()} — "
                f"{_format_amount(limit, pref_currency)}/month"
            )

        elif args and len(args) == 1:
            # Set user-level total budget: /budget 20000
            try:
                limit = float(args[0])
            except ValueError:
                await update.message.reply_text(
                    "❌ Amount must be a number.\n"
                    "Example: /budget 20000 — set total monthly budget"
                )
                return

            async with AsyncSessionLocal() as db:
                await budget_service.set_budget(db, user_id, limit, category=None)

            await update.message.reply_text(
                f"✅ Total monthly budget set: {_format_amount(limit, pref_currency)}"
            )

        else:
            # View all budgets (user total + categories)
            async with AsyncSessionLocal() as db:
                user_budget = await budget_service.get_user_budget(db, user_id)
                cat_budgets = await budget_service.get_budgets(db, user_id)

                if not user_budget and not cat_budgets:
                    await update.message.reply_text(
                        "📋 No budgets set yet.\n\n"
                        "Set budgets with:\n"
                        "• /budget <amount> — set total monthly budget\n"
                        "• /budget <category> <amount> — set category budget\n\n"
                        "Examples:\n"
                        "• /budget 20000\n"
                        "• /budget food 5000"
                    )
                    return

                # Build display text
                lines = ["📊 <b>Your Budgets</b>\n"]
                from datetime import date
                today = date.today()
                month_start = today.replace(day=1)

                # User-level total budget
                if user_budget:
                    user_budget_info = await budget_service.check_user_budget(db, user_id)
                    if user_budget_info:
                        spent = user_budget_info["spent"]
                        limit = user_budget_info["budget"]
                        pct = user_budget_info["percent"]
                        status_emoji = "🟢" if pct < 80 else "🟡" if pct < 100 else "🔴"
                        lines.append(
                            f"\n💰 <b>Total Monthly Budget</b>\n"
                            f"{status_emoji} {_format_amount(spent, pref_currency)} / "
                            f"{_format_amount(limit, pref_currency)} ({pct}%)"
                        )
                    else:
                        lines.append(
                            f"\n💰 <b>Total Monthly Budget</b>: "
                            f"{_format_amount(float(user_budget.monthly_limit), pref_currency)}"
                        )

                # Category budgets
                if cat_budgets:
                    lines.append("\n<b>Category Budgets</b>")
                    for b in cat_budgets:
                        result = await budget_service.check_budget(db, user_id, b.category)
                        if result:
                            spent = result["spent"]
                            limit = result["budget"]
                            pct = result["percent"]
                            status_emoji = "🟢" if pct < 80 else "🟡" if pct < 100 else "🔴"
                            emoji = tables._get_emoji(b.category)
                            lines.append(
                                f"{emoji} {b.category.title()}: "
                                f"{status_emoji} {_format_amount(spent, pref_currency)} / "
                                f"{_format_amount(limit, pref_currency)} ({pct}%)"
                            )
                        else:
                            emoji = tables._get_emoji(b.category)
                            lines.append(
                                f"{emoji} {b.category.title()}: "
                                f"{_format_amount(float(b.monthly_limit), pref_currency)}"
                            )

                await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    except Exception:
        logger.exception("Error in /budget handler")
        await update.message.reply_text("❌ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# /recurring
# ---------------------------------------------------------------------------

async def recurring_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List active recurring expenses with cancel buttons."""
    await _ensure_user(update)
    user_id = update.effective_user.id
    pref_currency = await _get_preferred_currency(user_id)

    try:
        async with AsyncSessionLocal() as db:
            entries = await recurring_service.get_user_recurring(db, user_id)

        if not entries:
            await update.message.reply_text(
                "🔄 No recurring expenses set.\n\n"
                "To create one, log an expense and mention it's recurring:\n"
                "• <i>spent 500 on Netflix, it's a monthly subscription</i>",
                parse_mode="HTML",
            )
            return

        lines = ["🔄 <b>Your Recurring Expenses</b>\n"]
        buttons = []
        for i, entry in enumerate(entries, 1):
            emoji = tables._get_emoji(entry.category)
            desc = f" — {entry.description}" if entry.description else ""
            next_date = entry.next_run_date.strftime("%b %d")
            lines.append(
                f"{i}. {emoji} {entry.category.title()} · "
                f"{_format_amount(float(entry.amount), pref_currency)}{desc}\n"
                f"   Next: {next_date} · Monthly"
            )
            buttons.append(
                InlineKeyboardButton(
                    f"❌ Cancel #{i}",
                    callback_data=f"cancel_recurring:{entry.id}",
                )
            )

        # Arrange cancel buttons in rows of 2
        keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    except Exception:
        logger.exception("Error in /recurring handler")
        await update.message.reply_text("❌ Something went wrong. Please try again.")


async def cancel_recurring_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle cancel recurring expense button press."""
    query = update.callback_query
    await query.answer()

    recurring_id = int(query.data.replace("cancel_recurring:", ""))
    user_id = query.from_user.id

    try:
        async with AsyncSessionLocal() as db:
            cancelled = await recurring_service.cancel_recurring(db, user_id, recurring_id)

        if cancelled:
            emoji = tables._get_emoji(cancelled.category)
            desc = f" — {cancelled.description}" if cancelled.description else ""
            await query.edit_message_text(
                f"✅ Cancelled recurring: {emoji} {cancelled.category.title()}{desc}\n\n"
                "Use /recurring to see your remaining recurring expenses."
            )
        else:
            await query.edit_message_text("❌ Recurring expense not found or already cancelled.")

    except Exception:
        logger.exception("Error cancelling recurring expense #%d", recurring_id)
        await query.edit_message_text("❌ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# /export
# ---------------------------------------------------------------------------

async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export expenses as a CSV document."""
    await _ensure_user(update)
    user_id = update.effective_user.id
    pref_currency = await _get_preferred_currency(user_id)
    args = context.args

    try:
        # Parse period argument
        period = "this_month"
        if args:
            arg = args[0].lower()
            if arg in ("all", "all_time"):
                period = "all_time"
            elif arg == "week":
                period = "this_week"
            elif arg == "today":
                period = "today"

        start, end = expense_service.resolve_date_range(period)

        async with AsyncSessionLocal() as db:
            csv_bytes, count = await export_service.generate_csv(
                db, user_id, start, end
            )
            total = await expense_service.get_total(db, user_id, start, end)

        if count == 0:
            await update.message.reply_text("📎 No expenses found for this period.")
            return

        # Build filename
        from datetime import date
        filename = f"expenses_{date.today().strftime('%Y-%m')}.csv"
        if period == "all_time":
            filename = "expenses_all_time.csv"

        # Send CSV as document
        buf = io.BytesIO(csv_bytes)
        buf.name = filename

        await update.message.reply_document(
            document=buf,
            caption=(
                f"📎 Exported {count} expense{'s' if count != 1 else ''} "
                f"({_format_amount(total, pref_currency)} total)"
            ),
        )

    except Exception:
        logger.exception("Error in /export handler")
        await update.message.reply_text("❌ Something went wrong generating your export. Please try again.")


# ---------------------------------------------------------------------------
# Voice message handler
# ---------------------------------------------------------------------------

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe a voice message and process it as text."""
    await _ensure_user(update)
    user_id = update.effective_user.id

    try:
        # Download the voice file
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = bytes(await file.download_as_bytearray())

        # Transcribe
        text = await voice_transcriber.transcribe(audio_bytes)

        if not text:
            await update.message.reply_text(
                "🎙️ Sorry, I couldn't understand that voice message. "
                "Try again or type your expense instead."
            )
            return

        # Show what we heard
        heard_msg = f'🎙️ I heard: "<i>{text}</i>"\n\n'

        # Process through the regular NLP pipeline
        pref_currency = await _get_preferred_currency(user_id)
        intent = await nlp_parser.parse(text, default_currency=pref_currency)

        if isinstance(intent, LogExpenseIntent):
            response = await _handle_log_expense(update, context, user_id, intent, pref_currency)
            await update.message.reply_text(heard_msg + response, parse_mode="HTML")

        elif isinstance(intent, QueryIntent):
            # Send the "heard" message first, then handle query normally
            await update.message.reply_text(heard_msg, parse_mode="HTML")
            await _handle_query(update, context, user_id, intent, pref_currency)

        elif isinstance(intent, DeleteIntent):
            await update.message.reply_text(heard_msg, parse_mode="HTML")
            async with AsyncSessionLocal() as db:
                deleted = await expense_service.delete_last_expense(db, user_id)
            if deleted:
                date_str = deleted.date.strftime("%b %d")
                await update.message.reply_text(
                    f"🗑️ Deleted: {_format_amount(float(deleted.amount), pref_currency)} "
                    f"for {deleted.category} on {date_str}"
                )
            else:
                await update.message.reply_text("🤷 No expenses to delete.")

        else:
            await update.message.reply_text(
                heard_msg + "🤔 I didn't understand that. Try something like:\n"
                "• \"spent 200 on lunch\"\n"
                "• \"how much this week?\"",
                parse_mode="HTML",
            )

    except Exception:
        logger.exception("Error handling voice message")
        await update.message.reply_text("❌ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# Free-text message handler (the main brain)
# ---------------------------------------------------------------------------

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parse natural language and route to the appropriate action."""
    await _ensure_user(update)
    user_id = update.effective_user.id
    text = update.message.text
    pref_currency = await _get_preferred_currency(user_id)

    try:
        intent = await nlp_parser.parse(text, default_currency=pref_currency)

        # --- Log expense ---
        if isinstance(intent, LogExpenseIntent):
            response = await _handle_log_expense(update, context, user_id, intent, pref_currency)
            await update.message.reply_text(response, parse_mode="HTML")

        # --- Query ---
        elif isinstance(intent, QueryIntent):
            await _handle_query(update, context, user_id, intent, pref_currency)

        # --- Delete ---
        elif isinstance(intent, DeleteIntent):
            async with AsyncSessionLocal() as db:
                if intent.target == "last":
                    deleted = await expense_service.delete_last_expense(db, user_id)
                else:
                    try:
                        expense_id = int(intent.target)
                        deleted = await expense_service.delete_expense_by_id(db, user_id, expense_id)
                    except ValueError:
                        deleted = await expense_service.delete_last_expense(db, user_id)

            if deleted is None:
                await update.message.reply_text("🤷 No matching expense found to delete.")
            else:
                date_str = deleted.date.strftime("%b %d")
                await update.message.reply_text(
                    f"🗑️ Deleted: {_format_amount(float(deleted.amount), pref_currency)} "
                    f"for {deleted.category} on {date_str}"
                )

        # --- Unknown ---
        elif isinstance(intent, UnknownIntent):
            await update.message.reply_text(
                "🤔 I didn't understand that. Try something like:\n"
                "• \"spent 200 on lunch\"\n"
                "• \"how much this week?\"\n"
                "• \"show my top categories\""
            )

    except Exception:
        logger.exception("Error handling message: %s", text)
        await update.message.reply_text("❌ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# Log expense handler (shared between text and voice)
# ---------------------------------------------------------------------------

async def _handle_log_expense(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    intent: LogExpenseIntent,
    pref_currency: str,
) -> str:
    """Handle a log_expense intent — returns the response text."""

    stated_currency = intent.currency.upper()
    amount = intent.amount
    original_amount = None
    original_currency = None

    # Currency conversion if needed
    if stated_currency != pref_currency:
        try:
            converted, rate = await currency_service.convert(
                amount, stated_currency, pref_currency
            )
            original_amount = amount
            original_currency = stated_currency
            amount = converted
        except Exception:
            logger.warning(
                "FX conversion failed %s→%s, storing raw amount",
                stated_currency,
                pref_currency,
            )
            # Store as-is with a warning
            original_amount = intent.amount
            original_currency = stated_currency

    # Save the expense
    async with AsyncSessionLocal() as db:
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

    # Build confirmation message
    date_str = expense.date.strftime("%b %d")
    desc = f" — {expense.description}" if expense.description else ""
    emoji = tables._get_emoji(expense.category)

    if original_currency and original_currency != pref_currency:
        orig_formatted = _format_amount(original_amount, original_currency)
        conv_formatted = _format_amount(float(expense.amount), pref_currency)
        parts = [f"✅ Logged {orig_formatted} (≈ {conv_formatted}) for {emoji} {expense.category.title()} on {date_str}{desc}"]
    else:
        parts = [f"✅ Logged {_format_amount(float(expense.amount), pref_currency)} for {emoji} {expense.category.title()} on {date_str}{desc}"]

    # Handle recurring
    if intent.recurring:
        async with AsyncSessionLocal() as db:
            recurring = await recurring_service.create_recurring(
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
        parts.append(f"🔄 Marked as recurring! I'll auto-log this on the {day}{suffix} of every month.")

    # Check category budget alert
    async with AsyncSessionLocal() as db:
        budget_info = await budget_service.check_budget(db, user_id, expense.category)

    if budget_info and budget_info["alert_level"]:
        spent_fmt = _format_amount(budget_info["spent"], pref_currency)
        limit_fmt = _format_amount(budget_info["budget"], pref_currency)
        pct = budget_info["percent"]

        if budget_info["alert_level"] == "danger":
            parts.append(
                f"🚨 {expense.category.title()} budget EXCEEDED! "
                f"{spent_fmt}/{limit_fmt} ({pct}%)"
            )
        elif budget_info["alert_level"] == "warning":
            parts.append(
                f"⚠️ You've used {pct}% of your {expense.category.title()} budget "
                f"({spent_fmt}/{limit_fmt})"
            )
        elif budget_info["alert_level"] == "info":
            parts.append(
                f"ℹ️ {expense.category.title()} budget: {pct}% used "
                f"({spent_fmt}/{limit_fmt})"
            )

    # Check user-level total budget alert (only if category check didn't already trigger a danger alert)
    async with AsyncSessionLocal() as db:
        user_budget_info = await budget_service.check_user_budget(db, user_id)

    if user_budget_info and user_budget_info["alert_level"]:
        # Only show user total alert if it would add new information
        # (i.e., category budget didn't already show a danger alert)
        show_user_alert = True
        if budget_info and budget_info["alert_level"] == "danger":
            show_user_alert = False

        if show_user_alert:
            spent_fmt = _format_amount(user_budget_info["spent"], pref_currency)
            limit_fmt = _format_amount(user_budget_info["budget"], pref_currency)
            pct = user_budget_info["percent"]

            if user_budget_info["alert_level"] == "danger":
                parts.append(
                    f"🚨💰 TOTAL monthly budget EXCEEDED! "
                    f"{spent_fmt}/{limit_fmt} ({pct}%)"
                )
            elif user_budget_info["alert_level"] == "warning":
                parts.append(
                    f"⚠️💰 You've used {pct}% of your TOTAL monthly budget "
                    f"({spent_fmt}/{limit_fmt})"
                )
            elif user_budget_info["alert_level"] == "info":
                parts.append(
                    f"ℹ️💰 TOTAL monthly budget: {pct}% used "
                    f"({spent_fmt}/{limit_fmt})"
                )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Query intent sub-router
# ---------------------------------------------------------------------------

async def _handle_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    intent: QueryIntent,
    pref_currency: str,
) -> None:
    """Route a query intent to the right service call and format the response."""
    start, end = expense_service.resolve_date_range(intent.period)
    period_label = intent.period.replace("_", " ").title()

    async with AsyncSessionLocal() as db:
        # If asking about a specific category
        if intent.category:
            total = await expense_service.get_category_total(
                db, user_id, intent.category, start, end
            )
            emoji = tables._get_emoji(intent.category)
            await update.message.reply_text(
                f"{emoji} {intent.category.title()} — {period_label}: "
                f"{_format_amount(total, pref_currency)}"
            )
            return

        # If asking for recent expenses list
        if intent.limit:
            expenses = await expense_service.get_recent(db, user_id, intent.limit)
            text = tables.format_recent_expenses(expenses, pref_currency)
            await update.message.reply_text(text)
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
            await _send_photo_bytes(update, context, chart_bytes)
            await update.message.reply_text(f"<pre>{table_text}</pre>", parse_mode="HTML")
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
            await _send_photo_bytes(update, context, chart_bytes, caption=f"📈 Spending Trend — {period_label}")
            return

        # Default: show total
        total = await expense_service.get_total(db, user_id, start, end)
        await update.message.reply_text(
            f"💰 {period_label}: {_format_amount(total, pref_currency)}"
        )
