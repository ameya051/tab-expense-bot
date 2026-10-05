"""ExtBot subclass that logs every message the bot sends.

Message.reply_text, CallbackQuery.edit_message_text and the other shortcuts
all call these Bot methods, so overriding them here covers every reply.
"""

import logging

from telegram.ext import ExtBot

from app.logging_setup import summarize

logger = logging.getLogger(__name__)

# Telegram can take well over 5 s to confirm an upload; timing out earlier
# reports a failure for a file that was actually delivered.
MEDIA_READ_TIMEOUT = 30.0


def _arg(args: tuple, kwargs: dict, index: int, name: str):
    """Fetch a call argument whether it was passed positionally or by keyword."""
    if name in kwargs:
        return kwargs[name]
    return args[index] if len(args) > index else None


def _describe_file(file) -> str:
    """Name and size of an outgoing photo/document (BytesIO, bytes or file id)."""
    if isinstance(file, (bytes, bytearray)):
        return f"<{len(file)} bytes>"
    if hasattr(file, "getbuffer"):
        return f"{getattr(file, 'name', 'file')} <{file.getbuffer().nbytes} bytes>"
    return summarize(file)


class LoggingBot(ExtBot):
    """Logs outgoing messages, then delegates to the normal ExtBot method."""

    async def send_message(self, *args, **kwargs):
        logger.info(
            "→ sendMessage chat=%s: %s",
            _arg(args, kwargs, 0, "chat_id"), summarize(_arg(args, kwargs, 1, "text")),
        )
        return await super().send_message(*args, **kwargs)

    async def send_photo(self, *args, **kwargs):
        logger.info(
            "→ sendPhoto chat=%s: %s caption=%s",
            _arg(args, kwargs, 0, "chat_id"),
            _describe_file(_arg(args, kwargs, 1, "photo")),
            summarize(kwargs.get("caption")),
        )
        kwargs.setdefault("read_timeout", MEDIA_READ_TIMEOUT)
        return await super().send_photo(*args, **kwargs)

    async def send_document(self, *args, **kwargs):
        logger.info(
            "→ sendDocument chat=%s: %s caption=%s",
            _arg(args, kwargs, 0, "chat_id"),
            _describe_file(_arg(args, kwargs, 1, "document")),
            summarize(kwargs.get("caption")),
        )
        kwargs.setdefault("read_timeout", MEDIA_READ_TIMEOUT)
        return await super().send_document(*args, **kwargs)

    async def edit_message_text(self, *args, **kwargs):
        logger.info(
            "→ editMessageText chat=%s message=%s: %s",
            kwargs.get("chat_id"), kwargs.get("message_id"),
            summarize(_arg(args, kwargs, 0, "text")),
        )
        return await super().edit_message_text(*args, **kwargs)

    async def answer_callback_query(self, *args, **kwargs):
        logger.info(
            "→ answerCallbackQuery text=%s alert=%s",
            summarize(kwargs.get("text")), kwargs.get("show_alert", False),
        )
        return await super().answer_callback_query(*args, **kwargs)

    async def leave_chat(self, *args, **kwargs):
        logger.info("→ leaveChat chat=%s", _arg(args, kwargs, 0, "chat_id"))
        return await super().leave_chat(*args, **kwargs)
