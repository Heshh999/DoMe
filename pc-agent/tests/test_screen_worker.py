"""The Screen view worker on Linux with the fake sources (design §5, §18 WP1.4; rules.screen_content): the pipe
protocol, the encoder helpers (level 3.1 sizing, Annex B, SPS/PPS before every IDR), the pre-encoded track (no capture
before DTLS, the 1 fps floor with DISTINCT still-frame packets, pause/resume), the PLI hook point on the pinned aiortc,
the import graph (the worker pulls in no api, relay or agent code and no ``aiortc.contrib``; the agent pulls in no
aiortc or PyAV), the in-process worker loop (liveness ticks, pipe EOF, a refused offer), the real child process through
``ProcessWorkerLauncher`` / ``WorkerSupervisor`` (stop, redacted stderr, no file written) and an aiortc loopback
session: the worker ⇄ a test "phone" ``RTCPeerConnection`` on this host receiving real decoded frames from the fake
source, heartbeats and the closed data-channel vocabulary, pause and resume, PLI → keyframe within 500 ms, and the
loopback-address sentinel at ``LOG_LEVEL=DEBUG``.

Evidence: unit-tested with the stock PyPI PyAV wheel (its ``libx264`` is development-only and never ships); Windows
capture (``ddagrab`` / WGC) and ``h264_mf`` encoding are **not yet verified on a device**."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import types
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from dome_protocol import ProtocolError, parse_screen_dc_message, sdp_fingerprints

from dome_agent.screen.sdp_check import check_offer
from dome_agent.screen.supervisor import ProcessWorkerLauncher, WorkerSupervisor, worker_command
from dome_agent.screen.worker import encode, fake, pipe
from dome_agent.screen.worker.main import EXIT_START_FAILED, EXIT_TICKS_STOPPED, WorkerApp
from dome_agent.settings import Settings

aiortc = pytest.importorskip("aiortc")
av = pytest.importorskip("av")

from dome_agent.screen.worker.rtc import ScreenPeer, pli_hook_available  # noqa: E402 - after the importorskip
from dome_agent.screen.worker.track import ScreenTrack  # noqa: E402

LIBX264 = "libx264" in av.codecs_available
needs_libx264 = pytest.mark.skipif(not LIBX264, reason="the development-only libx264 of the PyPI PyAV wheel is absent")
MODULE = "dome_agent.screen.worker"


async def wait_for(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def dev_h264_choice() -> encode.EncoderChoice:
    return encode.encoder_candidates(["h264"], dev_h264=True, platform="linux")[0]


def still_frame(width: int = 320, height: int = 180) -> Any:
    frame = av.VideoFrame(width, height, "yuv420p")
    for plane, value in zip(frame.planes, (90, 128, 128), strict=True):
        plane.update(bytes([value]) * plane.buffer_size)
    frame.time_base = encode.VIDEO_TIME_BASE
    return frame


# ----- the pipe -------------------------------------------------------------------------------------------------------
def test_pipe_framing_roundtrip_limits_and_eof() -> None:
    message = {"op": "tick", "n": 1, "text": "ünïcode"}
    data = pipe.encode_message(message)
    assert data[:4] == len(data[4:]).to_bytes(4, "big")
    assert pipe.read_message_sync(io.BytesIO(data + data)) == message
    stream = io.BytesIO(data + data)
    assert pipe.read_message_sync(stream) == message and pipe.read_message_sync(stream) == message
    assert pipe.read_message_sync(stream) is None  # EOF
    assert pipe.read_message_sync(io.BytesIO(data[:7])) is None  # a truncated body is EOF, not garbage
    with pytest.raises(ProtocolError) as exc:
        pipe.encode_message({"sdp": "x" * (pipe.MAX_MESSAGE_BYTES + 1)})
    assert exc.value.code == "PAYLOAD_TOO_LARGE"
    with pytest.raises(ProtocolError):
        pipe.read_message_sync(io.BytesIO((pipe.MAX_MESSAGE_BYTES + 1).to_bytes(4, "big") + b"{}"))
    with pytest.raises(ProtocolError):
        pipe.read_message_sync(io.BytesIO(len(b"[1,2]").to_bytes(4, "big") + b"[1,2]"))  # well framed, not an object
    out = io.BytesIO()
    pipe.write_message_sync(out, message)
    assert out.getvalue() == data


async def test_pipe_async_reader() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(pipe.encode_message({"ev": "hb"}) + pipe.encode_message({"ev": "bye"}))
    reader.feed_eof()
    assert await pipe.read_message(reader) == {"ev": "hb"}
    assert await pipe.read_message(reader) == {"ev": "bye"}
    assert await pipe.read_message(reader) is None


# ----- encoder helpers -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((1920, 1080), (1280, 720)),
        ((2560, 1600), (1152, 720)),  # 16:10
        ((1600, 1200), (960, 720)),  # 4:3
        ((1280, 720), (1280, 720)),
        ((800, 600), (800, 600)),  # never scaled up
        ((1080, 1920), (720, 1280)),  # portrait
        ((3, 3), (2, 2)),
    ],
)
def test_fit_level_31_table(size: tuple[int, int], expected: tuple[int, int]) -> None:
    assert encode.fit_level_31(*size) == expected


@pytest.mark.parametrize("size", [(3440, 1440), (5120, 1440), (1366, 768), (2736, 1824), (7680, 4320)])
def test_fit_level_31_bounds_and_aspect(size: tuple[int, int]) -> None:
    w, h = encode.fit_level_31(*size)
    assert max(w, h) <= 1280 and min(w, h) <= 720 and w % 2 == 0 and h % 2 == 0
    assert abs(w / h - size[0] / size[1]) / (size[0] / size[1]) < 0.01  # aspect kept within 1 %
    assert (w * h) // 256 <= 3600  # level 3.1 macroblocks per frame


def test_annex_b_helpers() -> None:
    sps, pps, idr, p = b"\x67\x42\xe0\x1f", b"\x68\xce\x38\x80", b"\x65\x88\x84", b"\x41\x9a\x02"
    sc = encode.START_CODE
    assert encode.nal_types(sc + sps + sc + pps + b"\x00\x00\x01" + idr) == [7, 8, 5]
    assert encode.nal_types(b"") == [] and encode.nal_types(b"\x00\x00\x01") == []
    # avcC (AVCDecoderConfigurationRecord): 1 SPS, 1 PPS
    avcc = (
        bytes([1, 0x42, 0xE0, 0x1F, 0xFF, 0xE1])
        + len(sps).to_bytes(2, "big")
        + sps
        + bytes([1])
        + len(pps).to_bytes(2, "big")
        + pps
    )
    param = encode.extradata_to_annexb(avcc)
    assert param == sc + sps + sc + pps and encode.nal_types(param) == [7, 8]
    assert encode.extradata_to_annexb(sc + sps) == sc + sps  # already Annex B
    assert encode.extradata_to_annexb(b"") == b"" and encode.extradata_to_annexb(b"\x02\x00") == b""
    assert encode.extradata_to_annexb(bytes([1, 0, 0, 0, 0, 0xE1, 0x00])) == b""  # truncated record
    # an IDR without parameter sets gets them; one that has them, and a P frame, are left alone
    assert encode.ensure_parameter_sets(sc + idr, param) == param + sc + idr
    assert encode.ensure_parameter_sets(param + sc + idr, param) == param + sc + idr
    assert encode.ensure_parameter_sets(sc + p, param) == sc + p
    assert encode.ensure_parameter_sets(sc + idr, b"") == sc + idr


def test_encoder_candidates_follow_the_platform_order() -> None:
    win = encode.encoder_candidates(["h264", "vp8"], dev_h264=False, platform="win32")
    assert [(c.codec_name, c.hardware, c.kind) for c in win] == [
        ("h264_mf", True, "h264"),
        ("h264_mf", False, "h264"),
        ("", False, "vp8"),
    ]
    assert win[0].options["hw_encoding"] == "1" and win[0].options["scenario"] == "display_remoting"
    assert win[1].options["hw_encoding"] == "0" and not any(c.development_only for c in win)
    linux = encode.encoder_candidates(["h264", "vp8"], dev_h264=True, platform="linux")
    assert (
        [c.codec_name for c in linux] == ["libx264", ""] and linux[0].development_only and "DEV ONLY" in linux[0].label
    )
    assert [c.kind for c in encode.encoder_candidates(["h264", "vp8"], dev_h264=False, platform="linux")] == ["vp8"]
    assert encode.encoder_candidates(["vp8"], dev_h264=True, platform="win32")[0].label == "vp8 (aiortc libvpx)"
    with pytest.raises(ProtocolError) as exc:
        encode.probe_encoders(encode.encoder_candidates(["h264"], dev_h264=False, platform="linux"))
    assert exc.value.code == "SCREEN_ENCODER_UNAVAILABLE"
    assert encode.probe_encoders(encode.encoder_candidates(["vp8"], dev_h264=False)).kind == "vp8"
    rows = encode.list_encoders(dev_h264=False)
    assert [r["kind"] for r in rows] == ["vp8"] and rows[0]["available"] is True


@needs_libx264
def test_h264_encoder_puts_sps_pps_before_every_idr_and_still_frames_differ() -> None:
    """Design §5.3 worker requirement and §5.2: every IDR access unit begins with NAL 7 and 8; a re-encoded still
    frame is a new encoder output, never a byte-identical copy."""
    enc = encode.H264Encoder(dev_h264_choice(), 320, 180, 15, 500)
    try:
        assert encode.nal_types(enc.parameter_sets) == [7, 8]
        frame = still_frame()
        outputs: list[bytes] = []
        for n, force in enumerate([True, False, False, True, False]):
            frame.pts = n * 6000
            packets = enc.encode(frame, force_idr=force)
            assert packets, "x264 ultrafast/zerolatency emits one packet per frame"
            outputs.append(b"".join(bytes(p) for p in packets))
        types = [encode.nal_types(o) for o in outputs]
        assert 5 in types[0] and 5 in types[3] and 5 not in types[1] and 5 not in types[2]
        for t in types:
            if 5 in t:
                assert t.index(7) < t.index(8) < t.index(5), t
        assert outputs[1] != outputs[2]  # two still-frame P packets are distinct encoder outputs
        assert enc.idr_count == 2 and enc.frames_in == 5 and enc.packets_out == 5 and enc.pending == 0
        enc.reopen(300)  # a bitrate step reopens the context; the parameter sets survive
        assert enc.bitrate_kbps == 300 and encode.nal_types(enc.parameter_sets) == [7, 8]
    finally:
        enc.close()


# ----- the fake sources --------------------------------------------------------------------------------------------------
def test_fake_sources_deliver_changed_frames_and_nothing_when_still() -> None:
    src = fake.SyntheticSource(64, 36, 60)
    src.open()
    a, b = src.read(), src.read()
    assert a is not None and b is not None and bytes(a.planes[0]) != bytes(b.planes[0]) and a.format.name == "yuv420p"
    still = fake.SyntheticSource(64, 36, 60, still=True)
    still.open()
    assert still.read() is not None and still.read() is None and still.read() is None
    lavfi = fake.LavfiTestSource(64, 64, 30, still=True)
    lavfi.open()
    try:
        frame = lavfi.read()
        assert frame is not None and (frame.width, frame.height, frame.format.name) == (64, 64, "yuv420p")
        assert lavfi.read() is None
    finally:
        lavfi.close()
    assert isinstance(fake.build_fake_source({"pattern": "synthetic"}, 64, 36, 30), fake.SyntheticSource)
    assert isinstance(fake.build_fake_source({}, 64, 36, 30), fake.LavfiTestSource)
    assert fake.build_fake_source({"pattern": "still"}, 64, 36, 30).still is True  # type: ignore[attr-defined]


# ----- the track ---------------------------------------------------------------------------------------------------------
class RecordingSource:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.width, self.height = inner.width, inner.height
        self.opened = 0
        self.closed = 0

    def open(self) -> None:
        self.opened += 1
        self.inner.open()

    def read(self) -> Any | None:
        return self.inner.read()

    def close(self) -> None:
        self.closed += 1
        self.inner.close()


@needs_libx264
async def test_track_captures_only_after_dtls_refreshes_still_frames_distinctly_and_pauses() -> None:
    source = RecordingSource(fake.SyntheticSource(320, 180, 30, still=True))
    allowed = asyncio.Event()
    events: list[dict[str, Any]] = []
    track = ScreenTrack(
        source=source,
        choice=dev_h264_choice(),
        width=320,
        height=180,
        fps=30,
        bitrate_kbps=500,
        capture_allowed=allowed,
        emit=events.append,
        floor_seconds=0.1,
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(track.recv(), 0.3)
    assert source.opened == 0 and track.frames_captured == 0  # rules.screen_sessions: nothing before DTLS
    allowed.set()
    packets = [await asyncio.wait_for(track.recv(), 5) for _ in range(5)]
    assert source.opened == 1 and track.frames_captured == 1 and track.frames_refreshed >= 4
    assert events and events[0] == {"ev": "first_frame"}
    hashes = list(track.sent_packet_hashes)
    assert len(set(hashes)) == len(hashes), "a held still frame is re-encoded, never re-sent"
    types = list(track.sent_nal_types)
    assert 5 in types[0] and all(t.index(7) < t.index(8) < t.index(5) for t in types if 5 in t)
    assert all(isinstance(p, av.Packet) for p in packets)
    track.pause()
    assert track.paused
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(track.recv(), 0.3)  # a paused track sends nothing
    idr_before = track.idr_sent
    track.resume()
    await asyncio.wait_for(track.recv(), 5)
    assert track.idr_sent == idr_before + 1  # resuming forces an IDR
    track.set_bitrate(300)
    await asyncio.wait_for(track.recv(), 5)
    assert track.bitrate_kbps == 300 and track.idr_sent == idr_before + 2  # a bitrate step reopens with an IDR
    await track.aclose()
    assert source.closed == 1 and track.readyState == "ended"
    with pytest.raises(asyncio.CancelledError):
        await track.recv()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ProtocolError("SCREEN_CAPTURE_UNAVAILABLE", "gone", detail={"detail": "busy"}),
            {"ev": "error", "code": "SCREEN_CAPTURE_UNAVAILABLE", "detail": "busy", "message": "gone"},
        ),
        # any other exception is named by its class only: its text never reaches the agent's log
        (
            OSError(r"C:\Users\Jane\secret.txt"),
            {"ev": "error", "code": "SCREEN_CAPTURE_UNAVAILABLE", "detail": None, "message": "OSError"},
        ),
    ],
)
async def test_track_reports_a_dead_source_as_capture_failed(error: Exception, expected: dict[str, Any]) -> None:
    class Dying:
        width, height = 64, 36

        def open(self) -> None:
            return None

        def read(self) -> Any:
            raise error

        def close(self) -> None:
            return None

    allowed = asyncio.Event()
    allowed.set()
    events: list[dict[str, Any]] = []
    track = ScreenTrack(
        source=Dying(),
        choice=encode.EncoderChoice("vp8", ""),
        width=64,
        height=36,
        fps=30,
        bitrate_kbps=500,
        capture_allowed=allowed,
        emit=events.append,
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(track.recv(), 5)
    assert events == [expected]
    await track.aclose()


# ----- the PLI hook point -------------------------------------------------------------------------------------------------
def test_pli_hook_point_exists_on_the_pinned_aiortc_and_forces_an_idr() -> None:
    """Design §5.3: aiortc 1.15.0 turns PLI/FIR into ``RTCRtpSender._send_keyframe``, which the worker replaces on the
    instance. A unit test fails the moment the pinned library loses that hook point."""
    assert aiortc.__version__ == "1.15.0" and pli_hook_available()

    class Track:
        def __init__(self, kind: str) -> None:
            self.choice = encode.EncoderChoice(kind, "" if kind == "vp8" else "libx264")
            self.forced = 0
            self.vp8_keyframe: Any = None

        def force_keyframe(self, **_kw: Any) -> None:
            self.forced += 1

    class Sender:
        def __init__(self) -> None:
            self.original_calls = 0

        def _send_keyframe(self) -> None:
            self.original_calls += 1

    for kind in ("h264", "vp8"):
        track = Track(kind)
        peer = ScreenPeer(
            offer_sdp="",
            stun_urls=[],
            track=track,
            capture_allowed=asyncio.Event(),
            emit=lambda e: None,
            bitrate_kbps={},  # type: ignore[arg-type]
        )
        sender = Sender()
        peer._install_pli_hook(sender)  # type: ignore[arg-type]  # noqa: SLF001
        assert peer.pli_hook
        sender._send_keyframe()  # what aiortc's RTCP dispatch calls on a PLI  # noqa: SLF001
        # the track decides when the IDR is due (at most twice a second); for VP8 it then calls aiortc's original
        assert track.forced == 1 and peer.pli_count == 1 and sender.original_calls == 0, kind
        if kind == "vp8":
            track.vp8_keyframe()
            assert sender.original_calls == 1
        else:
            assert track.vp8_keyframe is None
    peer = ScreenPeer(
        offer_sdp="",
        stun_urls=["stun:s.example:3478", "turn:t.example"],
        track=Track("h264"),
        capture_allowed=asyncio.Event(),
        emit=lambda e: None,
        bitrate_kbps={},
    )  # type: ignore[arg-type]
    assert peer._stun_urls == ["stun:s.example:3478"]  # noqa: SLF001  # the PC is STUN-only: no TURN URL is ever used
    with pytest.raises(ProtocolError):
        peer.send_dc({"type": "not-in-the-vocabulary"})  # outbound messages are validated too
    assert peer.send_dc({"type": "state", "state": "sending"}) is False  # no channel yet: not sent, not an error


# ----- import graph -----------------------------------------------------------------------------------------------------------
def _modules_after_importing(name: str) -> list[str]:
    code = f"import {name}, sys; print(sorted(m for m in sys.modules if m.startswith(('dome_agent', 'aiortc', 'av', 'aioice'))))"
    out = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    return list(json.loads(out.stdout.replace("'", '"')))


def test_worker_imports_no_agent_relay_api_or_recording_code() -> None:
    """Design §5.1 / §17.2: the worker cannot talk to the relay or the API and has no MediaRecorder; the agent, in
    turn, runs without aiortc or PyAV (the `screen` extra is optional)."""
    worker = _modules_after_importing(f"{MODULE}.main, {MODULE}.rtc, {MODULE}.track, {MODULE}.encode, {MODULE}.fake")
    forbidden = {
        "dome_agent.api",
        "dome_agent.relay_client",
        "dome_agent.agent",
        "dome_agent.store",
        "dome_agent.tray",
        "dome_agent.screen.session",
        "dome_agent.screen.supervisor",
        "dome_agent.input_session",
        "dome_agent.pairing",
    }
    assert not forbidden & set(worker), sorted(forbidden & set(worker))
    assert not any(m.startswith("aiortc.contrib") for m in worker)
    assert "aiortc" in worker  # the loopback peer really is aiortc
    agent = _modules_after_importing("dome_agent.agent")
    assert "dome_agent.screen.session" in agent and "dome_agent.screen.supervisor" in agent
    assert not any(m == "aiortc" or m.startswith(("aiortc.", "av", "aioice")) for m in agent), (
        "the agent must run without the screen extra"
    )
    assert f"{MODULE}.pipe" in agent and f"{MODULE}.main" not in agent and f"{MODULE}.rtc" not in agent


# ----- the in-process worker loop ---------------------------------------------------------------------------------------------
class InProcessWorker:
    """``WorkerApp`` on real pipes inside the test process: the agent side writes ``stdin``, reads ``stdout``."""

    def __init__(self) -> None:
        in_r, in_w = os.pipe()
        out_r, out_w = os.pipe()
        self._to_worker = os.fdopen(in_w, "wb", buffering=0)
        self._from_worker = os.fdopen(out_r, "rb", buffering=0)
        self._worker_out = os.fdopen(out_w, "wb", buffering=0)
        self.app = WorkerApp(os.fdopen(in_r, "rb", buffering=0), self._worker_out)
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.seen: list[dict[str, Any]] = []
        self.task: asyncio.Task[int] | None = None
        self._reader: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.ensure_future(self.app.run())
        self._reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        while True:
            event = await asyncio.to_thread(pipe.read_message_sync, self._from_worker)
            if event is None:
                return
            self.events.put_nowait(event)

    def send(self, message: dict[str, Any]) -> None:
        pipe.write_message_sync(self._to_worker, message)

    def close_stdin(self) -> None:
        self._to_worker.close()

    async def expect(self, kind: str, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no {kind} event; saw {[e.get('ev') for e in self.seen]}")
            event = await asyncio.wait_for(self.events.get(), remaining)
            self.seen.append(event)
            if event.get("ev") == kind:
                return event

    async def finish(self, timeout: float = 10.0) -> int:
        """The worker's exit code once it has stopped on its own (or after ``stop``)."""
        assert self.task is not None
        code = await asyncio.wait_for(self.task, timeout)
        await self.aclose()
        return code

    async def aclose(self) -> None:
        """Release the pipes whatever state the test left: a blocked reader thread would otherwise hold the loop."""
        if self.task is not None and not self.task.done():
            with contextlib.suppress(Exception):
                self.send({"op": "stop"})
            with contextlib.suppress(TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(self.task), 10)
            if not self.task.done():
                self.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self.task
        for stream in (self._worker_out, self._to_worker):
            with contextlib.suppress(OSError):
                stream.close()  # EOF for the reader thread on the other side
        if self._reader is not None:
            with contextlib.suppress(TimeoutError, Exception):
                await asyncio.wait_for(self._reader, 5)


