"""Budget tools: set, remove and review monthly budgets."""

from app.agent.outputs import TextOut
from app.agent.registry import ToolContext, ToolResult, tool
from app.database import AsyncSessionLocal
from app.formatting import esc, format_amount
from app.reports import tables
from app.schemas import RemoveBudgetArgs, SetBudgetArgs, ShowArgs
from app.services import budget_service

NO_BUDGETS_TEXT = (
    "📋 No budgets set yet.\n\n"
    "Just tell me, e.g. <i>set my food budget to 5000</i> or "
    "<i>my monthly budget is 20000</i>.\n\n"
    "Or use:\n"
    "• /budget &lt;amount&gt; — set total monthly budget\n"
    "• /budget &lt;category&gt; &lt;amount&gt; — set category budget"
)


def _status_emoji(pct: int) -> str:
    return "🟢" if pct < 80 else "🟡" if pct < 100 else "🔴"


@tool(
    "set_budget",
    "Create or update a monthly budget: the user's total budget when category "
    "is omitted, otherwise a budget for that category.",
    SetBudgetArgs,
    writes=True,
)
async def set_budget(ctx: ToolContext, args: SetBudgetArgs) -> ToolResult:
    async with AsyncSessionLocal() as db:
        await budget_service.set_budget(db, ctx.user_id, args.amount, category=args.category)

    amount = format_amount(args.amount, ctx.pref_currency)
    if args.category is not None:
        emoji = tables._get_emoji(args.category)
        text = f"✅ Budget set: {emoji} {esc(args.category.title())} — {amount}/month"
    else:
        text = f"✅ Total monthly budget set: {amount}"
    return ToolResult(
        data={"status": "set", "category": args.category or "total", "monthly_limit": amount},
        outputs=[TextOut(text)],
        final=True,
        wrote=True,
    )


@tool(
    "remove_budget",
    "Remove a monthly budget: the total budget when category is omitted, "
    "otherwise that category's budget.",
    RemoveBudgetArgs,
    writes=True,
)
async def remove_budget(ctx: ToolContext, args: RemoveBudgetArgs) -> ToolResult:
    async with AsyncSessionLocal() as db:
        removed = await budget_service.delete_budget(db, ctx.user_id, args.category)

    label = esc(args.category.title()) if args.category else "total monthly"
    if removed is None:
        return ToolResult(
            data={"status": "not found", "category": args.category or "total"},
            outputs=[TextOut(f"🤷 You don't have a {label} budget.")],
            final=True,
        )
    return ToolResult(
        data={"status": "removed", "category": args.category or "total"},
        outputs=[TextOut(f"🗑️ Removed your {label} budget.")],
        final=True,
        wrote=True,
    )


@tool(
    "get_budgets",
    "The user's budgets with this month's spending against each. Use "
    "show=false to get the figures without showing the overview.",
    ShowArgs,
)
async def get_budgets(ctx: ToolContext, args: ShowArgs) -> ToolResult:
    currency = ctx.pref_currency
    budgets: list[dict] = []
    lines = ["📊 <b>Your Budgets</b>\n"]

    async with AsyncSessionLocal() as db:
        user_budget = await budget_service.get_budget(db, ctx.user_id)
        cat_budgets = await budget_service.get_budgets(db, ctx.user_id)

        if not user_budget and not cat_budgets:
            outputs = [TextOut(NO_BUDGETS_TEXT)] if args.show else []
            return ToolResult(data={"budgets": []}, outputs=outputs, final=args.show)

        if user_budget:
            info = await budget_service.check_budget(db, ctx.user_id)
            spent = format_amount(info["spent"], currency)
            limit = format_amount(info["budget"], currency)
            budgets.append(
                {"category": "total", "spent": spent, "limit": limit, "percent": info["percent"]}
            )
            lines.append(
                f"\n💰 <b>Total Monthly Budget</b>\n"
                f"{_status_emoji(info['percent'])} {spent} / {limit} ({info['percent']}%)"
            )

        if cat_budgets:
            lines.append("\n<b>Category Budgets</b>")
            for b in cat_budgets:
                info = await budget_service.check_budget(db, ctx.user_id, b.category)
                spent = format_amount(info["spent"], currency)
                limit = format_amount(info["budget"], currency)
                budgets.append(
                    {"category": b.category, "spent": spent, "limit": limit,
                     "percent": info["percent"]}
                )
                lines.append(
                    f"{tables._get_emoji(b.category)} {esc(b.category.title())}: "
                    f"{_status_emoji(info['percent'])} {spent} / {limit} ({info['percent']}%)"
                )

    data = {"budgets": budgets}
    if not args.show:
        return ToolResult(data=data)
    return ToolResult(data=data, outputs=[TextOut("\n".join(lines))], final=True)
