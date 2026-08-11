"""Sliding-window rate limiting for the per-session AI search quota."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class RateLimitedError(Exception):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(
            f"Rate limited; retry in {retry_after_seconds:.0f} seconds."
        )
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class SlidingWindowLimit:
    max_calls: int
    window_seconds: float

    def retry_after(self, history: Sequence[float], now: float) -> float:
        """Seconds until a new call is allowed; 0.0 when allowed now."""
        recent = sorted(
            timestamp
            for timestamp in history
            if timestamp > now - self.window_seconds
        )
        if len(recent) < self.max_calls:
            return 0.0
        return recent[0] + self.window_seconds - now


def try_acquire(
    limits: Sequence[SlidingWindowLimit], history: list[float], now: float
) -> float:
    """Record a call in ``history`` if every limit allows it.

    ``history`` is pruned in place to the longest window. Returns 0.0 when
    the call was recorded, otherwise the seconds to wait, and records
    nothing.
    """
    if not limits:
        return 0.0
    longest = max(limit.window_seconds for limit in limits)
    history[:] = [
        timestamp for timestamp in history if timestamp > now - longest
    ]
    wait = max(limit.retry_after(history, now) for limit in limits)
    if wait > 0:
        return wait
    history.append(now)
    return 0.0