@pytest.fixture
async def worker() -> AsyncIterator[InProcessWorker]:
    w = InProcessWorker()
    try:
        yield w
    finally:
        await w.aclose()


async def test_worker_geometry_ops_announce_a_new_generation_and_a_failed_rebuild_changes_nothing() -> None:
    """`reserved` and `rebuild` (a monitor change, design §5.2) bump `gen` and force an IDR; a rebuild whose source
    cannot open is the honest SCREEN_CAPTURE_UNAVAILABLE (the agent then ends the session capture_failed) and leaves
    the running capture and its generation alone."""

    class StubTrack:
        def __init__(self) -> None:
            self.keyframes = 0
            self.replaced: list[tuple[Any, int, int]] = []

        def force_keyframe(self, **_kw: Any) -> None:
            self.keyframes += 1

        def replace_source(self, source: Any, width: int, height: int) -> None:
            self.replaced.append((source, width, height))

    out = io.BytesIO()
    app = WorkerApp(io.BytesIO(), out)
    track = StubTrack()
    app._track = track  # noqa: SLF001
    app._geometry = {"gen": 1, "surface_index": 0, "width": 1280, "height": 720, "rotation": 0, "reserved": []}  # noqa: SLF001
    app._source_spec = {"kind": "fake", "pattern": "synthetic", "fps": 5, "preflight": {"detail": "busy"}}  # noqa: SLF001
    await app._handle({"op": "reserved", "surface_index": 1, "reserved": [{"x": 0, "y": 0, "w": 65535, "h": 9}]})  # noqa: SLF001
    await app._handle(  # noqa: SLF001
        {"op": "rebuild", "source": {"surface_index": 1, "width": 2560, "height": 1600, "rotation": 90}, "reserved": []}
    )
    events = []
    stream = io.BytesIO(out.getvalue())
    while (message := pipe.read_message_sync(stream)) is not None:
        events.append(message)
    assert [e["gen"] for e in events] == [2, 3] and track.keyframes == 2
    assert events[0]["surface_index"] == 1 and events[0]["reserved"][0]["h"] == 9 and events[0]["width"] == 1280
    assert (events[1]["width"], events[1]["height"], events[1]["rotation"]) == (1152, 720, 90)  # level 3.1, 16:10
    assert [(w, h) for _s, w, h in track.replaced] == [(1152, 720)]
    assert "preflight" not in app._source_spec  # noqa: SLF001  # the start's verdict was about another monitor
    track.replaced[0][0].close()
    app._source_spec = {"kind": "ddagrab", "fps": 30}  # noqa: SLF001  # Desktop Duplication cannot open off Windows
    with pytest.raises(ProtocolError) as exc:
        await app._handle({"op": "rebuild", "source": {"width": 1920, "height": 1080}, "reserved": []})  # noqa: SLF001
    assert exc.value.code == "SCREEN_CAPTURE_UNAVAILABLE" and len(track.replaced) == 1
    assert app._geometry["gen"] == 3  # noqa: SLF001


