"""Device round 2 (2026-10-27, build 593d3fd): the founder's PC was ``live`` and connected peer to peer for 217 s,
Windows.Graphics.Capture delivered frames, and nothing showed which stage of the media path failed; Desktop Duplication
failed on every PC because ``scale_d3d11`` was given option names it does not have. This file covers the PC's fixes:

* the Desktop Duplication graph's option names against FFmpeg n8.1.3, the graph check of ``--self-test`` and its
  parity with the licence gate's copy;
* ``h264_mf`` opened low-delay without B-frames, unused options and the transform's name recorded, the probe at the
  real output size with FFmpeg's reason per failed candidate, the preference order, AVCC output, late extradata and
  in-band parameter sets;
* the track's drain pump against an encoder that holds 7 frames (the first packet and a PLI-forced IDR in under
  0.5 s, the pump bounded), the stall report, an encoder that raises or cannot open;
* the peer's stats (``rtp_stats``), the NACK count, the transport line (never an address), RTP payloads of at most
  1200 bytes, and every new log line under the supervisor's 400-character cap with no redacted key.

Evidence: unit-tested on Linux with the stock PyPI PyAV wheel. ``libx264`` (development only) stands in for
``h264_mf``, and a context that holds N frames stands in for a Media Foundation transform outside low-latency mode:
how the real transform behaves (Annex B or AVCC, in-band parameter sets, IDR flags, async latency, whether it honours
the low-latency flag) is **not yet verified on a device**."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import inspect
import json
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from dome_protocol import ProtocolError

from dome_agent.logsetup import configure_logging, redact_text_line
from dome_agent.screen.supervisor import STDERR_LINE_MAX
from dome_agent.screen.worker import capture_dda, encode, fake
from dome_agent.screen.worker import main as worker_main

from .ffmpeg_n813 import N813_FILTER_OPTIONS, unknown_options

aiortc = pytest.importorskip("aiortc")
av = pytest.importorskip("av")

from aiortc.codecs import h264 as aiortc_h264  # noqa: E402 - after the importorskip
from aiortc.codecs import vpx as aiortc_vpx  # noqa: E402
from aiortc.rtcrtpsender import RTCRtpSender  # noqa: E402

from dome_agent.screen.worker import rtc  # noqa: E402
from dome_agent.screen.worker import track as track_module  # noqa: E402
from dome_agent.screen.worker.main import EXIT_START_FAILED  # noqa: E402
from dome_agent.screen.worker.track import ScreenTrack  # noqa: E402

from .test_screen_worker import InProcessWorker, make_phone_offer, start_message  # noqa: E402

LIBX264 = "libx264" in av.codecs_available
needs_libx264 = pytest.mark.skipif(not LIBX264, reason="the development-only libx264 of the PyPI PyAV wheel is absent")

SPS = bytes([0x67, 0x4D, 0x40, 0x1F, 0xAA, 0xBB])  # Main profile, level 3.1
SPS_B = bytes([0x67, 0x42, 0xC0, 0x1F, 0xCC])  # Constrained Baseline 3.1 (what Safari's offer names)
PPS = bytes([0x68, 0xCE, 0x3C, 0x80])
IDR = bytes([0x65, 0x88, 0x84, 0x00, 0x10])
P_SLICE = bytes([0x41, 0x9A, 0x02, 0x03])
AUD = bytes([0x09, 0xF0])


def annexb(*nals: bytes) -> bytes:
    return b"".join(b"\x00\x00\x00\x01" + n for n in nals)


def avcc(*nals: bytes) -> bytes:
    return b"".join(len(n).to_bytes(4, "big") + n for n in nals)


def avcc_record(sps: bytes, pps: bytes) -> bytes:
    """An ``AVCDecoderConfigurationRecord`` (what some encoders put in extradata)."""
    return (
        bytes([1, sps[1], sps[2], sps[3], 0xFF, 0xE1])
        + len(sps).to_bytes(2, "big")
        + sps
        + bytes([1])
        + len(pps).to_bytes(2, "big")
        + pps
    )


def dev_h264_choice() -> encode.EncoderChoice:
    return encode.encoder_candidates(["h264"], dev_h264=True, platform="linux")[0]


def grey_frame(width: int = 64, height: int = 36) -> Any:
    frame = av.VideoFrame(width, height, "yuv420p")
    for plane in frame.planes:
        plane.update(bytes([100]) * plane.buffer_size)
    frame.pts = 0
    frame.time_base = encode.VIDEO_TIME_BASE
    return frame


# ----- A: the Desktop Duplication graph -----------------------------------------------------------------------------------
@pytest.mark.parametrize(("idx", "width", "height", "fps"), [(0, 1280, 720, 30), (1, 1152, 720, 15), (3, 960, 720, 30)])
def test_the_desktop_duplication_graph_uses_only_ffmpeg_n813_option_names(
    idx: int, width: int, height: int, fps: int
) -> None:
    """Device round 2: "Could not set non-existent option 'w' to value '1280'". Every ``key=`` of the graph is checked
    against the option tables of the n8.1.3 sources (``tests/ffmpeg_n813.py``)."""
    graph = capture_dda.ddagrab_graph(idx, width, height, fps)
    filters = capture_dda.graph_filters(graph)
    assert [name for name, _ in filters] == ["ddagrab", "scale_d3d11", "hwdownload", "format"]
    assert set(N813_FILTER_OPTIONS) == {name for name, _ in filters}
    for name, args in filters:
        assert unknown_options(name, args) == [], (name, args)
    assert f"scale_d3d11=width={width}:height={height}:format=nv12" in graph and f"output_idx={idx}" in graph
    # the checker itself catches the graph that failed on the founder's PC
    assert unknown_options("scale_d3d11", "w=1280:h=720:format=nv12") == ["w", "h"]
    assert "w=" not in capture_dda.__doc__.split("scale_d3d11=", 1)[1].split("``", 1)[0]


def test_the_licence_gate_parses_the_workers_own_graph() -> None:
    from .test_licence_gate import gate

    assert gate.DDAGRAB_GRAPH == capture_dda.ddagrab_graph(0, 1280, 720, 30)


def test_check_graph_options_creates_the_filters_unconfigured_and_names_ffmpegs_reason() -> None:
    """The check the Windows self-test runs on the real graph, here on filters the PyPI wheel has: each filter is
    created with its options (FFmpeg parses them), the graph is never configured, PyAV's log level is restored."""
    assert av.logging.get_level() is None
    assert capture_dda.check_graph_options(
        "testsrc=size=64x64:rate=5,scale=width=32:height=32,hwdownload,format=nv12"
    ) == [
        "testsrc",
        "scale",
        "hwdownload",
        "format",
    ]
    with pytest.raises(ValueError) as caught:
        capture_dda.check_graph_options("testsrc=size=64x64,hwdownload=w=1280")
    text = str(caught.value)
    assert text.startswith("hwdownload: ") and "No such option: w" in text and "Option not found" in text
    with pytest.raises(ValueError, match="nosuchfilter"):
        capture_dda.check_graph_options("nosuchfilter=a=1")
    assert av.logging.get_level() is None


