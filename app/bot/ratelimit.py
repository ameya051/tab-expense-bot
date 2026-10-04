"""In-memory per-user rate limiting (sliding window)."""

import time
from collections import defaultdict, deque

from app.config import settings

_WINDOW_SECONDS = 60.0
# Sweep idle users out of memory every this many checks.
_SWEEP_EVERY = 1000


class UserRateLimiter:
    """Sliding-window rate limiter keyed by Telegram user id.

    PTB handlers run on a single event loop, so a plain dict is sufficient.
    """

    def __init__(
        self,
        max_events: int | None = None,
        window_seconds: float = _WINDOW_SECONDS,
    ) -> None:
        self.max_events = (
            max_events if max_events is not None else settings.rate_limit_per_minute
        )
        self.window_seconds = window_seconds
        self._events: dict[int, deque[float]] = defaultdict(deque)
        self._checks = 0

    def check(self, user_id: int) -> bool:
        """Record an event for the user; return True if under the limit."""
        now = time.monotonic()
        self._checks += 1
        if self._checks % _SWEEP_EVERY == 0:
            self._sweep(now)
        events = self._events[user_id]
        while events and now - events[0] > self.window_seconds:
            events.popleft()
        if len(events) >= self.max_events:
            return False
        events.append(now)
        return True

    def _sweep(self, now: float) -> None:
        """Forget users whose newest event has left the window."""
        idle = [
            uid
            for uid, events in self._events.items()
            if not events or now - events[-1] > self.window_seconds
        ]
        for uid in idle:
            del self._events[uid]


user_rate_limiter = UserRateLimiter()
