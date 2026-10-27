"""Reporting tools: spending figures for the LLM to answer with, and charts for the user."""

from datetime import date

from app.agent.outputs import PhotoOut, TextOut
from app.agent.registry import ToolContext, ToolResult, tool
from app.database import AsyncSessionLocal
from app.formatting import esc, format_amount
from app.reports import charts, tables
from app.reports.render import render_chart
from app.schemas import DateRangeArgs, GetSpendingArgs, ShowChartArgs
from app.services import expense_service
from app.timeutils import today as local_today


def resolve_range(args: DateRangeArgs) -> tuple[date, date]:
    """Resolve a range: an explicit start/end date wins over the named period."""
    if args.start_date is None and args.end_date is None:
        return expense_service.resolve_date_range(args.period)
    all_time_start, today = expense_service.resolve_date_range("all_time")
    return (args.start_date or all_time_start, args.end_date or today)


def period_label(args: DateRangeArgs, start: date, end: date) -> str:
    """Human label with the exact dates used, e.g. "Last Week (Sep 28 – Oct 04)"."""
    fmt = "%b %d" if start.year == local_today().year else "%b %d %Y"
    dates = start.strftime(fmt) if start == end else f"{start.strftime(fmt)} – {end.strftime(fmt)}"

    if args.start_date is not None or args.end_date is not None:
        return dates
    name = args.period.replace("_", " ").title()
    if args.period == "all_time":
        return name
    return f"{name} ({dates})"


@tool(
    "get_spending",
    "Look up how much the user spent in a date range, optionally for one "
    "category or grouped by category or day. Returns figures for you to answer "
    "with; shows the user nothing.",
    GetSpendingArgs,
)
async def get_spending(ctx: ToolContext, args: GetSpendingArgs) -> ToolResult:
    start, end = resolve_range(args)
    currency = ctx.pref_currency
    data: dict = {"period": period_label(args, start, end)}

    async with AsyncSessionLocal() as db:
        if args.category:
            total = await expense_service.get_category_total(
                db, ctx.user_id, args.category, start, end
            )
            data["category"] = args.category.lower()
        else:
            total = await expense_service.get_total(db, ctx.user_id, start, end)
        data["total"] = format_amount(total, currency)

        if args.group_by == "category" and not args.category:
            rows = await expense_service.get_by_category(db, ctx.user_id, start, end)
            data["by_category"] = [
                {"category": r["category"], "total": format_amount(r["total"], currency)}
                for r in rows
            ]
        elif args.group_by == "day":
            rows = await expense_service.get_daily_trend(db, ctx.user_id, start, end)
            if args.category:
                # The daily trend isn't split by category; don't pass it off as one.
                data["by_day_note"] = "daily figures are only available for all categories"
            else:
                data["by_day"] = [
                    {"date": r["date"].isoformat(), "total": format_amount(r["total"], currency)}
                    for r in rows
                ]

    return ToolResult(data=data)


@tool(
    "show_chart",
    "Send the user a chart for a date range: kind=category is a bar chart plus "
    "a table of spending per category, kind=trend is a daily spending line chart.",
    ShowChartArgs,
)
async def show_chart(ctx: ToolContext, args: ShowChartArgs) -> ToolResult:
    start, end = resolve_range(args)
    label = period_label(args, start, end)
    currency = ctx.pref_currency

    if args.kind == "category":
        async with AsyncSessionLocal() as db:
            rows = await expense_service.get_by_category(db, ctx.user_id, start, end)
            total = await expense_service.get_total(db, ctx.user_id, start, end)
        if not rows:
            return _no_expenses(f"📊 {label}: No expenses found.", label)

        chart = await render_chart(charts.generate_category_bar_chart, rows, label, currency)
        table = tables.format_summary_table(rows, total, label, currency)
        return ToolResult(
            data={"shown": "category chart and table", "period": label,
                  "total": format_amount(total, currency)},
            outputs=[PhotoOut(chart), TextOut(f"<pre>{esc(table)}</pre>")],
            final=True,
        )

    async with AsyncSessionLocal() as db:
        rows = await expense_service.get_daily_trend(db, ctx.user_id, start, end)
    if not rows:
        return _no_expenses(f"📈 {label}: No expenses found.", label)

    chart = await render_chart(charts.generate_trend_line_chart, rows, label, currency)
    return ToolResult(
        data={"shown": "daily trend chart", "period": label},
        outputs=[PhotoOut(chart, caption=f"📈 Spending Trend — {label}")],
        final=True,
    )


def _no_expenses(text: str, label: str) -> ToolResult:
    return ToolResult(
        data={"shown": "nothing, no expenses in range", "period": label},
        outputs=[TextOut(esc(text))],
        final=True,
    )
