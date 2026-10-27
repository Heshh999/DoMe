"""The send-only video track (design §5.2, §5.3): capture → encode → aiortc's pre-encoded path.

* **No capture before DTLS**: ``recv()`` first waits for ``capture_allowed``, which :mod:`rtc` sets when the peer
  connection reaches ``connected`` (the handshake with the certificate the phone signed). The source is opened only
  then, so no frame exists for an unauthenticated peer.
* **Variable frame rate with a floor**: the source returns None while the screen is still; the track keeps the last
  raw frame and re-encodes it at ≥ 1 fps (``floor_seconds``). Encoded packets are never re-sent (a P-frame fed to the
  decoder twice corrupts the picture), so two consecutive still-frame packets are distinct encoder outputs.
* **H.264**: :class:`~.encode.H264Encoder` produces Annex B access units with SPS/PPS before every IDR; they go to
  aiortc as ``av.Packet`` (``encoder.pack``). **VP8**: the raw ``av.VideoFrame`` goes to aiortc's own libvpx encoder.
* **Pause**: a paused track reads and sends nothing (the secure desktop, a missing bar); resuming forces an IDR.
* **Requested IDRs are rate-limited** (design §5.3, "on PLI/FIR, rate-limited to 2/s"): a PLI/FIR, the phone's
  ``keyframe`` message or the pipe's ``keyframe`` op gets an IDR at most once per 500 ms; a request inside that window
  is coalesced into the next allowed slot (Safari repeats PLIs under loss; an IDR storm would only add bytes to a
  lossy, possibly relayed path). Join, resume, a new geometry and an encoder reopen are never delayed.
* A bitrate step reopens the encoder in the worker thread and forces an IDR.
* **H.264 drain pump** (device round 2): an encoder that holds N frames (a Media Foundation transform outside
  low-latency mode) fed at the 1 fps floor on a still screen took N seconds to give up a new picture or a requested
  IDR (5.5 to 9.2 s to the first packet on the founder's PC). After a newly captured frame or a forced IDR the track
  remembers its pts and, while the screen is still, re-encodes the held frame at the capture cadence (new P-frames of
  the same picture, never a re-sent packet) until a packet with that pts comes out (``pts >= target - 2``: mfenc maps
  pts to 100 ns sample times and back), for at most :data:`PUMP_MAX_SECONDS`; then the 1 fps floor as before. The
  latency is about N / fps whatever the transform does. VP8 is unchanged (aiortc's libvpx path does not buffer).
* **Keyframe facts and stall detection**: the time from the first frame given to the encoder to the first IDR out
  (``screen first keyframe``, logged once), and from each requested IDR to its packet. No IDR out
  :data:`KEYFRAME_DEADLINE_SECONDS` after the first submit, or an exception from the encoder, emits
  ``{"ev": "encoder_stalled", ...}`` once (the agent then offers VP8 first on the next start); an encoder that raised
  or could not open stops the track instead of escaping into aiortc's send loop, which would stop sending while the
  agent believed the session live. :meth:`ScreenTrack.stats` returns the counters for the peer's stats event.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import time
from collections.abc import Callable
from typing import Any

import structlog
from aiortc.mediastreams import MediaStreamTrack

from .encode import NAL_IDR, VIDEO_TIME_BASE, EncoderChoice, H264Encoder, nal_types
from .fake import FrameSource

log = structlog.get_logger(__name__)

Emit = Callable[[dict[str, Any]], None]
FLOOR_SECONDS = 1.0
IDR_MIN_INTERVAL_SECONDS = 0.5  # requested IDRs at most twice a second (design §5.3)
PUMP_MAX_SECONDS = 3.0  # the drain pump gives up on a picture after this long, then the 1 fps floor rules again
PUMP_PTS_TOLERANCE = 2  # 90 kHz ticks: mfenc maps pts to 100 ns sample times and back
KEYFRAME_DEADLINE_SECONDS = 5.0  # no IDR out this long after the first frame went in: encoder_stalled
KEYFRAME_SOURCES = ("pli", "dc", "op", "periodic")


class ScreenTrack(MediaStreamTrack):
    kind = "video"

    def __init__(
        self,
        *,
        source: FrameSource,
        choice: EncoderChoice,
        width: int,
        height: int,
        fps: int,
        bitrate_kbps: int,
        capture_allowed: asyncio.Event,
        emit: Emit,
        floor_seconds: float = FLOOR_SECONDS,
    ) -> None:
        super().__init__()
        self._source = source
        self.choice = choice
        self.width, self.height = width, height
        self.fps = max(1, int(fps))
        self.bitrate_kbps = int(bitrate_kbps)
        self._capture_allowed = capture_allowed
        self._emit = emit
        self.floor_seconds = floor_seconds
        self._running = asyncio.Event()
        self._running.set()
        self._encoder: H264Encoder | None = None
        self._opened = False
        self._force_idr = True  # the first access unit is an IDR
        self._idr_requested = False  # a PLI / keyframe request waiting for its slot
        self._last_idr_at = -IDR_MIN_INTERVAL_SECONDS
        self.idr_min_interval = IDR_MIN_INTERVAL_SECONDS
        self.keyframe_requests = 0
        # VP8: aiortc's own encoder makes the keyframe; the peer hands over the call that tells it to (PLI hook)
        self.vp8_keyframe: Callable[[], None] | None = None
        self._queue: collections.deque[Any] = collections.deque()
        self._last_frame: Any | None = None
        self._last_sent_at = 0.0
        self._start = time.monotonic()
        self._first_frame_reported = False
        self._pending_bitrate: int | None = None
        self._pending_source: tuple[FrameSource, int, int] | None = None  # a rebuild waiting for the capture loop
        self._inflight: asyncio.Future[Any] | None = None  # the worker-thread call in progress (never two at once)
        self.frames_sent = 0
        self.frames_captured = 0
        self.frames_refreshed = 0  # still-frame re-encodes (the 1 fps floor)
        self.frames_pumped = 0  # still-frame re-encodes that drain the encoder towards a waited-for picture
        self.pump_timeouts = 0
        self.frames_encoded = 0  # frames given to an H.264 encoder (every encoder of this track)
        self.access_units = 0
        self.au_bytes = 0
        self.idr_sent = 0
        self.rebuilds = 0
        self.keyframe_requests_by = dict.fromkeys(KEYFRAME_SOURCES, 0)
        self._awaiting_pts: int | None = None  # the pts of a picture that must still come out of the encoder
        self._awaiting_since = 0.0
        self._first_submit_at: float | None = None
        self._kf_requested_at: float | None = None  # the oldest IDR request (or the join) not yet answered by a packet
        self.first_keyframe_ms: int | None = None
        self.last_keyframe_ms: int | None = None
        self.max_keyframe_ms: int | None = None
        self.stalled: str | None = None  # the reason of the one encoder_stalled event
        self._retired_counts = {"prepended": 0, "non_annexb": 0}  # from encoders closed by a rebuild
        self.sent_nal_types: collections.deque[list[int]] = collections.deque(maxlen=256)
        self.sent_packet_hashes: collections.deque[int] = collections.deque(maxlen=256)

    # ----- controls from the peer / the agent -----
    def force_keyframe(self, *, urgent: bool = False, source: str = "pli") -> None:
        """An IDR on a coming access unit. A network request (``urgent`` False) waits for its 500 ms slot; ``urgent``
        (a new geometry generation) is sent on the next access unit. ``source`` (``pli``, ``dc``: the phone's
        ``keyframe`` message, ``op``: the agent's, ``periodic``) is counted for the stats."""
        if self._kf_requested_at is None:
            self._kf_requested_at = time.monotonic()
        if urgent:
            self._force_idr = True
            return
        self.keyframe_requests += 1
        key = source if source in self.keyframe_requests_by else "op"
        self.keyframe_requests_by[key] += 1
        self._idr_requested = True

    def _idr_due(self, now: float) -> bool:
        """An IDR would be taken on this access unit (:meth:`_take_idr` without taking it)."""
        return self._force_idr or (self._idr_requested and now - self._last_idr_at >= self.idr_min_interval)

    def _take_idr(self, now: float) -> bool:
        """Whether this access unit is an IDR: an urgent one, or a request whose slot has come. Any IDR sent also
        answers the requests that arrived before it."""
        if self._idr_requested and now - self._last_idr_at >= self.idr_min_interval:
            self._force_idr = True
        force, self._force_idr = self._force_idr, False
        if force:
            self._idr_requested = False
            self._last_idr_at = now
        return force

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._force_idr = True
        if self.first_keyframe_ms is None:
            self._first_submit_at = None  # the keyframe deadline counts from the next frame, not across the pause
        self._running.set()

    @property
    def source_size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def replace_source(self, source: FrameSource, width: int, height: int) -> None:
        """A rebuild (the streamed monitor changed): the capture loop swaps the source between two reads, never
        under a read in progress, reopens the encoder at the new size and sends an IDR. The held still frame is
        dropped (it has the old size)."""
        previous = self._pending_source
        self._pending_source = (source, int(width), int(height))
        if previous is not None:
            with contextlib.suppress(Exception):
                previous[0].close()  # never opened: superseded by a newer rebuild

    def set_bitrate(self, kbps: int) -> None:
        """Takes effect at the next frame: the encoder is reopened in the worker thread and an IDR follows."""
        self._pending_bitrate = max(100, int(kbps))

    def stop(self) -> None:
        """Mark the track ended and wake a paused ``recv``; the source and encoder are released by :meth:`aclose`
        (or by the recv loop itself) once no worker-thread call is inside them: closing a PyAV container or codec
        context under a thread still reading from it crashes the process."""
        super().stop()
        self._running.set()
        self._capture_allowed.set()  # an ended track must not stay parked before DTLS
        if self._inflight is None or self._inflight.done():
            self._release()

    async def aclose(self) -> None:
        """Stop, then wait for the in-flight capture/encode call before releasing PyAV objects."""
        self.stop()
        inflight = self._inflight
        if inflight is not None and not inflight.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(inflight), 2.0)
        self._release()

    def _release(self) -> None:
        pending, self._pending_source = self._pending_source, None
        if pending is not None:
            with contextlib.suppress(Exception):
                pending[0].close()
        self._queue.clear()
        self._last_frame = None
        self._awaiting_pts = None
        if self._encoder is not None:
            self._encoder.close()
            self._encoder = None
        if self._opened:
            try:
                self._source.close()
            except Exception:  # noqa: BLE001, S110 - closing a dead source
                pass
            self._opened = False

    # ----- MediaStreamTrack -----
    async def _in_thread(self, fn: Callable[..., Any], *args: Any) -> Any:
        """One worker-thread call at a time, remembered so ``aclose`` can wait for it."""
        loop = asyncio.get_running_loop()
        self._inflight = loop.run_in_executor(None, fn, *args)
        try:
            return await self._inflight
        finally:
            self._inflight = None
            if self.readyState == "ended":
                self._release()

    async def recv(self) -> Any:
        await self._capture_allowed.wait()  # rules.screen_sessions: no capture before DTLS connected
        while True:
            if self.readyState == "ended":
                raise asyncio.CancelledError
            if self._queue:
                return self._queue.popleft()
            await self._running.wait()
            if self.readyState == "ended":
                raise asyncio.CancelledError
            if self._pending_source is not None:
                await self._in_thread(self._swap_source)
            if not self._opened:
                if not await self._in_thread(self._open_quietly):
                    raise asyncio.CancelledError  # reported as an error event: the agent ends capture_failed
            if self._pending_bitrate is not None and self._encoder is not None:
                kbps, self._pending_bitrate = self._pending_bitrate, None
                self.bitrate_kbps = kbps
                if not await self._in_thread(self._reopen_encoder, kbps):
                    raise asyncio.CancelledError  # reported as encoder_stalled; the track has stopped
                self._force_idr = True
            frame = await self._in_thread(self._read_quietly)
            if self.readyState == "ended":
                raise asyncio.CancelledError
            now = time.monotonic()
            self.check_stall(now)
            new_picture = frame is not None
            if frame is None:
                if self._last_frame is None:
                    continue
                if self._pumping(now):
                    wait = self._last_sent_at + 1.0 / self.fps - now
                    if wait > 0:
                        await asyncio.sleep(wait)  # a source that answered at once: keep the capture cadence
                        continue
                    self.frames_pumped += 1
                elif now - self._last_sent_at < self.floor_seconds and not self._idr_due(now):
                    continue
                else:
                    self.frames_refreshed += 1  # the floor, or an IDR due now (a PLI on a still screen waits no tick)
                frame = self._last_frame  # still screen: re-encode the held raw frame (never re-send a packet)
            else:
                self._last_frame = frame
                self.frames_captured += 1
            frame.pts = int((now - self._start) * 90000)
            frame.time_base = VIDEO_TIME_BASE
            self._last_sent_at = now
            force = self._take_idr(now)
            if self.choice.kind == "vp8":
                if force and self.vp8_keyframe is not None:
                    self.vp8_keyframe()  # aiortc's libvpx encoder reads the flag for this frame
                self.frames_sent += 1
                self._report_first_frame()
                return frame
            if self._encoder is None and not await self._in_thread(self._open_encoder):
                raise asyncio.CancelledError  # reported as encoder_stalled; the track has stopped
            if self._first_submit_at is None:
                self._first_submit_at = now
                if self._kf_requested_at is None:
                    self._kf_requested_at = now  # the join's IDR
            if new_picture or force:
                self._awaiting_pts, self._awaiting_since = int(frame.pts), now  # must reach the phone: pump if held
            packets = await self._in_thread(self._encode_quietly, frame, force)
            if self.readyState == "ended" or packets is None:
                raise asyncio.CancelledError
            self.frames_encoded += 1
            for packet in packets:
                self._note_access_unit(packet)
            if packets:
                self.frames_sent += 1
                self._report_first_frame()

    def _pumping(self, now: float) -> bool:
        """Whether a picture is still inside the H.264 encoder (the drain pump runs); gives up after
        :data:`PUMP_MAX_SECONDS` without it."""
        if self._awaiting_pts is None or self.choice.kind != "h264":
            return False
        if now - self._awaiting_since > PUMP_MAX_SECONDS:
            self._awaiting_pts = None
            self.pump_timeouts += 1
            return False
        return True

    def _note_access_unit(self, packet: Any) -> None:
        """Queue one access unit for aiortc and update the counters, the pump's target and the keyframe timings."""
        data = bytes(packet)
        types = nal_types(data)
        self.sent_nal_types.append(types)
        self.sent_packet_hashes.append(hash(data))
        self.access_units += 1
        self.au_bytes += len(data)
        pts = packet.pts
        if self._awaiting_pts is not None and pts is not None and pts >= self._awaiting_pts - PUMP_PTS_TOLERANCE:
            self._awaiting_pts = None  # the waited-for picture is out: the pump stops
        if NAL_IDR in types:
            self.idr_sent += 1
            now = time.monotonic()
            if self.first_keyframe_ms is None and self._first_submit_at is not None:
                self.first_keyframe_ms = int((now - self._first_submit_at) * 1000)
                # nals, au_bytes, form (annexb4 / annexb3 / avcc), sps (three bytes), prepended, frames_in
                facts = dict(self._encoder.first_idr or {}) if self._encoder is not None else {}
                log.info("screen first keyframe", ms=self.first_keyframe_ms, pumped=self.frames_pumped, **facts)
            if self._kf_requested_at is not None:
                waited = int((now - self._kf_requested_at) * 1000)
                self._kf_requested_at = None
                self.last_keyframe_ms = waited
                self.max_keyframe_ms = waited if self.max_keyframe_ms is None else max(self.max_keyframe_ms, waited)
        self._queue.append(packet)

    def check_stall(self, now: float | None = None) -> None:
        """No IDR out :data:`KEYFRAME_DEADLINE_SECONDS` after the first frame went into the H.264 encoder: the one
        ``encoder_stalled`` event (also called by the peer's stats loop, in case an encode call never returns)."""
        if self.stalled is not None or self.choice.kind != "h264" or self.first_keyframe_ms is not None:
            return
        if self.paused:
            return  # a paused track feeds nothing: no keyframe is due (resume restarts the deadline)
        started = self._first_submit_at
        now = time.monotonic() if now is None else now
        if started is not None and now - started >= KEYFRAME_DEADLINE_SECONDS:
            self._report_stall("no_keyframe")

    def _report_stall(self, reason: str) -> None:
        if self.stalled is not None:
            return
        self.stalled = reason
        log.warning(
            "screen H.264 encoder stalled",
            reason=reason,
            encoded=self.frames_encoded,
            aus=self.access_units,
            pending=self._encoder.pending if self._encoder is not None else None,
            label=self.choice.label,
        )
        self._emit(
            {
                "ev": "encoder_stalled",
                "encoder": "h264",
                "reason": reason,
                "encoded": self.frames_encoded,
                "aus": self.access_units,
            }
        )

    def stats(self) -> dict[str, Any]:
        """The track's counters for the peer's stats event and ``screen media`` lines (numbers, flags, short words)."""
        enc = self._encoder
        retired = self._retired_counts
        return {
            "captured": self.frames_captured,
            "refreshed": self.frames_refreshed,
            "pumped": self.frames_pumped,
            "encoded": self.frames_encoded if self.choice.kind == "h264" else self.frames_sent,
            "aus": self.access_units,
            "idr": self.idr_sent,
            "au_bytes": self.au_bytes,
            "pending": enc.pending if enc is not None else 0,
            "prepended": retired["prepended"] + (enc.prepended if enc is not None else 0),
            "non_annexb": retired["non_annexb"] + (enc.non_annexb if enc is not None else 0),
            "kf_req": self.keyframe_requests,
            "kf_pli": self.keyframe_requests_by["pli"],
            "kf_dc": self.keyframe_requests_by["dc"],
            "kf_op": self.keyframe_requests_by["op"],
            "first_kf_ms": self.first_keyframe_ms,
            "last_kf_ms": self.last_keyframe_ms,
            "max_kf_ms": self.max_keyframe_ms,
            "stalled": self.stalled,
        }

    def _swap_source(self) -> None:
        """Worker thread, between two reads: the old source and encoder go, the new source opens on the next pass."""
        pending, self._pending_source = self._pending_source, None
        if pending is None:
            return
        source, width, height = pending
        if self._opened:
            with contextlib.suppress(Exception):
                self._source.close()
            self._opened = False
        if self._encoder is not None:
            self._retired_counts["prepended"] += self._encoder.prepended
            self._retired_counts["non_annexb"] += self._encoder.non_annexb
            self._encoder.close()
            self._encoder = None  # recreated at the new size on the next frame
        self._source, self.width, self.height = source, width, height
        self._last_frame = None
        self._awaiting_pts = None
        self._force_idr = True
        self.rebuilds += 1

    def _open_quietly(self) -> bool:
        """The source's one ``open()`` (after DTLS); a failure is reported like a dead source."""
        try:
            self._source.open()
        except Exception as exc:  # noqa: BLE001 - every open failure is the honest capture error
            self._emit(_error_event(exc))
            self.stop()
            return False
        self._opened = True
        return True

    def _read_quietly(self) -> Any | None:
        try:
            return self._source.read()
        except Exception as exc:  # noqa: BLE001 - the source is gone: the peer reports capture_failed
            self._emit(_error_event(exc))
            self.stop()
            return None

    def _open_encoder(self) -> bool:
        """Worker thread: the H.264 encoder at the current size and bitrate, its open facts logged; one that cannot
        open stops the track and is reported as ``encoder_stalled`` (``open_error``)."""
        try:
            self._encoder = H264Encoder(self.choice, self.width, self.height, self.fps, self.bitrate_kbps)
        except Exception as exc:  # noqa: BLE001 - the probe opened it a moment ago; the transform may refuse now
            self._encoder_failed("H.264 encoder could not open", "open_error", exc)
            return False
        log.info("screen encoder opened", **self._encoder.open_facts())
        return True

    def _reopen_encoder(self, kbps: int) -> bool:
        """Worker thread: a bitrate step's reopen (logged like an open); a failure stops the track like an open."""
        encoder = self._encoder
        if encoder is None:
            return True
        try:
            encoder.reopen(kbps)
        except Exception as exc:  # noqa: BLE001
            self._encoder_failed("H.264 encoder could not reopen", "open_error", exc)
            return False
        log.info("screen encoder opened", reopen=True, **encoder.open_facts())
        return True

    def _encode_quietly(self, frame: Any, force: bool) -> list[Any] | None:
        """Worker thread: one frame through the H.264 encoder, FFmpeg's error lines captured. An exception stops the
        track (aiortc's send loop would otherwise log a traceback and stop sending while the agent believes the
        session live) and is reported as ``encoder_stalled`` (``encode_error``); None then."""
        from .capture_dda import ffmpeg_error_lines, ffmpeg_error_text

        encoder = self._encoder
        if encoder is None:
            return None
        with ffmpeg_error_lines() as logged:
            try:
                return encoder.encode(frame, force_idr=force)
            except Exception as exc:  # noqa: BLE001 - every encoder failure is reported, never re-raised
                self._encoder_failed("H.264 encode failed", "encode_error", exc, ffmpeg_error_text(exc, logged))
                return None

    def _encoder_failed(self, message: str, reason: str, exc: BaseException, text: str | None = None) -> None:
        from .capture_dda import ffmpeg_error_text

        log.error(message, error=exc.__class__.__name__, reason=text or ffmpeg_error_text(exc))
        self._report_stall(reason)
        self.stop()

    def _report_first_frame(self) -> None:
        if not self._first_frame_reported:
            self._first_frame_reported = True
            self._emit({"ev": "first_frame"})


def _detail(exc: BaseException) -> str | None:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and isinstance(detail.get("detail"), str):
        return str(detail["detail"])
    return None


def _error_event(exc: BaseException) -> dict[str, Any]:
    """The worker's ``error`` event for a source that failed. ``message`` is a ``ProtocolError``'s own text (written
    by the capture modules, a library's words in it sanitised there) so the agent's log says why; any other exception
    is named by its class only, never by its text."""
    message = getattr(exc, "message", None)
    return {
        "ev": "error",
        "code": getattr(exc, "code", "SCREEN_CAPTURE_UNAVAILABLE"),
        "detail": _detail(exc),
        "message": message[:200] if isinstance(message, str) and message else exc.__class__.__name__,
    }


__all__ = ["FLOOR_SECONDS", "KEYFRAME_DEADLINE_SECONDS", "PUMP_MAX_SECONDS", "ScreenTrack"]