def test_the_windows_self_test_parses_the_desktop_duplication_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--self-test`` on Windows (mocked here: ddagrab and scale_d3d11 exist only in the Windows build) runs the
    graph check on ``ddagrab_graph(0, 1280, 720, 30)``; a failure makes the report fail."""
    seen: list[str] = []

    def check(graph: str) -> list[str]:
        seen.append(graph)
        return [name for name, _ in capture_dda.graph_filters(graph)]

    monkeypatch.setattr(capture_dda, "check_graph_options", check)
    monkeypatch.setattr(sys, "platform", "win32")
    report = worker_main.self_test(dev_h264=False)
    assert report["checks"]["ddagrab_graph_options"] == {
        "ok": True,
        "value": {"filters": ["ddagrab", "scale_d3d11", "hwdownload", "format"]},
    }
    assert seen == [capture_dda.ddagrab_graph(0, 1280, 720, 30)]

    def failing(_graph: str) -> list[str]:
        raise ValueError("scale_d3d11: No such option: w.; Option not found (errno 1414549496)")

    monkeypatch.setattr(capture_dda, "check_graph_options", failing)
    report = worker_main.self_test(dev_h264=False)
    assert report["ok"] is False
    assert report["checks"]["ddagrab_graph_options"]["error"].startswith("ValueError: scale_d3d11: No such option: w")


def test_a_capture_at_info_on_one_thread_does_not_turn_info_into_error_lines() -> None:
    """``ffmpeg_log_lines(INFO)`` (the encoder open) raises the global level while it runs; an ERROR block on another
    path reads only ERROR and worse, and the level returns to PyAV's default afterwards."""
    with capture_dda.ffmpeg_log_lines(capture_dda.AV_LOG_INFO) as info:
        with capture_dda.ffmpeg_error_lines() as errors:
            assert av.logging.get_level() == av.logging.INFO
            av.logging.log(av.logging.INFO, "h264_mf", "MFT name: 'x'\n")
            av.logging.log(av.logging.ERROR, "h264_mf", "ProcessInput() failed\n")
        assert capture_dda.ffmpeg_error_text(RuntimeError("boom"), errors).startswith("ProcessInput() failed; ")
        assert "MFT name" not in capture_dda.ffmpeg_error_text(RuntimeError("boom"), errors)
    assert info == [] and av.logging.get_level() is None


# ----- B: the encoder --------------------------------------------------------------------------------------------------------
class FakeContext:
    """PyAV's codec context as far as ``H264Encoder`` uses it, recording what it was given. ``script`` holds the
    access units each ``encode`` returns; ``late_extradata`` appears after the first encode (an asynchronous transform
    filling ``MF_MT_MPEG_SEQUENCE_HEADER`` late); options not in ``consumes`` stay in ``options`` as PyAV leaves them."""

    def __init__(
        self,
        *,
        extradata: bytes = b"",
        script: list[list[bytes]] | None = None,
        late_extradata: bytes | None = None,
        mft: str | None = None,
        consumes: frozenset[str] = frozenset({"scenario", "rate_control", "hw_encoding"}),
    ) -> None:
        self.extradata = extradata
        self.script = list(script or [])
        self.late_extradata = late_extradata
        self.mft = mft
        self.consumes = consumes
        self.flags = 0
        self.options: dict[str, str] = {}
        self.max_b_frames = 2
        self.opened_with: dict[str, str] | None = None
        self.pts = 0

    def open(self) -> None:
        if self.mft is not None:  # mf_utils.c logs the transform's friendly name at INFO
            av.logging.log(av.logging.INFO, "h264_mf", f"MFT name: '{self.mft}'\n")
        self.opened_with = dict(self.options)
        self.options = {k: v for k, v in self.options.items() if k not in self.consumes}

    def encode(self, frame: Any) -> list[Any]:
        if self.late_extradata is not None:
            self.extradata, self.late_extradata = self.late_extradata, None
        out = []
        for data in self.script.pop(0) if self.script else []:
            packet = av.Packet(data)
            packet.pts = self.pts
            packet.time_base = encode.VIDEO_TIME_BASE
            self.pts += 3000
            out.append(packet)
        return out

    def close(self, strict: bool = False) -> None:
        return None


def with_context(monkeypatch: pytest.MonkeyPatch, ctx: FakeContext) -> None:
    monkeypatch.setattr(av, "CodecContext", SimpleNamespace(create=lambda _name, _mode: ctx))


def test_h264_mf_opens_low_delay_without_b_frames_and_records_its_open_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """mfenc.c sends CODECAPI_AVLowLatencyMode only with AV_CODEC_FLAG_LOW_DELAY and always passes the context's
    max_b_frames; the transform's friendly name comes from its INFO line; no parameter set is ever in the facts, only
    the SPS's three level bytes."""
    from av.codec.context import Flags

    ctx = FakeContext(extradata=avcc_record(SPS, PPS), mft="Intel® Quick Sync Video H.264 Encoder MFT")
    with_context(monkeypatch, ctx)
    software = encode.encoder_candidates(["h264"], dev_h264=False, platform="win32")[1]
    enc = encode.H264Encoder(software, 1280, 720, 30, 1500)
    assert ctx.flags & Flags.low_delay and ctx.flags & Flags.global_header and ctx.max_b_frames == 0
    assert ctx.opened_with == {"scenario": "display_remoting", "rate_control": "cbr", "hw_encoding": "0"}
    facts = enc.open_facts()
    assert facts == {
        "label": "h264_mf",
        "mft": "Intel® Quick Sync Video H.264 Encoder MFT",
        "pix_fmt": "yuv420p",
        "size": "1280x720",
        "fps": 30,
        "kbps": 1500,
        "low_delay": True,
        "open_ms": facts["open_ms"],
        "xd_len": len(avcc_record(SPS, PPS)),
        "xd_nals": "7,8",
        "sps": "4d401f",
        "unused": None,
    }
    assert av.logging.get_level() is None
    # an option the codec did not consume is named (the old "max_b_frames" string was one: FFmpeg's option is "bf")
    ctx = FakeContext(extradata=annexb(SPS, PPS))
    with_context(monkeypatch, ctx)
    legacy = encode.EncoderChoice("h264", "h264_mf", options={**software.options, "max_b_frames": "0"})
    assert encode.H264Encoder(legacy, 1280, 720, 30, 1500).open_facts()["unused"] == "max_b_frames"


@needs_libx264
def test_a_real_encoder_names_unused_options_and_libx264_is_not_low_delay() -> None:
    choice = dev_h264_choice()
    assert encode.context_flags(choice) == ("global_header",)
    bogus = encode.EncoderChoice("h264", "libx264", options={**choice.options, "bogus": "1"}, development_only=True)
    enc = encode.H264Encoder(bogus, 320, 180, 15, 500)
    try:
        assert enc.unused_options == ["bogus"] and enc.open_facts()["unused"] == "bogus"
        assert enc.open_facts()["low_delay"] is False and enc.open_facts()["sps"] == "42c01f"
        assert enc._ctx.max_b_frames == 0  # noqa: SLF001
    finally:
        enc.close()


