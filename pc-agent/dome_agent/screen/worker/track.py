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
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import functools
import time
from collections.abc import Callable
from typing import Any

from aiortc.mediastreams import MediaStreamTrack

from .encode import VIDEO_TIME_BASE, EncoderChoice, H264Encoder, nal_types
from .fake import FrameSource

Emit = Callable[[dict[str, Any]], None]
FLOOR_SECONDS = 1.0
IDR_MIN_INTERVAL_SECONDS = 0.5  # requested IDRs at most twice a second (design §5.3)


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
        self.idr_sent = 0
        self.rebuilds = 0
        self.sent_nal_types: collections.deque[list[int]] = collections.deque(maxlen=256)
        self.sent_packet_hashes: collections.deque[int] = collections.deque(maxlen=256)

    # ----- controls from the peer / the agent -----
    def force_keyframe(self, *, urgent: bool = False) -> None:
        """An IDR on a coming access unit. A network request (``urgent`` False) waits for its 500 ms slot; ``urgent``
        (a new geometry generation) is sent on the next access unit."""
        if urgent:
            self._force_idr = True
            return
        self.keyframe_requests += 1
        self._idr_requested = True

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
                await self._in_thread(self._encoder.reopen, kbps)
                self._force_idr = True
            frame = await self._in_thread(self._read_quietly)
            if self.readyState == "ended":
                raise asyncio.CancelledError
            now = time.monotonic()
            if frame is None:
                if self._last_frame is None or now - self._last_sent_at < self.floor_seconds:
                    continue
                frame = self._last_frame  # still screen: re-encode the held raw frame (never re-send a packet)
                self.frames_refreshed += 1
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
            if self._encoder is None:
                self._encoder = await self._in_thread(
                    H264Encoder, self.choice, self.width, self.height, self.fps, self.bitrate_kbps
                )
            packets = await self._in_thread(functools.partial(self._encoder.encode, frame, force_idr=force))
            if self.readyState == "ended":
                raise asyncio.CancelledError
            for packet in packets:
                data = bytes(packet)
                types = nal_types(data)
                self.sent_nal_types.append(types)
                self.sent_packet_hashes.append(hash(data))
                if 5 in types:
                    self.idr_sent += 1
                self._queue.append(packet)
            if packets:
                self.frames_sent += 1
                self._report_first_frame()

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
            self._encoder.close()
            self._encoder = None  # recreated at the new size on the next frame
        self._source, self.width, self.height = source, width, height
        self._last_frame = None
        self._force_idr = True
        self.rebuilds += 1

    def _open_quietly(self) -> bool:
        """The source's one ``open()`` (after DTLS); a failure is reported like a dead source."""
        try:
            self._source.open()
        except Exception as exc:  # noqa: BLE001 - every open failure is the honest capture error
            self._emit(
                {"ev": "error", "code": getattr(exc, "code", "SCREEN_CAPTURE_UNAVAILABLE"), "detail": _detail(exc)}
            )
            self.stop()
            return False
        self._opened = True
        return True

    def _read_quietly(self) -> Any | None:
        try:
            return self._source.read()
        except Exception as exc:  # noqa: BLE001 - the source is gone: the peer reports capture_failed
            self._emit(
                {"ev": "error", "code": getattr(exc, "code", "SCREEN_CAPTURE_UNAVAILABLE"), "detail": _detail(exc)}
            )
            self.stop()
            return None

    def _report_first_frame(self) -> None:
        if not self._first_frame_reported:
            self._first_frame_reported = True
            self._emit({"ev": "first_frame"})


def _detail(exc: BaseException) -> str | None:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and isinstance(detail.get("detail"), str):
        return str(detail["detail"])
    return None


__all__ = ["FLOOR_SECONDS", "ScreenTrack"]
