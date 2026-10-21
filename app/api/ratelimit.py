"""Rate limits for live matcher calls (Jev costs money per call).

Each visitor gets a few live checks per minute and per day, and the whole server gets a
daily budget. Past a limit the API still answers, using the free offline keyword rules.

Counters live in memory, so on serverless hosts each warm instance keeps its own: the
limits are a guard against casual abuse and runaway cost, not an exact quota. A hard
spending cap on the TypeSafe account is the real backstop.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

MINUTE = 60.0
DAY = 24 * 60 * 60.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class SlidingWindow:
    """Counts events per key over a rolling window."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        return hits

    def full(self, key: str, now: float) -> bool:
        return len(self._prune(key, now)) >= self.limit

    def add(self, key: str, now: float) -> None:
        self._prune(key, now).append(now)

    def sweep(self, now: float) -> None:
        """Drop keys with no recent hits, so memory doesn't grow with every visitor."""
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self.window]:
            del self._hits[key]


class MatchLimiter:
    """Allows a live call if the visitor and the server are both under their limits."""

    def __init__(self, per_minute: int, per_day: int, global_per_day: int, clock=time.monotonic):
        self.per_minute = SlidingWindow(per_minute, MINUTE)
        self.per_day = SlidingWindow(per_day, DAY)
        self.global_day = SlidingWindow(global_per_day, DAY)
        self._clock = clock
        self._lock = threading.Lock()
        self._calls = 0

    @classmethod
    def from_env(cls) -> "MatchLimiter":
        return cls(
            per_minute=_env_int("SCAM_RATE_PER_MINUTE", 5),
            per_day=_env_int("SCAM_RATE_PER_DAY", 30),
            global_per_day=_env_int("SCAM_RATE_GLOBAL_PER_DAY", 1000),
        )

    def check(self, visitor: str) -> str | None:
        """Record a live call and return None, or return why it's refused (and record nothing)."""
        with self._lock:
            now = self._clock()
            self._calls += 1
            if self._calls % 500 == 0:
                for w in (self.per_minute, self.per_day, self.global_day):
                    w.sweep(now)
            if self.global_day.full("all", now):
                return "global_daily_limit"
            if self.per_minute.full(visitor, now):
                return "per_minute_limit"
            if self.per_day.full(visitor, now):
                return "daily_limit"
            for w, key in ((self.per_minute, visitor), (self.per_day, visitor), (self.global_day, "all")):
                w.add(key, now)
            return None


def visitor_key(headers, client_host: str | None) -> str:
    """The visitor's IP: the first X-Forwarded-For hop (set by Vercel and most proxies), else the socket."""
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = headers.get("x-real-ip")
    if real:
        return real.strip()
    return client_host or "unknown"
