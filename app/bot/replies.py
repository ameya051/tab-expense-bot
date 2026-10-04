"""Helpers for splitting long replies into Telegram-safe chunks."""

MAX_MESSAGE_LENGTH = 4096


def chunk_text(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text into chunks of at most max_len characters.

    Prefers newline boundaries; falls back to a hard split for single
    lines longer than max_len.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_len])
            line = line[max_len:]
        if current and len(current) + len(line) > max_len:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


async def reply_chunked(
    message,
    text: str,
    max_len: int = MAX_MESSAGE_LENGTH,
    parse_mode: str | None = None,
    reply_markup=None,
    **kwargs,
) -> None:
    """Send text as one or more replies, in order.

    parse_mode is applied to every chunk — callers must ensure HTML tags
    are balanced within each chunk (chunk_text never splits mid-line).
    reply_markup is attached to the first chunk only.
    """
    for i, chunk in enumerate(chunk_text(text, max_len)):
        await message.reply_text(
            chunk,
            parse_mode=parse_mode,
            reply_markup=reply_markup if i == 0 else None,
            **kwargs,
        )