def test_avcc_access_units_are_converted_counted_and_warned_about_once(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeContext(extradata=avcc_record(SPS, PPS), script=[[avcc(IDR)], [avcc(P_SLICE)], [b"\x12\x34\x56"]])
    with_context(monkeypatch, ctx)
    enc = encode.H264Encoder(encode.EncoderChoice("h264", "h264_mf"), 64, 36, 30, 500)
    with structlog.testing.capture_logs() as logs:
        first = enc.encode(grey_frame(), force_idr=True)
        second = enc.encode(grey_frame(), force_idr=False)
        third = enc.encode(grey_frame(), force_idr=False)
    # converted to Annex B, the IDR given SPS/PPS from the avcC extradata (aiortc's packetizer splits on start codes)
    assert bytes(first[0]) == annexb(SPS, PPS) + annexb(IDR) and bytes(second[0]) == annexb(P_SLICE)
    assert bytes(third[0]) == b"\x12\x34\x56"  # neither form: passed through unchanged
    assert enc.non_annexb == 3 and enc.prepended == 1 and enc.idr_count == 1
    assert enc.first_idr is not None and enc.first_idr["form"] == "avcc" and enc.first_idr["sps"] == "4d401f"
    assert enc.first_idr["nals"] == "7,8,5" and enc.first_idr["prepended"] is True
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert [e["event"] for e in warnings] == ["H.264 encoder output is not Annex B"] and warnings[0]["form"] == "avcc"


def test_extradata_that_arrives_late_is_read_after_each_encode(monkeypatch: pytest.MonkeyPatch) -> None:
    """mfenc.c refreshes extradata only when the transform reports a stream change (MF_E_TRANSFORM_STREAM_CHANGE): the
    re-read picks those sets up. (The in-band sets are the real guard: next test.)"""
    ctx = FakeContext(extradata=b"", script=[[annexb(AUD, IDR)]], late_extradata=annexb(SPS, PPS))
    with_context(monkeypatch, ctx)
    enc = encode.H264Encoder(encode.EncoderChoice("h264", "h264_mf"), 64, 36, 30, 500)
    assert enc.open_facts()["xd_len"] == 0 and enc.parameter_sets == b""
    [au] = enc.encode(grey_frame(), force_idr=True)
    assert bytes(au) == annexb(SPS, PPS) + annexb(AUD, IDR) and enc.prepended == 1
    assert enc.frames_in_before_first_packet == 1 and enc.first_au == enc.first_idr


def test_in_band_parameter_sets_are_preferred_when_an_idr_lacks_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """The extradata names one SPS, the stream another: a later IDR without parameter sets gets the ones the encoder
    really used."""
    ctx = FakeContext(extradata=annexb(SPS, PPS), script=[[annexb(SPS_B, PPS, IDR)], [annexb(P_SLICE)], [annexb(IDR)]])
    with_context(monkeypatch, ctx)
    enc = encode.H264Encoder(encode.EncoderChoice("h264", "h264_mf"), 64, 36, 30, 500)
    first = enc.encode(grey_frame(), force_idr=True)
    enc.encode(grey_frame(), force_idr=False)
    [later] = enc.encode(grey_frame(), force_idr=True)
    assert bytes(first[0]) == annexb(SPS_B, PPS, IDR) and enc.first_idr is not None
    assert enc.first_idr["prepended"] is False and enc.first_idr["sps"] == "42c01f"
    assert bytes(later) == annexb(SPS_B, PPS) + annexb(IDR) and encode.sps_triple(bytes(later)) == "42c01f"
    assert (enc.frames_in, enc.packets_out, enc.idr_count, enc.prepended) == (3, 3, 2, 1)


def test_bitstream_helpers() -> None:
    mixed = b"\x00\x00\x01" + SPS + b"\x00\x00\x00\x01" + PPS + b"\x00\x00\x01" + IDR
    assert encode.split_nals(mixed) == [SPS, PPS, IDR]
    assert encode.avcc_to_annexb(avcc(SPS, IDR)) == annexb(SPS, IDR)
    assert encode.avcc_to_annexb(avcc(SPS)[:-1]) is None  # the lengths do not chain to the end
    assert encode.avcc_to_annexb(b"\x00\x00\x00\x02\x80\x01") is None  # forbidden bit set: not a NAL
    assert encode.to_annexb(annexb(IDR)) == (annexb(IDR), "annexb4")
    assert encode.to_annexb(b"\x00\x00\x01" + IDR)[1] == "annexb3"
    assert encode.to_annexb(avcc(IDR)) == (annexb(IDR), "avcc")
    assert encode.sps_triple(annexb(PPS, IDR)) is None and encode.sps_triple(annexb(SPS_B)) == "42c01f"
    # real Annex B with 3-byte start codes stays Annex B: read as lengths, its bytes never chain through NAL headers
    for unit in (
        b"\x00\x00\x01" + AUD + b"\x00\x00\x01" + SPS + b"\x00\x00\x01" + PPS + b"\x00\x00\x01" + IDR,
        b"\x00\x00\x01" + SPS_B + b"\x00\x00\x01" + PPS + b"\x00\x00\x01" + IDR,
        b"\x00\x00\x01" + bytes([0x06, 0x05, 0x10]) + b"\x00\x00\x01" + IDR + bytes(300),
        b"\x00\x00\x01" + P_SLICE + bytes(range(1, 256)) + bytes(range(1, 60)),
        # an SEI of exactly 262 bytes: read as lengths it chains to the end (0x106 = 262) and its "header" 0x05 has the
        # forbidden bit clear; only the NAL header rules (an IDR needs nal_ref_idc) keep it Annex B
        b"\x00\x00\x01\x06\x05" + bytes([0x80]) * 261,
    ):
        assert encode.to_annexb(unit) == (unit, "annexb3")
    assert [encode.plausible_nal_header(h) for h in (0x65, 0x05, 0x41, 0x01, 0x67, 0x09, 0x29, 0x0C, 0x2C, 0x10)] == [
        True, False, True, True, True, True, False, True, False, False,
    ]  # fmt: skip


@pytest.mark.parametrize("size", [256, 300, 301, 261, 0x1E1, 511])
def test_avcc_whose_first_nal_is_256_to_511_bytes_is_not_taken_for_annex_b(size: int) -> None:
    """Review of device round 2: a 4-byte AVCC length of 0x000001xx starts with 00 00 01, so an AVCC access unit whose
    first NAL is 256-511 bytes (a still-screen P slice, a small IDR without an AUD) was classed annexb3 and passed
    through: the IDR went unrecognised (no SPS/PPS, a false encoder_stalled) and aiortc split garbage. 261 and 0x1e1
    give a low length byte that reads as a plausible NAL header (05, e1)."""
    for first in (bytes([0x41, 0x9A]), bytes([0x65, 0x88])):
        nal = first + bytes((i % 250) + 1 for i in range(size - 2))
        unit = avcc(nal) if first[0] == 0x41 else avcc(nal, P_SLICE)
        converted, form = encode.to_annexb(unit)
        assert unit.startswith(b"\x00\x00\x01") and form == "avcc"
        assert encode.split_nals(converted)[0] == nal
    # with an avcC extradata the encoder reads every unit as AVCC, whatever its first bytes


def test_an_avcc_encoder_gets_its_256_byte_idr_recognised_and_given_parameter_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    idr = bytes([0x65, 0x88]) + bytes((i % 250) + 1 for i in range(298))
    p_slice = bytes([0x41, 0x9A]) + bytes((i % 250) + 1 for i in range(298))
    end_of_sequence = bytes([0x0A])  # a 1-byte NAL: its AVCC length is 00 00 00 01, a 4-byte start code's bytes
    ctx = FakeContext(
        extradata=avcc_record(SPS, PPS),
        script=[[avcc(idr)], [avcc(p_slice)], [avcc(end_of_sequence, IDR)]],
    )
    with_context(monkeypatch, ctx)
    enc = encode.H264Encoder(encode.EncoderChoice("h264", "h264_mf"), 64, 36, 30, 500)
    [first] = enc.encode(grey_frame(), force_idr=True)
    [second] = enc.encode(grey_frame(), force_idr=False)
    [third] = enc.encode(grey_frame(), force_idr=True)
    assert bytes(first) == annexb(SPS, PPS, idr) and bytes(second) == annexb(p_slice)
    # an avcC extradata makes the stream AVCC: even a unit that starts like Annex B is read by its lengths
    assert bytes(third) == annexb(SPS, PPS, end_of_sequence, IDR)
    assert enc.idr_count == 2 and enc.prepended == 2 and enc.non_annexb == 3
    assert enc.first_idr is not None and enc.first_idr["form"] == "avcc"
    assert encode.to_annexb(avcc(end_of_sequence, IDR))[1] == "annexb4"  # without that knowledge it looks like Annex B
    assert encode.parameter_sets_of(annexb(SPS, IDR)) == b""
    assert encode.parameter_sets_of(annexb(AUD, SPS, PPS, IDR)) == annexb(SPS, PPS)
    assert encode.mft_name([(32, "h264_mf", "MFT name: 'NVIDIA H.264 Encoder MFT'\n")]) == "NVIDIA H.264 Encoder MFT"
    assert encode.mft_name([(16, "h264_mf", "something else")]) is None


def test_encoder_candidates_follow_the_preference_order() -> None:
    win = encode.encoder_candidates(["vp8", "h264"], dev_h264=False, platform="win32")
    assert [c.label for c in win] == ["vp8 (aiortc libvpx)", "h264_mf hw", "h264_mf"]
    assert [c.label for c in encode.encoder_candidates(["h264", "vp8", "h264"], dev_h264=False, platform="win32")] == [
        "h264_mf hw",
        "h264_mf",
        "vp8 (aiortc libvpx)",
    ]
    assert all("max_b_frames" not in c.options for c in win)
    assert win[1].options["hw_encoding"] == "1" and win[2].options["hw_encoding"] == "0"
    assert encode.context_flags(win[1]) == ("global_header", "low_delay")
    linux = encode.encoder_candidates(["vp8", "h264"], dev_h264=True, platform="linux")
    assert [c.label for c in linux] == ["vp8 (aiortc libvpx)", "libx264 DEV ONLY"]
    assert encode.probe_encoders(linux).kind == "vp8"


def test_the_probe_runs_at_the_given_size_and_names_every_failed_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, int]] = []

    def one_frame(choice: encode.EncoderChoice, width: int, height: int) -> str | None:
        calls.append((choice.label, width, height))
        return "open: Invalid argument (errno 22)" if choice.hardware else None

    monkeypatch.setattr(encode, "_encode_one_frame", one_frame)
    monkeypatch.setattr(av, "codecs_available", set(av.codecs_available) | {"h264_mf"})
    failures: list[encode.ProbeFailure] = []
    candidates = encode.encoder_candidates(["h264", "vp8"], dev_h264=False, platform="win32")
    assert encode.probe_encoders(candidates, 1152, 720, failures).label == "h264_mf"
    assert calls == [("h264_mf hw", 1152, 720), ("h264_mf", 1152, 720)]
    assert failures == [encode.ProbeFailure("h264_mf hw", "open: Invalid argument (errno 22)")]
    monkeypatch.setattr(av, "codecs_available", set(av.codecs_available) - {"h264_mf", "libvpx"})
    failures.clear()
    with pytest.raises(ProtocolError):
        encode.probe_encoders(candidates, 1280, 720, failures)
    assert [f.reason for f in failures] == [
        "h264_mf is not in this build",
        "h264_mf is not in this build",
        "libvpx is not in this build",
    ]


