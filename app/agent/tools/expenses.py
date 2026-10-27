"""Expense tools: log, list and delete expenses."""

import logging

from app.agent.outputs import TextOut
from app.agent.registry import ToolContext, ToolResult, tool
from app.database import AsyncSessionLocal
from app.formatting import esc, format_amount, recurring_schedule_text
from app.logging_setup import summarize
from app.reports import tables
from app.schemas import (
    DeleteExpenseArgs,
    ListExpensesArgs,
    LogExpenseArgs,
    is_storable_amount,
)
from app.services import budget_service, expense_service, recurring_service
from app.services.currency_service import (
    CurrencyConversionError,
    currency_service,
    is_supported_currency,
)
from app.timeutils import today

logger = logging.getLogger(__name__)


def _not_saved(html: str, reason: str) -> ToolResult:
    """A log_expense call that saved nothing; the message already tells the user why."""
    return ToolResult(
        data={"status": "not saved", "reason": reason}, outputs=[TextOut(html)], final=True
    )


@tool(
    "log_expense",
    "Record money the user spent. Also sets it up as recurring when the user "
    "says it repeats. Shows the user a confirmation with any budget alerts.",
    LogExpenseArgs,
    writes=True,
)
async def log_expense(ctx: ToolContext, args: LogExpenseArgs) -> ToolResult:
    pref_currency = ctx.pref_currency
    stated_currency = args.currency or pref_currency
    amount = args.amount
    original_amount = None
    original_currency = None

    # Currency conversion if needed — never store an unconverted amount
    if stated_currency != pref_currency:
        if not is_supported_currency(stated_currency):
            logger.warning("not saved: unsupported currency %s", summarize(stated_currency))
            return _not_saved(
                f"❌ I can't convert {esc(stated_currency)} to {pref_currency}, "
                f"so nothing was saved. Please log it in {pref_currency}.",
                "unsupported currency",
            )
        try:
            converted, rate = await currency_service.convert(
                amount, stated_currency, pref_currency
            )
        except CurrencyConversionError:
            logger.warning("not saved: no FX rate %s→%s", stated_currency, pref_currency)
            return _not_saved(
                f"❌ Couldn't convert {stated_currency} → {pref_currency} right now, "
                f"so nothing was saved. Try again in a minute, or log it in {pref_currency}.",
                "exchange rate unavailable",
            )
        if not is_storable_amount(converted):
            return _not_saved(
                f"❌ That converts to {format_amount(converted, pref_currency)}, which is "
                "outside the range I can store. Nothing was saved.",
                "converted amount out of range",
            )
        logger.info(
            "converted %s %s -> %s %s (rate %s)",
            amount, stated_currency, converted, pref_currency, rate,
        )
        original_amount = amount
        original_currency = stated_currency
        amount = converted

    async with AsyncSessionLocal() as db:
        expense = await expense_service.add_expense(
            db,
            user_id=ctx.user_id,
            amount=amount,
            category=args.category,
            expense_date=args.date or today(),
            currency=pref_currency,
            description=args.description,
            original_amount=original_amount,
            original_currency=original_currency,
        )

        # The expense is committed from here on, so later failures must not
        # surface as "something went wrong" (the user would retry and log it twice).
        date_str = expense.date.strftime("%b %d")
        desc = f" — {esc(expense.description)}" if expense.description else ""
        emoji = tables._get_emoji(expense.category)
        category_label = esc(expense.category.title())
        amount_text = format_amount(float(expense.amount), pref_currency)

        if original_currency:
            orig_formatted = format_amount(original_amount, original_currency)
            parts = [
                (
                    f"✅ Logged {orig_formatted} (≈ {amount_text}) for {emoji} "
                    f"{category_label} on {date_str}{desc}"
                )
            ]
        else:
            parts = [f"✅ Logged {amount_text} for {emoji} {category_label} on {date_str}{desc}"]

        data = {
            "status": "logged",
            "expense_id": expense.id,
            "amount": amount_text,
            "category": expense.category,
            "date": expense.date.isoformat(),
        }

        if args.recurring_frequency:
            try:
                recurring = await recurring_service.create_recurring(
                    db,
                    user_id=ctx.user_id,
                    amount=float(expense.amount),
                    category=expense.category,
                    description=expense.description,
                    frequency=args.recurring_frequency,
                    start_date=expense.date,
                )
                schedule = recurring_schedule_text(
                    recurring.frequency, recurring.day_of_month, recurring.next_run_date
                )
                parts.append(f"🔄 Marked as recurring! I'll auto-log this {schedule}.")
                data["recurring_id"] = recurring.id
            except Exception:
                logger.exception("Failed to create recurring expense for user_id=%s", ctx.user_id)
                await db.rollback()
                parts.append("⚠️ The expense was saved, but I couldn't set it up as recurring.")
                data["recurring"] = "failed to set up"

        try:
            parts.extend(
                await _budget_alert_lines(db, ctx.user_id, expense.category, pref_currency)
            )
        except Exception:
            logger.exception("Failed to check budgets for user_id=%s", ctx.user_id)
            await db.rollback()
            parts.append("⚠️ The expense was saved, but I couldn't check your budgets right now.")

    return ToolResult(data=data, outputs=[TextOut("\n".join(parts))], final=True, wrote=True)


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
    user_budget_info = await budget_service.check_budget(db, user_id)

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


@tool(
    "delete_expense",
    "Delete one expense: the given expense_id, or the most recently logged one "
    "if expense_id is omitted. Shows the user what was deleted.",
    DeleteExpenseArgs,
    writes=True,
)
async def delete_expense(ctx: ToolContext, args: DeleteExpenseArgs) -> ToolResult:
    async with AsyncSessionLocal() as db:
        if args.expense_id is not None:
            deleted = await expense_service.delete_expense_by_id(db, ctx.user_id, args.expense_id)
        else:
            deleted = await expense_service.delete_last_expense(db, ctx.user_id)

    if deleted is None:
        return ToolResult(
            data={"status": "not found"},
            outputs=[TextOut("🤷 No matching expense found to delete.")],
            final=True,
        )

    date_str = deleted.date.strftime("%b %d")
    return ToolResult(
        data={"status": "deleted", "expense_id": deleted.id},
        outputs=[
            TextOut(
                f"🗑️ Deleted: {format_amount(float(deleted.amount), deleted.currency)} "
                f"for {esc(deleted.category)} on {date_str}"
            )
        ],
        final=True,
        wrote=True,
    )


@tool(
    "list_expenses",
    "The user's most recent expenses, newest first, with their ids. Use "
    "show=false to only look up an id for a follow-up action.",
    ListExpensesArgs,
)
async def list_expenses(ctx: ToolContext, args: ListExpensesArgs) -> ToolResult:
    async with AsyncSessionLocal() as db:
        expenses = await expense_service.get_recent(
            db, ctx.user_id, args.limit, category=args.category
        )

    data = {
        "expenses": [
            {
                "id": e.id,
                "date": e.date.isoformat(),
                "category": e.category,
                "amount": format_amount(float(e.amount), ctx.pref_currency),
                "description": e.description,
            }
            for e in expenses
        ]
    }
    if not args.show:
        return ToolResult(data=data)
    text = tables.format_recent_expenses(expenses, ctx.pref_currency)
    return ToolResult(data=data, outputs=[TextOut(esc(text))], final=True)
