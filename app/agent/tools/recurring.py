"""Recurring expense tools: list and cancel. Creating one goes through log_expense."""

from app.agent.outputs import Button, TextOut
from app.agent.registry import ToolContext, ToolResult, tool
from app.database import AsyncSessionLocal
from app.formatting import esc, format_amount, recurring_schedule_text
from app.reports import tables
from app.schemas import CancelRecurringArgs, ShowArgs
from app.services import recurring_service

NO_RECURRING_TEXT = (
    "🔄 No recurring expenses set.\n\n"
    "To create one, log an expense and mention it's recurring:\n"
    "• <i>spent 500 on Netflix, it's a monthly subscription</i>\n"
    "• <i>1500 for the cleaner every week</i>\n"
    "• <i>200 on coffee every day</i>"
)


@tool(
    "list_recurring",
    "The user's active recurring expenses with their ids, shown with cancel "
    "buttons. Use show=false to only look up an id for a follow-up action.",
    ShowArgs,
)
async def list_recurring(ctx: ToolContext, args: ShowArgs) -> ToolResult:
    async with AsyncSessionLocal() as db:
        entries = await recurring_service.get_user_recurring(db, ctx.user_id)

    data = {
        "recurring": [
            {
                "id": e.id,
                "category": e.category,
                "description": e.description,
                "amount": format_amount(float(e.amount), ctx.pref_currency),
                "schedule": recurring_schedule_text(e.frequency, e.day_of_month, e.next_run_date),
                "next_run": e.next_run_date.isoformat(),
            }
            for e in entries
        ]
    }
    if not args.show:
        return ToolResult(data=data)
    if not entries:
        return ToolResult(data=data, outputs=[TextOut(NO_RECURRING_TEXT)], final=True)

    lines = ["🔄 <b>Your Recurring Expenses</b>\n"]
    for i, entry in enumerate(entries, 1):
        emoji = tables._get_emoji(entry.category)
        desc = f" — {esc(entry.description)}" if entry.description else ""
        next_date = entry.next_run_date.strftime("%b %d")
        schedule = recurring_schedule_text(
            entry.frequency, entry.day_of_month, entry.next_run_date
        )
        lines.append(
            f"{i}. {emoji} {esc(entry.category.title())} · "
            f"{format_amount(float(entry.amount), ctx.pref_currency)}{desc}\n"
            f"   Next: {next_date} · {schedule[0].upper()}{schedule[1:]}"
        )

    cancel_buttons = [
        Button(f"❌ Cancel #{i}", f"cancel_recurring:{entry.id}")
        for i, entry in enumerate(entries, 1)
    ]
    rows = [cancel_buttons[i:i + 2] for i in range(0, len(cancel_buttons), 2)]
    return ToolResult(data=data, outputs=[TextOut("\n".join(lines), buttons=rows)], final=True)


@tool(
    "cancel_recurring",
    "Stop a recurring expense from being logged again. Get the id from "
    "list_recurring first.",
    CancelRecurringArgs,
    writes=True,
)
async def cancel_recurring(ctx: ToolContext, args: CancelRecurringArgs) -> ToolResult:
    async with AsyncSessionLocal() as db:
        cancelled = await recurring_service.cancel_recurring(db, ctx.user_id, args.recurring_id)

    if cancelled is None:
        return ToolResult(
            data={"status": "not found or already cancelled"},
            outputs=[TextOut("❌ Recurring expense not found or already cancelled.")],
            final=True,
        )

    emoji = tables._get_emoji(cancelled.category)
    desc = f" — {esc(cancelled.description)}" if cancelled.description else ""
    return ToolResult(
        data={"status": "cancelled", "recurring_id": cancelled.id},
        outputs=[
            TextOut(f"✅ Cancelled recurring: {emoji} {esc(cancelled.category.title())}{desc}")
        ],
        final=True,
        wrote=True,
    )
