"""Send agent/tool outputs to Telegram."""

import io
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.error import TimedOut

from app.agent.outputs import Button, DocumentOut, Output, PhotoOut, TextOut
from app.bot.replies import reply_chunked

logger = logging.getLogger(__name__)

UPLOAD_TIMED_OUT_TEXT = (
    "⏳ Telegram is slow to confirm the upload. Your file may still arrive "
    "in a moment. If it doesn't, ask me again."
)


def _keyboard(rows: list[list[Button]]) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.label, callback_data=b.data) for b in row] for row in rows]
    )


async def send_outputs(message: Message, outputs: list[Output], prefix: str = "") -> None:
    """Reply to message with each output in order.

    prefix is already-escaped HTML (e.g. the "I heard: ..." line for voice
    messages). It joins the first text output, or goes out on its own first.
    """
    if prefix:
        if outputs and isinstance(outputs[0], TextOut):
            first = outputs[0]
            outputs = [TextOut(prefix + first.html, first.buttons), *outputs[1:]]
        else:
            await message.reply_text(prefix, parse_mode="HTML")

    for output in outputs:
        if isinstance(output, TextOut):
            await reply_chunked(
                message, output.html, parse_mode="HTML", reply_markup=_keyboard(output.buttons)
            )
        elif isinstance(output, PhotoOut):
            buf = io.BytesIO(output.png)
            buf.name = "chart.png"
            await message.reply_photo(photo=buf, caption=output.caption)
        elif isinstance(output, DocumentOut):
            buf = io.BytesIO(output.content)
            buf.name = output.filename
            try:
                await message.reply_document(document=buf, caption=output.caption)
            except TimedOut:
                # The upload may still have gone through, so don't report a failure.
                logger.warning("Telegram did not confirm the upload in time", exc_info=True)
                await message.reply_text(UPLOAD_TIMED_OUT_TEXT)


async def edit_with_outputs(query, outputs: list[Output]) -> None:
    """Answer a button tap: the first text output replaces the button message."""
    if outputs and isinstance(outputs[0], TextOut):
        first, rest = outputs[0], outputs[1:]
        await query.edit_message_text(
            first.html, parse_mode="HTML", reply_markup=_keyboard(first.buttons)
        )
    else:
        await query.edit_message_reply_markup(reply_markup=None)
        rest = outputs
    if rest:
        await send_outputs(query.message, rest)
