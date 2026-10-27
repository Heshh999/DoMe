"""Video encoding for the worker (design §5.3) and the Annex B helpers its tests assert on.

* Output size fits **H.264 level 3.1** (:func:`fit_level_31`: long edge ≤ 1280, short edge ≤ 720, even).
* Encoder order (:func:`encoder_candidates`, in the order of the start's ``encoder_preference``): on Windows
  ``h264_mf`` with ``hw_encoding=1`` (Media Foundation reaches NVIDIA, Intel and AMD hardware: no vendor SDK encoders),
  then ``h264_mf`` in software, then VP8 through aiortc's own libvpx encoder ("Basic picture mode"); a start whose
  preference names VP8 first (the agent's fallback after an H.264 stall) gets VP8 first. Off Windows there is no
  ``h264_mf``: with ``dev_h264`` the worker may use ``libx264`` from the stock PyPI PyAV wheel, which is **development
  and test only and never ships** (the licence gate refuses it; ``KNOWN_ISSUES.md``). :func:`probe_encoders` opens
  each candidate at the session's real output size and encodes one black frame; a candidate that fails is recorded
  with FFmpeg's reason (the worker logs one line per failed candidate).
* ``h264_mf`` is opened with ``AV_CODEC_FLAG_LOW_DELAY`` (``mfenc.c`` then asks the transform for
  ``CODECAPI_AVLowLatencyMode``: one input sample, one output sample) and ``max_b_frames = 0`` on the context (FFmpeg
  has no ``max_b_frames`` private option; the old option string was never consumed). An option the codec did not
  consume is recorded, with the open time, the transform's friendly name (``mf_utils.c`` logs ``MFT name: '...'`` at
  INFO, captured around the open), the extradata length and NAL types and the SPS profile/constraint/level bytes
  (:meth:`H264Encoder.open_facts`, logged once per open by the track as ``screen encoder opened``).
* Packets go through aiortc's pre-encoded path, so the worker must itself make sure **every IDR access unit begins
  with SPS and PPS**: :meth:`H264Encoder.encode` prepends them when an IDR arrives without them, preferring the last
  SPS and PPS the encoder put in band over the context's ``extradata`` (converted from avcC to Annex B when needed).
  The in-band sets are the real guard: ``mfenc.c`` (n8.1.3) writes ``extradata`` only at open (including its 70 ms
  wait for an asynchronous transform) and again when the transform reports ``MF_E_TRANSFORM_STREAM_CHANGE``, so the
  re-read after each encode while it is empty can only pick up the sets of a stream change. An access unit is AVCC
  (4-byte lengths, converted) when the encoder's extradata is avcC, or when the lengths chain exactly to the end
  through valid NAL headers (:func:`to_annexb`: a first NAL of 256 to 511 bytes starts with ``00 00 01`` and looked
  like Annex B); neither form is passed through; both are counted (``non_annexb``, one warning per encoder).
  ``h264_mf`` cannot change its bitrate mid-stream, so a bitrate step reopens the context (:meth:`H264Encoder.reopen`)
  and forces an IDR.
* Counters (:class:`H264Encoder`): frames in, access units out, IDRs, bytes, prepended parameter sets, frames in
  before the first packet, and the first access unit's and the first IDR's NAL types, size, form and SPS bytes.
"""

from __future__ import annotations

import fractions
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from dome_protocol import ProtocolError

log = structlog.get_logger(__name__)

NAL_SPS = 7
NAL_PPS = 8
NAL_IDR = 5
NAL_SEI = 6
NAL_AUD = 9
START_CODE = b"\x00\x00\x00\x01"
VIDEO_TIME_BASE = fractions.Fraction(1, 90000)
LEVEL_31_LONG = 1280
LEVEL_31_SHORT = 720
DEV_H264_CODECS = ("libx264",)  # development only: the stock PyAV wheel's GPL encoder, never in a shipping build
FACT_NALS_MAX = 16  # NAL types kept per access unit in the logged facts
_MFT_NAME = re.compile(r"MFT name: '(.*)'")


