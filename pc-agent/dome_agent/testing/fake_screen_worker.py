"""TEST DOUBLE: an in-process stand-in for the screen worker process (``dome_agent.screen.worker``).

Implements :class:`dome_agent.screen.supervisor.WorkerLauncher` / ``WorkerProcess`` without aiortc or a child
process, so ``ScreenSessionManager`` can be tested end to end through the agent: it answers ``start`` with a fake
answer SDP (or a scripted error), "connects" and sends the first frame after the scripted delays, keeps the viewer
lease alive with ``hb`` events unless told to stop, records every message (ticks included) and, like the real
worker, exits when the agent's ticks stop for ``tick_timeout_seconds`` or when ``stop`` arrives. Scripts are
attributes of :class:`FakeScreenWorkerLauncher`; the spawned :class:`FakeScreenWorker` is observable through it.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from ..screen.supervisor import EventHandler, ExitHandler, StderrHandler

FAKE_ANSWER_SDP = (
    "v=0\r\no=- 3900000000000000001 2 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0 1 2\r\na=msid-semantic:WMS *\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\nc=IN IP4 0.0.0.0\r\na=ice-ufrag:pcpc\r\na=ice-pwd:pcpcpcpcpcpcpcpcpcpcpcpc\r\n"
    "a=fingerprint:sha-256 C9:CA:CB:CC:CD:CE:CF:D0:D1:D2:D3:D4:D5:D6:D7:D8:D9:DA:DB:DC:DD:DE:DF:E0:E1:E2:E3:E4:E5:E6:E7:E8\r\n"
    "a=setup:active\r\na=mid:0\r\na=sendonly\r\na=rtcp-mux\r\na=rtpmap:96 H264/90000\r\n"
    "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\n"
    "a=fingerprint:sha-256 C9:CA:CB:CC:CD:CE:CF:D0:D1:D2:D3:D4:D5:D6:D7:D8:D9:DA:DB:DC:DD:DE:DF:E0:E1:E2:E3:E4:E5:E6:E7:E8\r\n"
    "a=mid:1\r\na=inactive\r\na=rtcp-mux\r\na=rtpmap:111 opus/48000/2\r\n"
    "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\nc=IN IP4 0.0.0.0\r\n"
    "a=fingerprint:sha-256 C9:CA:CB:CC:CD:CE:CF:D0:D1:D2:D3:D4:D5:D6:D7:D8:D9:DA:DB:DC:DD:DE:DF:E0:E1:E2:E3:E4:E5:E6:E7:E8\r\n"
    "a=mid:2\r\na=sctp-port:5000\r\n"
)


@dataclass
class FakeScreenWorkerLauncher:
    """Knobs for the next spawned worker (read at spawn; change them before the start)."""

    answer_delay: float = 0.0
    fail_start: str | None = None  # error code answered to `start` instead of an answer
    fail_detail: str | None = None
    connect_delay: float | None = 0.05  # None: never "connects" (connection_failed path)
    first_frame_delay: float = 0.05  # after connected
    path: str = "direct"
    encoder: str = "h264"
    hb_interval: float | None = 0.5  # automatic phone heartbeats; None: none (the lease then expires)
    exit_on_tick_timeout: bool = True
    exit_on_stop: bool = True  # False: ignore `stop` so the supervisor has to terminate
    announce_geometry: bool = True  # answer `rebuild` / `reserved` with a new `geometry` (False: never, a stuck worker)
    spawned: list[FakeScreenWorker] = field(default_factory=list)
    spawn_fail: Exception | None = None

    @property
    def current(self) -> FakeScreenWorker | None:
        return self.spawned[-1] if self.spawned else None

    async def spawn(
        self, *, on_event: EventHandler, on_exit: ExitHandler, on_stderr: StderrHandler
    ) -> FakeScreenWorker:
        if self.spawn_fail is not None:
            raise self.spawn_fail
        worker = FakeScreenWorker(self, on_event, on_exit, on_stderr, pid=1000 + len(self.spawned))
        self.spawned.append(worker)
        return worker


class FakeScreenWorker:
    def __init__(
        self,
        script: FakeScreenWorkerLauncher,
        on_event: EventHandler,
        on_exit: ExitHandler,
        on_stderr: StderrHandler,
        *,
        pid: int,
    ) -> None:
        self._script = script
        self._on_event = on_event
        self._on_exit = on_exit
        self._on_stderr = on_stderr
        self._pid = pid
        self._loop = asyncio.get_running_loop()
        self.messages: list[dict[str, Any]] = []
        self.ticks = 0
        self.last_tick_at: float | None = None
        self.start_message: dict[str, Any] | None = None
        self.exit_code: int | None = None
        self.exit_reason: str | None = None
        self.paused = False
        self.terminated = False
        self._exited = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._tick_timeout: float = 10.0
        self.gen = 1
        self.geometry: dict[str, Any] = {}

    # ----- WorkerProcess -----
    @property
    def pid(self) -> int:
        return self._pid

    @property
    def alive(self) -> bool:
        return self.exit_code is None

    async def send(self, message: dict[str, Any]) -> bool:
        if not self.alive:
            return False
        self.messages.append(message)
        op = message.get("op")
        if op == "start":
            self.start_message = message
            self._tick_timeout = float(message.get("tick_timeout_seconds", 10.0))
            self.last_tick_at = time.monotonic()
            self._spawn_task(self._run_start())
            if self._script.exit_on_tick_timeout:
                self._spawn_task(self._watch_ticks())
        elif op == "tick":
            self.ticks += 1
            self.last_tick_at = time.monotonic()
        elif op == "pause":
            self.paused = True
        elif op == "resume":
            self.paused = False
        elif op == "stop" and self._script.exit_on_stop:
            self._spawn_task(self._exit(0, "stop"))
        elif op == "keyframe":
            self._spawn_task(self.emit({"ev": "keyframe"}))
        elif op in ("rebuild", "reserved") and self._script.announce_geometry:
            # like the real worker: a new generation with the new geometry (and, for a rebuild, a new capture size)
            self.gen += 1
            geometry = dict(self.geometry)
            if op == "rebuild":
                source = dict(message.get("source", {}))
                geometry.update(
                    {k: int(source[k]) for k in ("surface_index", "width", "height", "rotation") if k in source}
                )
            elif isinstance(message.get("surface_index"), int):
                geometry["surface_index"] = int(message["surface_index"])
            geometry.update({"gen": self.gen, "reserved": list(message.get("reserved", []))})
            self.geometry = geometry
            self._spawn_task(self.emit({"ev": "geometry", **geometry}))
        return True

    async def wait(self, limit_seconds: float) -> int | None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._exited.wait(), limit_seconds)
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        if self.alive:
            self._spawn_task(self._exit(-9, "terminated"))

    async def close(self) -> None:
        for task in self._tasks:
            if not task.done() and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # ----- test controls -----
    async def emit(self, event: dict[str, Any]) -> None:
        if self.alive:
            await self._on_event(event)

    def stderr(self, line: str) -> None:
        self._on_stderr(line)

    async def crash(self, code: int = 70) -> None:
        await self._exit(code, "crash")

    def messages_of(self, op: str) -> list[dict[str, Any]]:
        return [m for m in self.messages if m.get("op") == op]

    # ----- the scripted life -----
    def _spawn_task(self, coro: Any) -> None:
        self._tasks.append(self._loop.create_task(coro))

    async def _run_start(self) -> None:
        script = self._script
        await asyncio.sleep(script.answer_delay)
        if not self.alive:
            return
        if script.fail_start is not None:
            await self.emit(
                {
                    "ev": "error",
                    "code": script.fail_start,
                    "detail": script.fail_detail,
                    "message": "fake worker refused",
                }
            )
            await self._exit(2, "start_failed")
            return
        assert self.start_message is not None
        source = self.start_message.get("source", {})
        geometry = {
            "gen": 1,
            "surface_index": int(source.get("surface_index", 0)),
            "width": int(source.get("width", 1920)),
            "height": int(source.get("height", 1080)),
            "rotation": int(source.get("rotation", 0)),
            "reserved": list(self.start_message.get("reserved", [])),
        }
        self.geometry = dict(geometry)
        await self.emit(
            {"ev": "answer", "answer_sdp": FAKE_ANSWER_SDP, "encoder": script.encoder, "geometry": geometry}
        )
        if script.connect_delay is None:
            return
        await asyncio.sleep(script.connect_delay)
        if not self.alive:
            return
        await self.emit({"ev": "connected", "path": script.path})
        await asyncio.sleep(script.first_frame_delay)
        if not self.alive:
            return
        await self.emit({"ev": "first_frame"})
        if script.hb_interval is not None:
            while self.alive:
                await self.emit({"ev": "hb"})
                await asyncio.sleep(script.hb_interval)

    async def _watch_ticks(self) -> None:
        while self.alive:
            await asyncio.sleep(0.1)
            if self.last_tick_at is not None and time.monotonic() - self.last_tick_at > self._tick_timeout:
                await self._exit(3, "ticks_stopped")
                return

    async def _exit(self, code: int, reason: str) -> None:
        if not self.alive:
            return
        self.exit_code = code
        self.exit_reason = reason
        with contextlib.suppress(Exception):
            await self._on_event({"ev": "exiting", "reason": reason})
        self._exited.set()
        await self._on_exit(code)


__all__ = ["FAKE_ANSWER_SDP", "FakeScreenWorker", "FakeScreenWorkerLauncher"]
