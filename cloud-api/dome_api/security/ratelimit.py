"""In-memory rate limiters (single relay process, see ADR-0001 D1)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    """``per_minute`` sustained rate with ``burst`` capacity, keyed by an opaque string."""

    def __init__(self, per_minute: int, burst: int) -> None:
        self.rate = per_minute / 60.0
        self.burst = float(burst)
        self._buckets: dict[str, _Bucket] = {}
        self._last_prune = time.monotonic()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        b = self._buckets.get(key)
        if b is None:
            b = _Bucket(tokens=self.burst, updated=now)
            self._buckets[key] = b
        else:
            b.tokens = min(self.burst, b.tokens + (now - b.updated) * self.rate)
            b.updated = now
        if now - self._last_prune > 300:
            self._prune(now)
        if b.tokens >= cost:
            b.tokens -= cost
            return True
        return False

    def forget(self, key: str) -> None:
        """Drop a key whose owner is gone (a closed socket); pruning would get to it eventually."""
        self._buckets.pop(key, None)

    def _prune(self, now: float) -> None:
        self._last_prune = now
        stale = [k for k, b in self._buckets.items() if now - b.updated > 600]
        for k in stale:
            del self._buckets[k]


@dataclass(slots=True)
class _Window:
    hits: list[float] = field(default_factory=list)


class SlidingWindowLimiter:
    """At most ``limit`` events per ``window_seconds`` per key."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._windows: dict[str, _Window] = {}
        self._last_prune = time.monotonic()

    def allow(self, key: str) -> bool:
        """Record an event if the key still has budget; ``False`` when the limit is reached."""
        if self.exhausted(key):
            return False
        self._windows[key].hits.append(time.monotonic())
        return True

    def exhausted(self, key: str) -> bool:
        """Whether the key has reached its limit, without recording anything."""
        now = time.monotonic()
        # Prune before looking the key up: pruning drops empty windows, and a window created for
        # this key just now is empty, so pruning afterwards would delete it under the caller
        # (allow/hit then index it: KeyError on the first request after five quiet minutes).
        if now - self._last_prune > 300:
            self._prune(now)
        w = self._windows.setdefault(key, _Window())
        w.hits = [t for t in w.hits if now - t < self.window]
        return len(w.hits) >= self.limit

    def hit(self, key: str) -> None:
        """Record an event unconditionally (used to count *failures* after the fact)."""
        self.exhausted(key)
        self._windows[key].hits.append(time.monotonic())

    def release(self, key: str) -> None:
        """Give back the most recent event recorded for ``key`` (a reservation whose work then failed)."""
        w = self._windows.get(key)
        if w is not None and w.hits:
            w.hits.pop()

    def _prune(self, now: float) -> None:
        self._last_prune = now
        for k in [k for k, w in self._windows.items() if not w.hits or now - w.hits[-1] > self.window]:
            del self._windows[k]
