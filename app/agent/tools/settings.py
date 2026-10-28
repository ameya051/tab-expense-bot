"""Settings tools: change the preferred currency."""

import logging

from app.agent.outputs import TextOut
from app.agent.registry import Confirmation, ToolContext, ToolResult, error_result, tool
from app.database import AsyncSessionLocal
from app.formatting import currency_change_note
from app.schemas import ChangeCurrencyArgs
from app.services import user_service
from app.services.currency_service import CurrencyConversionError, is_supported_currency

logger = logging.getLogger(__name__)


async def _confirm_change(ctx: ToolContext, args: ChangeCurrencyArgs) -> Confirmation | None:
    """Ask before converting every stored amount; no question if nothing would change."""
    if args.new_currency == ctx.pref_currency or not is_supported_currency(args.new_currency):
        return None
    return Confirmation(
        f"💱 Switch your currency from <b>{ctx.pref_currency}</b> to "
        f"<b>{args.new_currency}</b>?\n\n"
        "I'll convert all your expenses, budgets and recurring expenses at today's rate."
    )


@tool(
    "change_currency",
    "Change the user's preferred currency and convert all their stored amounts. "
    "The user is asked to confirm with a button before anything changes.",
    ChangeCurrencyArgs,
    writes=True,
    confirm=_confirm_change,
)
async def change_currency(ctx: ToolContext, args: ChangeCurrencyArgs) -> ToolResult:
    code = args.new_currency
    if not is_supported_currency(code):
        return error_result(f"{code} is not a supported currency")
    if code == ctx.pref_currency:
        return ToolResult(
            data={"status": "unchanged", "currency": code},
            outputs=[TextOut(f"👍 Your currency is already <b>{code}</b>.")],
            final=True,
        )

    async with AsyncSessionLocal() as db:
        try:
            result = await user_service.change_currency(db, ctx.user_id, code)
        except CurrencyConversionError:
            logger.warning("Currency change to %s failed for user %d: no FX rate", code, ctx.user_id)
            return ToolResult(
                data={"status": "failed", "reason": "exchange rates unavailable"},
                outputs=[
                    TextOut(
                        "❌ I couldn't get exchange rates to convert your existing expenses "
                        f"to <b>{code}</b> right now, so nothing was changed. "
                        "Please try again later."
                    )
                ],
                final=True,
            )
        await user_service.mark_onboarding_complete(db, ctx.user_id)

    return ToolResult(
        data={"status": "changed", "currency": code,
              "expenses_converted": result.expenses_converted},
        outputs=[
            TextOut(
                f"✅ Your currency is now <b>{code}</b>.\n\n{currency_change_note(result)}".strip()
            )
        ],
        final=True,
        wrote=True,
    )
