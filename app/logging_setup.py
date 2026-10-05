"""Logging configuration, per-update context tags and a service-call logger."""

import functools
import inspect
import logging
import time
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal

from app.config import settings
from app.timeutils import local_tz

# Tags like "upd=812 user=9876" for whatever update or job is being processed.
# Each update runs in its own asyncio task, so a value set while handling one
# update never leaks into another.
log_context: ContextVar[str] = ContextVar("log_context", default="-")

_LIST_PREVIEW = 5
_MAX_VALUE_CHARS = 2000


def set_log_context(**tags: object) -> None:
    """Tag every log line from the current task, e.g. set_log_context(upd=1, user=2)."""
    log_context.set(" ".join(f"{key}={value}" for key, value in tags.items()))


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.ctx = log_context.get()
        return True


class _LocalTimeFormatter(logging.Formatter):
    """Timestamps in the configured TIMEZONE (containers usually run in UTC)."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        stamp = datetime.fromtimestamp(record.created, local_tz())
        return f"{stamp:%Y-%m-%d %H:%M:%S},{int(record.msecs):03d}"


def configure_logging() -> None:
    """Install the root handler. Safe to call more than once."""
    handler = logging.StreamHandler()
    handler.addFilter(_ContextFilter())
    handler.setFormatter(
        _LocalTimeFormatter("%(asctime)s | %(levelname)-8s | %(name)s | %(ctx)s | %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # httpx logs every request URL, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Raw SQL via the root handler (echo=True would add a second handler and
    # print every statement twice).
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.log_sql else logging.WARNING
    )


def summarize(value: object) -> str:
    """One-line, size-bounded description of a value for log messages."""
    try:
        if isinstance(value, (bytes, bytearray)):
            text = f"<{len(value)} bytes>"
        elif isinstance(value, (date, datetime, Decimal)):
            text = str(value)  # 2026-09-28, not datetime.date(2026, 9, 28)
        elif isinstance(value, list):
            shown = ", ".join(summarize(item) for item in value[:_LIST_PREVIEW])
            more = f", +{len(value) - _LIST_PREVIEW} more" if len(value) > _LIST_PREVIEW else ""
            text = f"{len(value)} items: [{shown}{more}]"
        elif isinstance(value, tuple):
            text = "(" + ", ".join(summarize(item) for item in value) + ")"
        elif isinstance(value, dict):
            text = "{" + ", ".join(f"{k!r}: {summarize(v)}" for k, v in value.items()) + "}"
        else:
            text = repr(value)
    except Exception:  # noqa: BLE001 — logging must never break the caller
        text = f"<{type(value).__name__}>"
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > _MAX_VALUE_CHARS:
        text = f"{text[:_MAX_VALUE_CHARS]}…(+{len(text) - _MAX_VALUE_CHARS} chars)"
    return text


def log_call(fn):
    """Log an async service call: arguments (minus db), result summary and duration."""
    signature = inspect.signature(fn)
    logger = logging.getLogger(fn.__module__)

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            bound = signature.bind(*args, **kwargs).arguments
            arg_text = ", ".join(f"{k}={summarize(v)}" for k, v in bound.items() if k != "db")
        except TypeError:
            arg_text = "?"
        started = time.perf_counter()
        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            logger.warning(
                "%s(%s) -> raised %s: %s (%.1f ms)",
                fn.__name__, arg_text, type(exc).__name__, exc,
                (time.perf_counter() - started) * 1000,
            )
            raise
        logger.info(
            "%s(%s) -> %s (%.1f ms)",
            fn.__name__, arg_text, summarize(result), (time.perf_counter() - started) * 1000,
        )
        return result

    return wrapper