async def test_worker_exits_when_the_agent_ticks_stop(worker: InProcessWorker) -> None:
    w = worker
    w.start()
    w.app._tick_timeout = 0.4  # noqa: SLF001  # normally set by `start`; design §7: 10 s without a tick
    w.send({"op": "tick"})
    w.send({"op": "no-such-op"})  # unknown ops are dropped, never fatal
    t0 = time.monotonic()
    exiting = await w.expect("exiting", timeout=5)
    assert exiting["reason"] == "ticks_stopped" and time.monotonic() - t0 < 3
    assert await w.finish() == EXIT_TICKS_STOPPED


async def test_worker_exits_on_pipe_eof(worker: InProcessWorker) -> None:
    w = worker
    w.start()
    w.send({"op": "tick"})
    w.close_stdin()
    assert (await w.expect("exiting", timeout=5))["reason"] == "pipe_closed"
    assert await w.finish() == 0


async def test_worker_refuses_a_bad_offer_before_any_media_stack_and_exits(worker: InProcessWorker) -> None:
    w = worker
    w.start()
    w.send(
        {
            "op": "start",
            "offer_sdp": "v=0\r\nnot an offer\r\n",
            "stun_urls": [],
            "source": {"kind": "fake", "pattern": "synthetic"},
        }
    )
    error = await w.expect("error")
    assert (
        error["code"] == "SCREEN_OFFER_INVALID" and error["detail"] == "fingerprint" and "v=0" not in error["message"]
    )
    assert (await w.expect("exiting"))["reason"] == "start_failed"
    assert await w.finish() == EXIT_START_FAILED


