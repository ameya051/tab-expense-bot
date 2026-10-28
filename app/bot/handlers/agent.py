"""Free-text and voice messages go to the agent; confirmation buttons run its pending actions."""

import asyncio
import logging
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

from app.agent import memory
from app.agent.outputs import Output, TextOut
from app.agent.registry import ToolContext, execute_tool
from app.agent.runner import AgentRunner, AgentTurn
from app.bot.handlers.common import ensure_user, esc, get_preferred_currency
from app.bot.ratelimit import user_rate_limiter
from app.bot.render import edit_with_outputs, send_outputs
from app.logging_setup import summarize
from app.nlp.ai import AIUnavailableError
from app.nlp.transcriber import VoiceTranscriber

logger = logging.getLogger(__name__)

RATE_LIMIT_MESSAGE = "⏳ You're sending messages too fast. Please slow down a bit!"
AI_UNAVAILABLE_MESSAGE = "🤖 My AI service is temporarily unavailable. Please try again in a minute."
SOMETHING_WRONG_MESSAGE = "❌ Something went wrong. Please try again."
SAVED_BUT_INTERRUPTED_MESSAGE = "⚠️ Your change was saved, but I couldn't finish the reply."
UNKNOWN_MESSAGE = (
    "🤔 I didn't understand that. Try something like:\n"
    "• \"spent 200 on lunch\"\n"
    "• \"how much this week?\"\n"
    "• \"set my food budget to 5000\""
)

# One turn at a time per user (PTB runs updates concurrently), so history and
# follow-ups like "delete that" see the previous turn's result.
_user_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _get_agent(context: ContextTypes.DEFAULT_TYPE) -> AgentRunner:
    """Return the agent stored in bot_data."""
    return context.bot_data["agent"]


def _get_transcriber(context: ContextTypes.DEFAULT_TYPE) -> VoiceTranscriber:
    """Return the voice transcriber stored in bot_data."""
    return context.bot_data["voice_transcriber"]


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hand a text message to the agent."""
    await ensure_user(update)
    if not user_rate_limiter.check(update.effective_user.id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return
    await _run_agent(update, context, update.message.text)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe a voice message and hand the text to the agent."""
    await ensure_user(update)
    user_id = update.effective_user.id

    if not user_rate_limiter.check(user_id):
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    try:
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = bytes(await file.download_as_bytearray())
        text = await _get_transcriber(context).transcribe(audio_bytes)
    except AIUnavailableError:
        logger.warning("AI unavailable for voice message from user_id=%s", user_id)
        await update.message.reply_text(AI_UNAVAILABLE_MESSAGE)
        return
    except Exception:
        logger.exception("Error handling voice message")
        await update.message.reply_text(SOMETHING_WRONG_MESSAGE)
        return

    if not text:
        await update.message.reply_text(
            "🎙️ Sorry, I couldn't understand that voice message. "
            "Try again or type your expense instead."
        )
        return

    await _run_agent(update, context, text, prefix=f'🎙️ I heard: "<i>{esc(text)}</i>"\n\n')


async def _run_agent(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, prefix: str = ""
) -> None:
    """Run one agent turn and send what it produced."""
    user_id = update.effective_user.id
    async with _user_locks[user_id]:
        try:
            ctx = ToolContext(user_id=user_id, pref_currency=await get_preferred_currency(user_id))
        except Exception:
            logger.exception("Could not load user settings for message %s", summarize(text))
            await update.message.reply_text(SOMETHING_WRONG_MESSAGE)
            return

        turn = await _get_agent(context).run(text, ctx, memory.load_history(context.user_data))
        memory.save_turn(context.user_data, turn.messages)
        for pending in turn.pending:
            memory.store_pending(context.user_data, pending)

        try:
            await send_outputs(update.message, _turn_outputs(turn), prefix)
        except Exception:
            logger.exception("Failed to send the reply for message %s", summarize(text))
            if turn.wrote:
                await update.message.reply_text(SAVED_BUT_INTERRUPTED_MESSAGE)


def _turn_outputs(turn: AgentTurn) -> list[Output]:
    """Everything to send for a turn, ending with the agent's reply or a fallback."""
    outputs = list(turn.outputs)
    if turn.final_text:
        outputs.append(TextOut(esc(turn.final_text)))

    if turn.ai_failed or turn.failed:
        # After a commit, never say "something went wrong": users would retry
        # and log the expense twice.
        if turn.wrote:
            outputs.append(TextOut(SAVED_BUT_INTERRUPTED_MESSAGE))
        elif not outputs:
            message = AI_UNAVAILABLE_MESSAGE if turn.ai_failed else SOMETHING_WRONG_MESSAGE
            outputs.append(TextOut(esc(message)))
    elif not outputs:
        outputs.append(TextOut(esc(UNKNOWN_MESSAGE)))
    return outputs


async def agent_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run (Yes) or drop (No) an action the agent asked the user to confirm."""
    query = update.callback_query
    await query.answer()
    action, _, token = query.data.partition(":")
    pending = memory.pop_pending(context.user_data, token)

    if pending is None:
        await query.edit_message_text("⌛ This question has expired. Just ask me again.")
        return
    if action == "agent_cancel":
        memory.add_note(context.user_data, f"(user declined {pending.tool})")
        await query.edit_message_text("👌 Okay, nothing was changed.")
        return

    user_id = query.from_user.id
    async with _user_locks[user_id]:
        try:
            ctx = ToolContext(user_id=user_id, pref_currency=await get_preferred_currency(user_id))
            result = await execute_tool(pending.tool, pending.args, ctx, confirmed=True)
        except Exception:
            logger.exception("Confirmed %s failed", pending.tool)
            await query.edit_message_text(SOMETHING_WRONG_MESSAGE)
            return

        if pending.tool == "change_currency" and result.wrote:
            # Amounts in the history are in the old currency now.
            memory.clear_history(context.user_data)
        else:
            memory.add_note(context.user_data, f"(user confirmed {pending.tool}: {result.data})")

    await edit_with_outputs(query, result.outputs or [TextOut(SOMETHING_WRONG_MESSAGE)])
