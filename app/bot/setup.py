"""Bot application builder — creates and configures the python-telegram-bot Application."""

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers import (
    delete_handler,
    message_handler,
    report_handler,
    start_handler,
    summary_handler,
)


def create_bot_application(token: str) -> Application:
    """Build the PTB Application with all handlers registered."""
    app = (
        ApplicationBuilder()
        .token(token)
        .build()
    )

    # Command handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("summary", summary_handler))
    app.add_handler(CommandHandler("report", report_handler))
    app.add_handler(CommandHandler("delete", delete_handler))

    # Free-text handler — must be last (catch-all)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )

    return app
