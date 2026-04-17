"""Telegram bot handlers — commands and free-text message processing."""

import io
import logging

from telegram import Update
from telegram.ext import ContextTypes
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import AsyncSessionLocal
from app.nlp.parser import NLPParser
from app.schemas import DeleteIntent, LogExpenseIntent, QueryIntent, UnknownIntent
from app.services import expense_service, user_service
from app.reports import charts, tables

logger = logging.getLogger(__name__)

# Shared NLP parser instance
nlp_parser = NLPParser(api_key=settings.groq_api_key, model=settings.groq_model)


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


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message explaining bot usage."""
    await _ensure_user(update)
    welcome = (
        "👋 *Hey there!* I'm your personal expense tracker\\.\n\n"
        "Just tell me what you spent in plain language:\n"
        '• _"spent ₹500 on groceries"_\n'
        '• _"₹200 cab yesterday"_\n'
        '• _"lunch 150"_\n\n'
        "📊 *Commands:*\n"
        "/summary — Monthly category breakdown \\(chart \\+ table\\)\n"
        "/report — Spending trend over time\n"
        "/delete — Remove your last expense\n\n"
        "You can also ask me things like:\n"
        '• _"how much this week?"_\n'
        '• _"show my top categories"_\n'
        '• _"last 5 expenses"_'
    )
    await update.message.reply_text(welcome, parse_mode="MarkdownV2")


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

async def summary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send monthly category breakdown as chart + text table."""
    await _ensure_user(update)
    user_id = update.effective_user.id

    try:
        async with AsyncSessionLocal() as db:
            start, end = expense_service.resolve_date_range("this_month")
            data = await expense_service.get_by_category(db, user_id, start, end)
            total = await expense_service.get_total(db, user_id, start, end)

        period_label = _get_month_label()

        if not data:
            await update.message.reply_text(
                f"📊 {period_label}\n\nNo expenses recorded yet. "
                "Send me something like \"spent ₹200 on food\" to get started!"
            )
            return

        # Generate chart in threadpool (matplotlib is not async-safe)
        chart_bytes = await run_in_threadpool(
            charts.generate_category_bar_chart, data, period_label
        )

        # Generate text table
        table_text = tables.format_summary_table(data, total, period_label)

        # Send both
        await _send_photo_bytes(update, context, chart_bytes)
        await update.message.reply_text(f"```\n{table_text}\n```", parse_mode="MarkdownV2")

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
            charts.generate_trend_line_chart, data, period_label
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

    try:
        async with AsyncSessionLocal() as db:
            deleted = await expense_service.delete_last_expense(db, user_id)

        if deleted is None:
            await update.message.reply_text("🤷 No expenses to delete.")
        else:
            date_str = deleted.date.strftime("%b %d")
            await update.message.reply_text(
                f"🗑️ Deleted: ₹{deleted.amount:,.2f} for {deleted.category} on {date_str}"
            )

    except Exception:
        logger.exception("Error in /delete handler")
        await update.message.reply_text("❌ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# Free-text message handler (the main brain)
# ---------------------------------------------------------------------------

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parse natural language and route to the appropriate action."""
    await _ensure_user(update)
    user_id = update.effective_user.id
    text = update.message.text

    try:
        intent = await nlp_parser.parse(text)

        # --- Log expense ---
        if isinstance(intent, LogExpenseIntent):
            async with AsyncSessionLocal() as db:
                expense = await expense_service.add_expense(
                    db,
                    user_id=user_id,
                    amount=intent.amount,
                    category=intent.category,
                    expense_date=intent.date,
                    currency=intent.currency,
                    description=intent.description,
                )
            date_str = expense.date.strftime("%b %d")
            desc = f" — {expense.description}" if expense.description else ""
            emoji = tables._get_emoji(expense.category)
            await update.message.reply_text(
                f"✅ Logged ₹{expense.amount:,.2f} for {emoji} {expense.category} on {date_str}{desc}"
            )

        # --- Query ---
        elif isinstance(intent, QueryIntent):
            await _handle_query(update, context, user_id, intent)

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
                    f"🗑️ Deleted: ₹{deleted.amount:,.2f} for {deleted.category} on {date_str}"
                )

        # --- Unknown ---
        elif isinstance(intent, UnknownIntent):
            await update.message.reply_text(
                "🤔 I didn't understand that. Try something like:\n"
                "• \"spent ₹200 on lunch\"\n"
                "• \"how much this week?\"\n"
                "• \"show my top categories\""
            )

    except Exception:
        logger.exception("Error handling message: %s", text)
        await update.message.reply_text("❌ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# Query intent sub-router
# ---------------------------------------------------------------------------

async def _handle_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    intent: QueryIntent,
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
                f"{emoji} {intent.category.title()} — {period_label}: ₹{total:,.2f}"
            )
            return

        # If asking for recent expenses list
        if intent.limit:
            expenses = await expense_service.get_recent(db, user_id, intent.limit)
            text = tables.format_recent_expenses(expenses)
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
                charts.generate_category_bar_chart, data, period_label
            )
            table_text = tables.format_summary_table(data, total, period_label)
            await _send_photo_bytes(update, context, chart_bytes)
            await update.message.reply_text(f"```\n{table_text}\n```", parse_mode="MarkdownV2")
            return

        # Group by day → trend line chart
        if intent.group_by == "day":
            data = await expense_service.get_daily_trend(db, user_id, start, end)

            if not data:
                await update.message.reply_text(f"📈 {period_label}: No expenses found.")
                return

            chart_bytes = await run_in_threadpool(
                charts.generate_trend_line_chart, data, period_label
            )
            await _send_photo_bytes(update, context, chart_bytes, caption=f"📈 Spending Trend — {period_label}")
            return

        # Default: show total
        total = await expense_service.get_total(db, user_id, start, end)
        await update.message.reply_text(f"💰 {period_label}: ₹{total:,.2f}")