@needs_libx264
def test_a_candidate_that_cannot_open_carries_ffmpegs_reason() -> None:
    failures: list[encode.ProbeFailure] = []
    bad = encode.EncoderChoice("h264", "libx264", options={"preset": "no-such-preset"}, development_only=True)
    assert encode.probe_encoders([bad, encode.EncoderChoice("vp8", "")], 640, 360, failures).kind == "vp8"
    [failure] = failures
    assert failure.candidate == "libx264 DEV ONLY" and failure.reason.startswith("open: ") and "errno" in failure.reason
    rows = encode.list_encoders(dev_h264=False)
    assert rows == [{"encoder": "vp8 (aiortc libvpx)", "kind": "vp8", "available": True, "note": ""}]


async def test_the_worker_start_probes_at_the_output_size_and_logs_each_failed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def probe(
        candidates: list[encode.EncoderChoice], width: int, height: int, failures: list[encode.ProbeFailure]
    ) -> encode.EncoderChoice:
        seen["size"], seen["labels"] = (width, height), [c.label for c in candidates]
        failures.append(encode.ProbeFailure("h264_mf hw", "open: Invalid argument (errno 22)"))
        raise ProtocolError("SCREEN_ENCODER_UNAVAILABLE", "none")

    monkeypatch.setattr(worker_main, "probe_encoders", probe)
    offer = await make_phone_offer(aiortc.RTCPeerConnection(aiortc.RTCConfiguration(iceServers=[])), close=True)
    w = InProcessWorker()
    try:
        source = {"kind": "fake", "pattern": "synthetic", "surface_index": 0, "width": 1920, "height": 1080, "fps": 30}
        with structlog.testing.capture_logs() as logs:
            w.start()
            w.send(start_message(offer, source=source, encoder_preference=["vp8", "h264"]))
            error = await w.expect("error")
        assert error["code"] == "SCREEN_ENCODER_UNAVAILABLE" and await w.finish() == EXIT_START_FAILED
    finally:
        await w.aclose()
    assert seen == {"size": (1280, 720), "labels": ["vp8 (aiortc libvpx)", "libx264 DEV ONLY"]}
    [line] = [e for e in logs if e["event"] == "screen encoder candidate unavailable"]
    assert line["encoder"] == "h264_mf hw" and line["reason"] == "open: Invalid argument (errno 22)"


