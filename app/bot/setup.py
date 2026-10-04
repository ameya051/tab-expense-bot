"""Bot application builder — creates and configures the python-telegram-bot Application."""

import logging

from telegram import Chat, Update
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.bot.handlers import (
    budget_handler,
    cancel_recurring_callback,
    delete_handler,
    export_handler,
    message_handler,
    recurring_handler,
    report_handler,
    summary_handler,
    voice_handler,
)
from app.bot.onboarding import build_onboarding_handler, expired_currency_callback
from app.config import settings
from app.nlp.parser import NLPParser
from app.nlp.transcriber import VoiceTranscriber

logger = logging.getLogger(__name__)

# All data handlers are restricted to new messages in private chats — expense
# data must never be posted into group chats where other members could see it,
# and edited messages carry update.message=None (handlers would crash).
_PRIVATE = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE

_GROUP_TYPES = (Chat.GROUP, Chat.SUPERGROUP)
_JOINED_STATUSES = (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)

_GROUP_REFUSAL_TEXT = (
    "🚫 I only work in private chats for privacy reasons.\n"
    "Message me directly and I'll help you track your expenses!"
)


async def _group_refusal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refuse to operate outside private chats and stop further processing.

    Private-chat updates return early so group-0 handlers still see them.
    For everything else no data handler may run, so ApplicationHandlerStop is
    always raised. To avoid spamming groups, the bot only speaks when it is
    added to a group (then leaves) or when a command is addressed to it.
    """
    chat = update.effective_chat
    if chat is None or chat.type == Chat.PRIVATE:
        return

    member_update = update.my_chat_member
    if member_update is not None:
        if (
            chat.type in _GROUP_TYPES
            and member_update.new_chat_member.status in _JOINED_STATUSES
        ):
            try:
                await context.bot.send_message(chat_id=chat.id, text=_GROUP_REFUSAL_TEXT)
                await context.bot.leave_chat(chat_id=chat.id)
            except TelegramError:
                logger.warning("Could not refuse/leave chat_id=%s", chat.id)
        raise ApplicationHandlerStop

    message = update.message
    if message is not None and chat.type in _GROUP_TYPES and _is_command_for_me(
        message.text, context.bot.username
    ):
        await message.reply_text(_GROUP_REFUSAL_TEXT)
    raise ApplicationHandlerStop


def _is_command_for_me(text: str | None, bot_username: str | None) -> bool:
    """True if text is a /command that is unaddressed or addressed to this bot."""
    if not text or not text.startswith("/"):
        return False
    command = text.split(maxsplit=1)[0]
    if "@" not in command:
        return True
    target = command.split("@", 1)[1]
    return bool(bot_username) and target.lower() == bot_username.lower()


async def _error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unhandled handler errors with the offending update attached."""
    logger.exception(
        "Unhandled error while processing update_id=%s",
        update.update_id if update else None,
        exc_info=context.error,
    )


def create_bot_application(token: str) -> Application:
    """Build the PTB Application with all handlers registered."""
    # concurrent_updates: a slow AI call for one user must not block everyone
    app = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)
        .build()
    )

    # Shared NLP tools are constructed once and stored on bot_data so handlers
    # can access them via context.bot_data instead of module-level singletons.
    app.bot_data["nlp_parser"] = NLPParser(
        api_key=settings.openrouter_api_key, model=settings.openrouter_model
    )
    app.bot_data["voice_transcriber"] = VoiceTranscriber(
        api_key=settings.openrouter_api_key, model=settings.whisper_model
    )

    # Group-chat refusal — registered first in its own group so it consumes
    # non-private updates before any data handler can see them.
    # NOTE: TypeHandler's first arg must be a type (Update), not a predicate —
    # the private-chat gate lives inside the callback.
    app.add_handler(
        TypeHandler(Update, _group_refusal_handler),
        group=-1,
    )

    # Onboarding ConversationHandler (/start + /settings) — must be first
    app.add_handler(build_onboarding_handler())
    # Currency buttons pressed after the onboarding conversation has ended
    app.add_handler(
        CallbackQueryHandler(expired_currency_callback, pattern=r"^currency:")
    )

    # Command handlers
    app.add_handler(CommandHandler("summary", summary_handler, filters=_PRIVATE))
    app.add_handler(CommandHandler("report", report_handler, filters=_PRIVATE))
    app.add_handler(CommandHandler("delete", delete_handler, filters=_PRIVATE))
    app.add_handler(CommandHandler("budget", budget_handler, filters=_PRIVATE))
    app.add_handler(CommandHandler("recurring", recurring_handler, filters=_PRIVATE))
    app.add_handler(CommandHandler("export", export_handler, filters=_PRIVATE))

    # Callback query handler for cancel recurring buttons
    app.add_handler(
        CallbackQueryHandler(cancel_recurring_callback, pattern=r"^cancel_recurring:")
    )

    # Voice message handler
    app.add_handler(MessageHandler(filters.VOICE & _PRIVATE, voice_handler))

    # Free-text handler — must be last (catch-all)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & _PRIVATE, message_handler)
    )

    app.add_error_handler(_error_handler)

    return app
