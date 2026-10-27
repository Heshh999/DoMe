"""Where a controller socket's ``input_batch`` frames spend their time inside the relay, summarised once per second per
streaming socket as the ``relay.input_timing`` log line (DECISIONS #113). Counts and milliseconds only: no envelope, no
payload, no event content, and no identifier beyond the controller and PC ids the rejection lines already carry."""

from __future__ import annotations

from typing import Any

from dome_api.logging import get_logger

log = get_logger("dome_api.relay.input_timing")


class Stat:
    """Max and mean of one duration over the window, kept in seconds and reported in milliseconds to a tenth."""

    __slots__ = ("count", "total", "max")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.max = 0.0

    def add(self, seconds: float) -> None:
        self.count += 1
        self.total += seconds
        if seconds > self.max:
            self.max = seconds

    def fields(self, name: str) -> dict[str, float]:
        mean = self.total / self.count if self.count else 0.0
        return {f"{name}_max_ms": round(self.max * 1000.0, 1), f"{name}_mean_ms": round(mean * 1000.0, 1)}


class InputTiming:
    """One window of input-batch timings for a controller socket. ``count`` opens the window on the first batch the
    socket loop takes and counts every one; the loop records the loop lag and queue depth of those it routes and the
    outcome of those it drops or refuses before routing; ``route_input_batch`` adds the authorization, verification
    and forwarding times and its own outcome; ``flush`` writes the line once ``interval`` has passed since the window
    opened (never per batch, never more than once per ``interval`` per socket) or, when the socket closes, for a
    window still in progress."""

    __slots__ = (
        "window_started",
        "pc_id",
        "pcs",
        "received",
        "forwarded",
        "dropped",
        "refused",
        "auth_hits",
        "auth_misses",
        "queued_max",
        "auth",
        "verify",
        "forward",
        "lag",
    )

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.window_started: float | None = None  # monotonic time of the window's first batch
        self.pc_id: str | None = None  # the window's first PC; ``pcs`` says how many it saw
        self.pcs: set[str] = set()
        self.received = 0  # input_batch frames the loop took off the socket
        self.forwarded = 0  # sent on to the PC
        self.dropped = 0  # dropped unrouted: waited the whole age budget behind a newer batch (DECISIONS #112)
        self.refused: dict[str, int] = {}  # error code -> batches refused with it (the pre-database bucket included)
        self.auth_hits = 0  # authorization served from the socket's cache (DECISIONS #110)
        self.auth_misses = 0  # authorization read from the database
        self.queued_max = 0  # most frames waiting behind a batch when the loop took it
        self.auth = Stat()  # time in ``input_authorization``
        self.verify = Stat()  # signature, binding, window and payload schema
        self.forward = Stat()  # the send to the PC's socket, its lock included
        self.lag = Stat()  # from the frame's arrival on the socket to the start of its routing

    def count(self, now: float, pc_id: str) -> None:
        if self.window_started is None:
            self.window_started = now
            self.pc_id = pc_id
        self.pcs.add(pc_id)
        self.received += 1

    def route(self, lag: float, queued: int) -> None:
        self.lag.add(lag)
        if queued > self.queued_max:
            self.queued_max = queued

    def refuse(self, code: str) -> None:
        self.refused[code] = self.refused.get(code, 0) + 1

    def summary(self, now: float) -> dict[str, Any]:
        window = now - self.window_started if self.window_started is not None else 0.0
        out: dict[str, Any] = {
            "pc_id": self.pc_id,
            "window_ms": round(window * 1000.0),
            "received": self.received,
            "forwarded": self.forwarded,
            "dropped": self.dropped,
            # codes as values, never as keys: the redactor masks keys that end in ``_key`` (UNKNOWN_KEY)
            "refused": [{"reason": code, "count": n} for code, n in sorted(self.refused.items())],
            "auth_hits": self.auth_hits,
            "auth_misses": self.auth_misses,
            "queued_max": self.queued_max,
        }
        if len(self.pcs) > 1:
            out["pcs"] = len(self.pcs)
        for name, stat in (("auth", self.auth), ("verify", self.verify), ("forward", self.forward), ("lag", self.lag)):
            out.update(stat.fields(name))
        return out

    def flush(self, now: float, interval: float, *, controller_id: str, final: bool = False) -> bool:
        """Write the window's line if it is due (``interval`` seconds since its first batch, or ``final`` with any batch
        in it) and start the next one. ``interval`` 0 turns the line off."""
        if interval <= 0 or self.window_started is None:
            return False
        if not final and now - self.window_started < interval:
            return False
        log.info("relay.input_timing", controller_id=controller_id, **self.summary(now))
        self._reset()
        return True


__all__ = ["InputTiming", "Stat"]