async def test_a_vp8_first_preference_makes_the_worker_answer_vp8() -> None:
    """Change F's worker half: the agent's ``encoder_preference = ["vp8", "h264"]`` (after an H.264 stall) is probed
    in that order, so the worker answers VP8 although H.264 works here."""
    offer = await make_phone_offer(aiortc.RTCPeerConnection(aiortc.RTCConfiguration(iceServers=[])), close=True)
    w = InProcessWorker()
    try:
        with structlog.testing.capture_logs() as logs:
            w.start()
            w.send(start_message(offer, encoder_preference=["vp8", "h264"]))
            answer = await w.expect("answer", timeout=20)
        assert answer["encoder"] == "vp8" and "VP8/90000" in answer["answer_sdp"]
        assert "H264/90000" not in answer["answer_sdp"]
        [answered] = [e for e in logs if e["event"] == "screen worker answered"]
        assert answered["preference"] == "vp8,h264" and answered["encoder"] == "vp8 (aiortc libvpx)"
        w.send({"op": "stop"})
        assert await w.finish() == 0
    finally:
        await w.aclose()


# ----- C: the track: drain pump, keyframe facts, stalls -----------------------------------------------------------------------
class HoldingContext:
    """A real codec context that gives up each access unit only ``hold`` frames later: a Media Foundation transform
    outside low-latency mode (device round 2: 5.5 to 9.2 s from the first captured frame to the first packet at the
    1 fps floor on a still screen)."""

    def __init__(self, inner: Any, hold: int) -> None:
        self.inner, self.hold = inner, hold
        self.held: collections.deque[Any] = collections.deque()

    def encode(self, frame: Any) -> list[Any]:
        self.held.extend(self.inner.encode(frame))
        out = []
        while len(self.held) > self.hold:
            out.append(self.held.popleft())
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def holding_encoder(hold: int) -> type[encode.H264Encoder]:
    class Holding(encode.H264Encoder):
        def open(self) -> None:
            super().open()
            self._ctx = HoldingContext(self._ctx, hold)

    return Holding


def still_h264_track(events: list[dict[str, Any]], *, fps: int = 30, floor: float = 1.0) -> ScreenTrack:
    allowed = asyncio.Event()
    allowed.set()
    return ScreenTrack(
        source=fake.SyntheticSource(320, 180, fps, still=True),
        choice=dev_h264_choice(),
        width=320,
        height=180,
        fps=fps,
        bitrate_kbps=500,
        capture_allowed=allowed,
        emit=events.append,
        floor_seconds=floor,
    )


async def stop_track(track: ScreenTrack, pull: asyncio.Future[Any] | None = None) -> None:
    """End a track whose ``recv`` may be inside a worker-thread call: stop first, let that call finish, then close."""
    track.stop()
    if pull is not None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(pull, 5)
    await track.aclose()


@needs_libx264
async def test_the_drain_pump_gets_a_held_picture_and_a_pli_idr_out_in_under_half_a_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(track_module, "H264Encoder", holding_encoder(7))
    events: list[dict[str, Any]] = []
    track = still_h264_track(events)
    try:
        with structlog.testing.capture_logs() as logs:
            t0 = time.monotonic()
            first = await asyncio.wait_for(track.recv(), 5)
            first_after = time.monotonic() - t0
        assert first_after < 0.5, first_after  # at the 1 fps floor: about 7 s
        assert 5 in encode.nal_types(bytes(first)) and track.frames_captured == 1 and track.frames_pumped >= 7
        stats = track.stats()
        assert stats["first_kf_ms"] is not None and stats["first_kf_ms"] < 500 and stats["pending"] == 7
        assert (stats["captured"], stats["aus"], stats["idr"]) == (1, 1, 1) and stats["encoded"] == 8
        assert events == [{"ev": "first_frame"}]
        [opened] = [e for e in logs if e["event"] == "screen encoder opened"]
        assert opened["label"] == "libx264 DEV ONLY" and opened["low_delay"] is False and opened["xd_nals"] == "7,8"
        [keyframe] = [e for e in logs if e["event"] == "screen first keyframe"]
        assert keyframe["ms"] == stats["first_kf_ms"] and keyframe["frames_in"] == 8 and keyframe["sps"] == "42c01f"
        assert keyframe["prepended"] is True and keyframe["nals"].startswith("7,8") and "5" in keyframe["nals"]
        # the picture is out: the pump stops and the 1 fps floor rules again
        pumped = track.frames_pumped
        t1 = time.monotonic()
        await asyncio.wait_for(track.recv(), 5)
        assert time.monotonic() - t1 >= 0.8 and track.frames_pumped == pumped and track.frames_refreshed == 1
        # a PLI on a still screen: the IDR is encoded at once (no floor tick) and pumped out
        idr_before = track.idr_sent
        t2 = time.monotonic()
        track.force_keyframe()
        while track.idr_sent == idr_before:
            await asyncio.wait_for(track.recv(), 5)
        assert time.monotonic() - t2 < 0.5
        stats = track.stats()
        assert stats["last_kf_ms"] is not None and stats["last_kf_ms"] < 500
        assert (stats["kf_req"], stats["kf_pli"], stats["kf_dc"], stats["kf_op"]) == (1, 1, 0, 0)
        assert stats["stalled"] is None and track.pump_timeouts == 0
    finally:
        await stop_track(track)


@needs_libx264
async def test_the_pump_is_bounded_and_a_silent_encoder_is_reported_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(track_module, "H264Encoder", holding_encoder(10**6))  # never gives anything up
    monkeypatch.setattr(track_module, "PUMP_MAX_SECONDS", 0.3)
    monkeypatch.setattr(track_module, "KEYFRAME_DEADLINE_SECONDS", 0.6)
    events: list[dict[str, Any]] = []
    track = still_h264_track(events, floor=0.25)
    pull = asyncio.ensure_future(track.recv())
    try:
        with structlog.testing.capture_logs() as logs:
            await asyncio.sleep(1.6)
        assert not pull.done()  # nothing ever came out
        assert track.pump_timeouts == 1 and 3 <= track.frames_pumped <= 0.3 * 30 + 3, track.frames_pumped
        assert track.frames_refreshed >= 3  # after the pump gave up: the floor
        stalls = [e for e in events if e.get("ev") == "encoder_stalled"]
        assert len(stalls) == 1 and stalls[0]["reason"] == "no_keyframe" and stalls[0]["encoder"] == "h264"
        assert stalls[0]["aus"] == 0 and stalls[0]["encoded"] >= 1
        warnings = [e for e in logs if e["event"] == "screen H.264 encoder stalled"]
        assert len(warnings) == 1 and warnings[0]["log_level"] == "warning" and warnings[0]["pending"] >= 1
        track.check_stall()  # the peer's stats loop asks too: still once
        assert len([e for e in events if e.get("ev") == "encoder_stalled"]) == 1
        assert track.stats()["stalled"] == "no_keyframe" and track.stats()["first_kf_ms"] is None
    finally:
        await stop_track(track, pull)


