"""CSV export tool."""

from datetime import date

from app.agent.outputs import DocumentOut, TextOut
from app.agent.registry import ToolContext, ToolResult, tool
from app.agent.tools.reports import period_label, resolve_range
from app.database import AsyncSessionLocal
from app.formatting import format_amount
from app.schemas import ExportArgs
from app.services import expense_service, export_service
from app.timeutils import today


def _export_filename(args: ExportArgs, start: date, end: date) -> str:
    """Build a CSV filename that matches the exported range."""
    if args.start_date is not None or args.end_date is not None:
        return f"expenses_{start.isoformat()}_{end.isoformat()}.csv"
    day = today()
    match args.period:
        case "all_time":
            return "expenses_all_time.csv"
        case "this_week":
            return f"expenses_week_{day.isoformat()}.csv"
        case "today":
            return f"expenses_{day.isoformat()}.csv"
        case "this_month":
            return f"expenses_{day.strftime('%Y-%m')}.csv"
        case _:
            return f"expenses_{start.isoformat()}_{end.isoformat()}.csv"


@tool(
    "export_csv",
    "Send the user a CSV file of their expenses in a date range.",
    ExportArgs,
)
async def export_csv(ctx: ToolContext, args: ExportArgs) -> ToolResult:
    start, end = resolve_range(args)
    label = period_label(args, start, end)

    async with AsyncSessionLocal() as db:
        csv_bytes, count = await export_service.generate_csv(db, ctx.user_id, start, end)
        total = await expense_service.get_total(db, ctx.user_id, start, end)

    if count == 0:
        return ToolResult(
            data={"exported": 0, "period": label},
            outputs=[TextOut("📎 No expenses found for this period.")],
            final=True,
        )

    caption = (
        f"📎 Exported {count} expense{'s' if count != 1 else ''} "
        f"({format_amount(total, ctx.pref_currency)} total)"
    )
    return ToolResult(
        data={"exported": count, "period": label},
        outputs=[DocumentOut(csv_bytes, _export_filename(args, start, end), caption)],
        final=True,
    )
