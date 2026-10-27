"""Short per-user conversation memory and pending confirmations, kept in PTB user_data.

In memory only: lost on restart and not shared between workers. That is fine
for follow-ups like "delete that", which happen within minutes.
"""

import time
from typing import Any

from app.agent.registry import PendingAction
from app.config import settings

_HISTORY = "agent_history"
_HISTORY_AT = "agent_history_at"
_PENDING = "agent_pending"

PENDING_TTL_SECONDS = 300


def load_history(user_data: dict) -> list[dict[str, Any]]:
    """Chat messages from recent turns, or [] if the user has been idle too long."""
    last = user_data.get(_HISTORY_AT)
    if last is None or time.monotonic() - last > settings.agent_history_ttl_minutes * 60:
        user_data.pop(_HISTORY, None)
        return []
    return list(user_data.get(_HISTORY, []))


def save_turn(user_data: dict, messages: list[dict[str, Any]]) -> None:
    """Append a turn's messages, keeping only the last agent_history_turns turns.

    Trimming happens at user messages, so an assistant tool call is never
    separated from its tool results.
    """
    history = load_history(user_data) + messages
    starts = [i for i, m in enumerate(history) if m["role"] == "user"]
    if len(starts) > settings.agent_history_turns:
        history = history[starts[-settings.agent_history_turns]:]
    user_data[_HISTORY] = history
    user_data[_HISTORY_AT] = time.monotonic()


def add_note(user_data: dict, text: str) -> None:
    """Record something that happened outside the LLM loop, e.g. a confirmed button."""
    history = load_history(user_data)
    if not history:
        return
    history.append({"role": "assistant", "content": text})
    user_data[_HISTORY] = history
    user_data[_HISTORY_AT] = time.monotonic()


def clear_history(user_data: dict) -> None:
    """Forget the conversation, e.g. after amounts changed currency."""
    user_data.pop(_HISTORY, None)
    user_data.pop(_HISTORY_AT, None)


def store_pending(user_data: dict, pending: PendingAction) -> None:
    """Remember an action until the user taps Yes or No (or it expires)."""
    actions = _live_pending(user_data)
    actions[pending.token] = pending
    user_data[_PENDING] = actions


def pop_pending(user_data: dict, token: str) -> PendingAction | None:
    """Take a pending action by its button token; None if unknown or expired."""
    actions = _live_pending(user_data)
    pending = actions.pop(token, None)
    user_data[_PENDING] = actions
    return pending


def _live_pending(user_data: dict) -> dict[str, PendingAction]:
    now = time.monotonic()
    return {
        token: p
        for token, p in user_data.get(_PENDING, {}).items()
        if now - p.created_at <= PENDING_TTL_SECONDS
    }
