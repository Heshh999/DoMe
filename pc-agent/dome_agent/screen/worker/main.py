"""The worker process (design §5.1, §7, §15): ``DoMe.exe screen-worker`` / ``python -m dome_agent.screen.worker``.

Reads length-prefixed JSON from stdin (a thread; the agent's anonymous pipe), writes events to stdout, logs to
stderr only (the agent redacts and relogs those lines; no file of its own), writes nothing else except a crash log
through ``crashguard``. Exits on pipe EOF, on ``stop``, and when the agent's liveness ticks stop for
``tick_timeout_seconds`` (so a frozen agent UI thread cannot leave an unattended stream behind bars that no longer
repaint; the Job object covers an agent that died). The aiortc / aioice loggers are pinned to WARNING here too.

**The pointer drawn by the phone** (protocol 1.5, design 2a §3.5-§3.7): a ``start`` carrying ``cursor`` (``drawn`` or
``overlay``; absent for a 1.4-stamped start) builds the source with the pointer left out when ``overlay`` was asked;
once the source opened, the worker knows the mode in effect (Windows.Graphics.Capture on an old Windows keeps it
drawn), tells the agent (``{"ev": "cursor", "mode", "source"}``), repeats it in every ``state`` message and, in
``overlay``, runs the cursor poller (:mod:`cursor`), whose ``cursor_shape`` / ``cursor_pos`` messages reach the phone
from the worker's loop (``cursor_pos`` of an older geometry generation is dropped and counted). A session whose start
carried no ``cursor`` never sends a 1.5 key or message (:meth:`WorkerApp._shape_for_vocabulary`): a 1.4 phone
validates with ``additionalProperties: false``.

**Hardware H.264** (2a §3.8): the ``start``'s ``hw_encoding`` false drops the hardware candidate; every probed
candidate is logged (``screen encoder probed``) and sent to the agent after the answer (``{"ev": "encoders",
"probed": [...]}``); the source delivers the chosen encoder's pixel format where it can.

``--self-test`` (run by the Windows installer workflow on the frozen build) reports the imports, aiortc's version,
the encoders available (with FFmpeg's reason for each one that is not), how many decoders this build has, a decoded
``lavfi`` ``testsrc`` frame, whether the PLI hook point exists and, on Windows, whether the Windows.Graphics.Capture
fallback's native module loads (no capture is started) and whether every option of the Desktop Duplication graph
parses (``capture_dda.check_graph_options``: the filters are created, the graph is never configured, so no desktop or
GPU is needed); ``--list-encoders`` lists the encoders. Both print JSON lines and never a frame.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Sequence
from typing import IO, Any

import structlog
from dome_protocol import ProtocolError

from ...logsetup import pin_media_loggers, redact_event
from .cursor import CursorPoller, CursorReader
from .encode import ProbeFailure, ProbeResult, encoder_candidates, fit_level_31, list_encoders, probe_encoders
from .pipe import read_message_sync, write_message_sync

log = structlog.get_logger(__name__)

EXIT_OK = 0
EXIT_START_FAILED = 2
EXIT_TICKS_STOPPED = 3
DEV_H264_ENV = "DOME_SCREEN_DEV_H264"  # "1": allow libx264 from the PyPI PyAV wheel (development and tests only)
CURSOR_MODES = ("drawn", "overlay")
# the worker's cursor counters in every stats event (numbers and the kind word; no key contains "png")
CURSOR_COUNTERS = (
    "cursor_pos_sent",
    "cursor_shape_sent",
    "cursor_custom_sent",
    "cursor_bitmap_max_chars",
    "cursor_dropped_gen",
)


def configure_worker_logging(level: str = "INFO") -> None:
    """stderr only, every event through the agent's redaction processor, media loggers pinned (design §4.4)."""
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        numeric = logging.INFO
    processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_event,
    ]
    structlog.configure(
        processors=[*processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(numeric)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
        )
    )
    root.addHandler(console)
    pin_media_loggers(numeric)


