"""Telegram bot command handlers."""

import io
import logging

from pydantic import ValidationError
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.handlers.common import (
    ensure_user,
    esc,
    format_amount,
    get_preferred_currency,
    render_chart,
    reply_chunked,
    send_photo_bytes,
)
from app.bot.handlers.keyboards import build_cancel_recurring_keyboard
from app.bot.ratelimit import user_rate_limiter
from app.database import AsyncSessionLocal
from app.logging_setup import summarize
from app.reports import charts, tables
from app.schemas import BudgetArgs
from app.services import (
    budget_service,
    expense_service,
    export_service,
    recurring_service,
)
from app.timeutils import today

logger = logging.getLogger(__name__)

RATE_LIMIT_MESSAGE = "⏳ You're sending requests too fast. Please slow down a bit!"

EXPORT_PERIODS = {
    "": "this_month",
    "month": "this_month",
    "all": "all_time",
    "all_time": "all_time",
    "week": "this_week",
    "today": "today",
}

EXPORT_USAGE = (
    "Usage: /export [period]\n"
    "• /export — this month\n"
    "• /export week — this week\n"
    "• /export today — today\n"
    "• /export all — everything"
)


def _get_month_label() -> str:
    """Return a human-readable label like 'April 2026'."""
    return today().strftime("%B %Y")


def _export_filename(period: str) -> str:
    """Build a CSV filename that matches the exported period."""
    day = today()
    match period:
        case "all_time":
            return "expenses_all_time.csv"
        case "this_week":
            return f"expenses_week_{day.isoformat()}.csv"
        case "today":
            return f"expenses_{day.isoformat()}.csv"
        case _:
            return f"expenses_{day.strftime('%Y-%m')}.csv"


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

