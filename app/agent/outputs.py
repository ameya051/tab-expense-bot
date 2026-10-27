"""What tools show the user. Telegram-agnostic; app/bot/render.py sends them."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Button:
    """An inline button; data is the callback_data the bot receives on a tap."""

    label: str
    data: str


@dataclass(frozen=True)
class TextOut:
    """A message in Telegram HTML. Escape user- or LLM-derived text with esc()."""

    html: str
    buttons: list[list[Button]] = field(default_factory=list)


@dataclass(frozen=True)
class PhotoOut:
    """A PNG image with an optional plain-text caption."""

    png: bytes
    caption: str | None = None


@dataclass(frozen=True)
class DocumentOut:
    """A file attachment with an optional plain-text caption."""

    content: bytes
    filename: str
    caption: str | None = None


Output = TextOut | PhotoOut | DocumentOut