@needs_libx264
async def test_a_pause_before_the_first_keyframe_is_not_a_stall(monkeypatch: pytest.MonkeyPatch) -> None:
    """A UAC prompt or a missing bar right after the start pauses the track: no keyframe is due while nothing is fed,
    and the deadline counts again from the first frame after the resume."""
    monkeypatch.setattr(track_module, "H264Encoder", holding_encoder(10**6))
    monkeypatch.setattr(track_module, "KEYFRAME_DEADLINE_SECONDS", 0.4)
    events: list[dict[str, Any]] = []
    track = still_h264_track(events)
    pull = asyncio.ensure_future(track.recv())
    try:
        await asyncio.sleep(0.15)
        track.pause()
        await asyncio.sleep(0.6)
        track.check_stall()  # what the peer's stats loop does meanwhile
        assert not [e for e in events if e.get("ev") == "encoder_stalled"]
        track.resume()
        track.check_stall()
        assert not [e for e in events if e.get("ev") == "encoder_stalled"]  # the deadline restarted
        await asyncio.sleep(0.8)
        assert [e["reason"] for e in events if e.get("ev") == "encoder_stalled"] == ["no_keyframe"]
    finally:
        await stop_track(track, pull)


class FailingContext(HoldingContext):
    def encode(self, frame: Any) -> list[Any]:
        av.logging.log(av.logging.ERROR, "h264_mf", "ProcessInput() failed: MF_E_NOTACCEPTING\n")
        raise av.error.ExternalError(capture_dda.AVERROR_EXTERNAL, "Generic error in an external library")


@needs_libx264
async def test_an_encoder_that_raises_stops_the_track_and_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before: the exception escaped into aiortc's send loop, which logged a traceback and stopped sending while the
    agent believed the session live."""

    class Failing(encode.H264Encoder):
        def open(self) -> None:
            super().open()
            self._ctx = FailingContext(self._ctx, 0)

    monkeypatch.setattr(track_module, "H264Encoder", Failing)
    events: list[dict[str, Any]] = []
    track = still_h264_track(events)
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(track.recv(), 5)
    assert track.readyState == "ended"
    assert [e for e in events if e["ev"] == "encoder_stalled"] == [
        {"ev": "encoder_stalled", "encoder": "h264", "reason": "encode_error", "encoded": 0, "aus": 0}
    ]
    [line] = [e for e in logs if e["event"] == "H.264 encode failed"]
    assert line["log_level"] == "error" and line["error"] == "ExternalError"
    assert line["reason"].startswith("ProcessInput() failed: MF_E_NOTACCEPTING; Generic error")
    await track.aclose()


async def test_an_encoder_that_cannot_open_stops_the_track_and_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any) -> Any:
        raise encode.EncoderOpenError("no transform for this size; Invalid argument (errno 22)")

    monkeypatch.setattr(track_module, "H264Encoder", refuse)
    events: list[dict[str, Any]] = []
    track = still_h264_track(events)
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(track.recv(), 5)
    assert [e["reason"] for e in events if e["ev"] == "encoder_stalled"] == ["open_error"]
    [line] = [e for e in logs if e["event"] == "H.264 encoder could not open"]
    assert line["error"] == "EncoderOpenError" and "no transform" in line["reason"]
    await track.aclose()


@needs_libx264
async def test_a_bitrate_reopen_that_fails_stops_the_track_and_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bitrate step reopens h264_mf; a reopen that raises must stop the track and say so (encoder_stalled
    open_error), never escape into aiortc's send loop (DECISIONS #101)."""

    class NoReopen(encode.H264Encoder):
        def reopen(self, bitrate_kbps: int) -> None:
            raise encode.EncoderOpenError("the transform refused the new rate; Invalid argument (errno 22)")

    monkeypatch.setattr(track_module, "H264Encoder", NoReopen)
    events: list[dict[str, Any]] = []
    track = still_h264_track(events)
    await asyncio.wait_for(track.recv(), 5)
    track.set_bitrate(800)
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(asyncio.CancelledError):
            for _ in range(50):
                await asyncio.wait_for(track.recv(), 5)
    assert track.readyState == "ended"
    assert [e["reason"] for e in events if e["ev"] == "encoder_stalled"] == ["open_error"]
    [line] = [e for e in logs if e["event"] == "H.264 encoder could not reopen"]
    assert line["log_level"] == "error" and "refused the new rate" in line["reason"]
    await track.aclose()


class StubEncoder:
    """What the track uses of an H.264 encoder, without a codec: ``encode`` may block until ``gate`` is set."""

    gate: Any = None

    def __init__(self, *_args: Any) -> None:
        self.pending, self.prepended, self.non_annexb, self.first_idr = 0, 0, 0, None

    def open_facts(self) -> dict[str, Any]:
        return {"label": "stub"}

    def encode(self, frame: Any, *, force_idr: bool) -> list[Any]:
        self.pending += 1
        if StubEncoder.gate is not None:
            StubEncoder.gate.wait(5)
        return []

    def close(self) -> None:
        return None