async def summary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send monthly category breakdown as chart + text table."""
    await ensure_user(update)
    user_id = update.effective_user.id
    if not user_rate_limiter.check(user_id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    pref_currency = await get_preferred_currency(user_id)

    try:
        async with AsyncSessionLocal() as db:
            start, end = expense_service.resolve_date_range("this_month")
            data = await expense_service.get_by_category(db, user_id, start, end)
            total = await expense_service.get_total(db, user_id, start, end)

        period_label = _get_month_label()

        if not data:
            await update.message.reply_text(
                f"📊 {period_label}\n\nNo expenses recorded yet. "
                'Send me something like "spent 200 on food" to get started!'
            )
            return

        chart_bytes = await render_chart(
            charts.generate_category_bar_chart, data, period_label, pref_currency
        )
        table_text = tables.format_summary_table(data, total, period_label, pref_currency)

        await send_photo_bytes(update, context, chart_bytes)
        await reply_chunked(update.message, f"<pre>{esc(table_text)}</pre>", parse_mode="HTML")

    except Exception:
        logger.exception("Error in /summary handler")
        await update.message.reply_text("❌ Something went wrong generating your summary. Please try again.")


# ---------------------------------------------------------------------------
# /report
# ---------------------------------------------------------------------------

async def report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send monthly spending trend as a line chart."""
    await ensure_user(update)
    user_id = update.effective_user.id
    if not user_rate_limiter.check(user_id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    pref_currency = await get_preferred_currency(user_id)

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

        chart_bytes = await render_chart(
            charts.generate_trend_line_chart, data, period_label, pref_currency
        )
        await send_photo_bytes(update, context, chart_bytes, caption=f"📈 Spending Trend — {period_label}")

    except Exception:
        logger.exception("Error in /report handler")
        await update.message.reply_text("❌ Something went wrong generating your report. Please try again.")


# ---------------------------------------------------------------------------
# /delete
# ---------------------------------------------------------------------------

async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete the user's most recent expense."""
    await ensure_user(update)
    user_id = update.effective_user.id
    pref_currency = await get_preferred_currency(user_id)

    try:
        async with AsyncSessionLocal() as db:
            deleted = await expense_service.delete_last_expense(db, user_id)

        if deleted is None:
            await update.message.reply_text("🤷 No expenses to delete.")
        else:
            date_str = deleted.date.strftime("%b %d")
            await update.message.reply_text(
                f"🗑️ Deleted: {format_amount(float(deleted.amount), pref_currency)} "
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
    await ensure_user(update)
    user_id = update.effective_user.id
    pref_currency = await get_preferred_currency(user_id)
    args = context.args

    try:
        if not args:
            # View all budgets (user total + categories)
            async with AsyncSessionLocal() as db:
                user_budget = await budget_service.get_budget(db, user_id)
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

                lines = ["📊 <b>Your Budgets</b>\n"]

                if user_budget:
                    user_budget_info = await budget_service.check_budget(db, user_id)
                    if user_budget_info:
                        spent = user_budget_info["spent"]
                        limit = user_budget_info["budget"]
                        pct = user_budget_info["percent"]
                        status_emoji = "🟢" if pct < 80 else "🟡" if pct < 100 else "🔴"
                        lines.append(
                            f"\n💰 <b>Total Monthly Budget</b>\n"
                            f"{status_emoji} {format_amount(spent, pref_currency)} / "
                            f"{format_amount(limit, pref_currency)} ({pct}%)"
                        )
                    else:
                        lines.append(
                            f"\n💰 <b>Total Monthly Budget</b>: "
                            f"{format_amount(float(user_budget.monthly_limit), pref_currency)}"
                        )

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
                                f"{emoji} {esc(b.category.title())}: "
                                f"{status_emoji} {format_amount(spent, pref_currency)} / "
                                f"{format_amount(limit, pref_currency)} ({pct}%)"
                            )
                        else:
                            emoji = tables._get_emoji(b.category)
                            lines.append(
                                f"{emoji} {esc(b.category.title())}: "
                                f"{format_amount(float(b.monthly_limit), pref_currency)}"
                            )

                await reply_chunked(
                    update.message, "\n".join(lines), parse_mode="HTML"
                )
            return

        try:
            parsed = BudgetArgs.model_validate(args)
        except ValidationError as exc:
            # ctx.error holds the original ValueError object — stringify it.
            ctx = exc.errors()[0].get("ctx") or {}
            error_msg = str(ctx.get("error") or "Invalid budget arguments")
            logger.info("/budget rejected: %s", summarize(error_msg))
            await update.message.reply_text("❌ " + error_msg)
            return
        logger.info("/budget parsed: %s", summarize(parsed))

        async with AsyncSessionLocal() as db:
            if parsed.category is not None:
                await budget_service.set_budget(db, user_id, parsed.amount, category=parsed.category)
                emoji = tables._get_emoji(parsed.category)
                await update.message.reply_text(
                    f"✅ Budget set: {emoji} {parsed.category.title()} — "
                    f"{format_amount(parsed.amount, pref_currency)}/month"
                )
            else:
                await budget_service.set_budget(db, user_id, parsed.amount, category=None)
                await update.message.reply_text(
                    f"✅ Total monthly budget set: {format_amount(parsed.amount, pref_currency)}"
                )

    except Exception:
        logger.exception("Error in /budget handler")
        await update.message.reply_text("❌ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# /recurring
# ---------------------------------------------------------------------------

async def recurring_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List active recurring expenses with cancel buttons."""
    await ensure_user(update)
    user_id = update.effective_user.id
    pref_currency = await get_preferred_currency(user_id)

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
        for i, entry in enumerate(entries, 1):
            emoji = tables._get_emoji(entry.category)
            desc = f" — {esc(entry.description)}" if entry.description else ""
            next_date = entry.next_run_date.strftime("%b %d")
            lines.append(
                f"{i}. {emoji} {esc(entry.category.title())} · "
                f"{format_amount(float(entry.amount), pref_currency)}{desc}\n"
                f"   Next: {next_date} · Monthly"
            )

        reply_markup = build_cancel_recurring_keyboard(entries)

        await reply_chunked(
            update.message,
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
    await ensure_user(update)
    user_id = update.effective_user.id
    if not user_rate_limiter.check(user_id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    pref_currency = await get_preferred_currency(user_id)
    args = context.args

    try:
        arg = args[0].lower() if args else ""
        period = EXPORT_PERIODS.get(arg)
        if period is None:
            await update.message.reply_text(EXPORT_USAGE)
            return

        start, end = expense_service.resolve_date_range(period)
        logger.info("/export period=%s range %s..%s", period, start, end)

        async with AsyncSessionLocal() as db:
            csv_bytes, count = await export_service.generate_csv(db, user_id, start, end)
            total = await expense_service.get_total(db, user_id, start, end)

        if count == 0:
            await update.message.reply_text("📎 No expenses found for this period.")
            return

        buf = io.BytesIO(csv_bytes)
        buf.name = _export_filename(period)

        await update.message.reply_document(
            document=buf,
            caption=(
                f"📎 Exported {count} expense{'s' if count != 1 else ''} "
                f"({format_amount(total, pref_currency)} total)"
            ),
        )

    except Exception:
        logger.exception("Error in /export handler")
        await update.message.reply_text("❌ Something went wrong generating your export. Please try again.")
