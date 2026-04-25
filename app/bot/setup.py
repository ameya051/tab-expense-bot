"""Bot application builder — creates and configures the python-telegram-bot Application."""

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
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
from app.bot.onboarding import build_onboarding_handler


def create_bot_application(token: str) -> Application:
    """Build the PTB Application with all handlers registered."""
    app = (
        ApplicationBuilder()
        .token(token)
        .build()
    )

    # Onboarding ConversationHandler (/start + /settings) — must be first
    app.add_handler(build_onboarding_handler())

    # Command handlers
    app.add_handler(CommandHandler("summary", summary_handler))
    app.add_handler(CommandHandler("report", report_handler))
    app.add_handler(CommandHandler("delete", delete_handler))
    app.add_handler(CommandHandler("budget", budget_handler))
    app.add_handler(CommandHandler("recurring", recurring_handler))
    app.add_handler(CommandHandler("export", export_handler))

    # Callback query handler for cancel recurring buttons
    app.add_handler(
        CallbackQueryHandler(cancel_recurring_callback, pattern=r"^cancel_recurring:")
    )

    # Voice message handler
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))

    # Free-text handler — must be last (catch-all)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )

    return app