async def test_the_stats_loop_reports_an_encode_call_that_never_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The track's own loop cannot see a stall while it waits inside an encode call: the peer's stats loop asks the
    track every round, so the agent still hears encoder_stalled (and offers VP8 next time)."""
    import threading

    gate = threading.Event()
    monkeypatch.setattr(StubEncoder, "gate", gate)
    monkeypatch.setattr(track_module, "H264Encoder", StubEncoder)
    monkeypatch.setattr(track_module, "KEYFRAME_DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(rtc, "STATS_INTERVAL_SECONDS", 0.1)
    events: list[dict[str, Any]] = []
    track = still_h264_track(events)
    pull = asyncio.ensure_future(track.recv())
    peer = fake_peer()

    class Sender:
        async def getStats(self) -> dict[str, Any]:  # noqa: N802 - aiortc's name
            return {"out": SimpleNamespace(type="outbound-rtp", packetsSent=0, bytesSent=0)}

    peer.track, peer.sender, peer._emit = track, Sender(), events.append  # type: ignore[assignment]  # noqa: SLF001
    loop_task = asyncio.ensure_future(peer._stats_loop())  # noqa: SLF001
    try:
        for _ in range(40):
            await asyncio.sleep(0.05)
            if any(e.get("ev") == "encoder_stalled" for e in events):
                break
        assert not pull.done() and track._inflight is not None  # noqa: SLF001  # still inside the encode call
        stalls = [e for e in events if e.get("ev") == "encoder_stalled"]
        assert len(stalls) == 1 and stalls[0]["reason"] == "no_keyframe" and stalls[0]["aus"] == 0
        stats = [e for e in events if e.get("ev") == "stats"]
        assert stats and stats[-1]["stalled"] == "no_keyframe"
    finally:
        peer._closed = True  # noqa: SLF001
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task
        gate.set()
        await stop_track(track, pull)


def test_counters_of_an_encoder_retired_by_a_rebuild_are_kept() -> None:
    track = still_h264_track([])
    old = StubEncoder()
    old.prepended, old.non_annexb = 2, 1
    track._encoder = old  # type: ignore[assignment]  # noqa: SLF001
    track.replace_source(fake.SyntheticSource(160, 90, 30, still=True), 160, 90)
    track._swap_source()  # noqa: SLF001
    assert track._encoder is None and track.rebuilds == 1  # noqa: SLF001
    track._encoder = StubEncoder()  # type: ignore[assignment]  # noqa: SLF001
    track._encoder.prepended = 1  # type: ignore[union-attr]  # noqa: SLF001
    stats = track.stats()
    assert (stats["prepended"], stats["non_annexb"]) == (3, 1)


def test_vp8_tracks_never_pump_or_stall() -> None:
    track = ScreenTrack(
        source=fake.SyntheticSource(64, 36, 30, still=True),
        choice=encode.EncoderChoice("vp8", ""),
        width=64,
        height=36,
        fps=30,
        bitrate_kbps=500,
        capture_allowed=asyncio.Event(),
        emit=lambda e: None,
    )
    track._awaiting_pts, track._first_submit_at = 0, time.monotonic() - 60  # noqa: SLF001
    assert track._pumping(time.monotonic()) is False  # noqa: SLF001
    track.check_stall()
    assert track.stalled is None and track.stats()["encoded"] == 0


# ----- D: the peer ----------------------------------------------------------------------------------------------------------------
def test_rtp_payloads_are_at_most_1200_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """aiortc packetizes up to its module global PACKET_MAX (1300); with headers, SRTP and an IPv6 tunnel, a VPN or a
    cellular path that exceeds the MTU. The peer lowers both packetizers' limit before the first send."""
    monkeypatch.setattr(aiortc_h264, "PACKET_MAX", aiortc_h264.PACKET_MAX)  # restored after the test
    monkeypatch.setattr(aiortc_vpx, "PACKET_MAX", aiortc_vpx.PACKET_MAX)
    rtc.limit_rtp_payloads()
    assert aiortc_h264.PACKET_MAX == aiortc_vpx.PACKET_MAX == rtc.RTP_PAYLOAD_MAX == 1200
    body = bytes((i % 250) + 1 for i in range(30_000))  # no zero byte: no start code inside
    packet = av.Packet(annexb(SPS, PPS, bytes([0x65]) + body))
    packet.pts, packet.time_base = 0, encode.VIDEO_TIME_BASE
    payloads, _timestamp = aiortc_h264.H264Encoder().pack(packet)
    assert len(payloads) >= 25 and max(len(p) for p in payloads) <= 1200
    noise = av.VideoFrame(640, 360, "yuv420p")
    for index, plane in enumerate(noise.planes):
        plane.update(bytes((i * 7919 + index * 31) % 251 for i in range(plane.buffer_size)))
    noise.pts, noise.time_base = 0, encode.VIDEO_TIME_BASE
    vp8_payloads, _timestamp = aiortc_vpx.Vp8Encoder().encode(noise, force_keyframe=True)
    assert len(vp8_payloads) > 1 and max(len(p) for p in vp8_payloads) <= 1200


def test_rtp_stats_reduce_the_senders_report() -> None:
    report = [
        SimpleNamespace(type="outbound-rtp", packetsSent=52, bytesSent=45_000),
        SimpleNamespace(type="transport", bytesSent=99),
        SimpleNamespace(type="remote-inbound-rtp", packetsLost=3, jitter=900, roundTripTime=0.0094, fractionLost=64),
    ]
    assert rtc.rtp_stats(report) == {
        "rtp_packets": 52,
        "rtp_bytes": 45_000,
        "rr": True,
        "lost": 3,
        "jitter_ms": 10.0,
        "rtt_ms": 9,
        "loss": 0.25,
    }
    no_report_yet = rtc.rtp_stats([SimpleNamespace(type="outbound-rtp", packetsSent=5, bytesSent=500)])
    assert no_report_yet["rr"] is False and no_report_yet["rtt_ms"] is None and no_report_yet["loss"] is None
    first_rr = rtc.rtp_stats([SimpleNamespace(type="remote-inbound-rtp", packetsLost=0, jitter=0, roundTripTime=None)])
    assert first_rr["rr"] is True and first_rr["rtt_ms"] is None and first_rr["loss"] == 0.0


def fake_peer(*, host: str = "192.168.1.20", kind: str = "h264") -> rtc.ScreenPeer:
    track = SimpleNamespace(
        choice=encode.EncoderChoice(kind, "libx264" if kind == "h264" else ""), bitrate_kbps=1500, stats=lambda: {}
    )
    peer = rtc.ScreenPeer(
        offer_sdp="",
        stun_urls=[],
        track=track,  # type: ignore[arg-type]
        capture_allowed=asyncio.Event(),
        emit=lambda e: None,
        bitrate_kbps={},
    )
    local = SimpleNamespace(type="srflx", transport="udp", host=host, port=50000)
    remote = SimpleNamespace(type="prflx", transport="udp", host="203.0.113.9", port=60000)
    connection = SimpleNamespace(_nominated={1: SimpleNamespace(local_candidate=local, remote_candidate=remote)})
    peer.sender = SimpleNamespace(transport=SimpleNamespace(transport=SimpleNamespace(_connection=connection)))  # type: ignore[assignment]
    codec = SimpleNamespace(
        mimeType="video/H264", payloadType=98, parameters={"profile-level-id": "42e01f", "packetization-mode": 1}
    )
    peer.transceiver = SimpleNamespace(_codecs=[codec])
    peer.path = "direct"
    return peer


def test_the_transport_line_names_types_family_and_codec_never_an_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aiortc_h264, "PACKET_MAX", 1200)
    facts = fake_peer().transport_facts()
    assert facts == {
        "path": "direct",
        "local_type": "srflx",
        "remote_type": "prflx",
        "protocol": "udp",
        "ip": "v4",
        "codec": "H264",
        "pt": 98,
        "profile_level_id": "42e01f",
        "packetization_mode": "1",
        "packet_max": 1200,
    }
    assert fake_peer(host="2001:db8::5").transport_facts()["ip"] == "v6"
    text = json.dumps(facts)
    assert "192.168" not in text and "203.0.113" not in text and "50000" not in text
    bare = fake_peer()
    bare.sender, bare.transceiver = None, None
    assert bare.transport_facts() == {
        "path": "direct",
        "local_type": None,
        "remote_type": None,
        "protocol": None,
        "ip": None,
        "packet_max": 1200,
    }


async def test_nacked_packets_are_counted_on_the_sender_instance() -> None:
    """aiortc's RTCP dispatch awaits ``self._retransmit(seq)`` for each NACKed packet (pinned here)."""
    assert inspect.iscoroutinefunction(RTCRtpSender._retransmit)  # noqa: SLF001
    assert "await self._retransmit(seq)" in inspect.getsource(RTCRtpSender._handle_rtcp_packet)  # noqa: SLF001
    calls: list[int] = []

    class Sender:
        async def _retransmit(self, sequence_number: int) -> None:
            calls.append(sequence_number)

    peer, sender = fake_peer(), Sender()
    peer._count_nacks(sender)  # type: ignore[arg-type]  # noqa: SLF001
    await sender._retransmit(7)  # noqa: SLF001
    await sender._retransmit(8)  # noqa: SLF001
    assert peer.nack_count == 2 and calls == [7, 8]
    peer._count_nacks(SimpleNamespace())  # type: ignore[arg-type]  # noqa: SLF001  # no hook point: left alone


