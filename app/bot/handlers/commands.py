"""Slash commands: shortcuts that run agent tools directly, without the LLM."""

import logging
from typing import Any

from pydantic import ValidationError
from telegram import Update
from telegram.ext import ContextTypes

from app.agent.outputs import TextOut
from app.agent.registry import ToolContext, execute_tool
from app.bot.handlers.common import ensure_user, get_preferred_currency
from app.bot.ratelimit import user_rate_limiter
from app.bot.render import edit_with_outputs, send_outputs
from app.logging_setup import summarize
from app.schemas import BudgetArgs

logger = logging.getLogger(__name__)

RATE_LIMIT_MESSAGE = "⏳ You're sending requests too fast. Please slow down a bit!"
SOMETHING_WRONG_MESSAGE = "❌ Something went wrong. Please try again."

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
    "• /export all — everything\n\n"
    'Or just ask, e.g. "export my expenses from September".'
)


async def _run_command_tool(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    name: str,
    args: dict[str, Any],
    *,
    rate_limited: bool = True,
) -> None:
    """Run one tool for a slash command and send its outputs."""
    await ensure_user(update)
    user_id = update.effective_user.id
    if rate_limited and not user_rate_limiter.check(user_id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    try:
        ctx = ToolContext(user_id=user_id, pref_currency=await get_preferred_currency(user_id))
        result = await execute_tool(name, args, ctx)
        await send_outputs(update.message, result.outputs or [TextOut(SOMETHING_WRONG_MESSAGE)])
    except Exception:
        logger.exception("Error in /%s (%s)", update.message.text.split()[0].lstrip("/"), name)
        await update.message.reply_text(SOMETHING_WRONG_MESSAGE)


async def summary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send this month's category breakdown as chart + text table."""
    await _run_command_tool(
        update, context, "show_chart", {"period": "this_month", "kind": "category"}
    )


async def report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send this month's spending trend as a line chart."""
    await _run_command_tool(
        update, context, "show_chart", {"period": "this_month", "kind": "trend"}
    )


async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete the user's most recent expense."""
    await _run_command_tool(update, context, "delete_expense", {}, rate_limited=False)


async def budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show budgets, or set a total (/budget 20000) or category (/budget food 5000) budget."""
    args = context.args
    if not args:
        await _run_command_tool(update, context, "get_budgets", {}, rate_limited=False)
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

    await _run_command_tool(
        update,
        context,
        "set_budget",
        {"amount": parsed.amount, "category": parsed.category},
        rate_limited=False,
    )


async def recurring_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List active recurring expenses with cancel buttons."""
    await _run_command_tool(update, context, "list_recurring", {}, rate_limited=False)


async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export expenses as a CSV document."""
    arg = context.args[0].lower() if context.args else ""
    period = EXPORT_PERIODS.get(arg)
    if period is None:
        await update.message.reply_text(EXPORT_USAGE)
        return
    await _run_command_tool(update, context, "export_csv", {"period": period})


async def cancel_recurring_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle cancel recurring expense button press."""
    query = update.callback_query
    await query.answer()

    recurring_id = int(query.data.replace("cancel_recurring:", ""))
    user_id = query.from_user.id

    try:
        ctx = ToolContext(user_id=user_id, pref_currency=await get_preferred_currency(user_id))
        result = await execute_tool("cancel_recurring", {"recurring_id": recurring_id}, ctx)
        await edit_with_outputs(query, result.outputs or [TextOut(SOMETHING_WRONG_MESSAGE)])
    except Exception:
        logger.exception("Error cancelling recurring expense #%d", recurring_id)
        await query.edit_message_text(SOMETHING_WRONG_MESSAGE)