def fit_level_31(width: int, height: int) -> tuple[int, int]:
    """Scale down (never up) so the long edge is ≤ 1280 and the short edge ≤ 720, keeping the aspect ratio; both
    dimensions even (16:9 → 1280×720, 16:10 → 1152×720, 4:3 → 960×720, 21:9 → 1280×548)."""
    width, height = max(2, int(width)), max(2, int(height))
    long_edge, short_edge = max(width, height), min(width, height)
    scale = min(1.0, LEVEL_31_LONG / long_edge, LEVEL_31_SHORT / short_edge)
    w = max(2, int(width * scale) // 2 * 2)
    h = max(2, int(height * scale) // 2 * 2)
    return w, h


def nal_types(annexb: bytes) -> list[int]:
    """The NAL unit types in an Annex B byte string, in order (start codes 00 00 01 or 00 00 00 01)."""
    out: list[int] = []
    i = 0
    while True:
        i = annexb.find(b"\x00\x00\x01", i)
        if i == -1:
            return out
        i += 3
        if i < len(annexb):
            out.append(annexb[i] & 0x1F)


def split_nals(annexb: bytes) -> list[bytes]:
    """The NAL units of an Annex B byte string without their start codes (the zero byte of a 4-byte start code is
    not part of the NAL before it), like aiortc's ``_split_bitstream``."""
    out: list[bytes] = []
    i = annexb.find(b"\x00\x00\x01")
    while i != -1:
        start = i + 3
        nxt = annexb.find(b"\x00\x00\x01", start)
        end = len(annexb) if nxt == -1 else (nxt - 1 if annexb[nxt - 1] == 0 else nxt)
        if end > start:
            out.append(annexb[start:end])
        i = nxt
    return out


def plausible_nal_header(header: int) -> bool:
    """A NAL header an H.264 encoder emits in an access unit: forbidden bit clear, a slice (1, 5), SEI, a parameter
    set, an AUD or an end/filler unit (6 to 12), with ``nal_ref_idc`` as the standard requires (non-zero for IDR,
    SPS and PPS; zero for 6, 9 to 12)."""
    if header & 0x80:
        return False
    kind, ref = header & 0x1F, (header >> 5) & 0x03
    if kind == 1:
        return True
    if kind in (NAL_IDR, NAL_SPS, NAL_PPS):
        return ref != 0
    if kind in (NAL_SEI, NAL_AUD, 10, 11, 12):
        return ref == 0
    return False


def avcc_to_annexb(data: bytes, length_size: int = 4, *, strict: bool = False) -> bytes | None:
    """An AVCC access unit (each NAL preceded by its big-endian length) as Annex B, or None when the lengths do not
    chain exactly to the end or a NAL has its forbidden bit set (then it is not AVCC); ``strict`` also requires every
    NAL header to be :func:`plausible_nal_header`."""
    out = bytearray()
    pos = 0
    while pos < len(data):
        if pos + length_size > len(data):
            return None
        size = int.from_bytes(data[pos : pos + length_size], "big")
        pos += length_size
        if size == 0 or pos + size > len(data) or data[pos] & 0x80:
            return None
        if strict and not plausible_nal_header(data[pos]):
            return None
        out += START_CODE + data[pos : pos + size]
        pos += size
    return bytes(out) if out else None


def to_annexb(data: bytes, *, avcc: bool = False) -> tuple[bytes, str]:
    """The access unit as Annex B and the form it arrived in: ``annexb4`` / ``annexb3`` (a leading 4- or 3-byte start
    code: used as is), ``avcc`` (converted) or ``unknown`` (neither: passed through unchanged).

    ``avcc`` (the encoder's extradata is an avcC record) reads every unit as AVCC first. Without it a unit that starts
    with ``00 00 01`` is still tried as AVCC: a 4-byte length of 256 to 511 (a still-screen P slice, a small IDR
    without an AUD) begins with exactly those bytes. It is AVCC when the lengths chain exactly to the end through
    plausible NAL headers; real Annex B practically never does (its byte after the NAL header, read as one, is a
    slice header's first byte with the top bit set, an AUD's ``0xf0``, a profile or a reserved type)."""
    if avcc or data.startswith(b"\x00\x00\x01"):
        converted = avcc_to_annexb(data, strict=True)
        if converted is not None:
            return converted, "avcc"
    if data.startswith(START_CODE):
        return data, "annexb4"
    if data.startswith(b"\x00\x00\x01"):
        return data, "annexb3"
    converted = avcc_to_annexb(data)
    if converted is not None:
        return converted, "avcc"
    return data, "unknown"


def sps_triple(annexb: bytes) -> str | None:
    """``profile_idc``, the constraint flags and ``level_idc`` of the first SPS, as 6 hex characters (``42c01f`` is
    Constrained Baseline 3.1); None without an SPS. Only these three bytes are ever logged, never parameter sets."""
    for nal in split_nals(annexb):
        if nal and nal[0] & 0x1F == NAL_SPS and len(nal) >= 4:
            return nal[1:4].hex()
    return None


def parameter_sets_of(annexb: bytes) -> bytes:
    """The SPS and PPS NAL units of an access unit, with 4-byte start codes, when it carries both; else empty."""
    nals = split_nals(annexb)
    sps = [n for n in nals if n and n[0] & 0x1F == NAL_SPS]
    pps = [n for n in nals if n and n[0] & 0x1F == NAL_PPS]
    if not sps or not pps:
        return b""
    return b"".join(START_CODE + n for n in (*sps, *pps))


def extradata_to_annexb(extradata: bytes) -> bytes:
    """SPS/PPS from ``codec_context.extradata``: already Annex B (starts with a start code) or avcC
    (``AVCDecoderConfigurationRecord``, first byte 0x01), converted to Annex B. Empty when neither."""
    if not extradata:
        return b""
    if extradata.startswith(b"\x00\x00\x01") or extradata.startswith(b"\x00\x00\x00\x01"):
        return bytes(extradata)
    if extradata[0] != 1 or len(extradata) < 7:
        return b""
    out = bytearray()
    pos = 5
    count = extradata[pos] & 0x1F
    pos += 1
    for _ in range(count):
        if pos + 2 > len(extradata):
            return b""
        size = int.from_bytes(extradata[pos : pos + 2], "big")
        pos += 2
        out += START_CODE + extradata[pos : pos + size]
        pos += size
    if pos >= len(extradata):
        return bytes(out)
    count = extradata[pos]
    pos += 1
    for _ in range(count):
        if pos + 2 > len(extradata):
            return b""
        size = int.from_bytes(extradata[pos : pos + 2], "big")
        pos += 2
        out += START_CODE + extradata[pos : pos + size]
        pos += size
    return bytes(out)


def ensure_parameter_sets(access_unit: bytes, parameter_sets: bytes) -> bytes:
    """An IDR access unit that lacks SPS and PPS gets them prepended (design §5.3 worker requirement)."""
    types = nal_types(access_unit)
    if NAL_IDR in types and not (NAL_SPS in types and NAL_PPS in types) and parameter_sets:
        return parameter_sets + access_unit
    return access_unit


def mft_name(logged: list[tuple[int, str, str]]) -> str | None:
    """The Media Foundation transform ``h264_mf`` picked, from its INFO line (``mf_utils.c``: ``MFT name: '...'``),
    sanitised; None when the line was not captured (another codec, or a log level someone else set)."""
    from .capture_dda import sanitise_reason

    for _level, _name, message in logged:
        found = _MFT_NAME.search(str(message))
        if found:
            return sanitise_reason(found.group(1), 64) or None
    return None


@dataclass(frozen=True, slots=True)
class EncoderChoice:
    kind: str  # h264 | vp8
    codec_name: str  # the PyAV codec, "" for vp8 (aiortc encodes)
    hardware: bool = False
    options: dict[str, str] = field(default_factory=dict)
    development_only: bool = False

    @property
    def label(self) -> str:
        if self.kind == "vp8":
            return "vp8 (aiortc libvpx)"
        return f"{self.codec_name}{' hw' if self.hardware else ''}{' DEV ONLY' if self.development_only else ''}"


@dataclass(frozen=True, slots=True)
class ProbeFailure:
    """A candidate :func:`probe_encoders` could not use, and why (FFmpeg's words, sanitised)."""

    candidate: str  # EncoderChoice.label
    reason: str


class EncoderOpenError(RuntimeError):
    """The codec context did not open; the message is FFmpeg's reason, sanitised (``capture_dda.ffmpeg_error_text``)."""


def _h264_candidates(*, dev_h264: bool, platform: str) -> list[EncoderChoice]:
    if platform == "win32":
        # no "max_b_frames" here: FFmpeg's option is "bf", the string was never consumed; the context's
        # max_b_frames is set to 0 in H264Encoder.open (mfenc always passes it to the transform)
        mf = {"scenario": "display_remoting", "rate_control": "cbr"}
        # the hardware candidate stays on yuv420p this round (KNOWN_ISSUES: GPU transforms take NV12)
        return [
            EncoderChoice("h264", "h264_mf", hardware=True, options={**mf, "hw_encoding": "1"}),
            EncoderChoice("h264", "h264_mf", hardware=False, options={**mf, "hw_encoding": "0"}),
        ]
    if dev_h264:
        return [
            EncoderChoice(
                "h264",
                name,
                options={
                    "preset": "ultrafast",
                    "tune": "zerolatency",
                    "profile": "baseline",
                    "level": "3.1",
                    "forced-idr": "1",
                    "x264-params": "repeat-headers=0:bframes=0:keyint=600:annexb=1",
                },
                development_only=True,
            )
            for name in DEV_H264_CODECS
        ]
    return []


def encoder_candidates(preference: list[str], *, dev_h264: bool, platform: str = sys.platform) -> list[EncoderChoice]:
    """The ordered candidates for this platform. ``preference`` is what the phone offered (h264 and/or vp8) in the
    order the agent wants them tried: ``["vp8", "h264"]`` puts VP8 first (the fallback after an H.264 stall)."""
    out: list[EncoderChoice] = []
    for kind in dict.fromkeys(preference):  # ordered, without repeats
        if kind == "h264":
            out.extend(_h264_candidates(dev_h264=dev_h264, platform=platform))
        elif kind == "vp8":
            out.append(EncoderChoice("vp8", ""))
    return out


def context_flags(choice: EncoderChoice) -> tuple[str, ...]:
    """The ``av.codec.context.Flags`` :meth:`H264Encoder.open` sets for a candidate: ``global_header`` always (the
    parameter sets land in extradata; the worker prepends them itself) and ``low_delay`` for ``h264_mf`` (``mfenc.c``
    then sends ``CODECAPI_AVLowLatencyMode``)."""
    if choice.codec_name == "h264_mf":
        return ("global_header", "low_delay")
    return ("global_header",)


class H264Encoder:
    """A PyAV codec context producing Annex B access units with SPS/PPS before every IDR, and the counters and open
    facts the worker logs (never a parameter set or a frame: NAL types, sizes and the SPS's three level bytes)."""

    def __init__(self, choice: EncoderChoice, width: int, height: int, fps: int, bitrate_kbps: int) -> None:
        self.choice = choice
        self.width, self.height = width, height
        self.fps = max(1, int(fps))
        self.bitrate_kbps = int(bitrate_kbps)
        self._ctx: Any = None
        self._extradata_sets = b""  # SPS/PPS from the context's extradata (Annex B)
        self._avcc = False  # the extradata is an avcC record: the access units are AVCC
        self._inband_sets = b""  # the last SPS and PPS the encoder put in band (preferred when prepending)
        self._warned_form = False
        # facts of the last open (H264Encoder.open_facts)
        self.open_ms = 0
        self.mft: str | None = None
        self.unused_options: list[str] = []
        self.flags: tuple[str, ...] = ()
        self.extradata_len = 0
        # counters since construction (a reopen keeps them)
        self.frames_in = 0
        self.packets_out = 0
        self.idr_count = 0
        self.bytes_out = 0
        self.prepended = 0
        self.non_annexb = 0
        self.encode_ms_total = 0.0
        self.frames_in_before_first_packet: int | None = None
        self.first_au: dict[str, Any] | None = None
        self.first_idr: dict[str, Any] | None = None
        self.open()

    def open(self) -> None:
        import av
        from av.codec.context import Flags

        from .capture_dda import AV_LOG_INFO, ffmpeg_error_text, ffmpeg_log_lines

        ctx: Any = av.CodecContext.create(self.choice.codec_name, "w")  # a VideoCodecContext at runtime
        ctx.width, ctx.height = self.width, self.height
        ctx.pix_fmt = "yuv420p"
        ctx.time_base = VIDEO_TIME_BASE
        ctx.framerate = fractions.Fraction(self.fps, 1)
        ctx.bit_rate = self.bitrate_kbps * 1000
        ctx.max_b_frames = 0  # mfenc always passes this to the transform (AVEncMPVDefaultBPictureCount)
        flags = context_flags(self.choice)
        for name in flags:
            ctx.flags |= getattr(Flags, name)
        ctx.options = dict(self.choice.options)
        started = time.monotonic()
        with ffmpeg_log_lines(AV_LOG_INFO) as logged:
            try:
                ctx.open()
            except Exception as exc:  # noqa: BLE001 - no transform, no Media Feature Pack, an option it refuses
                raise EncoderOpenError(ffmpeg_error_text(exc, logged)) from None
        self.open_ms = int((time.monotonic() - started) * 1000)
        self.mft = mft_name(logged)
        self.unused_options = sorted(str(k) for k in dict(ctx.options or {}))  # PyAV keeps what was not consumed
        self.flags = flags
        self._ctx = ctx
        self._read_extradata()
        self._inband_sets = b""  # a reopen may bring new parameter sets

    def _read_extradata(self) -> None:
        extradata = bytes(self._ctx.extradata or b"")
        self.extradata_len = len(extradata)
        self._extradata_sets = extradata_to_annexb(extradata)
        self._avcc = extradata[:1] == b"\x01"

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close(strict=False)
            except Exception:  # noqa: BLE001, S110 - closing a hardware context may raise once the device is gone
                pass
            self._ctx = None

    @property
    def parameter_sets(self) -> bytes:
        """What an IDR without SPS/PPS gets: the in-band ones once seen, else the extradata's."""
        return self._inband_sets or self._extradata_sets

    @property
    def pending(self) -> int:
        """Frames given to the encoder that have not come out yet (one access unit per frame for H.264)."""
        return max(0, self.frames_in - self.packets_out)

    def open_facts(self) -> dict[str, Any]:
        """The ``screen encoder opened`` line's fields (no parameter set: its NAL types and the SPS's three bytes)."""
        return {
            "label": self.choice.label,
            "mft": self.mft,
            "pix_fmt": "yuv420p",
            "size": f"{self.width}x{self.height}",
            "fps": self.fps,
            "kbps": self.bitrate_kbps,
            "low_delay": "low_delay" in self.flags,
            "open_ms": self.open_ms,
            "xd_len": self.extradata_len,
            "xd_nals": ",".join(str(t) for t in nal_types(self._extradata_sets)) or None,
            "sps": sps_triple(self._extradata_sets),
            "unused": ",".join(self.unused_options) or None,
        }

    def reopen(self, bitrate_kbps: int) -> None:
        """A bitrate step: ``h264_mf`` cannot change mid-stream, so the context is reopened (≈ 100 ms) and the next
        frame is an IDR (the caller forces it)."""
        self.close()
        self.bitrate_kbps = int(bitrate_kbps)
        self.open()

    def encode(self, frame: Any, *, force_idr: bool) -> list[Any]:
        """Encode one ``av.VideoFrame`` (yuv420p, pts in the 90 kHz time base) into ``av.Packet``s whose IDR access
        units begin with SPS and PPS. Frames from ``lavfi`` arrive flagged I: the flag is reset unless an IDR is
        wanted, or every frame would be a keyframe."""
        from av.video.frame import PictureType

        frame.pict_type = PictureType.I if force_idr else PictureType.NONE
        started = time.monotonic()
        packets = self._ctx.encode(frame)
        self.encode_ms_total += (time.monotonic() - started) * 1000
        self.frames_in += 1
        if not self._extradata_sets:
            # mfenc refreshes extradata only when the transform reports a stream change (MF_E_TRANSFORM_STREAM_CHANGE);
            # the in-band parameter sets (_inband_sets) are what protects an IDR that lacks them
            self._read_extradata()
        return [self._access_unit(packet) for packet in packets]

    def _access_unit(self, packet: Any) -> Any:
        import av

        data, form = to_annexb(bytes(packet), avcc=self._avcc)
        if form not in ("annexb4", "annexb3"):
            self.non_annexb += 1
            if not self._warned_form:
                self._warned_form = True
                log.warning(
                    "H.264 encoder output is not Annex B",
                    form=form,
                    action="converted" if form == "avcc" else "passed through",
                    label=self.choice.label,
                )
        types = nal_types(data)
        in_band = parameter_sets_of(data)
        if in_band:
            self._inband_sets = in_band
        fixed = ensure_parameter_sets(data, self.parameter_sets)
        prepended = fixed is not data
        if prepended:
            self.prepended += 1
        if NAL_IDR in types:
            self.idr_count += 1
        self.packets_out += 1
        self.bytes_out += len(fixed)
        if self.frames_in_before_first_packet is None:
            self.frames_in_before_first_packet = self.frames_in
        if self.first_au is None or (NAL_IDR in types and self.first_idr is None):
            facts = {
                "nals": ",".join(str(t) for t in nal_types(fixed)[:FACT_NALS_MAX]),
                "au_bytes": len(fixed),
                "form": form,
                "sps": sps_triple(fixed),
                "prepended": prepended,
                "frames_in": self.frames_in,
            }
            if self.first_au is None:
                self.first_au = facts
            if NAL_IDR in types and self.first_idr is None:
                self.first_idr = facts
        new = av.Packet(fixed)
        new.pts = packet.pts
        new.dts = packet.dts
        new.time_base = packet.time_base if packet.time_base else VIDEO_TIME_BASE
        return new


def probe_encoders(
    candidates: list[EncoderChoice],
    width: int = LEVEL_31_LONG,
    height: int = LEVEL_31_SHORT,
    failures: list[ProbeFailure] | None = None,
) -> EncoderChoice:
    """Open each candidate at ``width`` x ``height`` (the session's real output size) and encode one black frame;
    the first that works wins. Every candidate that does not is appended to ``failures`` with FFmpeg's reason.
    ``SCREEN_ENCODER_UNAVAILABLE`` when none works."""
    import av

    def failed(choice: EncoderChoice, reason: str) -> None:
        if failures is not None:
            failures.append(ProbeFailure(choice.label, reason))

    for choice in candidates:
        if choice.kind == "vp8":
            if "libvpx" in av.codecs_available:
                return choice
            failed(choice, "libvpx is not in this build")
            continue
        if choice.codec_name not in av.codecs_available:
            failed(choice, f"{choice.codec_name} is not in this build")
            continue
        reason = _encode_one_frame(choice, width, height)
        if reason is None:
            return choice
        failed(choice, reason)
    raise ProtocolError("SCREEN_ENCODER_UNAVAILABLE", "Your PC has no video encoder DoMe can use for Screen view.")


def _encode_one_frame(choice: EncoderChoice, width: int, height: int) -> str | None:
    """Open the candidate and encode one black frame: None when it works, else why not (FFmpeg's words)."""
    import av

    from .capture_dda import AV_LOG_ERROR, ffmpeg_error_text, ffmpeg_log_lines

    try:
        enc = H264Encoder(choice, width, height, 30, 500)
    except EncoderOpenError as exc:
        return f"open: {exc}"
    except Exception as exc:  # noqa: BLE001 - an unknown codec, a size the context refuses
        return f"open: {ffmpeg_error_text(exc)}"
    try:
        frame = av.VideoFrame(width, height, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.pts = 0
        frame.time_base = VIDEO_TIME_BASE
        with ffmpeg_log_lines(AV_LOG_ERROR) as logged:
            try:
                enc.encode(frame, force_idr=True)
            except Exception as exc:  # noqa: BLE001 - opened but cannot encode
                return f"encode: {ffmpeg_error_text(exc, logged)}"
    finally:
        enc.close()
    return None


def list_encoders(*, dev_h264: bool) -> list[dict[str, Any]]:
    """``--list-encoders``: every candidate and whether it works here at 1280x720 (never any frame content); a
    candidate that does not work carries FFmpeg's reason."""
    rows: list[dict[str, Any]] = []
    for choice in encoder_candidates(["h264", "vp8"], dev_h264=dev_h264):
        failures: list[ProbeFailure] = []
        try:
            probe_encoders([choice], failures=failures)
            ok, why = True, ""
        except ProtocolError as exc:
            ok, why = False, exc.code
        row: dict[str, Any] = {"encoder": choice.label, "kind": choice.kind, "available": ok, "note": why}
        if failures:
            row["reason"] = failures[0].reason
        rows.append(row)
    return rows


__all__ = [
    "DEV_H264_CODECS",
    "NAL_AUD",
    "NAL_IDR",
    "NAL_PPS",
    "NAL_SPS",
    "VIDEO_TIME_BASE",
    "EncoderChoice",
    "EncoderOpenError",
    "H264Encoder",
    "ProbeFailure",
    "avcc_to_annexb",
    "context_flags",
    "encoder_candidates",
    "ensure_parameter_sets",
    "extradata_to_annexb",
    "fit_level_31",
    "list_encoders",
    "mft_name",
    "nal_types",
    "parameter_sets_of",
    "plausible_nal_header",
    "probe_encoders",
    "split_nals",
    "sps_triple",
    "to_annexb",
]