async def test_the_peer_counts_nacks_on_its_real_sender_from_the_answer_on() -> None:
    """The wiring, not only the wrapper: after answer() the sender aiortc dispatches NACKs to is the counting one."""
    from aiortc import RTCPeerConnection, RTCSessionDescription

    from .test_screen_worker import make_phone_offer

    phone = RTCPeerConnection()
    offer = await make_phone_offer(phone)
    allowed = asyncio.Event()
    track = ScreenTrack(
        source=fake.SyntheticSource(64, 36, 30, still=True),
        choice=encode.EncoderChoice("vp8", ""),
        width=64,
        height=36,
        fps=30,
        bitrate_kbps=500,
        capture_allowed=allowed,
        emit=lambda e: None,
    )
    peer = rtc.ScreenPeer(
        offer_sdp=offer, stun_urls=[], track=track, capture_allowed=allowed, emit=lambda e: None, bitrate_kbps={}
    )
    try:
        answer = await peer.answer()
        await phone.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))
        for _ in range(200):  # connected, so closing does not cut aiortc's connect task short
            if peer.pc is not None and peer.pc.connectionState == "connected":
                break
            await asyncio.sleep(0.05)
        assert peer.sender is not None and peer.nack_count == 0
        await peer.sender._retransmit(7)  # noqa: SLF001  # what _handle_rtcp_packet awaits per NACKed packet
        await peer.sender._retransmit(8)  # noqa: SLF001
        assert peer.nack_count == 2
    finally:
        await peer.close()
        await phone.close()


async def test_the_screen_media_line_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `screen media` line per MEDIA_LOG_SECONDS, never per frame or per stats round."""

    class Sender:
        async def getStats(self) -> dict[str, Any]:  # noqa: N802 - aiortc's name
            return {"out": SimpleNamespace(type="outbound-rtp", packetsSent=1, bytesSent=100)}

    monkeypatch.setattr(rtc, "MEDIA_LOG_SECONDS", 0.2)
    peer = fake_peer()
    peer.sender = Sender()  # type: ignore[assignment]
    with structlog.testing.capture_logs() as logs:
        task = asyncio.ensure_future(peer._media_log_loop())  # noqa: SLF001
        await asyncio.sleep(1.05)
        peer._closed = True  # noqa: SLF001
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    lines = [e for e in logs if e["event"] == "screen media"]
    assert 3 <= len(lines) <= 1.05 / 0.2 + 1, len(lines)
    assert lines[0]["rtp_packets"] == 1


async def test_the_media_snapshot_merges_rtp_counts_the_peers_counters_and_the_tracks() -> None:
    class Report(dict[str, Any]):
        pass

    class Sender:
        async def getStats(self) -> Report:  # noqa: N802 - aiortc's name
            report = Report()
            report["out"] = SimpleNamespace(type="outbound-rtp", packetsSent=10, bytesSent=9_000)
            return report

    peer = fake_peer()
    peer.track.stats = lambda: {"captured": 2, "aus": 3}  # type: ignore[method-assign]
    peer.sender = Sender()  # type: ignore[assignment]
    peer.pli_count, peer.nack_count = 4, 1
    snapshot = await peer.media_snapshot()
    assert snapshot is not None
    assert (snapshot["rtp_packets"], snapshot["rtp_bytes"], snapshot["rr"]) == (10, 9_000, False)
    assert (snapshot["pli"], snapshot["nack"], snapshot["captured"], snapshot["aus"]) == (4, 1, 2, 3)
    assert snapshot["encoder_label"] == "libx264" and snapshot["bitrate_kbps"] == 1500
    fields = rtc.media_log_fields(snapshot)
    assert fields["rtp_kb"] == 8 and "rtp_bytes" not in fields and "stalled" not in fields


def test_every_new_log_line_fits_the_supervisors_cap_and_no_key_is_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The worker's stderr lines are relogged by the agent cut at ``STDERR_LINE_MAX`` (400) characters, after its
    free-text redaction (runs of 40+ token characters, addresses); the worker's own redaction blanks any key that
    contains ``sig``, ``payload``, ``sdp``, ``candidate``, ``token``... Every new line, rendered by the worker's real
    ConsoleRenderer with large values, must arrive whole and unredacted."""
    big = {
        "captured": 9_999_999,
        "refreshed": 9_999_999,
        "pumped": 9_999_999,
        "encoded": 9_999_999,
        "aus": 9_999_999,
        "idr": 999_999,
        "au_bytes": 10**12,
        "pending": 99_999,
        "rtp_packets": 99_999_999,
        "rtp_bytes": 10**12,
        "rr": True,
        "lost": 9_999_999,
        "rtt_ms": 99_999,
        "pli": 999_999,
        "kf_req": 999_999,
        "nack": 9_999_999,
        "first_kf_ms": 9_999_999,
        "stalled": "encode_error",
    }
    ctx = FakeContext(
        extradata=avcc_record(SPS, PPS),
        mft="M" * 30 + " Quick Sync Video H.264 Encoder MFT " + "x" * 40,
        consumes=frozenset(),
        script=[[annexb(AUD, SPS, PPS, *([bytes([0x06, 0x05])] * 12), IDR)]],
    )
    with_context(monkeypatch, ctx)
    hardware = encode.encoder_candidates(["h264"], dev_h264=False, platform="win32")[0]
    enc = encode.H264Encoder(hardware, 1280, 720, 30, 1500)
    enc.open_ms, enc.extradata_len = 99_999, 9_999
    enc.encode(grey_frame(), force_idr=True)
    worker_main.configure_worker_logging("INFO")
    try:
        log = structlog.get_logger("dome_agent.screen.worker.rtc")
        log.info("screen media", **rtc.media_log_fields(big))
        log.info("screen media final", **rtc.media_log_fields(big))
        log.info("screen transport", **fake_peer(host="2001:db8::5").transport_facts())
        log.info("screen encoder opened", reopen=True, **enc.open_facts())
        log.info("screen first keyframe", ms=9_999_999, pumped=9_999_999, **(enc.first_idr or {}))
        log.info(
            "screen encoder candidate unavailable",
            encoder="h264_mf hw",
            reason="open: " + capture_dda.sanitise_reason("word " * 100),
        )
        log.warning("screen H.264 encoder stalled", reason="encode_error", encoded=9_999_999, aus=9_999_999)
        log.info(
            "screen worker answered",
            encoder="h264_mf hw",
            size="1280x720",
            fps=30,
            pli_hook=True,
            preference="vp8,h264",
        )
        err = capsys.readouterr().err
    finally:
        configure_logging("INFO", None)
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 8, lines
    assert "mft=" in lines[3] and "unused=" in lines[3] and "nals=" in lines[4]
    for line in lines:
        assert len(line) < STDERR_LINE_MAX, (len(line), line)
        assert "[redacted]" not in line, line
        assert redact_text_line(line) == line, line  # no run of 40 token characters, no address