async def test_worker_without_a_usable_encoder_reports_it(worker: InProcessWorker) -> None:
    offer = await make_phone_offer(aiortc.RTCPeerConnection(aiortc.RTCConfiguration(iceServers=[])), close=True)
    w = worker
    w.app._dev_h264 = False  # noqa: SLF001  # a Linux box without the development encoder, and the phone offered only H.264
    w.start()
    w.send(
        {
            "op": "start",
            "offer_sdp": offer,
            "stun_urls": [],
            "source": {"kind": "fake", "pattern": "synthetic"},
            "encoder_preference": ["h264"],
        }
    )
    error = await w.expect("error")
    assert error["code"] == "SCREEN_ENCODER_UNAVAILABLE"
    assert await w.finish() == EXIT_START_FAILED


# ----- the real child process ---------------------------------------------------------------------------------------------------
def _child_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["DOME_AGENT_STATE_DIR"] = str(tmp_path / "state")
    env["HOME"] = str(tmp_path / "home")
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH", "")) if p
    )
    return env


def test_real_worker_process_exits_on_eof_and_writes_no_file(tmp_path: Path) -> None:
    (tmp_path / "home").mkdir()
    proc = subprocess.run(
        [*worker_command()],
        input=pipe.encode_message({"op": "tick"}),
        capture_output=True,
        env=_child_env(tmp_path),
        cwd=str(tmp_path / "home"),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")[-800:]
    assert pipe.read_message_sync(io.BytesIO(proc.stdout)) == {"ev": "exiting", "reason": "pipe_closed"}
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == ["home"], (
        "the worker writes no file"
    )
    assert worker_command()[1:] == ["-m", MODULE]


def test_worker_command_in_a_frozen_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert worker_command() == [sys.executable, "screen-worker"]


def test_self_test_reports_honestly_in_a_child_process(tmp_path: Path) -> None:
    """``--self-test`` (design §15): what this build can do, as JSON, never a frame. On the stock PyPI wheel the decoder
    set is NOT "exactly wrapped_avframe", and the report says so instead of pretending to be the LGPL build."""
    proc = subprocess.run(
        [*worker_command(), "--self-test", "--dev-h264"],
        capture_output=True,
        text=True,
        env=_child_env(tmp_path),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    checks = report["checks"]
    assert report["ok"] is True and checks["imports"]["value"]["aiortc"] == "1.15.0"
    assert checks["pli_hook"]["value"] is True
    assert checks["lavfi_testsrc_frame"]["value"] == {"width": 64, "height": 64, "format": "yuv420p"}
    assert checks["decoders"]["value"]["exactly_wrapped_avframe"] is False and checks["decoders"]["value"]["count"] > 1
    encoders = {row["encoder"]: row["available"] for row in checks["encoders"]["value"]}
    assert encoders["vp8 (aiortc libvpx)"] is True and encoders.get("libx264 DEV ONLY") is LIBX264
    listing = subprocess.run(
        [*worker_command(), "--list-encoders"], capture_output=True, text=True, env=_child_env(tmp_path), timeout=60
    )
    assert listing.returncode == 0 and [r["kind"] for r in json.loads(listing.stdout.strip().splitlines()[-1])] == [
        "vp8"
    ]
    assert not (tmp_path / "state").exists()


async def test_process_launcher_and_supervisor_stop_order_and_redacted_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real launcher: a child on the pipe pair, events and exit reported exactly once, stderr relogged through
    the redacting pipeline (an address in a library line never reaches the log), ``stop`` → exit 0, no file."""
    events: list[dict[str, Any]] = []
    exits: list[int | None] = []
    exited = asyncio.Event()

    async def on_event(ev: dict[str, Any]) -> None:
        events.append(ev)

    async def on_exit(code: int | None) -> None:
        exits.append(code)
        exited.set()

    launcher = ProcessWorkerLauncher(env=_child_env(tmp_path), command=[*worker_command(), "--log-level", "WARNING"])
    supervisor = WorkerSupervisor(launcher, tick_interval=0.2, stop_grace=10.0)
    proc = await asyncio.wait_for(supervisor.start({"op": "tick"}, on_event=on_event, on_exit=on_exit), 30)
    try:
        assert proc.alive and proc.pid > 0 and supervisor.running
        await supervisor.send({"op": "x 10.1.2.3:4000"})  # the worker warns about the unknown op on stderr
        await asyncio.sleep(1.5)
        code = await asyncio.wait_for(supervisor.stop(), 30)
        await asyncio.wait_for(exited.wait(), 10)
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(supervisor.stop(), 10)
    assert code == 0 and exits == [0] and events[-1] == {"ev": "exiting", "reason": "stop"}
    assert supervisor.stderr_lines >= 1 and not supervisor.running
    captured = capsys.readouterr()
    text = captured.out + captured.err  # structlog prints to stdout, or to stderr once configure_logging ran
    assert "screen worker" in text and "10.1.2.3" not in text and "[redacted]" in text
    assert not (tmp_path / "state").exists()


# ----- loopback: the worker ⇄ a test phone ----------------------------------------------------------------------------------------
async def make_phone_offer(phone: Any, *, close: bool = False) -> str:
    """What `lib/screen.ts` does (design §4.2 step 2): recvonly video and audio, the data channel, vanilla ICE."""
    phone.addTransceiver("video", direction="recvonly")
    phone.addTransceiver("audio", direction="recvonly")
    phone.createDataChannel("dome-screen-v1", ordered=True)
    await phone.setLocalDescription(await phone.createOffer())
    sdp = str(phone.localDescription.sdp)
    if close:
        await phone.close()
    return sdp


def start_message(offer: str, **overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "op": "start",
        "offer_sdp": offer,
        "stun_urls": [],
        "source": {
            "kind": "fake",
            "pattern": "synthetic",
            "surface_index": 0,
            "left": 0,
            "top": 0,
            "width": 640,
            "height": 360,
            "rotation": 0,
            "fps": 15,
        },
        "reserved": [{"x": 0, "y": 0, "w": 65535, "h": 2000}],
        "lease_seconds": 8,
        "heartbeat_seconds": 2,
        "tick_timeout_seconds": 10,
        "connect_timeout_seconds": 20,
        "encoder_preference": ["h264", "vp8"],
        "bitrate_kbps": {"direct": 1500, "relayed": 1000, "data_saver": 500},
        "audio": False,
    }
    message.update(overrides)
    return message


class Phone:
    """An aiortc peer playing the iPhone on this host: receives the track, collects data-channel messages."""

    def __init__(self) -> None:
        self.pc = aiortc.RTCPeerConnection(aiortc.RTCConfiguration(iceServers=[]))
        self.track: Any = None
        self.channel: Any = None
        self.dc_messages: list[dict[str, Any]] = []
        self.frames: list[Any] = []
        self._recv_task: asyncio.Task[None] | None = None

        @self.pc.on("track")
        def on_track(track: Any) -> None:
            if track.kind == "video":
                self.track = track

    async def offer(self) -> str:
        self.pc.addTransceiver("video", direction="recvonly")
        self.pc.addTransceiver("audio", direction="recvonly")
        self.channel = self.pc.createDataChannel("dome-screen-v1", ordered=True)

        @self.channel.on("message")
        def on_message(message: Any) -> None:
            self.dc_messages.append(
                parse_screen_dc_message(message, "pc_to_phone")
            )  # every PC message is in the vocabulary

        await self.pc.setLocalDescription(await self.pc.createOffer())
        return str(self.pc.localDescription.sdp)

    async def accept(self, answer_sdp: str) -> None:
        await self.pc.setRemoteDescription(aiortc.RTCSessionDescription(sdp=answer_sdp, type="answer"))
        if self.track is None:
            self.track = self.pc.getTransceivers()[0].receiver.track
        self._recv_task = asyncio.ensure_future(self._recv_frames())

    async def _recv_frames(self) -> None:
        with contextlib.suppress(Exception):
            while True:
                self.frames.append(await self.track.recv())

    async def send_pli(self, media_ssrc: int) -> None:
        await self.pc.getTransceivers()[0].receiver._send_rtcp_pli(media_ssrc)  # noqa: SLF001 - what Safari's decoder does on loss

    def dc_of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.dc_messages if m["type"] == kind]

    async def close(self) -> None:
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
        await self.pc.close()


@needs_libx264
async def test_loopback_session_h264_with_heartbeats_vocabulary_pause_pli_and_sentinel(
    settings: Settings, worker: InProcessWorker
) -> None:
    """Design §18 WP1.4 / §17.2: real frames from the fake source reach an aiortc "phone" over this host; the data channel
    carries only the closed vocabulary; pause/resume; a PLI from the phone yields a keyframe within 500 ms; and with the
    agent's log at DEBUG no loopback address, fingerprint or ICE credential reaches the log file, the crash log or the
    diagnostics bundle."""
    from dome_agent.diagnostics import build_bundle
    from dome_agent.logsetup import configure_logging

    configure_logging("DEBUG", settings.log_path)
    phone = Phone()
    w = worker
    try:
        offer = await phone.offer()
        summary = check_offer(offer)
        assert summary.h264 and summary.vp8
        offer_fingerprint = summary.fingerprint
        ice_pwd = next(line.split(":", 1)[1] for line in offer.split("\r\n") if line.startswith("a=ice-pwd:"))
        w.start()
        w.send(start_message(offer))
        answer = await w.expect("answer", timeout=20)
        assert answer["encoder"] == "h264" and answer["pli_hook"] == "present"
        assert answer["geometry"] == {
            "gen": 1,
            "surface_index": 0,
            "width": 640,
            "height": 360,
            "rotation": 0,
            "reserved": [{"x": 0, "y": 0, "w": 65535, "h": 2000}],
        }
        answer_sdp = str(answer["answer_sdp"])
        answer_fingerprint = sdp_fingerprints(answer_sdp)[0]
        assert "a=sendonly" in answer_sdp and "a=inactive" in answer_sdp  # video send-only, audio inactive in Phase 1
        assert w.app._track is not None and w.app._track.frames_captured == 0  # noqa: SLF001  # no capture before DTLS
        await phone.accept(answer_sdp)
        connected = await w.expect("connected", timeout=20)
        assert connected["path"] == "direct" and w.app._peer.path == "direct"  # noqa: SLF001
        await w.expect("first_frame", timeout=20)
        await wait_for(lambda: len(phone.frames) >= 3, timeout=20)
        first = phone.frames[0]
        assert (first.width, first.height) == (640, 360) and first.key_frame
        await wait_for(lambda: phone.channel.readyState == "open", timeout=10)
        await wait_for(lambda: bool(phone.dc_of("geometry")) and bool(phone.dc_of("state")), timeout=10)
        assert phone.dc_of("geometry")[0]["gen"] == 1 and phone.dc_of("state")[0] == {
            "type": "state",
            "state": "sending",
            "encoder": "h264",
        }
        # heartbeats and the vocabulary: only hb / keyframe / bye / audio come through; anything else is dropped
        phone.channel.send(json.dumps({"type": "hb"}))
        await w.expect("hb", timeout=5)
        phone.channel.send(json.dumps({"type": "nope"}))
        phone.channel.send("garbage")
        phone.channel.send(json.dumps({"type": "hb", "extra": 1}))
        await wait_for(lambda: w.app._peer.dc_dropped == 3, timeout=5)  # noqa: SLF001
        assert w.app._peer.dc_received == 4  # noqa: SLF001  # one heartbeat accepted, three messages dropped
        # PLI → IDR through the hooked _send_keyframe within 500 ms (design §5.3)
        idr_before = w.app._track.idr_sent  # noqa: SLF001
        frames_before = len(phone.frames)
        t0 = time.monotonic()
        await phone.send_pli(w.app._peer.sender._ssrc)  # noqa: SLF001
        await wait_for(lambda: w.app._track.idr_sent > idr_before, timeout=0.5)  # noqa: SLF001
        assert w.app._peer.pli_count >= 1  # noqa: SLF001
        await wait_for(lambda: any(f.key_frame for f in phone.frames[frames_before:]), timeout=3)
        assert time.monotonic() - t0 < 3.5
        phone.channel.send(json.dumps({"type": "keyframe"}))
        await w.expect("keyframe", timeout=5)
        # pause (a missing bar) → the phone is told, frames stop; resume → sending again with an IDR
        w.send({"op": "pause", "reason": "indicator"})
        await wait_for(
            lambda: any(m == {"type": "state", "state": "paused", "reason": "indicator"} for m in phone.dc_messages),
            timeout=5,
        )
        await asyncio.sleep(0.4)
        paused_count = len(phone.frames)
        await asyncio.sleep(0.5)
        assert len(phone.frames) == paused_count, "a paused worker sends no frame"
        idr_before = w.app._track.idr_sent  # noqa: SLF001
        w.send({"op": "resume"})
        await wait_for(lambda: phone.dc_of("state")[-1]["state"] == "sending", timeout=5)
        await wait_for(lambda: len(phone.frames) > paused_count, timeout=10)
        assert w.app._track.idr_sent >= idr_before + 1  # noqa: SLF001
        # the other agent → phone messages, and a geometry generation bump
        w.send({"op": "allowance", "unlimited": False, "remaining_seconds": None, "resets_at": None})
        w.send({"op": "restricted", "restricted": True, "reason": "permission_ui"})
        w.send({"op": "reserved", "reserved": [{"x": 0, "y": 0, "w": 65535, "h": 3000}]})
        await wait_for(
            lambda: (
                bool(phone.dc_of("allowance")) and bool(phone.dc_of("restricted")) and len(phone.dc_of("geometry")) >= 2
            ),
            timeout=5,
        )
        assert phone.dc_of("allowance")[-1] == {
            "type": "allowance",
            "unlimited": False,
            "remaining_seconds": None,
            "resets_at": None,
        }
        assert phone.dc_of("restricted")[-1] == {"type": "restricted", "restricted": True, "reason": "permission_ui"}
        geometry = await w.expect("geometry", timeout=5)
        assert (
            geometry["gen"] == 2
            and phone.dc_of("geometry")[-1]["gen"] == 2
            and phone.dc_of("geometry")[-1]["reserved"][0]["h"] == 3000
        )
        # the streamed monitor changed size (design §5.2 "rebuild instead of fail"): a new source at the new size, the
        # encoder reopened with SPS/PPS + IDR, a new generation announced to the agent and to the phone
        w.send(
            {
                "op": "rebuild",
                "source": {"kind": "fake", "surface_index": 0, "width": 480, "height": 270, "rotation": 0},
                "reserved": [{"x": 0, "y": 0, "w": 65535, "h": 4000}],
            }
        )
        rebuilt = await w.expect("geometry", timeout=5)
        assert (rebuilt["gen"], rebuilt["width"], rebuilt["height"], rebuilt["reserved"][0]["h"]) == (3, 480, 270, 4000)
        await wait_for(lambda: phone.dc_of("geometry")[-1]["gen"] == 3, timeout=5)
        await wait_for(lambda: any((f.width, f.height) == (480, 270) for f in phone.frames), timeout=10)
        resized = next(f for f in phone.frames if (f.width, f.height) == (480, 270))
        assert resized.key_frame and w.app._track.rebuilds == 1  # noqa: SLF001
        assert w.app._track.source_size == (480, 270)  # noqa: SLF001
        stats = await w.expect("stats", timeout=8)
        assert stats["relayed_bytes"] == 0 and stats["bytes_sent"] > 0 and stats["bitrate_kbps"] == 1500
        # device round 2: what the media path did, in every stats event
        assert stats["rtp_packets"] > 0 and stats["rtp_bytes"] == stats["bytes_sent"] and stats["aus"] > 0
        assert stats["captured"] > 0 and stats["idr"] >= 2 and stats["encoder_label"] == "libx264 DEV ONLY"
        assert stats["first_kf_ms"] is not None and stats["pli"] >= 1 and stats["kf_pli"] >= 1 and stats["kf_dc"] >= 1
        assert stats["stalled"] is None and stats["pending"] == 0 and stats["nack"] == 0
        # the phone's receiver reports on our SSRC (aiortc sends one about every second); aiortc's sender computes a
        # round-trip time only from a report that echoes its latest sender report (rtcrtpsender.py: `__lsr ==
        # report.lsr`), so the first reports may carry rr=True with rtt_ms None: wait for both, bounded
        for _ in range(10):
            if stats["rr"] and stats["rtt_ms"] is not None:
                break
            stats = await w.expect("stats", timeout=8)
        assert stats["rr"] is True and stats["rtt_ms"] is not None and stats["lost"] == 0
        from aiortc.codecs import h264 as aiortc_h264

        assert aiortc_h264.PACKET_MAX == 1200  # lowered before the first send
        phone.channel.send(json.dumps({"type": "bye"}))
        await w.expect("bye", timeout=5)
        w.send({"op": "stop"})
        assert (await w.expect("exiting", timeout=10))["reason"] == "stop"
        assert await w.finish() == 0
    finally:
        await phone.close()
        configure_logging("INFO", None)
    # the sentinel (design §4.4, §17.2): with everything at DEBUG, no address, fingerprint or credential in any sink
    log_text = settings.log_path.read_text(encoding="utf-8")
    assert log_text, "the agent log was written"
    for line in ("screen encoder opened", "screen first keyframe", "screen transport", "screen media final"):
        assert line in log_text, line
    bundle = json.dumps(build_bundle(settings, {"screen": {"session": None}}))
    for needle in ("127.0.0.1:", "[::1]", offer_fingerprint, answer_fingerprint, ice_pwd, "a=ice-pwd", "a=candidate"):
        assert needle not in log_text, needle
        assert needle not in bundle, needle
    assert not (settings.log_dir / "crash.log").exists()


@needs_libx264
async def test_loopback_session_vp8_fallback(worker: InProcessWorker) -> None:
    """ "Basic picture mode": the phone offered VP8 only (or H.264 failed): aiortc's own libvpx encoder, raw frames, the
    PLI hook calls aiortc's original flag too."""
    phone = Phone()
    w = worker
    try:
        offer = await phone.offer()
        w.start()
        w.send(start_message(offer, encoder_preference=["vp8"]))
        answer = await w.expect("answer", timeout=20)
        assert answer["encoder"] == "vp8"
        await phone.accept(str(answer["answer_sdp"]))
        await w.expect("first_frame", timeout=20)
        await wait_for(lambda: len(phone.frames) >= 2, timeout=20)
        assert (phone.frames[0].width, phone.frames[0].height) == (640, 360)
        await wait_for(lambda: bool(phone.dc_of("state")), timeout=10)
        assert phone.dc_of("state")[0]["encoder"] == "vp8"
        await phone.send_pli(w.app._peer.sender._ssrc)  # noqa: SLF001
        await wait_for(lambda: w.app._peer.pli_count >= 1, timeout=2)  # noqa: SLF001
        w.send({"op": "stop"})
        assert await w.finish() == 0
    finally:
        await phone.close()


# ----- review round (2026-10-22): no capture before DTLS on the Windows kinds, the IDR rate limit, the DDA source ---
class Recording:
    """A FrameSource that records every call (the ``ddagrab`` / WGC stand-in: real capture needs Windows)."""

    def __init__(self, log: list[tuple[str, int]], tag: int, width: int = 320, height: int = 180) -> None:
        self.log, self.tag = log, tag
        self.width, self.height = width, height
        self.inner = fake.SyntheticSource(width, height, 15)
        self.opened = 0

    def open(self) -> None:
        self.opened += 1
        self.log.append(("open", self.tag))
        self.inner.open()

    def read(self) -> Any | None:
        return self.inner.read()

    def close(self) -> None:
        self.log.append(("close", self.tag))
        self.inner.close()


def test_build_source_constructs_the_windows_sources_without_opening_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """rules.screen_sessions / design §5.1: on the shipping path `_build_source` ran before the answer and opened
    Desktop Duplication (and WGC) at once, capturing the desktop before DTLS. Now it only constructs."""
    from dome_agent.screen.worker import capture_dda, capture_wgc
    from dome_agent.screen.worker import main as worker_main

    calls: list[str] = []

    class Dda:
        def __init__(self, index: int, width: int, height: int, fps: int) -> None:
            calls.append("dda.init")
            self.width, self.height = width, height

        def open(self) -> None:
            calls.append("dda.open")
            raise ProtocolError("SCREEN_CAPTURE_UNAVAILABLE", "no", detail={"detail": "unsupported_display"})

        def read(self) -> None:
            return None

        def close(self) -> None:
            calls.append("dda.close")

    class Wgc:
        def __init__(self, index: int, width: int, height: int, fps: int, **kw: Any) -> None:
            calls.append(f"wgc.init primary={kw['primary']} expected={kw['expected_size']}")
            self.width, self.height = width, height

        def open(self) -> None:
            calls.append("wgc.open")

        def read(self) -> None:
            return None

        def close(self) -> None:
            calls.append("wgc.close")

    monkeypatch.setattr(capture_dda, "DdagrabSource", Dda)
    monkeypatch.setattr(capture_wgc, "WgcSource", Wgc)
    monkeypatch.setattr(sys, "platform", "win32")
    spec = {"surface_index": 0, "primary": True, "width": 2560, "height": 1440}
    source = worker_main._build_source("ddagrab", spec, 1280, 720, 30)  # noqa: SLF001
    wgc_only = worker_main._build_source("wgc", spec, 1280, 720, 30)  # noqa: SLF001
    monkeypatch.setattr(sys, "platform", "linux")
    assert calls == ["dda.init", "wgc.init primary=True expected=(2560, 1440)"]  # constructed, never opened
    calls.clear()
    source.open()  # the track's one open, after DTLS: Desktop Duplication fails here, WGC takes over
    assert calls == ["dda.open", "wgc.init primary=True expected=(2560, 1440)", "wgc.open"]
    source.close()
    wgc_only.open()
    assert calls[-2:] == ["wgc.close", "wgc.open"]


def test_dda_with_fallback_keeps_desktop_duplication_when_it_opens() -> None:
    from dome_agent.screen.worker.capture_dda import DdaWithFallback

    log: list[tuple[str, int]] = []
    fell: list[str] = []
    wgc_built: list[int] = []

    def build_wgc() -> Recording:
        wgc_built.append(1)
        return Recording(log, 2)

    source = DdaWithFallback(Recording(log, 1), build_wgc, on_fallback=fell.append)
    assert log == [] and (source.width, source.height) == (320, 180)
    with pytest.raises(ProtocolError):
        source.read()  # nothing is read from a source that was never opened
    source.open()
    assert source.read() is not None and log == [("open", 1)] and fell == [] and wgc_built == []
    source.close()
    assert log == [("open", 1), ("close", 1)]


async def test_no_windows_source_opens_before_dtls_connects_even_across_a_rebuild(
    worker: InProcessWorker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker loop with the shipping kind (`ddagrab`): the source is built at `start`, a monitor change asks for a
    rebuild between the answer and DTLS, and nothing opens until the phone's DTLS connects; then exactly one source
    (the rebuilt one) opens, exactly once."""
    from dome_agent.screen.worker import main as worker_main

    log: list[tuple[str, int]] = []
    built: list[Recording] = []

    def build(kind: str, spec: dict[str, Any], width: int, height: int, fps: int) -> Recording:
        assert kind == "ddagrab"
        source = Recording(log, len(built), width, height)
        built.append(source)
        return source

    monkeypatch.setattr(worker_main, "_build_source", build)
    phone = Phone()
    w = worker
    try:
        offer = await phone.offer()
        w.start()
        message = start_message(offer, encoder_preference=["vp8"])
        message["source"] = {**message["source"], "kind": "ddagrab"}
        w.send(message)
        answer = await w.expect("answer", timeout=20)
        assert built and log == []  # built, not opened: no frame exists for an unauthenticated peer
        w.send({"op": "rebuild", "source": {"surface_index": 0, "width": 800, "height": 600}, "reserved": []})
        await w.expect("geometry", timeout=10)
        assert len(built) == 2 and log == []  # the rebuilt source is not opened either
        await asyncio.sleep(0.3)
        assert log == []
        await phone.accept(str(answer["answer_sdp"]))
        await w.expect("connected", timeout=20)
        await w.expect("first_frame", timeout=20)
        assert [entry for entry in log if entry[0] == "open"] == [("open", 1)]  # once, the rebuilt source, after DTLS
        assert built[0].opened == 0 and built[1].opened == 1
    finally:
        await phone.close()


def _vp8_track(source: Any) -> ScreenTrack:
    return ScreenTrack(
        source=source,
        choice=encode.EncoderChoice("vp8", ""),
        width=64,
        height=36,
        fps=30,
        bitrate_kbps=500,
        capture_allowed=asyncio.Event(),
        emit=lambda e: None,
    )


def test_requested_idrs_are_rate_limited_to_two_per_second() -> None:
    """Design §5.3: IDR on PLI/FIR rate-limited to 2/s; extra requests coalesce into the next allowed slot; a new
    geometry is never delayed."""
    track = _vp8_track(fake.SyntheticSource(64, 36, 30))
    assert track._take_idr(10.0) is True  # noqa: SLF001  # join
    for i in range(10):  # 10 PLIs in 100 ms right after the join IDR
        track.force_keyframe()
        assert track._take_idr(10.0 + i * 0.01) is False  # noqa: SLF001
    assert track._take_idr(10.5) is True  # noqa: SLF001  # one IDR, in the next slot
    assert track._take_idr(10.6) is False  # noqa: SLF001  # every request was answered by it
    track.force_keyframe(urgent=True)
    assert track._take_idr(10.61) is True  # noqa: SLF001  # a new geometry: at once
    sent = 0
    for i in range(10):  # a quiet second later, a burst of 10 PLIs in 100 ms
        track.force_keyframe()
        sent += track._take_idr(12.0 + i * 0.01)  # noqa: SLF001
    assert sent == 1 and track.keyframe_requests == 20


async def test_a_pli_storm_on_a_live_track_yields_at_most_two_idrs() -> None:
    calls: list[float] = []
    track = _vp8_track(fake.SyntheticSource(64, 36, 30))
    track.vp8_keyframe = lambda: calls.append(time.monotonic())
    track._capture_allowed.set()  # noqa: SLF001
    await asyncio.wait_for(track.recv(), 5)  # the join keyframe
    await asyncio.sleep(0.6)
    calls.clear()
    t0 = time.monotonic()
    pulls = asyncio.ensure_future(_pull(track, 0.4))
    for _ in range(10):
        track.force_keyframe()
        await asyncio.sleep(0.01)
    await pulls
    assert 1 <= len(calls) <= 2 and calls[0] - t0 < 0.2
    await track.aclose()


async def _pull(track: ScreenTrack, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await asyncio.wait_for(track.recv(), 5)


class _Scripted:
    """Like PyAV's decode iterator (a generator): an item that is an exception is raised, and that raise FINISHES the
    iterator, so every later ``next`` is StopIteration until the container hands out a fresh one."""

    def __init__(self, items: list[Any]) -> None:
        self.items = items  # shared with the container: a fresh iterator continues the script
        self.dead = False

    def __iter__(self) -> Any:
        return self

    def __next__(self) -> Any:
        if self.dead or not self.items:
            raise StopIteration
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            self.dead = True
            raise item
        return item

    def close(self) -> None:
        self.dead = True


class _Container:
    """A scripted stand-in for PyAV's InputContainer: ``decode`` hands out iterators over the same script."""

    def __init__(self, items: list[Any] | None = None) -> None:
        self.items = list(items or [])
        self.decodes = 0
        self.streams = types.SimpleNamespace(video=[object()])

    def decode(self, _stream: Any) -> _Scripted:
        self.decodes += 1
        return _Scripted(self.items)

    def close(self) -> None:
        return None


def _scripted_dda(monkeypatch: pytest.MonkeyPatch, scripts: list[list[Any]]) -> Any:
    """A DdagrabSource whose graph is a scripted container per (re)open (:class:`_Container`)."""
    from dome_agent.screen.worker import capture_dda

    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "win32")
        source = capture_dda.DdagrabSource(0, 64, 36, 30)
    opens: list[int] = []
    containers: list[_Container] = []

    def open_() -> None:
        opens.append(1)
        if not scripts:
            raise ProtocolError("SCREEN_CAPTURE_UNAVAILABLE", "no graph", detail={"detail": "driver"})
        container = _Container(scripts.pop(0))
        containers.append(container)
        source._container = container  # noqa: SLF001
        source._frames = container.decode(None)  # noqa: SLF001

    source.open = open_
    source.opens = opens
    source.containers = containers
    return source


def test_a_still_desktop_is_not_access_lost_and_a_frame_resets_the_rebuild_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EAGAIN (PyAV's BlockingIOError: no frame yet, the first read of a fresh graph) is None, never a rebuild;
    access-lost errors rebuild the graph, and a frame in between resets the count, so a long session survives any
    number of UAC prompts (each is a desktop switch). The same still frame after a rebuild counts as a change (a new
    graph starts afresh), so every reopen here yields one frame."""
    import errno

    frame = still_frame(64, 36)
    eagain = av.error.BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
    scripts = [[eagain, eagain, frame, OSError("access lost")]]
    for _ in range(6):  # six desktop switches, each followed by a good frame
        scripts.append([frame, OSError("access lost")])
    scripts.append([frame, frame])
    source = _scripted_dda(monkeypatch, scripts)
    source.open()
    assert source.read() is None and source.read() is None and source.rebuilds == 0  # still: no rebuild
    # each EAGAIN finished PyAV's generator: a fresh one over the same container, never a reopen (a dead generator
    # would otherwise answer StopIteration at every read, for ever, with no error)
    assert source.containers[0].decodes == 3 and len(source.opens) == 1
    got = 0
    for _ in range(40):
        out = source.read()
        got += out is not None
        if not scripts and got >= 8:
            break
    assert got >= 8 and source.rebuilds == 0 and len(source.opens) == 8  # never gave up


def test_a_reopen_that_fails_backs_off_and_does_not_end_capture_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rebuild while the secure desktop is still up fails to open: the source reports None and retries with a
    back-off (the agent's secure-desktop pause decides the end), and gives up only after REOPEN_ATTEMPTS. (All of this
    after the graph's first frame: before it a failure raises at once and the session switches to
    Windows.Graphics.Capture, DECISIONS #104.)"""
    from dome_agent.screen.worker import capture_dda

    frame = still_frame(64, 36)
    monkeypatch.setattr(capture_dda, "REOPEN_BACKOFF_SECONDS", (0.0,))
    source = _scripted_dda(monkeypatch, [[frame, OSError("access lost")]])
    source.open()
    assert source.read() is not None and source.proven
    for _ in range(capture_dda.REOPEN_ATTEMPTS - 1):
        assert source.read() is None  # the first read loses access; reopens then fail and back off
    with pytest.raises(ProtocolError) as exc:
        for _ in range(3):
            source.read()
    assert exc.value.code == "SCREEN_CAPTURE_UNAVAILABLE"
    # three access-lost errors in a row, without a frame in between, still give up
    source = _scripted_dda(
        monkeypatch, [[frame, OSError("lost")], [OSError("lost")], [OSError("lost")], [OSError("lost")], []]
    )
    source.open()
    assert source.read() is not None
    with pytest.raises(ProtocolError):
        for _ in range(10):
            source.read()
    assert source.rebuilds == capture_dda.REBUILD_LIMIT + 1


def test_a_graph_that_ends_is_rebuilt_not_read_for_ever(monkeypatch: pytest.MonkeyPatch) -> None:
    """EOF (the decode iterator ends: StopIteration) is a failure like access lost — the graph is rebuilt and a frame
    resets the count — never a source that silently answers None at every read."""
    from dome_agent.screen.worker import capture_dda

    frame = still_frame(64, 36)
    source = _scripted_dda(monkeypatch, [[frame], [frame], [], [], [], []])
    source.open()
    assert source.read() is not None
    assert source.read() is None and source.rebuilds == 1 and len(source.opens) == 2  # EOF: rebuilt
    assert source.read() is not None and source.rebuilds == 0  # a frame from the new graph resets the count
    with pytest.raises(ProtocolError) as exc:
        for _ in range(10):
            source.read()
    assert exc.value.code == "SCREEN_CAPTURE_UNAVAILABLE" and source.rebuilds == capture_dda.REBUILD_LIMIT + 1
