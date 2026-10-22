"""TEST DOUBLE: a scripted screen worker PROCESS for ``test_screen_session`` (spawned by the real
``ProcessWorkerLauncher``, so its events arrive on the supervisor's own pipe-reader task, as in production).

``python tests/scripted_screen_worker.py <event>``: answers ``start`` with the fake answer SDP, reports ``connected``,
``first_frame`` and heartbeats, then after half a second emits the scripted ``event`` (``bye``, ``error`` or
``disconnected``) once, and exits 0 on ``stop`` or pipe EOF like the real worker. No aiortc, no capture."""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Any

from dome_agent.screen.worker.pipe import read_message_sync, write_message_sync
from dome_agent.testing.fake_screen_worker import FAKE_ANSWER_SDP

SCRIPTED = {
    "bye": {"ev": "bye"},
    "error": {"ev": "error", "code": "SCREEN_CAPTURE_UNAVAILABLE", "detail": "driver", "message": "scripted"},
    "disconnected": {"ev": "disconnected", "reason": "failed"},
}


def main(argv: list[str]) -> int:
    scripted = SCRIPTED[argv[1]]
    out = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    lock = threading.Lock()
    inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def emit(message: dict[str, Any]) -> None:
        with lock:
            write_message_sync(out, message)

    def reader() -> None:
        stdin = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
        while True:
            message = read_message_sync(stdin)
            inbox.put(message)
            if message is None:
                return

    threading.Thread(target=reader, daemon=True).start()
    start: dict[str, Any] | None = None
    while start is None:
        message = inbox.get()
        if message is None:
            return 0
        if message.get("op") == "start":
            start = message
    source = start.get("source", {})
    geometry = {
        "gen": 1,
        "surface_index": int(source.get("surface_index", 0)),
        "width": int(source.get("width", 1920)),
        "height": int(source.get("height", 1080)),
        "rotation": 0,
        "reserved": list(start.get("reserved", [])),
    }
    emit({"ev": "answer", "answer_sdp": FAKE_ANSWER_SDP, "encoder": "h264", "geometry": geometry})
    emit({"ev": "connected", "path": "direct"})
    emit({"ev": "first_frame"})
    fire_at = time.monotonic() + 0.5
    fired = False
    next_hb = 0.0
    while True:
        try:
            message = inbox.get(timeout=0.05)
        except queue.Empty:
            message = {}
        if message is None:
            return 0
        if message.get("op") == "stop":
            emit({"ev": "exiting", "reason": "stop"})
            return 0
        now = time.monotonic()
        if not fired and now >= fire_at:
            fired = True
            emit(scripted)
        elif now >= next_hb:
            next_hb = now + 0.5
            emit({"ev": "hb"})


if __name__ == "__main__":
    sys.exit(main(sys.argv))