class WorkerApp:
    def __init__(self, stdin: IO[bytes], stdout: IO[bytes], *, dev_h264: bool | None = None) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._out_lock = threading.Lock()
        self._dev_h264 = dev_h264
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_tick: float | None = None
        self._tick_timeout = 10.0
        self._peer: Any = None
        self._track: Any = None
        self._capture_allowed = asyncio.Event()
        self._gen = 1
        self._geometry: dict[str, Any] = {}
        self._source_spec: dict[str, Any] = {}
        self._exit_code: int | None = None
        self._exit_reason = ""
        self._stop_event = asyncio.Event()
        # the pointer (1.5): what the start asked (None: a 1.4 session), what is in effect (the guess until the source
        # opened), the poller and the messages it handed over from its thread
        self._cursor_requested: str | None = None
        self._cursor_effective: str | None = None
        self._cursor_drawn_asked = True  # the source keeps the pointer in the picture (anything but overlay)
        self._pix_fmt = "yuv420p"  # what the chosen encoder is fed (sources deliver it where they can)
        self._cursor_poller: CursorPoller | None = None
        self._cursor_lock = threading.Lock()
        self._cursor_pending_pos: dict[str, Any] | None = None  # one slot: the latest position wins
        self._cursor_pending_shapes: collections.deque[dict[str, Any]] = collections.deque(maxlen=4)
        self._cursor_flush_scheduled = False
        self.cursor_counts: dict[str, int] = dict.fromkeys(CURSOR_COUNTERS, 0)
        self.cursor_last_kind: str | None = None
        self._cursor_retired = {"bitmap_too_large": 0, "poll_errors": 0}  # of pollers stopped meanwhile
        self.dropped_1_4 = 0  # 1.5 messages refused in a session whose start carried no cursor

    # ----- plumbing -----
    def emit(self, event: dict[str, Any]) -> None:
        with self._out_lock:
            try:
                write_message_sync(self._stdout, event)
            except (BrokenPipeError, OSError, ProtocolError):
                self._request_exit(EXIT_OK, "pipe_closed")

    def _request_exit(self, code: int, reason: str) -> None:
        if self._exit_code is None:
            self._exit_code = code
            self._exit_reason = reason
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._stop_event.set)

    def _reader(self) -> None:
        loop = self._loop
        assert loop is not None
        while True:
            try:
                message = read_message_sync(self._stdin)
            except (ProtocolError, OSError):
                message = None
            try:
                loop.call_soon_threadsafe(self._queue.put_nowait, message)
            except RuntimeError:
                return  # the loop is already closed: the worker is exiting
            if message is None:
                return

    # ----- the loop -----
    async def run(self) -> int:
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._reader, name="dome-screen-worker-stdin", daemon=True).start()
        dispatch = asyncio.ensure_future(self._dispatch())
        watchdog = asyncio.ensure_future(self._watch_ticks())
        await self._stop_event.wait()
        for task in (dispatch, watchdog):
            task.cancel()
        await asyncio.gather(dispatch, watchdog, return_exceptions=True)
        await self._shutdown()
        self.emit({"ev": "exiting", "reason": self._exit_reason or "stop"})
        return self._exit_code if self._exit_code is not None else EXIT_OK

    async def _dispatch(self) -> None:
        while True:
            message = await self._queue.get()
            if message is None:
                self._request_exit(EXIT_OK, "pipe_closed")
                return
            try:
                await self._handle(message)
            except ProtocolError as exc:
                self.emit({"ev": "error", "code": exc.code, "detail": _detail(exc), "message": exc.message[:200]})
                if self._peer is None:
                    self._request_exit(EXIT_START_FAILED, "start_failed")
                    return
            except Exception as exc:  # noqa: BLE001 - never die silently: the agent ends the session capture_failed
                log.error("screen worker op failed", op=str(message.get("op"))[:32], error=exc.__class__.__name__)
                self.emit(
                    {
                        "ev": "error",
                        "code": "SCREEN_CAPTURE_UNAVAILABLE",
                        "detail": "driver",
                        "message": exc.__class__.__name__,
                    }
                )
                self._request_exit(EXIT_START_FAILED, "failed")
                return

    async def _watch_ticks(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            if self._last_tick is not None and time.monotonic() - self._last_tick > self._tick_timeout:
                log.warning("agent liveness ticks stopped; exiting")
                self._request_exit(EXIT_TICKS_STOPPED, "ticks_stopped")
                return

    async def _handle(self, message: dict[str, Any]) -> None:
        op = message.get("op")
        if op == "tick":
            self._last_tick = time.monotonic()
        elif op == "start":
            await self._start(message)
        elif op == "stop":
            self._request_exit(EXIT_OK, "stop")
        elif op == "pause":
            if self._track is not None:
                self._track.pause()
                if self._cursor_poller is not None:
                    self._cursor_poller.pause()  # one visible: false, then nothing while paused
                    self._cursor_flush()
                reason = message.get("reason")
                state: dict[str, Any] = {"type": "state", "state": "paused"}
                if reason in ("secure_desktop", "indicator"):
                    state["reason"] = reason
                self._send_dc(self._with_cursor_mode(state))
        elif op == "resume":
            if self._track is not None:
                self._track.resume()
                self._send_dc(self._state_message())
                if self._cursor_poller is not None:
                    self._cursor_poller.resume()  # shape and position again at once
        elif op == "keyframe":
            if self._track is not None:
                self._track.force_keyframe(source="op")
        elif op == "allowance":
            self._send_dc(
                {
                    "type": "allowance",
                    "unlimited": bool(message.get("unlimited", False)),
                    "remaining_seconds": message.get("remaining_seconds")
                    if isinstance(message.get("remaining_seconds"), int)
                    else None,
                    "resets_at": message.get("resets_at") if isinstance(message.get("resets_at"), str) else None,
                }
            )
        elif op == "restricted":
            restricted: dict[str, Any] = {"type": "restricted", "restricted": bool(message.get("restricted", False))}
            if message.get("reason") in ("elevated", "secure_desktop", "locked", "permission_ui"):
                restricted["reason"] = message["reason"]
            self._send_dc(restricted)
        elif op == "reserved":
            if self._geometry:
                update: dict[str, Any] = {"reserved": list(message.get("reserved", []))[:8]}
                if isinstance(message.get("surface_index"), int):
                    update["surface_index"] = int(message["surface_index"])
                self._announce_geometry(update)
        elif op == "rebuild":
            self._rebuild(message)
        else:
            log.warning("unknown pipe op dropped", op=str(op)[:32])

    def _announce_geometry(self, update: dict[str, Any]) -> None:
        """A new generation: the agent mirrors it (taps carry the ``gen`` the phone saw), the phone re-maps on the
        data channel's ``geometry``, and the next access unit is an IDR."""
        self._gen += 1
        self._geometry = {**self._geometry, **update, "gen": self._gen}
        if self._cursor_poller is not None:
            # before the geometry goes out: positions of the new generation only, shape and position sent again
            self._cursor_poller.set_surface(self._monitor_rect(), self._gen)
        self.emit({"ev": "geometry", **self._geometry})
        self._send_dc({"type": "geometry", **self._geometry})
        if self._track is not None:
            self._track.force_keyframe(urgent=True)  # the phone re-maps on this picture: never delayed

    def _rebuild(self, message: dict[str, Any]) -> None:
        """The streamed monitor changed size, rotation or place (design §5.2 "rebuild instead of fail"): a new source
        at the new level-3.1 size replaces the old one inside the track (the encoder reopens with an IDR), and a new
        ``gen`` is announced. A source that cannot be built is the honest ``SCREEN_CAPTURE_UNAVAILABLE``: the agent
        ends the session ``capture_failed``."""
        track = self._track
        if track is None or not self._geometry:
            return
        spec = {**self._source_spec, **{k: v for k, v in dict(message.get("source", {})).items() if k != "kind"}}
        # the start's pre-flight verdict was about the monitor the start picked, at that moment: the rebuilt source
        # tries Desktop Duplication again (a failure falls back to Windows.Graphics.Capture as before)
        spec.pop("preflight", None)
        width, height = fit_level_31(int(spec.get("width", 1280)), int(spec.get("height", 720)))
        source = _build_source(
            str(spec.get("kind", "ddagrab")),
            spec,
            width,
            height,
            int(spec.get("fps", 30)),
            cursor_drawn=self._cursor_drawn_asked,
            pix_fmt=self._pix_fmt,
        )
        self._source_spec = spec
        track.replace_source(source, width, height)
        rotation = int(spec.get("rotation", 0))
        self._announce_geometry(
            {
                "surface_index": int(spec.get("surface_index", 0)),
                "width": width,
                "height": height,
                "rotation": rotation if rotation in (0, 90, 180, 270) else 0,
                "reserved": list(message.get("reserved", []))[:8],
            }
        )
        log.info("screen capture rebuilt", size=f"{width}x{height}")

    async def _start(self, message: dict[str, Any]) -> None:
        if self._peer is not None:
            raise ProtocolError("SCREEN_SESSION_OWNED", "this worker already serves a session")
        self._last_tick = time.monotonic()
        self._tick_timeout = float(message.get("tick_timeout_seconds", 10.0))
        source_spec = dict(message.get("source", {}))
        self._source_spec = source_spec
        kind = str(source_spec.get("kind", "ddagrab"))
        fps = int(source_spec.get("fps", 30))
        src_w, src_h = int(source_spec.get("width", 1280)), int(source_spec.get("height", 720))
        width, height = fit_level_31(src_w, src_h)
        dev_h264 = (
            self._dev_h264 if self._dev_h264 is not None else (kind == "fake" or os.environ.get(DEV_H264_ENV) == "1")
        )
        preference = [c for c in message.get("encoder_preference", ["h264", "vp8"]) if c in ("h264", "vp8")]
        cursor = message.get("cursor")
        self._cursor_requested = cursor if cursor in CURSOR_MODES else None
        self._cursor_effective = self._cursor_requested  # the guess until the source opened
        self._cursor_drawn_asked = self._cursor_requested != "overlay"
        hw = bool(message.get("hw_encoding", True))  # the agent's DOME_SCREEN_H264_HW=0 sends false
        failures: list[ProbeFailure] = []
        results: list[ProbeResult] = []
        try:
            # at the session's real output size: a transform may refuse a size the old 64x64 probe never asked for
            choice = probe_encoders(
                encoder_candidates(preference, dev_h264=dev_h264, hw=hw), width, height, failures, results
            )
        finally:
            for failure in failures:  # FFmpeg's reason per candidate: the diagnostics bundle says why it was skipped
                # the key is "encoder": any key containing "candidate" is redacted (ICE candidates never reach a log)
                log.info("screen encoder candidate unavailable", encoder=failure.candidate, reason=failure.reason)
            for result in results:  # never a parameter set's bytes: where they came from, in one word
                log.info(
                    "screen encoder probed",
                    encoder=result.encoder,
                    ok=result.ok,
                    gated=result.gated,
                    frames_in=result.frames_in,
                    open_ms=result.open_ms,
                    mft=result.mft,
                    parameter_sets=result.parameter_sets,
                    reason=result.reason,
                )
        self._pix_fmt = choice.pix_fmt
        source = _build_source(
            kind, source_spec, width, height, fps, cursor_drawn=self._cursor_drawn_asked, pix_fmt=self._pix_fmt
        )
        from .rtc import ScreenPeer  # aiortc is imported only when a session really starts
        from .track import ScreenTrack

        bitrate = {k: int(v) for k, v in dict(message.get("bitrate_kbps", {})).items()}
        track = ScreenTrack(
            source=source,
            choice=choice,
            width=width,
            height=height,
            fps=fps,
            bitrate_kbps=bitrate.get("direct", 1500),
            capture_allowed=self._capture_allowed,
            emit=self.emit,
            on_source_open=self._on_source_open,
        )
        peer = ScreenPeer(
            offer_sdp=str(message.get("offer_sdp", "")),
            stun_urls=[u for u in message.get("stun_urls", []) if isinstance(u, str)],
            track=track,
            capture_allowed=self._capture_allowed,
            emit=self.emit,
            bitrate_kbps=bitrate,
            relayed_max_kbps=message.get("relayed_max_kbps")
            if isinstance(message.get("relayed_max_kbps"), int)
            else None,
            extra_stats=self._cursor_stats,
        )
        try:
            answer_sdp = await peer.answer()
        except Exception:
            await peer.close()
            raise
        self._track, self._peer = track, peer
        self._geometry = {
            "gen": self._gen,
            "surface_index": int(source_spec.get("surface_index", 0)),
            "width": width,
            "height": height,
            "rotation": int(source_spec.get("rotation", 0))
            if int(source_spec.get("rotation", 0)) in (0, 90, 180, 270)
            else 0,
            "reserved": list(message.get("reserved", []))[:8],
        }
        self.emit(
            {
                "ev": "answer",
                "answer_sdp": answer_sdp,
                "encoder": choice.kind,
                "geometry": dict(self._geometry),
                "pli_hook": "present" if peer.pli_hook else "missing",
            }
        )
        self.emit({"ev": "encoders", "probed": [result.as_dict() for result in results]})
        log.info(
            "screen worker answered",
            encoder=choice.label,
            size=f"{width}x{height}",
            fps=fps,
            pli_hook=peer.pli_hook,
            preference=",".join(preference),
        )
        # the first geometry and state reach the phone once the channel opens
        asyncio.ensure_future(self._announce_when_open())

    async def _announce_when_open(self) -> None:
        for _ in range(400):  # up to ~20 s: the connect timeout
            peer = self._peer
            if peer is None:
                return
            channel = peer.channel
            if channel is not None and channel.readyState == "open":
                self._send_dc({"type": "geometry", **self._geometry})
                self._send_dc(self._state_message())
                if self._cursor_poller is not None:
                    self._cursor_poller.resend()  # what it reported before the channel opened was not delivered
                return
            await asyncio.sleep(0.05)

    def _state_message(self) -> dict[str, Any]:
        state: dict[str, Any] = {"type": "state", "state": "sending"}
        if self._track is not None and self._track.choice.kind in ("h264", "vp8"):
            state["encoder"] = self._track.choice.kind
        if self._peer is not None and self._peer.data_saver:
            state["data_saver"] = True
        return self._with_cursor_mode(state)

    def _with_cursor_mode(self, state: dict[str, Any]) -> dict[str, Any]:
        """``cursor`` in EVERY state message (sending and paused) of a session whose start carried it, once the mode
        in effect is known (rules.screen_cursor (2))."""
        if self._cursor_requested is not None and self._cursor_effective in CURSOR_MODES:
            state["cursor"] = self._cursor_effective
        return state

    def _shape_for_vocabulary(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """The message as this session's phone may receive it (rules.screen_cursor (6)): in a session whose start
        carried no ``cursor`` (a 1.4 phone, which validates with additionalProperties:false) ``state`` loses its
        ``cursor`` key and ``cursor_pos`` / ``cursor_shape`` are not sent at all (None; counted ``dropped_1_4``)."""
        if self._cursor_requested is not None:
            return message
        kind = message.get("type")
        if kind in ("cursor_pos", "cursor_shape"):
            self.dropped_1_4 += 1
            return None
        if kind == "state" and "cursor" in message:
            return {key: value for key, value in message.items() if key != "cursor"}
        return message

    def _send_dc(self, message: dict[str, Any]) -> bool:
        """Every outbound data-channel message: shaped for the session's vocabulary, validated by the peer, a
        ``cursor_pos`` on the cursor channel when the phone opened it. True when it was sent."""
        peer = self._peer
        if peer is None:
            return False
        shaped = self._shape_for_vocabulary(message)
        if shaped is None:
            return False
        try:
            if shaped.get("type") == "cursor_pos":
                return bool(peer.send_cursor_pos(shaped))
            return bool(peer.send_dc(shaped))
        except ProtocolError as exc:
            log.error("outbound data-channel message failed the vocabulary check; not sent", code=exc.code)
        except Exception as exc:  # noqa: BLE001
            log.debug("data channel send failed", error=exc.__class__.__name__)
        return False

    # ----- the pointer (1.5) -----
    def _monitor_rect(self) -> tuple[int, int, int, int]:
        """The streamed monitor in physical pixels (the source spec's rectangle, as the agent maps taps)."""
        spec = self._source_spec
        return (
            int(spec.get("left", 0)),
            int(spec.get("top", 0)),
            int(spec.get("width", 1280)),
            int(spec.get("height", 720)),
        )

    def _on_source_open(self, info: dict[str, Any]) -> None:
        """The track's worker thread, after a source opened (or a fallback inside it changed what it is)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._source_opened, dict(info))

    def _source_opened(self, info: dict[str, Any]) -> None:
        """The loop: the mode in effect is known now (rules.screen_cursor (2)); the agent hears it, the phone gets it
        in a state message, and the poller runs in overlay only."""
        if self._cursor_requested is None or self._track is None or self._peer is None:
            return
        drawn = self._cursor_requested == "drawn" or bool(info.get("cursor_drawn", True))
        self._cursor_effective = "drawn" if drawn else "overlay"
        source = str(info.get("source", "fake"))
        self.emit({"ev": "cursor", "mode": self._cursor_effective, "source": source})
        if not self._track.paused:
            self._send_dc(self._state_message())
        if self._cursor_effective == "overlay":
            self._start_cursor_poller()
        else:
            self._stop_cursor_poller()

    def _start_cursor_poller(self) -> None:
        if self._cursor_poller is not None:
            return
        try:
            reader = _cursor_reader(self._source_spec, self._monitor_rect())
        except Exception as exc:  # noqa: BLE001 - a malformed test script, a reader that cannot load
            log.warning("cursor reporting unavailable", error=exc.__class__.__name__)
            return
        poller = CursorPoller(reader, on_pos=self._cursor_on_pos, on_shape=self._cursor_on_shape)
        poller.set_surface(self._monitor_rect(), self._gen)
        if self._track is not None and self._track.paused:
            poller.pause()
        self._cursor_poller = poller
        poller.start()

    def _stop_cursor_poller(self) -> None:
        poller, self._cursor_poller = self._cursor_poller, None
        if poller is None:
            return
        poller.stop()
        self._cursor_retired["bitmap_too_large"] += poller.bitmap_too_large
        self._cursor_retired["poll_errors"] += poller.poll_errors
        with self._cursor_lock:
            self._cursor_pending_pos = None
            self._cursor_pending_shapes.clear()

    def _cursor_on_pos(self, message: dict[str, Any]) -> None:
        """The poller's thread (or the loop, for a pause): one slot, the latest position wins."""
        with self._cursor_lock:
            self._cursor_pending_pos = message
        self._schedule_cursor_flush()

    def _cursor_on_shape(self, message: dict[str, Any]) -> None:
        with self._cursor_lock:
            self._cursor_pending_shapes.append(message)
        self._schedule_cursor_flush()

    def _schedule_cursor_flush(self) -> None:
        with self._cursor_lock:
            if self._cursor_flush_scheduled:
                return
            self._cursor_flush_scheduled = True
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):  # the loop closed meanwhile: the worker is exiting
            loop.call_soon_threadsafe(self._cursor_flush)

    def _cursor_flush(self) -> None:
        """The loop: shapes before the position; a position of an older geometry generation is dropped."""
        with self._cursor_lock:
            self._cursor_flush_scheduled = False
            shapes = list(self._cursor_pending_shapes)
            self._cursor_pending_shapes.clear()
            position, self._cursor_pending_pos = self._cursor_pending_pos, None
        if self._cursor_effective != "overlay":
            return
        counts = self.cursor_counts
        for shape in shapes:
            if not self._send_dc(shape):
                continue
            counts["cursor_shape_sent"] += 1
            self.cursor_last_kind = str(shape.get("kind"))
            if shape.get("kind") == "custom":
                counts["cursor_custom_sent"] += 1
                counts["cursor_bitmap_max_chars"] = max(counts["cursor_bitmap_max_chars"], len(str(shape["png"])))
        if position is None:
            return
        if position.get("gen") != self._gen:
            counts["cursor_dropped_gen"] += 1
            return
        if self._send_dc(position):
            counts["cursor_pos_sent"] += 1

    def _cursor_stats(self) -> dict[str, Any]:
        """The cursor counters for the stats event (numbers and the last kind word; never a position or a bitmap)."""
        poller = self._cursor_poller
        too_large = self._cursor_retired["bitmap_too_large"] + (poller.bitmap_too_large if poller else 0)
        poll_errors = self._cursor_retired["poll_errors"] + (poller.poll_errors if poller else 0)
        return {
            **self.cursor_counts,
            "cursor_bitmap_too_large": too_large,
            "cursor_poll_errors": poll_errors,
            "cursor_last_kind": self.cursor_last_kind,
            "dropped_1_4": self.dropped_1_4,
        }

    async def _shutdown(self) -> None:
        self._stop_cursor_poller()
        peer, self._peer = self._peer, None
        track, self._track = self._track, None
        if peer is not None:
            await peer.close()  # closes the track too, waiting for the in-flight capture call
        elif track is not None:
            await track.aclose()


def _cursor_reader(spec: dict[str, Any], rect: tuple[int, int, int, int]) -> CursorReader:
    """The pointer behind the poller: the fake source's (its ``cursor_script``, else a walk) or Windows'."""
    if str(spec.get("kind", "ddagrab")) == "fake":
        from .fake import FakeCursorReader

        return FakeCursorReader(spec.get("cursor_script"), rect=rect)
    from .cursor_win import Win32CursorReader

    return Win32CursorReader()


def _build_source(
    kind: str, spec: dict[str, Any], width: int, height: int, fps: int, *, cursor_drawn: bool, pix_fmt: str
) -> Any:
    """Construct the capture source for ``kind`` WITHOUT opening it (rules.screen_sessions, design §5.1: no frame
    exists for an unauthenticated peer). The only ``open()`` is the track's, once ``capture_allowed`` is set by DTLS
    connecting to the certificate the phone signed; a rebuild's source is handed over unopened the same way. A
    source that cannot open then is reported as the worker's ``error`` and the agent ends ``capture_failed``.

    ``cursor_drawn`` False (an overlay session) leaves the pointer out: ``ddagrab`` ``draw_mouse=0``,
    Windows.Graphics.Capture ``cursor_capture=False``. ``pix_fmt`` is the chosen encoder's: Windows.Graphics.Capture
    converts to it; Desktop Duplication delivers NV12 anyway; the fakes stay yuv420p (their spec's ``pix_fmt`` may say
    ``nv12``) so the track's conversion is exercised on Linux."""
    if kind == "fake":
        from .fake import build_fake_source

        return build_fake_source(spec, width, height, fps, cursor_drawn=cursor_drawn)
    if kind in ("ddagrab", "wgc"):
        if sys.platform != "win32":
            raise ProtocolError(
                "SCREEN_CAPTURE_UNAVAILABLE", "screen capture needs Windows", detail={"detail": "unsupported_display"}
            )
        index = int(spec.get("surface_index", 0))
        primary = bool(spec.get("primary", index == 0))
        expected = (int(spec.get("width", width)), int(spec.get("height", height)))
        origin = (int(spec.get("left", 0)), int(spec.get("top", 0)))

        def wgc() -> Any:
            from .capture_wgc import WgcSource

            return WgcSource(
                index,
                width,
                height,
                fps,
                primary=primary,
                expected_size=expected,
                origin=origin,
                cursor_capture=None if cursor_drawn else False,
                pix_fmt=pix_fmt,
            )

        if kind == "wgc":
            return wgc()
        from .capture_dda import DdagrabSource, DdaWithFallback, locate_ddagrab_output, preflight_skip

        def fell_back(exc: ProtocolError) -> None:
            # the message carries FFmpeg's own reason, sanitised (capture_dda.ffmpeg_error_text), or why Desktop
            # Duplication was skipped (the monitor is on another adapter): the diagnostics bundle then says why, not
            # only the contract's detail word
            log.warning(
                "Desktop Duplication unavailable; trying Windows.Graphics.Capture",
                detail=_detail(exc) or "driver",
                reason=exc.message,
            )

        def locate() -> Any:
            # at open time (after DTLS): ddagrab's output_idx is an index among the DEFAULT adapter's outputs, found by
            # the monitor's rectangle; the surface index stands in when DXGI cannot answer
            return locate_ddagrab_output(
                left=origin[0], top=origin[1], width=expected[0], height=expected[1], fallback_idx=index
            )

        # the agent's pre-flight may have found Desktop Duplication failing (spec["preflight"]): skipped, WGC at once
        return DdaWithFallback(
            DdagrabSource(index, width, height, fps, draw_mouse=cursor_drawn),
            wgc,
            on_fallback=fell_back,
            locate=locate,
            skip=preflight_skip(spec.get("preflight")),
        )
    raise ProtocolError(
        "SCREEN_CAPTURE_UNAVAILABLE", f"unknown source kind {kind!r}", detail={"detail": "unsupported_display"}
    )


def _detail(exc: ProtocolError) -> str | None:
    """The contract's short detail word: ``detail`` for the capture / encoder errors, ``reason`` for
    ``SCREEN_OFFER_INVALID`` (the agent puts it back under the right key). Never the SDP."""
    if not isinstance(exc.detail, dict):
        return None
    detail = exc.detail.get("detail", exc.detail.get("reason"))
    return detail if isinstance(detail, str) else None


def self_test(*, dev_h264: bool) -> dict[str, Any]:
    """What the frozen build can do here (design §15 ``--self-test``); never a frame, never an address."""
    report: dict[str, Any] = {"ok": True, "checks": {}}

    def check(name: str, fn: Any) -> None:
        try:
            report["checks"][name] = {"ok": True, "value": fn()}
        except Exception as exc:  # noqa: BLE001
            report["ok"] = False
            report["checks"][name] = {"ok": False, "error": f"{exc.__class__.__name__}: {str(exc)[:120]}"}

    def imports() -> dict[str, str]:
        import aiortc
        import av

        return {"aiortc": aiortc.__version__, "av": av.__version__}

    def decoders() -> dict[str, Any]:
        import av

        names = sorted(name for name in av.codecs_available if _is_decoder(av, name))
        return {"count": len(names), "exactly_wrapped_avframe": names == ["wrapped_avframe"], "sample": names[:5]}

    def lavfi_frame() -> dict[str, Any]:
        from .fake import LavfiTestSource

        src = LavfiTestSource(64, 64, 30)
        src.open()
        try:
            frame = src.read()
        finally:
            src.close()
        if frame is None:
            raise RuntimeError("lavfi testsrc delivered no frame")
        return {"width": frame.width, "height": frame.height, "format": frame.format.name}

    def hook() -> bool:
        from .rtc import pli_hook_available

        return pli_hook_available()

    def wgc_native() -> dict[str, Any]:
        from .capture_wgc import load_native

        native = load_native()  # loads the module only: no capture session, no frame
        return {"classes": sorted(name for name in dir(native) if name.startswith("Native"))}

    def ddagrab_graph_options() -> dict[str, Any]:
        # the real graph's filters created with their options, never configured: an option name this FFmpeg lacks
        # fails here ("Option not found"), as scale_d3d11's w/h did on every PC in device round 2
        from .capture_dda import check_graph_options, ddagrab_graph

        return {"filters": check_graph_options(ddagrab_graph(0, 1280, 720, 30))}

    def dxgi_outputs() -> dict[str, Any]:
        # numbers only: how many adapters DXGI lists and how many outputs each has (ddagrab's output_idx counts the
        # first adapter's); a build machine without a desktop shows a software adapter with none
        from ...platform.windows.dxgi import enumerate_adapters

        adapters = enumerate_adapters()
        return {
            "adapters": len(adapters),
            "outputs_per_adapter": [len(a.outputs) for a in adapters],
            "software_adapters": sum(1 for a in adapters if a.software),
        }

    check("imports", imports)
    check("encoders", lambda: list_encoders(dev_h264=dev_h264))
    check("decoders", decoders)
    check("lavfi_testsrc_frame", lavfi_frame)
    check("pli_hook", hook)
    if sys.platform == "win32":  # windows-capture, DXGI, ddagrab and scale_d3d11 are Windows only
        check("wgc_native", wgc_native)
        check("ddagrab_graph_options", ddagrab_graph_options)
        check("dxgi_outputs", dxgi_outputs)
    return report


def _is_decoder(av: Any, name: str) -> bool:
    try:
        av.Codec(name, "r")
    except Exception:  # noqa: BLE001
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="screen-worker", description="DoMe Screen view worker (started by the agent)")
    parser.add_argument("--self-test", action="store_true", help="report what this build can do, as JSON")
    parser.add_argument("--list-encoders", action="store_true", help="list the video encoders available here, as JSON")
    parser.add_argument(
        "--dev-h264", action="store_true", help=f"allow the development-only libx264 (also {DEV_H264_ENV}=1)"
    )
    parser.add_argument("--log-level", default=os.environ.get("DOME_AGENT_LOG_LEVEL", "INFO"))
    return parser


def run_worker_process(argv: Sequence[str] | None = None) -> int:
    from ...crashguard import run_guarded

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    configure_worker_logging(str(args.log_level))
    dev_h264 = bool(args.dev_h264) or os.environ.get(DEV_H264_ENV) == "1"
    if args.self_test:
        report = self_test(dev_h264=dev_h264)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ok"] else 1
    if args.list_encoders:
        print(json.dumps(list_encoders(dev_h264=dev_h264)))
        return 0

    def main() -> int:
        # The stdin reader is a daemon thread blocked in read(): on the raw FileIO it holds no buffer lock, so the
        # interpreter can finalise while it is still blocked (a BufferedReader there aborts the exit with
        # "could not acquire lock ... at interpreter shutdown").
        stdin: IO[bytes] = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
        return asyncio.run(WorkerApp(stdin, sys.stdout.buffer).run())

    return run_guarded(main, program="DoMe screen-worker", argv=list(argv or []))


__all__ = [
    "EXIT_START_FAILED",
    "EXIT_TICKS_STOPPED",
    "WorkerApp",
    "configure_worker_logging",
    "run_worker_process",
    "self_test",
]
